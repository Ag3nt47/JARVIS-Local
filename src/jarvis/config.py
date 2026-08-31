"""Configuration loading with safe local-first defaults."""

from __future__ import annotations

import ipaddress
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def _default_project_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS).resolve()  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[2]


def _lookup(data: dict[str, Any], section: str, key: str, default: Any) -> Any:
    """Read a sectioned value, falling back to a top-level value."""
    section_data = data.get(section, {})
    if isinstance(section_data, dict) and key in section_data:
        return section_data[key]
    return data.get(key, default)


@dataclass(slots=True)
class Settings:
    """Runtime settings. Defaults deliberately expose no non-local service."""

    project_root: Path = field(default_factory=_default_project_root)
    data_dir: Path = field(default_factory=lambda: _default_project_root() / "data")
    host: str = "127.0.0.1"
    port: int = 8765
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_chat_model: str = "qwen3:4b"
    ollama_embed_model: str = "nomic-embed-text:latest"
    allowed_roots: tuple[Path, ...] = field(
        default_factory=lambda: (_default_project_root(),)
    )
    cors_origins: tuple[str, ...] = ()
    scheduler_interval_seconds: int = 60
    inactivity_minutes: int = 120
    quiet_hours_enabled: bool = True
    quiet_hours_start: str = "22:00"
    quiet_hours_end: str = "07:00"
    notification_dedupe_minutes: int = 30
    max_chat_tool_rounds: int = 4
    max_file_bytes: int = 1_000_000
    max_file_write_bytes: int = 262_144
    max_search_results: int = 100
    ollama_timeout_seconds: float = 300.0
    ollama_context_window: int = 8192
    temperature: float = 0.2
    approval_ttl_seconds: int = 900
    deny_hidden_paths: bool = True
    outbound_notifications_enabled: bool = False

    @property
    def database_path(self) -> Path:
        return self.data_dir / "jarvis.sqlite3"

    @property
    def log_path(self) -> Path:
        return self.data_dir / "jarvis.log"

    def validate(self) -> None:
        """Reject insecure or malformed configuration before startup."""
        if not 1 <= self.port <= 65535:
            raise ValueError("JARVIS port must be between 1 and 65535")
        try:
            address = ipaddress.ip_address(self.host)
        except ValueError:
            address = None
        is_local = self.host == "localhost" or (address is not None and address.is_loopback)
        if not is_local:
            raise ValueError(
                "Non-loopback binding is disabled in this local-only baseline."
            )
        parsed = urlparse(self.ollama_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("OLLAMA_BASE_URL must be a valid http(s) URL")
        if not self.ollama_chat_model.strip() or not self.ollama_embed_model.strip():
            raise ValueError("Ollama model names cannot be empty")
        if not 2048 <= self.ollama_context_window <= 131_072:
            raise ValueError("ollama.context_window must be between 2048 and 131072")
        if self.scheduler_interval_seconds < 5:
            raise ValueError("scheduler.interval_seconds must be at least 5")
        for value in (self.quiet_hours_start, self.quiet_hours_end):
            try:
                hours, minutes = (int(part) for part in value.split(":"))
            except (ValueError, TypeError) as exc:
                raise ValueError("Quiet hours must use HH:MM format") from exc
            if not (0 <= hours <= 23 and 0 <= minutes <= 59):
                raise ValueError("Quiet hours must use 24-hour HH:MM values")
        roots = []
        for root in self.allowed_roots:
            roots.append(root.expanduser().resolve())
        if not roots:
            raise ValueError("At least one allowed file root is required")
        self.allowed_roots = tuple(roots)
        self.project_root = self.project_root.resolve()
        self.data_dir = self.data_dir.expanduser().resolve()


def load_settings(config_path: str | Path | None = None) -> Settings:
    """Load ``config/default.toml`` and apply explicit environment overrides."""
    project_root = _default_project_root()
    configured_path = config_path or os.getenv("JARVIS_CONFIG")
    path = Path(configured_path) if configured_path else project_root / "config" / "default.toml"
    data: dict[str, Any] = {}
    if configured_path and not path.is_file():
        raise FileNotFoundError(f"JARVIS config file not found: {path.expanduser().resolve()}")
    if path.exists():
        with path.open("rb") as handle:
            data = tomllib.load(handle)

    data_dir_raw = os.getenv(
        "JARVIS_DATA_DIR",
        str(
            _lookup(
                data,
                "storage",
                "data_dir",
                _lookup(data, "system", "data_dir", project_root / "data"),
            )
        ),
    )
    roots_raw = _lookup(
        data,
        "security",
        "allowed_roots",
        _lookup(data, "system", "workspace_roots", [str(project_root)]),
    )
    if isinstance(roots_raw, str):
        roots_raw = [roots_raw]

    settings = Settings(
        project_root=project_root,
        data_dir=(project_root / data_dir_raw if not Path(data_dir_raw).is_absolute() else Path(data_dir_raw)),
        host=os.getenv(
            "JARVIS_HOST",
            str(_lookup(data, "server", "host", _lookup(data, "system", "host", "127.0.0.1"))),
        ),
        port=int(
            os.getenv(
                "JARVIS_PORT",
                str(_lookup(data, "server", "port", _lookup(data, "system", "port", 8765))),
            )
        ),
        ollama_base_url=os.getenv(
            "OLLAMA_BASE_URL",
            str(_lookup(data, "ollama", "base_url", "http://127.0.0.1:11434")),
        ).rstrip("/"),
        ollama_chat_model=os.getenv(
            "OLLAMA_CHAT_MODEL", str(_lookup(data, "ollama", "chat_model", "qwen3:4b"))
        ),
        ollama_embed_model=os.getenv(
            "OLLAMA_EMBED_MODEL",
            str(
                _lookup(
                    data,
                    "ollama",
                    "embed_model",
                    _lookup(data, "ollama", "embedding_model", "nomic-embed-text:latest"),
                )
            ),
        ),
        allowed_roots=tuple(
            (project_root / str(root) if not Path(str(root)).is_absolute() else Path(str(root)))
            for root in roots_raw
        ),
        cors_origins=tuple(_lookup(data, "security", "cors_origins", [])),
        scheduler_interval_seconds=int(
            _lookup(
                data,
                "scheduler",
                "interval_seconds",
                _lookup(data, "tasks", "scan_interval_seconds", 60),
            )
        ),
        inactivity_minutes=int(_lookup(data, "tasks", "inactivity_minutes", 120)),
        quiet_hours_enabled=bool(_lookup(data, "tasks", "quiet_hours_enabled", True)),
        quiet_hours_start=str(
            _lookup(data, "tasks", "quiet_hours_start", _lookup(data, "tasks", "quiet_start", "22:00"))
        ),
        quiet_hours_end=str(
            _lookup(data, "tasks", "quiet_hours_end", _lookup(data, "tasks", "quiet_end", "07:00"))
        ),
        notification_dedupe_minutes=int(
            _lookup(
                data,
                "tasks",
                "notification_dedupe_minutes",
                int(_lookup(data, "tasks", "dedupe_window_hours", 0.5) * 60),
            )
        ),
        max_chat_tool_rounds=int(
            _lookup(data, "ollama", "max_tool_rounds", _lookup(data, "ollama", "max_agent_steps", 4))
        ),
        max_file_bytes=int(
            _lookup(
                data,
                "security",
                "max_file_bytes",
                _lookup(data, "security", "max_file_read_bytes", 1_000_000),
            )
        ),
        max_file_write_bytes=int(
            _lookup(data, "security", "max_file_write_bytes", 262_144)
        ),
        max_search_results=int(_lookup(data, "security", "max_search_results", 100)),
        ollama_timeout_seconds=float(_lookup(data, "ollama", "request_timeout_seconds", 300)),
        ollama_context_window=int(
            os.getenv(
                "OLLAMA_CONTEXT_WINDOW",
                str(_lookup(data, "ollama", "context_window", 8192)),
            )
        ),
        temperature=float(_lookup(data, "ollama", "temperature", 0.2)),
        approval_ttl_seconds=int(_lookup(data, "security", "approval_ttl_seconds", 900)),
        deny_hidden_paths=bool(_lookup(data, "security", "deny_hidden_paths", True)),
        outbound_notifications_enabled=bool(
            _lookup(data, "messaging", "outbound_enabled", False)
        ),
    )
    settings.validate()
    return settings
