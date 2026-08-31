"""Persistent, non-secret operator preferences for desktop and peer resources."""

from __future__ import annotations

import base64
import binascii
import json
import os
import platform
import shutil
import socket
import tempfile
import uuid
from pathlib import Path
from typing import Literal

import psutil
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class PreferenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SharedRootPreference(PreferenceModel):
    path: str = Field(min_length=1, max_length=4096)
    permissions: list[Literal["list", "read", "compute"]] = Field(
        default_factory=lambda: ["list", "read"], min_length=1, max_length=3
    )


class FederationPreference(PreferenceModel):
    enabled: bool = False
    transport: Literal["tailscale"] = "tailscale"
    peer_urls: list[HttpUrl] = Field(default_factory=list, max_length=16)
    compute_enabled: bool = True
    data_enabled: bool = False
    admin_capable: bool = False
    require_target_approval: bool = True
    shared_roots: list[SharedRootPreference] = Field(default_factory=list, max_length=16)

    @field_validator("peer_urls")
    @classmethod
    def peer_urls_cannot_contain_credentials_or_queries(cls, urls: list[HttpUrl]) -> list[HttpUrl]:
        if any(url.username or url.password or url.query or url.fragment for url in urls):
            raise ValueError("Peer URLs cannot contain credentials, queries, or fragments")
        return urls

    @model_validator(mode="after")
    def admin_requires_local_approval(self) -> FederationPreference:
        if self.admin_capable and not self.require_target_approval:
            raise ValueError("Admin-capable mode always requires target-local approval")
        return self


class DesktopPreference(PreferenceModel):
    """Non-secret desktop shell and local privacy choices."""

    adaptive_dashboard_enabled: bool = True
    gesture_control_enabled: bool = False
    gesture_low_power_mode: bool = False
    privacy_mode_enabled: bool = False
    transparent_globe_enabled: bool = False
    transparent_globe_size: int = Field(default=720, ge=280, le=1200)
    transparent_globe_position: Literal["bottom_left", "bottom_right"] = "bottom_right"
    companion_layout_version: Literal[2] = 2


INTEGRATION_IDS = (
    "angerona",
    "ollama",
    "tailscale",
    "signal",
    "mdns",
    "wake_on_lan",
    "ups",
    "ddc_ci",
    "vision",
    "backup",
    "openai",
)


class OperatorPreferences(PreferenceModel):
    instance_name: str = Field(default_factory=socket.gethostname, min_length=1, max_length=100)
    instance_id: str = Field(default_factory=lambda: str(uuid.uuid4()), pattern=r"^[a-f0-9-]{36}$")
    federation: FederationPreference = Field(default_factory=FederationPreference)
    desktop: DesktopPreference = Field(default_factory=DesktopPreference)
    integrations: dict[str, bool] = Field(
        default_factory=lambda: {
            integration_id: integration_id in {"angerona", "ollama"}
            for integration_id in INTEGRATION_IDS
        }
    )

    @model_validator(mode="after")
    def restrict_integrations(self) -> OperatorPreferences:
        unknown = set(self.integrations) - set(INTEGRATION_IDS)
        if unknown:
            raise ValueError(f"Unknown integrations: {', '.join(sorted(unknown))}")
        self.integrations = {
            integration_id: bool(self.integrations.get(integration_id, False))
            for integration_id in INTEGRATION_IDS
        }
        return self


class FederationUpdate(PreferenceModel):
    enabled: bool | None = None
    peer_urls: list[HttpUrl] | None = Field(default=None, max_length=16)
    compute_enabled: bool | None = None
    data_enabled: bool | None = None
    admin_capable: bool | None = None
    shared_roots: list[SharedRootPreference] | None = Field(default=None, max_length=16)

    @field_validator("peer_urls")
    @classmethod
    def peer_urls_cannot_contain_credentials_or_queries(
        cls, urls: list[HttpUrl] | None
    ) -> list[HttpUrl] | None:
        if urls and any(url.username or url.password or url.query or url.fragment for url in urls):
            raise ValueError("Peer URLs cannot contain credentials, queries, or fragments")
        return urls


class DesktopUpdate(PreferenceModel):
    adaptive_dashboard_enabled: bool | None = None
    gesture_control_enabled: bool | None = None
    gesture_low_power_mode: bool | None = None
    privacy_mode_enabled: bool | None = None
    transparent_globe_enabled: bool | None = None
    transparent_globe_size: int | None = Field(default=None, ge=280, le=1200)
    transparent_globe_position: Literal["bottom_left", "bottom_right"] | None = None


class PreferencesUpdate(PreferenceModel):
    instance_name: str | None = Field(default=None, min_length=1, max_length=100)
    federation: FederationUpdate | None = None
    desktop: DesktopUpdate | None = None
    integrations: dict[str, bool] | None = None


