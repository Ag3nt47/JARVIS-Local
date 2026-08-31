from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import jarvis.desktop as desktop_module
from jarvis.desktop import (
    COMPANION_HTML,
    LOOPBACK_HOST,
    CompanionController,
    CompanionStatus,
    HudCompanionBridge,
    WindowsDesktopLayer,
    _companion_requested,
    _initial_companion_geometry,
    _initial_hud_geometry,
    _initial_hud_position,
    _write_desktop_status,
    desktop_settings,
)
from jarvis.preferences import PreferencesService, PreferencesUpdate


def test_preferences_persist_brain_drive_and_never_expose_signing_key(
    tmp_path: Path, monkeypatch
) -> None:
    private_seed = "eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg"
    monkeypatch.setenv("JARVIS_FEDERATION_PRIVATE_KEY", private_seed)
    path = tmp_path / "settings.json"
    service = PreferencesService(path)

    first = service.public(ollama_ready=True)

    assert first["settings"]["federation"]["signing_key_configured"] is True
    assert first["settings"]["federation"]["shared_roots"][0]["path"] == (r"F:\BRAIN DRIVE\_root")
    assert private_seed not in path.read_text(encoding="utf-8")

    service.update(
        PreferencesUpdate.model_validate(
            {
                "instance_name": "Mac Compute",
                "federation": {
                    "enabled": True,
                    "admin_capable": True,
                    "data_enabled": True,
                    "peer_urls": ["http://100.64.0.2:9443"],
                },
                "integrations": {"tailscale": True},
            }
        )
    )
    reloaded = PreferencesService(path).public()
    assert reloaded["settings"]["instance_name"] == "Mac Compute"
    assert reloaded["settings"]["federation"]["admin_capable"] is True
    assert reloaded["settings"]["federation"]["require_target_approval"] is True
    integrations = {item["id"]: item for item in reloaded["settings"]["integrations"]}
    assert integrations["tailscale"]["enabled"] is True


def test_preferences_reject_unknown_integration(tmp_path: Path) -> None:
    service = PreferencesService(tmp_path / "settings.json")
    with pytest.raises(ValueError, match="Unknown integrations"):
        service.update(PreferencesUpdate.model_validate({"integrations": {"remote_shell": True}}))


