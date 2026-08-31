from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from jarvis.desktop import (
    CompanionBridge,
    CompanionController,
    CompanionStatus,
    HudCompanionBridge,
    WindowsDesktopLayer,
    _fit_companion_box,
)
from jarvis.preferences import PreferencesService, PreferencesUpdate


class _FakeWindow:
    def __init__(self, *, fail_show: bool = False) -> None:
        self.native = object()
        self.fail_show = fail_show
        self.calls: list[str] = []
        self.scripts: list[str] = []

    def show(self) -> None:
        self.calls.append("show")
        if self.fail_show:
            raise RuntimeError("simulated native show failure")

    def hide(self) -> None:
        self.calls.append("hide")

    def restore(self) -> None:
        self.calls.append("restore")

    def destroy(self) -> None:
        self.calls.append("destroy")

    def evaluate_js(self, script: str) -> None:
        self.scripts.append(script)


class _FakeLayer:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.apply_calls: list[tuple[int, str, bool]] = []
        self.shown_without_activation = 0
        self.activated = 0

    def apply(self, _window, preference, *, visible: bool = True) -> CompanionStatus:
        self.apply_calls.append(
            (
                preference.transparent_globe_size,
                preference.transparent_globe_position,
                visible,
            )
        )
        if not self.available:
            return CompanionStatus(
                mode="full_app_fallback",
                fallback_reason="simulated unsupported host",
            )
        return CompanionStatus(
            active=visible,
            mode="desktop_child" if visible else "full_app",
            companion_available=True,
            companion_visible=visible,
            desktop_attached=True,
            true_alpha=True,
            non_activating=True,
            taskbar_hidden=True,
        )

    def show_without_activation(self, _window) -> None:
        self.shown_without_activation += 1

    def activate(self, _window) -> None:
        self.activated += 1


class _FakeHotkey:
    def __init__(self, callback) -> None:
        self.callback = callback
        self.started = 0
        self.stopped = 0

    def start(self) -> bool:
        self.started += 1
        return True

    def stop(self) -> None:
        self.stopped += 1


def _controller(
    tmp_path: Path,
    *,
    initial_visible: bool = False,
    globe: _FakeWindow | None = None,
    layer: _FakeLayer | None = None,
) -> tuple[
    CompanionController,
    _FakeWindow,
    _FakeWindow,
    _FakeLayer,
    PreferencesService,
]:
    preferences = PreferencesService(tmp_path / "settings.json")
    if initial_visible:
        preferences.update(
            PreferencesUpdate.model_validate(
                {"desktop": {"transparent_globe_enabled": True}}
            )
        )
    full = _FakeWindow()
    companion = globe or _FakeWindow()
    desktop_layer = layer or _FakeLayer()
    controller = CompanionController(
        full,
        companion,
        preferences.desktop(),
        tmp_path,
        layer=desktop_layer,
        preferences=preferences,
        initial_visible=initial_visible,
    )
    controller.hotkey = _FakeHotkey(controller.restore_full_app)
    return controller, full, companion, desktop_layer, preferences


def _public_methods(instance: object) -> set[str]:
    return {
        name
        for name, member in inspect.getmembers(type(instance), predicate=inspect.isfunction)
        if not name.startswith("_")
    }


def test_hud_bridge_exposes_only_the_strict_boolean_visibility_action() -> None:
    bridge = HudCompanionBridge()

    assert _public_methods(bridge) == {"set_companion_visibility"}
    for unsafe_name in (
        "run_command",
        "execute_tool",
        "stage_command",
        "open_destination",
        "exit_app",
        "update_preferences",
        "status",
    ):
        assert not hasattr(bridge, unsafe_name)
    with pytest.raises(TypeError, match="boolean"):
        bridge.set_companion_visibility(1)  # type: ignore[arg-type]


def test_hud_bridge_reloads_only_schema_validated_size_and_position(
    tmp_path: Path,
) -> None:
    controller, full, globe, layer, preferences = _controller(tmp_path)
    bridge = HudCompanionBridge()
    bridge._bind(controller)
    preferences.update(
        PreferencesUpdate.model_validate(
            {
                "desktop": {
                    "transparent_globe_size": 1200,
                    "transparent_globe_position": "bottom_left",
                }
            }
        )
    )

    result = bridge.set_companion_visibility(True)

    assert result["applied"] is True
    assert layer.apply_calls[-1] == (1200, "bottom_left", False)
    assert "hide" not in full.calls
    assert globe.calls == ["show"]
    with pytest.raises(ValueError):
        PreferencesUpdate.model_validate(
            {"desktop": {"transparent_globe_size": 1201}}
        )
    with pytest.raises(ValueError):
        PreferencesUpdate.model_validate(
            {"desktop": {"transparent_globe_position": "top_left"}}
        )


def test_live_enable_keeps_the_full_hud_visible_and_disable_hides_companion(
    tmp_path: Path,
) -> None:
    controller, full, globe, layer, preferences = _controller(tmp_path)
    controller.start()
    assert full.calls == ["show"]
    assert globe.calls == ["hide"]
    assert controller.status()["companion_available"] is True
    assert controller.status()["companion_visible"] is False

    enabled = controller.set_companion_visibility(True)

    assert enabled["applied"] is True
    assert enabled["runtime"]["companion_visible"] is True
    assert "hide" not in full.calls
    assert layer.shown_without_activation == 1
    assert preferences.desktop().transparent_globe_enabled is True

    disabled = controller.set_companion_visibility(False)

    assert disabled["applied"] is True
    assert disabled["runtime"]["active"] is False
    assert disabled["runtime"]["companion_visible"] is False
    assert globe.calls[-1] == "hide"
    assert preferences.desktop().transparent_globe_enabled is False


