"""Safe, persistent communications-route setup and readiness checks.

This module is deliberately a configuration boundary, not a generic messaging or
HTTP-request API. Secret values come from fixed environment variables or a dedicated
write-only API backed by Windows DPAPI user-scope encryption. Plaintext is never stored,
returned, or included in audit details. Network verification is limited to hard-coded
Telegram and Discord API hosts. Private web URLs are validated but never fetched,
preventing the setup center from becoming an SSRF tool.
"""

from __future__ import annotations

import ctypes
import ipaddress
import json
import math
import os
import re
import shutil
from ctypes import wintypes
from datetime import UTC, datetime
from typing import Any, Final, Literal, TypeAlias
from urllib.parse import urlsplit

import httpx

from .audit import AuditLog, utc_now
from .config import Settings
from .database import Database

ProviderId: TypeAlias = Literal["signal", "telegram", "discord", "web_app"]
RouteStatus: TypeAlias = Literal[
    "disabled", "setup_required", "ready", "unavailable", "unverified", "blocked", "error"
]

PROVIDERS: Final[tuple[ProviderId, ...]] = ("signal", "telegram", "discord", "web_app")
DELIVERY_PROVIDERS: Final[tuple[ProviderId, ...]] = ("signal", "telegram", "discord")
STATUS_VALUES: Final[tuple[RouteStatus, ...]] = (
    "disabled",
    "setup_required",
    "ready",
    "unavailable",
    "unverified",
    "blocked",
    "error",
)
TELEGRAM_TOKEN_ENV: Final = "JARVIS_TELEGRAM_BOT_TOKEN"
DISCORD_WEBHOOK_ENV: Final = "JARVIS_DISCORD_WEBHOOK_URL"
DISCORD_BOT_TOKEN_ENV: Final = "JARVIS_DISCORD_BOT_TOKEN"
WEB_ROUTE_TOKEN_ENV: Final = "JARVIS_WEB_ROUTE_TOKEN"