def test_desktop_settings_use_persistent_data_and_scoped_workspace(
    tmp_path: Path, monkeypatch
) -> None:
    asset_root = tmp_path / "app"
    config_dir = asset_root / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "default.toml").write_text(
        """[system]
host = "127.0.0.1"
port = 8765
[ollama]
base_url = "http://127.0.0.1:11434"
chat_model = "llama3:latest"
embedding_model = "nomic-embed-text:latest"
""",
        encoding="utf-8",
    )
    data_root = tmp_path / "persistent"
    workspace = tmp_path / "workspace"
    brain_root = tmp_path / "brain"
    monkeypatch.setenv("JARVIS_DESKTOP_DATA_DIR", str(data_root))
    monkeypatch.setenv("JARVIS_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("JARVIS_BRAIN_ROOT", str(brain_root))

    settings = desktop_settings(asset_root)

    assert settings.host == LOOPBACK_HOST
    assert settings.data_dir == data_root.resolve()
    assert settings.allowed_roots == (
        workspace.resolve(),
        brain_root.resolve(),
        asset_root.resolve(),
    )
    assert data_root.is_dir()
    assert workspace.is_dir()
    assert json.loads((PreferencesService(data_root / "settings.json").path).read_text())


def test_retired_globe_preference_remains_load_compatible_but_is_not_advertised(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.json"
    service = PreferencesService(path)
    default = service.public()["settings"]["desktop"]
    assert default["transparent_globe_enabled"] is False
    assert default["privacy_mode_enabled"] is False
    assert default["transparent_globe_size"] == 720
    assert default["transparent_globe_size_min"] == 280
    assert default["transparent_globe_size_max"] == 1200
    assert default["return_hotkey"] == "Ctrl+Alt+J"
    assert default["runtime"]["active"] is False

    service.update(
        PreferencesUpdate.model_validate(
            {
                "desktop": {
                    "privacy_mode_enabled": True,
                    "transparent_globe_enabled": True,
                    "transparent_globe_size": 420,
                    "transparent_globe_position": "bottom_left",
                }
            }
        )
    )
    persisted = PreferencesService(path).public()
    desktop = persisted["settings"]["desktop"]
    assert desktop["transparent_globe_enabled"] is False
    assert desktop["privacy_mode_enabled"] is True
    assert desktop["transparent_globe_supported"] is False
    assert desktop["live_toggle_available"] is False
    assert desktop["transparent_globe_size"] == 420
    assert desktop["transparent_globe_position"] == "bottom_left"
    assert persisted["restart_required"] is False

    with pytest.raises(ValueError):
        PreferencesUpdate.model_validate({"desktop": {"transparent_globe_size": 100}})
    with pytest.raises(ValueError):
        PreferencesUpdate.model_validate({"desktop": {"transparent_globe_size": 1201}})


def test_legacy_default_globe_size_migrates_once(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    PreferencesService(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["desktop"] = {
        "transparent_globe_enabled": False,
        "transparent_globe_size": 380,
        "transparent_globe_position": "bottom_right",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    migrated = PreferencesService(path).desktop()

    assert migrated.transparent_globe_size == 720
    assert json.loads(path.read_text(encoding="utf-8"))["desktop"][
        "companion_layout_version"
    ] == 2


def test_retired_companion_cannot_be_enabled_by_environment(monkeypatch) -> None:
    preference = PreferencesService.defaults().desktop
    assert _companion_requested(preference) is False
    monkeypatch.setenv("JARVIS_GLOBE_COMPANION", "yes")
    assert _companion_requested(preference) is False
    monkeypatch.setenv("JARVIS_GLOBE_COMPANION", "off")
    assert _companion_requested(preference) is False


def test_desktop_main_creates_only_dashboard_and_clears_saved_globe(
    tmp_path: Path, monkeypatch
) -> None:
    preferences = PreferencesService(tmp_path / "settings.json")
    preferences.update(
        PreferencesUpdate.model_validate(
            {"desktop": {"transparent_globe_enabled": True}}
        )
    )
    created_windows: list[dict[str, object]] = []
    started: list[dict[str, object]] = []

    class FakeServer:
        url = "http://127.0.0.1:8765/"

        def __init__(self, _settings: object) -> None:
            self.stopped = False

        def start(self) -> None:
            return None

        def stop(self) -> None:
            self.stopped = True

    fake_webview = SimpleNamespace(
        settings={},
        create_window=lambda *args, **kwargs: created_windows.append(kwargs) or object(),
        start=lambda **kwargs: started.append(kwargs),
    )
    monkeypatch.setattr(
        desktop_module,
        "desktop_settings",
        lambda: SimpleNamespace(data_dir=tmp_path),
    )
    monkeypatch.setattr(desktop_module, "LocalServer", FakeServer)
    monkeypatch.setattr(desktop_module.multiprocessing, "freeze_support", lambda: None)
    monkeypatch.setitem(sys.modules, "webview", fake_webview)

    assert desktop_module.main() == 0
    assert len(created_windows) == 1
    assert "html" not in created_windows[0]
    assert "js_api" not in created_windows[0]
    assert len(started) == 1
    assert preferences.desktop().transparent_globe_enabled is False


def test_full_hud_geometry_stays_inside_the_work_area() -> None:
    width, height, minimum = _initial_hud_geometry((1366, 728))
    assert (width, height) == (1270, 664)
    assert minimum == (640, 480)
    assert _initial_hud_position(width, height, (0, 0, 1366, 728)) == (48, 32)


@pytest.mark.parametrize(
    ("work_area", "expected_geometry"),
    [
        ((1920, 1040), (1440, 900, (640, 480))),
        ((800, 600), (704, 536, (640, 480))),
        ((640, 480), (544, 416, (544, 416))),
        ((400, 300), (400, 300, (400, 300))),
        ((240, 180), (240, 180, (240, 180))),
    ],
)
def test_full_hud_geometry_scales_down_for_small_work_areas(
    work_area: tuple[int, int],
    expected_geometry: tuple[int, int, tuple[int, int]],
) -> None:
    geometry = _initial_hud_geometry(work_area)

    assert geometry == expected_geometry
    width, height, minimum = geometry
    assert 1 <= minimum[0] <= width <= work_area[0]
    assert 1 <= minimum[1] <= height <= work_area[1]


def test_runtime_status_is_public_only_while_its_process_is_alive(tmp_path: Path) -> None:
    service = PreferencesService(tmp_path / "settings.json")
    _write_desktop_status(
        tmp_path,
        CompanionStatus(
            active=True,
            mode="desktop_child",
            desktop_attached=True,
            true_alpha=True,
            non_activating=True,
            taskbar_hidden=True,
            hotkey_registered=True,
        ),
    )
    runtime = service.public()["settings"]["desktop"]["runtime"]
    assert runtime["active"] is True
    assert runtime["mode"] == "desktop_child"
    assert "pid" not in runtime
    assert "process_created_at" not in runtime

    payload = json.loads((tmp_path / "desktop-status.json").read_text(encoding="utf-8"))
    payload["pid"] = max(os.getpid() + 1_000_000, 2_000_000)
    (tmp_path / "desktop-status.json").write_text(json.dumps(payload), encoding="utf-8")
    stale = service.public()["settings"]["desktop"]["runtime"]
    assert stale["active"] is False
    assert stale["mode"] == "inactive"


class _FakeHandle:
    def ToInt64(self) -> int:
        return 42


class _FakeNative:
    Handle = _FakeHandle()


class _FakeWindow:
    def __init__(self) -> None:
        self.native = _FakeNative()
        self.calls: list[str] = []
        self.scripts: list[str] = []

    def hide(self) -> None:
        self.calls.append("hide")

    def show(self) -> None:
        self.calls.append("show")

    def restore(self) -> None:
        self.calls.append("restore")

    def destroy(self) -> None:
        self.calls.append("destroy")

    def evaluate_js(self, script: str) -> None:
        self.scripts.append(script)


class _ShowFailingWindow(_FakeWindow):
    def show(self) -> None:
        self.calls.append("show")
        raise RuntimeError("native show failed")


class _FakeHotkey:
    def __init__(self) -> None:
        self.stopped = False

    def start(self) -> bool:
        return True

    def stop(self) -> None:
        self.stopped = True


class _FakeLayer:
    def __init__(self, status: CompanionStatus) -> None:
        self.status = status
        self.shown = 0
        self.activated = 0
        self.visible_requests: list[bool] = []

    def apply(self, _window, _preference, *, visible: bool = True) -> CompanionStatus:
        self.visible_requests.append(visible)
        return self.status

    def show_without_activation(self, _window) -> None:
        self.shown += 1

    def activate(self, _window) -> None:
        self.activated += 1


def test_companion_controller_restores_and_navigates_only_fixed_destinations(
    tmp_path: Path,
) -> None:
    full = _FakeWindow()
    globe = _FakeWindow()
    layer = _FakeLayer(
        CompanionStatus(
            active=True,
            mode="desktop_child",
            companion_available=True,
            companion_visible=True,
            desktop_attached=True,
            true_alpha=True,
            non_activating=True,
            taskbar_hidden=True,
        )
    )
    controller = CompanionController(
        full,
        globe,
        PreferencesService.defaults().desktop.model_copy(
            update={"transparent_globe_enabled": True}
        ),
        tmp_path,
        layer=layer,
    )
    controller.hotkey = _FakeHotkey()
    controller.start()
    assert full.calls == ["show"]
    assert controller.status()["hotkey_registered"] is True

    controller.open_destination("projects")
    assert "projectsPanel" in full.scripts[-1]
    assert full.calls[-2:] == ["show", "restore"]
    with pytest.raises(ValueError, match="Unknown companion destination"):
        controller.open_destination("run-command")

    controller.stage_command('review "safe" draft')
    assert "chatInput" in full.scripts[-1]
    assert 'review \\"safe\\" draft' in full.scripts[-1]
    assert "requestSubmit" not in full.scripts[-1]
    with pytest.raises(ValueError, match="1 to 2000"):
        controller.stage_command("")

    assert controller.on_full_closing() is False
    assert full.calls[-1] == "hide"
    assert globe.calls[-1] == "show"
    assert controller.status()["active"] is True
    controller.stop()


def test_hud_bridge_live_toggle_persists_without_hiding_full_hud(tmp_path: Path) -> None:
    full = _FakeWindow()
    globe = _FakeWindow()
    preferences = PreferencesService(tmp_path / "settings.json")
    layer = _FakeLayer(
        CompanionStatus(
            mode="full_app",
            companion_available=True,
            companion_visible=False,
            desktop_attached=True,
            true_alpha=True,
            non_activating=True,
            taskbar_hidden=True,
        )
    )
    controller = CompanionController(
        full,
        globe,
        preferences.desktop(),
        tmp_path,
        layer=layer,
        preferences=preferences,
    )
    controller.hotkey = _FakeHotkey()
    controller.start()
    bridge = HudCompanionBridge()
    bridge._bind(controller)
    public_methods = {
        name
        for name in dir(bridge)
        if not name.startswith("_") and callable(getattr(bridge, name))
    }
    assert public_methods == {"set_companion_visibility"}

    enabled = bridge.set_companion_visibility(True)

    assert enabled["applied"] is True
    assert enabled["preference_enabled"] is True
    assert enabled["runtime"]["companion_visible"] is True
    assert "hide" not in full.calls
    assert PreferencesService(tmp_path / "settings.json").desktop().transparent_globe_enabled
    public_desktop = PreferencesService(tmp_path / "settings.json").public()["settings"][
        "desktop"
    ]
    assert public_desktop["transparent_globe_enabled"] is False
    assert public_desktop["transparent_globe_supported"] is False
    assert public_desktop["live_toggle_available"] is False
    assert public_desktop["runtime"]["companion_visible"] is True
    assert public_desktop["restart_required"] is False
    assert layer.visible_requests == [False, False]

    disabled = bridge.set_companion_visibility(False)
    assert disabled["applied"] is True
    assert disabled["runtime"]["companion_visible"] is False
    assert not PreferencesService(tmp_path / "settings.json").desktop().transparent_globe_enabled
    with pytest.raises(TypeError, match="boolean"):
        bridge.set_companion_visibility("yes")  # type: ignore[arg-type]
    controller.stop()


def test_live_show_failure_preserves_hud_and_saved_off_state(tmp_path: Path) -> None:
    full = _FakeWindow()
    globe = _ShowFailingWindow()
    preferences = PreferencesService(tmp_path / "settings.json")
    layer = _FakeLayer(
        CompanionStatus(
            mode="full_app",
            companion_available=True,
            desktop_attached=True,
            true_alpha=True,
        )
    )
    controller = CompanionController(
        full,
        globe,
        preferences.desktop(),
        tmp_path,
        layer=layer,
        preferences=preferences,
    )
    controller.hotkey = _FakeHotkey()
    controller.start()

    result = controller.set_companion_visibility(True)

    assert result["applied"] is False
    assert result["runtime"]["companion_visible"] is False
    assert full.calls[-2:] == ["show", "restore"]
    assert layer.activated == 1
    assert not PreferencesService(tmp_path / "settings.json").desktop().transparent_globe_enabled
    assert controller.on_full_closing() is None
    controller.stop()


def test_initial_companion_geometry_clamps_large_pet_to_work_area() -> None:
    preference = PreferencesService.defaults().desktop.model_copy(
        update={"transparent_globe_size": 1200}
    )

    large = _initial_companion_geometry(preference, (0, 0, 1920, 1440))
    compact = _initial_companion_geometry(preference, (10, 20, 650, 500))

    assert large == (1200, 1300, 692, 112)
    size, height, x, y = compact
    assert size < 1200
    assert 10 <= x and x + size <= 650
    assert 20 <= y and y + height <= 500
    default = _initial_companion_geometry(
        PreferencesService.defaults().desktop, (0, 0, 1536, 864)
    )
    assert default[:2] == (720, 820)


class _FakeWin32:
    def __init__(self, *, composition: bool = True) -> None:
        self.composition = composition
        self.last_error = 0
        self.styles = {
            WindowsDesktopLayer.GWL_STYLE: WindowsDesktopLayer.WS_POPUP,
            WindowsDesktopLayer.GWL_EXSTYLE: WindowsDesktopLayer.WS_EX_APPWINDOW,
        }
        self.parent: tuple[int, int] | None = None
        self.positions: list[tuple[int, ...]] = []
        self._region = 1000

    def DwmIsCompositionEnabled(self, pointer) -> int:
        pointer._obj.value = int(self.composition)
        return 0

    def FindWindowW(self, _class_name, _title) -> int:
        return 100

    def FindWindowExW(self, hwnd, _after, class_name, _title) -> int:
        return 101 if hwnd == 100 and class_name == "SHELLDLL_DefView" else 0

    def GetWindowLongPtrW(self, _hwnd, index) -> int:
        return self.styles[index]

    def SetWindowLongPtrW(self, _hwnd, index, value) -> int:
        previous = self.styles[index]
        self.styles[index] = value
        return previous

    def SetLastError(self, value) -> None:
        self.last_error = value

    def GetLastError(self) -> int:
        return self.last_error

    def SetParent(self, hwnd, parent) -> int:
        self.parent = (hwnd, parent)
        return 0

    def GetClientRect(self, _hwnd, pointer) -> bool:
        pointer._obj.right = 1920
        pointer._obj.bottom = 1080
        return True

    def GetDpiForWindow(self, _hwnd) -> int:
        return 96

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

    def DeleteObject(self, _region) -> bool:
        return True

    def SetWindowPos(self, *args) -> bool:
        self.positions.append(tuple(int(value) for value in args))
        return True

    def ShowWindow(self, *_args) -> bool:
        return True


def test_windows_layer_attaches_below_desktop_icons_with_nonactivating_styles() -> None:
    api = _FakeWin32()
    layer = WindowsDesktopLayer(
        user32=api,
        kernel32=api,
        gdi32=api,
        dwmapi=api,
        platform_name="nt",
    )
    preference = PreferencesService.defaults().desktop
    outcome = layer.apply(_FakeWindow(), preference)
    assert outcome.mode == "desktop_child"
    assert outcome.desktop_attached is True
    assert outcome.true_alpha is True
    assert outcome.non_activating is True
    assert outcome.taskbar_hidden is True
    assert api.parent == (42, 100)
    assert api.styles[layer.GWL_STYLE] & layer.WS_CHILD
    assert not api.styles[layer.GWL_STYLE] & layer.WS_POPUP
    assert api.styles[layer.GWL_EXSTYLE] & layer.WS_EX_NOACTIVATE
    assert not api.styles[layer.GWL_EXSTYLE] & layer.WS_EX_APPWINDOW
    # The companion is directly behind SHELLDLL_DefView (desktop icons), not
    # HWND_BOTTOM where a wallpaper provider can paint over it.
    assert api.positions[-1][1] == 101


def test_windows_layer_falls_back_to_full_app_without_true_alpha() -> None:
    api = _FakeWin32(composition=False)
    layer = WindowsDesktopLayer(
        user32=api,
        kernel32=api,
        gdi32=api,
        dwmapi=api,
        platform_name="nt",
    )
    outcome = layer.apply(_FakeWindow(), PreferencesService.defaults().desktop)
    assert outcome.active is False
    assert outcome.mode == "full_app_fallback"
    assert "true-alpha" in (outcome.fallback_reason or "")


def test_companion_document_has_blueprint_nodes_and_accessible_menu() -> None:
    for label in ("PROJECTS", "ANGERONA", "MCP / OLLAMA", "COMMS"):
        assert label in COMPANION_HTML
    for action in ("Open HUD", "Projects", "Settings", "Pause motion", "Exit"):
        assert action in COMPANION_HTML
    assert 'role="menu"' in COMPANION_HTML
    assert "open_destination" in COMPANION_HTML
    assert "stage_command" in COMPANION_HTML
    assert "voiceDraft" in COMPANION_HTML


def test_dashboard_exposes_capability_deck_privacy_and_due_reminders() -> None:
    root = Path(__file__).resolve().parents[1]
    document = (root / "web" / "index.html").read_text(encoding="utf-8")
    script = (root / "web" / "app.js").read_text(encoding="utf-8")

    for element_id in (
        "openCommandPalette",
        "commandPaletteDialog",
        "commandPaletteInput",
        "privacyModeToggle",
        "taskDueAt",
    ):
        assert f'id="{element_id}"' in document
    assert "styles.css?app_version=0.4.0" in document
    assert "app.js?app_version=0.4.0" in document
    assert 'event.key.toLowerCase() === "k"' in script
    assert 'id: "read_clipboard"' in script