def test_show_failure_fails_closed_and_restores_the_full_hud(tmp_path: Path) -> None:
    globe = _FakeWindow(fail_show=True)
    controller, full, globe, layer, preferences = _controller(tmp_path, globe=globe)

    result = controller.set_companion_visibility(True)

    assert result["applied"] is False
    assert result["preference_enabled"] is False
    assert result["runtime"]["active"] is False
    assert result["runtime"]["companion_visible"] is False
    assert result["runtime"]["mode"] == "full_app_fallback"
    assert result["runtime"]["fallback_reason"] == (
        "Live companion show failed safely: RuntimeError"
    )
    assert full.calls == ["show", "show", "restore"]
    assert globe.calls == ["show", "hide"]
    assert layer.activated == 1
    assert preferences.desktop().transparent_globe_enabled is False


def test_saved_off_state_allows_normal_hud_close_without_reenabling_globe(
    tmp_path: Path,
) -> None:
    controller, _full, globe, _layer, preferences = _controller(
        tmp_path, initial_visible=True
    )
    controller.start()
    controller.set_companion_visibility(False)
    globe.calls.clear()

    close_result = controller.on_full_closing()

    assert close_result is None
    assert globe.calls == []
    assert preferences.desktop().transparent_globe_enabled is False
    assert controller.status()["companion_visible"] is False


def test_registered_hotkey_callback_restores_hud_without_executing_or_navigating(
    tmp_path: Path,
) -> None:
    controller, full, _globe, layer, _preferences = _controller(
        tmp_path, initial_visible=True
    )
    controller.start()
    full.calls.clear()

    controller.hotkey.callback()

    assert full.calls == ["show", "restore"]
    assert full.scripts == []
    assert layer.activated == 1
    assert controller.status()["companion_visible"] is True


def test_companion_navigation_remains_a_fixed_nonexecuting_allowlist(
    tmp_path: Path,
) -> None:
    controller, full, _globe, _layer, _preferences = _controller(tmp_path)
    bridge = CompanionBridge()
    bridge._bind(controller)

    for destination in ("projects", "settings", "angerona", "models", "federation"):
        controller.open_destination(destination)
    approved_scripts = list(full.scripts)
    for destination in (
        "run-command",
        "tools",
        "javascript:alert(1)",
        "../../settings",
        "",
    ):
        with pytest.raises(ValueError, match="Unknown companion destination"):
            bridge.open_destination(destination)

    assert full.scripts == approved_scripts
    controller.stage_command("display system status")
    assert "requestSubmit" not in full.scripts[-1]
    assert "fetch(" not in full.scripts[-1]


def test_companion_bridge_exposes_only_fixed_actions_and_no_controller_graph() -> None:
    bridge = CompanionBridge()

    public_names = {name for name in dir(bridge) if not name.startswith("_")}

    assert public_names == {
        "exit_app",
        "open_destination",
        "restore_full_app",
        "stage_command",
        "status",
    }
    assert not hasattr(bridge, "controller")


@pytest.mark.parametrize("position", ["bottom_left", "bottom_right"])
@pytest.mark.parametrize(
    ("width", "height", "scale"),
    [(320, 240, 1.0), (320, 240, 2.0), (80, 60, 1.5), (1, 1, 2.0)],
)
def test_companion_box_clamps_large_size_to_every_work_area(
    position: str,
    width: int,
    height: int,
    scale: float,
) -> None:
    size, total_height, x, y = _fit_companion_box(
        1200, position, width, height, scale=scale
    )

    assert 1 <= size <= width
    assert 1 <= total_height <= height
    assert 0 <= x < width
    assert 0 <= y < height
    assert x + size <= width
    assert y + total_height <= height


class _SmallWorkAreaWin32:
    def __init__(self) -> None:
        self.positions: list[tuple[int, ...]] = []
        self._region = 100

    def GetClientRect(self, _host, pointer) -> bool:
        pointer._obj.right = 320
        pointer._obj.bottom = 240
        return True

    def GetDpiForWindow(self, _hwnd) -> int:
        return 192

    def FindWindowExW(self, _host, _after, _name, _title) -> int:
        return 44

    def SetWindowPos(self, *args) -> bool:
        self.positions.append(tuple(int(value) for value in args))
        return True

    def CreateEllipticRgn(self, *_args) -> int:
        self._region += 1
        return self._region

    def CreateRectRgn(self, *_args) -> int:
        self._region += 1
        return self._region

    def CombineRgn(self, *_args) -> int:
        return 1

    def SetWindowRgn(self, *_args) -> bool:
        return True

    def DeleteObject(self, *_args) -> bool:
        return True


def test_windows_layer_uses_the_work_area_clamp_for_actual_positioning() -> None:
    api = _SmallWorkAreaWin32()
    layer = WindowsDesktopLayer(
        user32=api,
        kernel32=api,
        gdi32=api,
        dwmapi=api,
        platform_name="nt",
    )
    preference = PreferencesService.defaults().desktop.model_copy(
        update={
            "transparent_globe_size": 1200,
            "transparent_globe_position": "bottom_right",
        }
    )

    assert layer._position_in_host(42, 43, preference, visible=False) is True

    _hwnd, _insert_after, x, y, width, height, flags = api.positions[-1]
    assert x >= 0 and y >= 0
    assert x + width <= 320
    assert y + height <= 240
    assert not flags & layer.SWP_SHOWWINDOW