INTEGRATION_CATALOG: tuple[dict[str, str], ...] = (
    {
        "id": "angerona",
        "name": "Angerona",
        "description": "Primary endpoint defense with authenticated read-only MCP visibility.",
    },
    {
        "id": "ollama",
        "name": "Ollama",
        "description": "Local tool-capable chat, embeddings, and Mac compute offload.",
    },
    {
        "id": "tailscale",
        "name": "Tailscale",
        "description": "Encrypted Windows-to-Mac transport and node identity.",
    },
    {
        "id": "signal",
        "name": "Signal",
        "description": "Operator notifications and approved remote prompts.",
    },
    {"id": "mdns", "name": "mDNS Discovery", "description": "Optional trusted-LAN node discovery."},
    {
        "id": "wake_on_lan",
        "name": "Wake-on-LAN",
        "description": "Wake an explicitly configured compute node.",
    },
    {
        "id": "ups",
        "name": "UPS Control",
        "description": "Power status and guarded shutdown coordination.",
    },
    {
        "id": "ddc_ci",
        "name": "Display Control",
        "description": "DDC/CI monitor input and power controls.",
    },
    {
        "id": "vision",
        "name": "Vision Capture",
        "description": "Permission-scoped screen and camera analysis.",
    },
    {
        "id": "backup",
        "name": "Encrypted Backup",
        "description": "Guarded restic snapshots and restore checks.",
    },
    {
        "id": "openai",
        "name": "GPT Cloud",
        "description": "Optional cloud model provider; currently deferred.",
    },
)


def _signing_key_configured() -> bool:
    """Validate the optional Ed25519 seed without exposing or persisting it."""

    value = os.getenv("JARVIS_FEDERATION_PRIVATE_KEY", "").strip()
    if not value or "=" in value:
        return False
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        return False
    canonical = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return len(raw) == 32 and canonical == value


def _tailscale_available() -> bool:
    executable = shutil.which("tailscale")
    if executable:
        return True
    program_files = os.getenv("ProgramFiles", r"C:\Program Files")
    return (Path(program_files) / "Tailscale" / "tailscale.exe").is_file()