_E164_RE = re.compile(r"^\+[1-9][0-9]{7,14}$")
_TELEGRAM_CHAT_RE = re.compile(r"^-?[1-9][0-9]{4,19}$")
_TELEGRAM_TOKEN_RE = re.compile(r"^[1-9][0-9]{7,11}:[A-Za-z0-9_-]{30,80}$")
_DISCORD_ID_RE = re.compile(r"^[1-9][0-9]{16,19}$")
_DISCORD_BOT_TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]{30,200}$")
_DISCORD_WEBHOOK_PATH_RE = re.compile(
    r"^/api/webhooks/(?P<id>[1-9][0-9]{16,19})/(?P<token>[A-Za-z0-9._-]{40,200})$"
)
_WEB_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~-]{32,256}$")
_RFC1918_NETWORKS = tuple(
    ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
_TAILSCALE_NETWORK = ipaddress.ip_network("100.64.0.0/10")
_IPV6_PRIVATE_NETWORK = ipaddress.ip_network("fc00::/7")


class CommunicationError(Exception):
    """Base class for communications setup errors."""


class CommunicationRateLimitError(CommunicationError):
    """Raised when a provider verification is requested during its cooldown."""

    def __init__(self, retry_after: int) -> None:
        super().__init__("Communications route verification is temporarily rate limited")
        self.retry_after = max(1, retry_after)


class CommunicationSecretStorageError(CommunicationError):
    """Raised when protected local credential storage is unavailable or fails closed."""


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class ProtectedSecretStore:
    """Store communications credentials encrypted by Windows DPAPI user scope."""

    _ENTROPY: Final = b"JARVIS communications credentials v1"
    _UI_FORBIDDEN: Final = 0x01

    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def available() -> bool:
        return os.name == "nt" and hasattr(ctypes, "WinDLL")

    @staticmethod
    def _blob(value: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
        buffer = ctypes.create_string_buffer(value, len(value))
        blob = _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        return blob, buffer

    @classmethod
    def _protect(cls, plaintext: bytes) -> bytes:
        if not cls.available():
            raise CommunicationSecretStorageError(
                "Protected credential storage requires Windows DPAPI"
            )
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        input_blob, input_buffer = cls._blob(plaintext)
        entropy_blob, entropy_buffer = cls._blob(cls._ENTROPY)
        output_blob = _DataBlob()
        try:
            succeeded = crypt32.CryptProtectData(
                ctypes.byref(input_blob),
                "JARVIS communications credential",
                ctypes.byref(entropy_blob),
                None,
                None,
                cls._UI_FORBIDDEN,
                ctypes.byref(output_blob),
            )
            if not succeeded:
                raise CommunicationSecretStorageError("Windows DPAPI protection failed")
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            if output_blob.pbData:
                kernel32.LocalFree(output_blob.pbData)
            del input_buffer, entropy_buffer

    @classmethod
    def _unprotect(cls, protected: bytes) -> bytes:
        if not cls.available():
            raise CommunicationSecretStorageError(
                "Protected credential storage requires Windows DPAPI"
            )
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        input_blob, input_buffer = cls._blob(protected)
        entropy_blob, entropy_buffer = cls._blob(cls._ENTROPY)
        output_blob = _DataBlob()
        description = ctypes.c_wchar_p()
        try:
            succeeded = crypt32.CryptUnprotectData(
                ctypes.byref(input_blob),
                ctypes.byref(description),
                ctypes.byref(entropy_blob),
                None,
                None,
                cls._UI_FORBIDDEN,
                ctypes.byref(output_blob),
            )
            if not succeeded:
                raise CommunicationSecretStorageError("Windows DPAPI decryption failed")
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            if output_blob.pbData:
                kernel32.LocalFree(output_blob.pbData)
            if description:
                kernel32.LocalFree(description)
            del input_buffer, entropy_buffer

    def replace(self, provider: ProviderId, credential_kind: str, plaintext: str) -> None:
        """Encrypt then atomically replace one provider credential."""

        if not isinstance(plaintext, str) or not plaintext:
            raise ValueError("Credential cannot be empty")
        protected = self._protect(plaintext.encode("utf-8"))
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO communication_secrets
                (provider,credential_kind,protected_value,updated_at) VALUES (?,?,?,?)
                ON CONFLICT(provider) DO UPDATE SET
                    credential_kind=excluded.credential_kind,
                    protected_value=excluded.protected_value,
                    updated_at=excluded.updated_at""",
                (provider, credential_kind, protected, utc_now()),
            )

    def read(self, provider: ProviderId, credential_kind: str) -> str | None:
        """Decrypt a matching credential for immediate provider use only."""

        row = self.database.query_one(
            """SELECT protected_value FROM communication_secrets
            WHERE provider=? AND credential_kind=?""",
            (provider, credential_kind),
        )
        if row is None:
            return None
        try:
            return self._unprotect(bytes(row["protected_value"])).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CommunicationSecretStorageError("Protected credential is invalid") from exc

    def clear(self, provider: ProviderId) -> bool:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM communication_secrets WHERE provider=?", (provider,)
            )
        return cursor.rowcount == 1


def _private_web_origin(value: Any) -> tuple[str, str]:
    """Validate and canonicalize a loopback, private-LAN, or tailnet web origin."""

    if not isinstance(value, str) or not 1 <= len(value) <= 300:
        raise ValueError("Web route URL must contain 1-300 characters")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Web route must be an HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Web route cannot contain credentials, a query, or a fragment")
    if parsed.path not in {"", "/"}:
        raise ValueError("Web route must be an origin without an application path")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Web route port is invalid") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Web route port must be between 1 and 65535")

    hostname = parsed.hostname.casefold()
    address: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
    if hostname == "localhost":
        network_scope = "loopback"
    else:
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError as exc:
            raise ValueError("Web route host must be localhost or a private IP literal") from exc
        if address.is_loopback:
            network_scope = "loopback"
        elif isinstance(address, ipaddress.IPv4Address) and any(
            address in network for network in _RFC1918_NETWORKS
        ):
            network_scope = "private_lan"
        elif isinstance(address, ipaddress.IPv4Address) and address in _TAILSCALE_NETWORK:
            network_scope = "tailscale"
        elif isinstance(address, ipaddress.IPv6Address) and address in _IPV6_PRIVATE_NETWORK:
            network_scope = "private_lan"
        else:
            raise ValueError("Web route host is outside loopback, private LAN, and tailnet ranges")
    if network_scope != "loopback" and parsed.scheme != "https":
        raise ValueError("Non-loopback web routes require HTTPS")

    display_host = hostname
    if address is not None and isinstance(address, ipaddress.IPv6Address):
        display_host = f"[{address.compressed}]"
    elif address is not None:
        display_host = address.compressed
    port_suffix = f":{port}" if port is not None else ""
    return f"{parsed.scheme}://{display_host}{port_suffix}", network_scope


def _discord_webhook_valid(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and parsed.hostname in {"discord.com", "discordapp.com"}
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
        and port in {None, 443}
        and _DISCORD_WEBHOOK_PATH_RE.fullmatch(parsed.path)
    )


class CommunicationsService:
    """Manage four fixed route types and perform bounded readiness checks."""

    TEST_COOLDOWN_SECONDS: Final = 120

    def __init__(self, database: Database, audit: AuditLog, settings: Settings) -> None:
        self.database = database
        self.audit = audit
        self.settings = settings
        self.secrets = ProtectedSecretStore(database)
        self._client: httpx.AsyncClient | None = None
        self._ensure_defaults()

    def _ensure_defaults(self) -> None:
        now = utc_now()
        defaults: dict[ProviderId, dict[str, Any]] = {
            "signal": {},
            "telegram": {},
            "discord": {"mode": "webhook"},
            "web_app": {
                "base_url": f"http://127.0.0.1:{self.settings.port}",
                "network_scope": "loopback",
                "auth_enabled": False,
            },
        }
        with self.database.transaction() as connection:
            for priority, provider in enumerate(PROVIDERS, start=1):
                connection.execute(
                    """INSERT OR IGNORE INTO communication_routes
                    (provider,enabled,priority,config_json,secret_refs_json,created_at,updated_at)
                    VALUES (?,0,?,?,?,?,?)""",
                    (
                        provider,
                        priority,
                        json.dumps(defaults[provider], sort_keys=True, separators=(",", ":")),
                        json.dumps(self._secret_refs(provider, defaults[provider])),
                        now,
                        now,
                    ),
                )
            connection.execute(
                """INSERT OR IGNORE INTO communication_policy
                (id,failover_enabled,updated_at) VALUES (1,0,?)""",
                (now,),
            )

    @staticmethod
    def _secret_refs(provider: ProviderId, config: dict[str, Any]) -> list[str]:
        if provider == "telegram":
            return [TELEGRAM_TOKEN_ENV]
        if provider == "discord":
            return [
                DISCORD_WEBHOOK_ENV
                if config.get("mode", "webhook") == "webhook"
                else DISCORD_BOT_TOKEN_ENV
            ]
        if provider == "web_app" and config.get("auth_enabled"):
            return [WEB_ROUTE_TOKEN_ENV]
        return []

    @staticmethod
    def _credential_kind(provider: ProviderId, config: dict[str, Any]) -> str | None:
        if provider == "telegram":
            return "telegram_bot_token"
        if provider == "discord":
            return (
                "discord_webhook_url"
                if config.get("mode", "webhook") == "webhook"
                else "discord_bot_token"
            )
        if provider == "web_app" and config.get("auth_enabled"):
            return "web_token"
        return None

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        decoded = dict(row)
        decoded["enabled"] = bool(decoded["enabled"])
        decoded["config"] = json.loads(decoded.pop("config_json"))
        decoded["secret_refs"] = json.loads(decoded.pop("secret_refs_json"))
        return decoded

    def _route(self, provider: ProviderId) -> dict[str, Any]:
        row = self.database.query_one(
            "SELECT * FROM communication_routes WHERE provider=?", (provider,)
        )
        if row is None:
            raise KeyError("Communications route not found")
        return self._decode(row)

    @staticmethod
    def _signal_cli_available() -> bool:
        discovered = shutil.which("signal-cli")
        if discovered:
            return True
        if os.name == "nt":
            program_files = os.getenv("ProgramFiles", r"C:\Program Files")
            candidates = (
                os.path.join(program_files, "signal-cli", "bin", "signal-cli.bat"),
                os.path.join(program_files, "signal-cli", "bin", "signal-cli.exe"),
            )
            return any(os.path.isfile(candidate) for candidate in candidates)
        return False

    @staticmethod
    def _credential_valid(reference: str, value: str) -> bool:
        if reference == TELEGRAM_TOKEN_ENV:
            return bool(_TELEGRAM_TOKEN_RE.fullmatch(value))
        if reference == DISCORD_WEBHOOK_ENV:
            return _discord_webhook_valid(value)
        if reference == DISCORD_BOT_TOKEN_ENV:
            return bool(_DISCORD_BOT_TOKEN_RE.fullmatch(value))
        if reference == WEB_ROUTE_TOKEN_ENV:
            return bool(_WEB_TOKEN_RE.fullmatch(value))
        return False

    def _credential_value(
        self, provider: ProviderId, config: dict[str, Any], reference: str
    ) -> tuple[str | None, str]:
        value = os.getenv(reference, "")
        if value:
            return value, "environment"
        kind = self._credential_kind(provider, config)
        if kind is None:
            return None, "missing"
        try:
            protected = self.secrets.read(provider, kind)
        except CommunicationSecretStorageError:
            return None, "protected_local_error"
        return (protected, "protected_local") if protected else (None, "missing")

    def _credential_state(
        self, provider: ProviderId, config: dict[str, Any], reference: str
    ) -> tuple[bool, bool, str]:
        value, source = self._credential_value(provider, config, reference)
        return bool(value), bool(value and self._credential_valid(reference, value)), source

    def _current_status(
        self, route: dict[str, Any], *, enforce_global_outbound_policy: bool = True
    ) -> tuple[RouteStatus, str]:
        if not route["enabled"]:
            return "disabled", "Route is disabled"
        provider: ProviderId = route["provider"]
        config = route["config"]
        if provider == "signal":
            if not config.get("account") or not config.get("recipient"):
                return "setup_required", "Signal account and recipient are required"
            if not self._signal_cli_available():
                return "unavailable", "Local signal-cli was not found"
            return (
                "unverified",
                "Local signal-cli was detected; executable and account linkage are not verified",
            )
        if provider == "telegram":
            if not config.get("chat_id"):
                return "setup_required", "Telegram chat ID is required"
        elif provider == "discord":
            if config.get("mode") == "bot" and not config.get("channel_id"):
                return "setup_required", "Discord bot channel ID is required"
        elif provider == "web_app":
            try:
                _, scope = _private_web_origin(config.get("base_url"))
            except ValueError:
                return "setup_required", "Private web origin is invalid"
            if scope != "loopback" and not config.get("auth_enabled"):
                return "setup_required", "Private web routes require token authentication"

        for reference in route["secret_refs"]:
            present, valid, source = self._credential_state(provider, config, reference)
            if not present:
                return (
                    "setup_required",
                    f"Provider credential is not configured; set {reference} or use write-only setup",
                )
            if not valid:
                return "setup_required", f"Provider credential from {source} is malformed"
        if (
            provider in {"telegram", "discord"}
            and enforce_global_outbound_policy
            and not self.settings.outbound_notifications_enabled
        ):
            return "blocked", "Outbound communications are disabled by local configuration"
        return "ready", "Route configuration is ready"

    def health_alert_readiness(
        self, provider: Literal["telegram", "discord"]
    ) -> dict[str, Any]:
        """Report readiness for the separately opted-in, fixed health-alert channel."""

        route = self._route(provider)
        status, detail = self._current_status(
            route, enforce_global_outbound_policy=False
        )
        return {"status": status, "detail": detail}

    def _prerequisites(self, route: dict[str, Any]) -> list[dict[str, Any]]:
        provider: ProviderId = route["provider"]
        config = route["config"]
        items: list[dict[str, Any]] = []
        if provider == "signal":
            items.extend(
                (
                    {
                        "id": "signal_cli",
                        "label": "Local signal-cli installation",
                        "ready": self._signal_cli_available(),
                    },
                    {
                        "id": "account",
                        "label": "Signal account in E.164 format",
                        "ready": bool(config.get("account")),
                    },
                    {
                        "id": "recipient",
                        "label": "Signal recipient in E.164 format",
                        "ready": bool(config.get("recipient")),
                    },
                )
            )
        elif provider in {"telegram", "discord"}:
            if provider == "telegram":
                items.append(
                    {
                        "id": "chat_id",
                        "label": "Telegram chat ID",
                        "ready": bool(config.get("chat_id")),
                    }
                )
            elif config.get("mode") == "bot":
                items.append(
                    {
                        "id": "channel_id",
                        "label": "Discord channel ID",
                        "ready": bool(config.get("channel_id")),
                    }
                )
            items.append(
                {
                    "id": "outbound_policy",
                    "label": "Outbound communications enabled in local configuration",
                    "ready": self.settings.outbound_notifications_enabled,
                }
            )
        else:
            items.append(
                {
                    "id": "private_origin",
                    "label": "Loopback, private-LAN, or tailnet web origin",
                    "ready": bool(config.get("base_url")),
                }
            )
        for reference in route["secret_refs"]:
            present, valid, source = self._credential_state(provider, config, reference)
            kind = self._credential_kind(provider, config) or "credential"
            label = {
                "telegram_bot_token": "Telegram bot credential",
                "discord_webhook_url": "Discord webhook credential",
                "discord_bot_token": "Discord bot credential",
                "web_token": "Private web route token",
            }.get(kind, "Provider credential")
            items.append(
                {
                    "id": f"credential:{kind}",
                    "label": label,
                    "ready": present and valid,
                    "configured": present,
                    "source": source,
                    "environment_reference": reference,
                }
            )
        return items

    def _public_route(self, route: dict[str, Any]) -> dict[str, Any]:
        status, detail = self._current_status(route)
        delivery_supported = route["provider"] in DELIVERY_PROVIDERS
        last_test = None
        if route["last_test_at"]:
            last_test = {
                "tested_at": route["last_test_at"],
                "status": route["last_test_status"],
                "detail": route["last_test_detail"],
            }
        return {
            "provider": route["provider"],
            "label": {
                "signal": "Signal",
                "telegram": "Telegram Bot",
                "discord": "Discord",
                "web_app": "JARVIS Web App",
            }[route["provider"]],
            "enabled": route["enabled"],
            "channel_kind": "outbound_delivery" if delivery_supported else "interactive_access",
            "delivery_supported": delivery_supported,
            "priority": route["priority"] if delivery_supported else None,
            "configuration": self._public_configuration(route),
            "status": status,
            "status_detail": detail,
            "prerequisites": self._prerequisites(route),
            "last_test": last_test,
            "test_kind": {
                "signal": "local_prerequisites",
                "telegram": "vendor_identity",
                "discord": "vendor_identity",
                "web_app": "policy_validation",
            }[route["provider"]],
        }

    @staticmethod
    def _masked_identifier(value: Any) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        visible_prefix = 2 if len(value) > 6 else 1
        visible_suffix = 4 if len(value) > 6 else 2
        hidden = max(3, len(value) - visible_prefix - visible_suffix)
        return f"{value[:visible_prefix]}{'•' * hidden}{value[-visible_suffix:]}"

    @classmethod
    def _public_configuration(cls, route: dict[str, Any]) -> dict[str, Any]:
        provider: ProviderId = route["provider"]
        config = route["config"]
        if provider == "signal":
            return {
                "account_configured": bool(config.get("account")),
                "account_masked": cls._masked_identifier(config.get("account")),
                "recipient_configured": bool(config.get("recipient")),
                "recipient_masked": cls._masked_identifier(config.get("recipient")),
            }
        if provider == "telegram":
            return {
                "chat_id_configured": bool(config.get("chat_id")),
                "chat_id_masked": cls._masked_identifier(config.get("chat_id")),
            }
        if provider == "discord":
            return {
                "mode": config.get("mode", "webhook"),
                "channel_id_configured": bool(config.get("channel_id")),
                "channel_id_masked": cls._masked_identifier(config.get("channel_id")),
            }
        return dict(config)

    def snapshot(self) -> dict[str, Any]:
        """Return all routes, safe readiness details, and failover policy."""

        rows = self.database.query_all(
            "SELECT * FROM communication_routes ORDER BY priority ASC, provider ASC"
        )
        policy = self.database.query_one("SELECT * FROM communication_policy WHERE id=1") or {}
        routes = [self._public_route(self._decode(row)) for row in rows]
        return {
            "routes": routes,
            "policy": {
                "failover_enabled": bool(policy.get("failover_enabled", 0)),
                "delivery_order": [
                    route["provider"] for route in routes if route["delivery_supported"]
                ],
                "applies_to": list(DELIVERY_PROVIDERS),
                "configured_only": True,
                "semantics": (
                    "Priority/failover preference only; no outbound send endpoint is exposed. "
                    "Future delivery must stop on authentication, target, payload, rate-limit, "
                    "or ambiguous-timeout failures."
                ),
            },
            "status_values": list(STATUS_VALUES),
            "secrets": {
                "accepted_via_api": self.secrets.available(),
                "plaintext_returned": False,
                "write_only_replace_supported": self.secrets.available(),
                "storage": (
                    "windows_dpapi_user_scope_with_environment_override"
                    if self.secrets.available()
                    else "environment_references_only"
                ),
                "environment_references": {
                    "telegram": TELEGRAM_TOKEN_ENV,
                    "discord_webhook": DISCORD_WEBHOOK_ENV,
                    "discord_bot": DISCORD_BOT_TOKEN_ENV,
                    "web_app": WEB_ROUTE_TOKEN_ENV,
                },
            },
        }

    @staticmethod
    def _validate_signal(config: dict[str, Any]) -> dict[str, Any]:
        account = config.get("account")
        recipient = config.get("recipient")
        if account is not None and (
            not isinstance(account, str) or not _E164_RE.fullmatch(account)
        ):
            raise ValueError("Signal account must use E.164 format")
        if recipient is not None and (
            not isinstance(recipient, str) or not _E164_RE.fullmatch(recipient)
        ):
            raise ValueError("Signal recipient must use E.164 format")
        return {key: config[key] for key in ("account", "recipient") if key in config}

    @staticmethod
    def _validate_telegram(config: dict[str, Any]) -> dict[str, Any]:
        chat_id = config.get("chat_id")
        if chat_id is not None and (
            not isinstance(chat_id, str) or not _TELEGRAM_CHAT_RE.fullmatch(chat_id)
        ):
            raise ValueError("Telegram chat ID is invalid")
        return {"chat_id": chat_id} if chat_id is not None else {}

    @staticmethod
    def _validate_discord(config: dict[str, Any]) -> dict[str, Any]:
        mode = config.get("mode", "webhook")
        if mode not in {"webhook", "bot"}:
            raise ValueError("Discord mode must be webhook or bot")
        validated: dict[str, Any] = {"mode": mode}
        if mode == "bot":
            channel_id = config.get("channel_id")
            if not isinstance(channel_id, str) or not _DISCORD_ID_RE.fullmatch(channel_id):
                raise ValueError("Discord bot mode requires a valid channel ID")
            validated["channel_id"] = channel_id
        return validated

    @staticmethod
    def _validate_web(config: dict[str, Any]) -> dict[str, Any]:
        base_url, scope = _private_web_origin(config.get("base_url"))
        auth_enabled = config.get("auth_enabled", False)
        if not isinstance(auth_enabled, bool):
            raise TypeError("Web route auth_enabled must be boolean")
        if scope != "loopback" and not auth_enabled:
            raise ValueError("Non-loopback web routes require token authentication")
        return {
            "base_url": base_url,
            "network_scope": scope,
            "auth_enabled": auth_enabled,
        }

    def update_route(
        self,
        provider: ProviderId,
        changes: dict[str, Any],
        *,
        actor: str = "user:hud",
    ) -> dict[str, Any]:
        """Update one provider through its fixed schema and audit field names only."""

        if provider not in PROVIDERS:
            raise KeyError("Communications route not found")
        allowed = {
            "signal": {"enabled", "account", "recipient"},
            "telegram": {"enabled", "chat_id"},
            "discord": {"enabled", "mode", "channel_id"},
            "web_app": {"enabled", "base_url", "auth_enabled"},
        }[provider]
        invalid = set(changes) - allowed
        if invalid:
            raise ValueError(f"Unsupported {provider} route fields: {', '.join(sorted(invalid))}")
        if not changes:
            raise ValueError("At least one communications route field is required")
        current = self._route(provider)
        enabled = changes.get("enabled", current["enabled"])
        if not isinstance(enabled, bool):
            raise TypeError("Communications route enabled must be boolean")
        merged = dict(current["config"])
        merged.update({key: value for key, value in changes.items() if key != "enabled"})
        validator = {
            "signal": self._validate_signal,
            "telegram": self._validate_telegram,
            "discord": self._validate_discord,
            "web_app": self._validate_web,
        }[provider]
        config = validator(merged)
        secret_refs = self._secret_refs(provider, config)
        now = utc_now()
        with self.database.transaction() as connection:
            credential_context_changed = (
                provider == "discord" and current["config"].get("mode") != config.get("mode")
            ) or (
                provider == "web_app"
                and current["config"].get("auth_enabled")
                and not config.get("auth_enabled")
            )
            connection.execute(
                """UPDATE communication_routes
                SET enabled=?,config_json=?,secret_refs_json=?,updated_at=?,
                    last_test_at=NULL,last_test_status=NULL,last_test_detail=NULL
                WHERE provider=?""",
                (
                    int(enabled),
                    json.dumps(config, sort_keys=True, separators=(",", ":")),
                    json.dumps(secret_refs, separators=(",", ":")),
                    now,
                    provider,
                ),
            )
            if credential_context_changed:
                connection.execute(
                    "DELETE FROM communication_secrets WHERE provider=?", (provider,)
                )
        self.audit.append(
            actor,
            "communications.route_updated",
            f"communications:{provider}",
            {"provider": provider, "fields": sorted(changes)},
        )
        return self._public_route(self._route(provider))

    def replace_credential(
        self, provider: ProviderId, plaintext: str, actor: str = "user:hud"
    ) -> dict[str, Any]:
        """Validate and DPAPI-protect a write-only provider credential."""

        route = self._route(provider)
        references = route["secret_refs"]
        kind = self._credential_kind(provider, route["config"])
        if len(references) != 1 or kind is None:
            raise ValueError("This route does not accept a stored credential")
        reference = references[0]
        if not self._credential_valid(reference, plaintext):
            raise ValueError("Credential format is invalid for this provider")
        self.secrets.replace(provider, kind, plaintext)
        self.audit.append(
            actor,
            "communications.credential_replaced",
            f"communications:{provider}",
            {"provider": provider, "credential_kind": kind},
        )
        return self._public_route(self._route(provider))

    def clear_credential(self, provider: ProviderId, actor: str = "user:hud") -> dict[str, Any]:
        """Remove a DPAPI-protected value; environment overrides are unaffected."""

        if provider == "signal":
            raise ValueError("Signal route has no stored credential")
        cleared = self.secrets.clear(provider)
        self.audit.append(
            actor,
            "communications.credential_cleared",
            f"communications:{provider}",
            {"provider": provider, "cleared": cleared},
        )
        return self._public_route(self._route(provider))

    def reorder(self, provider_order: list[ProviderId], actor: str = "user:hud") -> dict[str, Any]:
        """Atomically order the three outbound providers; web access is excluded."""

        if len(provider_order) != len(DELIVERY_PROVIDERS) or set(provider_order) != set(
            DELIVERY_PROVIDERS
        ):
            raise ValueError(
                "Provider order must contain Signal, Telegram, and Discord exactly once"
            )
        now = utc_now()
        with self.database.transaction() as connection:
            for priority, provider in enumerate(provider_order, start=1):
                connection.execute(
                    "UPDATE communication_routes SET priority=?,updated_at=? WHERE provider=?",
                    (priority, now, provider),
                )
            connection.execute(
                "UPDATE communication_routes SET priority=4,updated_at=? WHERE provider='web_app'",
                (now,),
            )
        self.audit.append(
            actor,
            "communications.order_updated",
            "communications:policy",
            {"provider_order": provider_order},
        )
        return self.snapshot()

    def update_failover(self, enabled: bool, actor: str = "user:hud") -> dict[str, Any]:
        """Persist whether callers may attempt the next configured route after failure."""

        if not isinstance(enabled, bool):
            raise TypeError("failover_enabled must be boolean")
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE communication_policy SET failover_enabled=?,updated_at=? WHERE id=1",
                (int(enabled), utc_now()),
            )
        self.audit.append(
            actor,
            "communications.failover_updated",
            "communications:policy",
            {"failover_enabled": enabled},
        )
        return self.snapshot()

    def _claim_test(self, provider: ProviderId) -> None:
        """Atomically consume a persisted verification lease before any outbound request."""

        now = datetime.now(UTC)
        retry_after = self.TEST_COOLDOWN_SECONDS
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT last_test_at FROM communication_routes WHERE provider=?", (provider,)
            ).fetchone()
            if row is None:
                raise KeyError("Communications route not found")
            if row[0]:
                try:
                    elapsed = (now - datetime.fromisoformat(row[0])).total_seconds()
                except (TypeError, ValueError):
                    elapsed = self.TEST_COOLDOWN_SECONDS
                if elapsed < self.TEST_COOLDOWN_SECONDS:
                    retry_after = max(1, self.TEST_COOLDOWN_SECONDS - int(elapsed))
                    raise CommunicationRateLimitError(retry_after)
            connection.execute(
                """UPDATE communication_routes
                SET last_test_at=?,last_test_status=NULL,last_test_detail=? WHERE provider=?""",
                (now.isoformat(), "Verification in progress", provider),
            )

    def _record_test(
        self,
        provider: ProviderId,
        status: RouteStatus,
        detail: str,
        *,
        connectivity_checked: bool,
        credential_verified: bool | None = None,
        actor: str,
    ) -> dict[str, Any]:
        tested_at = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE communication_routes
                SET last_test_at=?,last_test_status=?,last_test_detail=? WHERE provider=?""",
                (tested_at, status, detail, provider),
            )
        self.audit.append(
            actor,
            "communications.route_tested",
            f"communications:{provider}",
            {
                "provider": provider,
                "status": status,
                "connectivity_checked": connectivity_checked,
                "credential_verified": credential_verified,
            },
        )
        return {
            "provider": provider,
            "status": status,
            "detail": detail,
            "tested_at": tested_at,
            "connectivity_checked": connectivity_checked,
            "credential_verified": credential_verified,
        }

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(8.0, connect=4.0),
                follow_redirects=False,
                trust_env=False,
            )
        return self._client

    async def test_route(self, provider: ProviderId, actor: str = "user:hud") -> dict[str, Any]:
        """Run a rate-limited, provider-specific readiness check without sending a message."""

        route = self._route(provider)
        self._claim_test(provider)
        status, detail = self._current_status(route)
        outbound_policy_only = status == "blocked" and provider in {"telegram", "discord"}
        if status != "ready" and not outbound_policy_only:
            return self._record_test(
                provider,
                status,
                detail,
                connectivity_checked=False,
                credential_verified=None,
                actor=actor,
            )
        if provider == "signal":
            return self._record_test(
                provider,
                "unverified",
                "Local signal-cli was detected but not executed; account linkage is unverified",
                connectivity_checked=False,
                credential_verified=None,
                actor=actor,
            )
        if provider == "web_app":
            return self._record_test(
                provider,
                "ready",
                "Private web route policy validated; the URL was not fetched",
                connectivity_checked=False,
                credential_verified=None,
                actor=actor,
            )

        client = await self._http()
        try:
            if provider == "telegram":
                token, _ = self._credential_value(provider, route["config"], TELEGRAM_TOKEN_ENV)
                if token is None:
                    raise KeyError("credential unavailable")
                response = await client.get(f"https://api.telegram.org/bot{token}/getMe")
                succeeded = response.status_code == 200 and bool(response.json().get("ok"))
            else:
                config = route["config"]
                if config.get("mode") == "webhook":
                    webhook, _ = self._credential_value(provider, config, DISCORD_WEBHOOK_ENV)
                    if webhook is None:
                        raise KeyError("credential unavailable")
                    response = await client.get(webhook)
                else:
                    bot_token, _ = self._credential_value(provider, config, DISCORD_BOT_TOKEN_ENV)
                    if bot_token is None:
                        raise KeyError("credential unavailable")
                    response = await client.get(
                        "https://discord.com/api/v10/users/@me",
                        headers={"Authorization": f"Bot {bot_token}"},
                    )
                succeeded = response.status_code == 200
        except (httpx.HTTPError, KeyError, ValueError, TypeError, json.JSONDecodeError):
            succeeded = False
        return self._record_test(
            provider,
            "blocked" if succeeded and outbound_policy_only else "ready" if succeeded else "error",
            (
                "Provider identity verified; outbound delivery remains blocked by local policy"
                if succeeded and outbound_policy_only
                else "Provider identity verified; no message was sent"
                if succeeded
                else "Provider verification failed without sending a message"
            ),
            connectivity_checked=True,
            credential_verified=succeeded,
            actor=actor,
        )

    async def deliver_health_alert(
        self,
        provider: ProviderId,
        *,
        metric: str,
        event_type: str,
        value: float,
        threshold: float,
    ) -> dict[str, Any]:
        """Deliver one generated health event through a configured fixed provider.

        This is intentionally an internal typed operation. Callers cannot supply a
        message, URL, command, or destination, and no corresponding public API exists.
        """

        metrics = {
            "cpu_percent": ("CPU usage", "%"),
            "memory_percent": ("Memory usage", "%"),
            "disk_percent": ("Data-volume usage", "%"),
            "temperature_celsius": ("Maximum reported temperature", "°C"),
        }
        if provider not in {"telegram", "discord"}:
            return {"provider": provider, "status": "unavailable", "remote_status": None}
        if metric not in metrics or event_type not in {"triggered", "ongoing", "recovered"}:
            raise ValueError("Unsupported structured health alert")
        numeric = all(
            isinstance(item, (int, float))
            and not isinstance(item, bool)
            and math.isfinite(item)
            for item in (value, threshold)
        )
        value_range = (-50.0, 200.0) if metric == "temperature_celsius" else (0.0, 100.0)
        threshold_range = (
            (20.0, 120.0) if metric == "temperature_celsius" else (1.0, 100.0)
        )
        if (
            not numeric
            or not value_range[0] <= value <= value_range[1]
            or not threshold_range[0] <= threshold <= threshold_range[1]
        ):
            raise ValueError("Health alert values are outside the supported metric range")
        route = self._route(provider)
        if not route["enabled"]:
            return {"provider": provider, "status": "disabled", "remote_status": None}
        status, _ = self._current_status(
            route, enforce_global_outbound_policy=False
        )
        if status != "ready":
            return {"provider": provider, "status": status, "remote_status": None}

        label, unit = metrics[metric]
        state = "recovered" if event_type == "recovered" else "remains above" if event_type == "ongoing" else "crossed"
        message = (
            f"JARVIS system health: {label} {state} its threshold — "
            f"{value:.1f}{unit} / {threshold:.1f}{unit}."
        )
        client = await self._http()
        remote_status: int | None = None
        delivered = False
        try:
            if provider == "telegram":
                token, _ = self._credential_value(provider, route["config"], TELEGRAM_TOKEN_ENV)
                if token is None:
                    raise KeyError("credential unavailable")
                response = await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={
                        "chat_id": route["config"]["chat_id"],
                        "text": message,
                        "disable_web_page_preview": True,
                    },
                )
                remote_status = response.status_code
                delivered = remote_status == 200 and bool(response.json().get("ok"))
            elif route["config"].get("mode") == "webhook":
                webhook, _ = self._credential_value(
                    provider, route["config"], DISCORD_WEBHOOK_ENV
                )
                if webhook is None:
                    raise KeyError("credential unavailable")
                response = await client.post(
                    webhook,
                    json={"content": message, "allowed_mentions": {"parse": []}},
                )
                remote_status = response.status_code
                delivered = remote_status in {200, 204}
            else:
                bot_token, _ = self._credential_value(
                    provider, route["config"], DISCORD_BOT_TOKEN_ENV
                )
                if bot_token is None:
                    raise KeyError("credential unavailable")
                response = await client.post(
                    f"https://discord.com/api/v10/channels/{route['config']['channel_id']}/messages",
                    headers={"Authorization": f"Bot {bot_token}"},
                    json={"content": message, "allowed_mentions": {"parse": []}},
                )
                remote_status = response.status_code
                delivered = remote_status in {200, 201}
        except (httpx.HTTPError, KeyError, ValueError, TypeError, json.JSONDecodeError):
            delivered = False
        outcome = "delivered" if delivered else "error"
        self.audit.append(
            "system:health-monitor",
            "communications.health_alert_delivery",
            f"communications:{provider}",
            {
                "provider": provider,
                "metric": metric,
                "event_type": event_type,
                "outcome": outcome,
                "remote_status": remote_status,
            },
        )
        return {"provider": provider, "status": outcome, "remote_status": remote_status}

    async def close(self) -> None:
        """Close the lazily created outbound verification client."""

        if self._client is not None:
            await self._client.aclose()
            self._client = None


__all__ = [
    "DELIVERY_PROVIDERS",
    "DISCORD_BOT_TOKEN_ENV",
    "DISCORD_WEBHOOK_ENV",
    "PROVIDERS",
    "STATUS_VALUES",
    "TELEGRAM_TOKEN_ENV",
    "WEB_ROUTE_TOKEN_ENV",
    "CommunicationError",
    "CommunicationRateLimitError",
    "CommunicationSecretStorageError",
    "CommunicationsService",
    "ProtectedSecretStore",
    "ProviderId",
    "RouteStatus",
]