class PreferencesService:
    """Load and atomically store UI preferences without ever persisting secrets."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._preferences = self._load()

    @staticmethod
    def defaults() -> OperatorPreferences:
        defaults = OperatorPreferences()
        defaults.federation.shared_roots = [
            SharedRootPreference(
                path=r"F:\BRAIN DRIVE\_root", permissions=["list", "read", "compute"]
            )
        ]
        return defaults

    def _load(self) -> OperatorPreferences:
        if not self.path.is_file():
            preferences = self.defaults()
            self._store(preferences)
            return preferences
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            desktop = payload.get("desktop") if isinstance(payload, dict) else None
            migrated = False
            if isinstance(desktop, dict) and "companion_layout_version" not in desktop:
                if desktop.get("transparent_globe_size") == 380:
                    desktop["transparent_globe_size"] = 720
                desktop["companion_layout_version"] = 2
                migrated = True
            preferences = OperatorPreferences.model_validate(payload)
            if migrated:
                self._store(preferences)
            return preferences
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError(f"Invalid JARVIS preferences file: {self.path}") from exc

    def _store(self, preferences: OperatorPreferences) -> None:
        serialized = preferences.model_dump_json(indent=2)
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=".jarvis-settings-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_name = handle.name
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.path)
        finally:
            if temporary_name:
                Path(temporary_name).unlink(missing_ok=True)

    @staticmethod
    def _root_payload(root: SharedRootPreference) -> dict[str, object]:
        path = Path(root.path).expanduser()
        available = False
        try:
            available = path.resolve().is_dir()
        except OSError:
            pass
        return {
            "path": root.path,
            "available": available,
            "permissions": root.permissions,
        }

    def desktop(self) -> DesktopPreference:
        """Return a defensive copy of the configured desktop shell preference."""

        self._preferences = self._load()
        return self._preferences.desktop.model_copy(deep=True)

    def _desktop_runtime_status(self) -> dict[str, object]:
        """Read the desktop shell heartbeat and reject stale process records."""

        fallback: dict[str, object] = {
            "active": False,
            "mode": "inactive",
            "companion_available": False,
            "companion_visible": False,
            "desktop_attached": False,
            "true_alpha": False,
            "alpha_pixel_verified": False,
            "non_activating": False,
            "taskbar_hidden": False,
            "hotkey_registered": False,
            "fallback_reason": None,
            "updated_at": None,
        }
        path = self.path.parent / "desktop-status.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            pid = payload.get("pid")
            created_at = payload.get("process_created_at")
            if (
                isinstance(pid, int)
                and pid > 0
                and isinstance(created_at, (int, float))
                and psutil.pid_exists(pid)
                and abs(psutil.Process(pid).create_time() - float(created_at)) < 1.0
            ):
                allowed = set(fallback)
                return {**fallback, **{key: payload[key] for key in allowed if key in payload}}
        except (OSError, ValueError, TypeError, psutil.Error):
            pass
        return fallback

    def public(
        self,
        *,
        ollama_ready: bool = False,
        integration_status: dict[str, bool] | None = None,
        federation_status: dict[str, object] | None = None,
    ) -> dict[str, object]:
        self._preferences = self._load()
        federation = self._preferences.federation
        desktop = self._preferences.desktop
        desktop_runtime = self._desktop_runtime_status()
        signing_ready = _signing_key_configured()
        transport_ready = _tailscale_available()
        capability_ready = bool((federation_status or {}).get("ready"))
        transport_exposed = bool((federation_status or {}).get("transport_exposed"))
        federation_ready = bool(
            federation.enabled
            and federation.peer_urls
            and signing_ready
            and transport_ready
            and capability_ready
            and transport_exposed
        )
        configured = {
            "ollama": ollama_ready,
            "tailscale": transport_ready,
            **(integration_status or {}),
        }
        integrations = []
        for definition in INTEGRATION_CATALOG:
            integration_id = definition["id"]
            enabled = self._preferences.integrations[integration_id]
            is_configured = configured.get(integration_id, False)
            integrations.append(
                {
                    **definition,
                    "enabled": enabled,
                    "configured": is_configured,
                    "status": "ready"
                    if enabled and is_configured
                    else "setup_required"
                    if enabled
                    else "disabled",
                }
            )
        desktop_restart_required = False
        return {
            "settings": {
                "instance_name": self._preferences.instance_name,
                "instance_id": self._preferences.instance_id,
                "platform": platform.system().lower(),
                "federation": {
                    "enabled": federation.enabled,
                    "transport": federation.transport,
                    "transport_available": transport_ready,
                    "signing_key_configured": signing_ready,
                    "capability_endpoint_ready": capability_ready,
                    "transport_exposed": transport_exposed,
                    "ready": federation_ready,
                    "peer_urls": [str(url) for url in federation.peer_urls],
                    "compute_enabled": federation.compute_enabled,
                    "data_enabled": federation.data_enabled,
                    "admin_capable": federation.admin_capable,
                    "require_target_approval": federation.require_target_approval,
                    "shared_roots": [self._root_payload(root) for root in federation.shared_roots],
                },
                "desktop": {
                    "adaptive_dashboard_enabled": desktop.adaptive_dashboard_enabled,
                    "gesture_control_enabled": desktop.gesture_control_enabled,
                    "gesture_low_power_mode": desktop.gesture_low_power_mode,
                    "privacy_mode_enabled": desktop.privacy_mode_enabled,
                    "camera_frames_stored": False,
                    "transparent_globe_enabled": False,
                    "transparent_globe_supported": False,
                    "transparent_globe_size": desktop.transparent_globe_size,
                    "transparent_globe_position": desktop.transparent_globe_position,
                    "transparent_globe_size_min": 280,
                    "transparent_globe_size_max": 1200,
                    "live_toggle_available": False,
                    "restart_required": desktop_restart_required,
                    "return_hotkey": "Ctrl+Alt+J",
                    "interaction_note": (
                        "Hover and click remain available without activation; keyboard focus may be "
                        "limited by Windows NOACTIVATE, so Ctrl+Alt+J always returns to the full HUD."
                    ),
                    "runtime": desktop_runtime,
                },
                "integrations": integrations,
            },
            "restart_required": bool(
                federation.enabled
                or desktop_restart_required
            ),
        }

    def update(self, patch: PreferencesUpdate) -> OperatorPreferences:
        self._preferences = self._load()
        payload = self._preferences.model_dump(mode="json")
        changes = patch.model_dump(exclude_unset=True, mode="json")
        if "instance_name" in changes:
            payload["instance_name"] = changes["instance_name"]
        federation_changes = changes.get("federation")
        if federation_changes:
            payload["federation"].update(federation_changes)
            payload["federation"]["require_target_approval"] = True
        desktop_changes = changes.get("desktop")
        if desktop_changes:
            payload["desktop"].update(desktop_changes)
        integration_changes = changes.get("integrations")
        if integration_changes:
            unknown = set(integration_changes) - set(INTEGRATION_IDS)
            if unknown:
                raise ValueError(f"Unknown integrations: {', '.join(sorted(unknown))}")
            payload["integrations"].update(integration_changes)
        preferences = OperatorPreferences.model_validate(payload)
        self._store(preferences)
        self._preferences = preferences
        return preferences
