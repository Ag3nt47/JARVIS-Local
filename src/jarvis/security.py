"""Application firewall, IDS/IPS counters, and live defensive posture reporting."""

from __future__ import annotations

import ipaddress
import os
import platform
import re
import subprocess
import threading
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .audit import AuditLog
from .config import Settings

SECURITY_FLOW: tuple[tuple[str, str, str], ...] = (
    ("devices", "transport", "encrypted channel"),
    ("transport", "peer_auth", "stable peer identity"),
    ("peer_auth", "app_firewall", "authenticated request"),
    ("app_firewall", "ids", "allowed traffic"),
    ("ids", "ips", "observed event"),
    ("ips", "capability", "clean request"),
    ("capability", "approval", "scoped operation"),
    ("approval", "action", "approved action"),
    ("action", "audit", "result and evidence"),
)


def _loopback(address: str | None) -> bool:
    if not address:
        return False
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return address == "localhost"


class SecurityMonitor:
    """Track and block unsafe HTTP traffic without inspecting private content."""

    def __init__(
        self,
        settings: Settings,
        audit: AuditLog,
        *,
        request_size_limit: int = 1_048_576,
        rate_limit_per_minute: int = 600,
        block_seconds: int = 900,
    ) -> None:
        self.settings = settings
        self.audit = audit
        self.request_size_limit = request_size_limit
        self.rate_limit_per_minute = rate_limit_per_minute
        self.block_seconds = block_seconds
        self._lock = threading.RLock()
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._blocked_until: dict[str, float] = {}
        self._counters = {
            "requests": 0,
            "allowed": 0,
            "blocked": 0,
            "suspicious": 0,
            "rate_limited": 0,
        }

    @staticmethod
    def _suspicious_path(path: str) -> bool:
        normalized = path.casefold()
        return any(token in normalized for token in ("..", "%2e", "%00", "\\"))

    def inspect(self, request: Request) -> tuple[int, str] | None:
        client = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with self._lock:
            self._counters["requests"] += 1
            if self._blocked_until.get(client, 0) > now:
                self._counters["blocked"] += 1
                return 403, "Client is temporarily blocked by the application firewall"
            if not _loopback(client):
                self._counters["blocked"] += 1
                self._counters["suspicious"] += 1
                self._blocked_until[client] = now + self.block_seconds
                self._audit_rejection(client, request, "non_loopback")
                return 403, "Remote traffic is disabled until authenticated federation is paired"
            if request.method not in {"GET", "HEAD", "POST", "PATCH", "DELETE", "OPTIONS"}:
                self._counters["blocked"] += 1
                self._counters["suspicious"] += 1
                self._audit_rejection(client, request, "method")
                return 405, "HTTP method blocked by policy"
            if self._suspicious_path(request.url.path):
                self._counters["blocked"] += 1
                self._counters["suspicious"] += 1
                self._audit_rejection(client, request, "path")
                return 403, "Suspicious path blocked by policy"
            raw_length = request.headers.get("content-length", "0")
            try:
                content_length = int(raw_length)
            except ValueError:
                content_length = self.request_size_limit + 1
            if content_length > self.request_size_limit:
                self._counters["blocked"] += 1
                self._audit_rejection(client, request, "request_size")
                return 413, "Request exceeds the application firewall size limit"
            history = self._requests[client]
            while history and history[0] <= now - 60:
                history.popleft()
            history.append(now)
            if len(history) > self.rate_limit_per_minute:
                self._counters["blocked"] += 1
                self._counters["rate_limited"] += 1
                self._blocked_until[client] = now + min(self.block_seconds, 60)
                self._audit_rejection(client, request, "rate_limit")
                return 429, "Request rate exceeded the application firewall limit"
            self._counters["allowed"] += 1
        return None

    def _audit_rejection(self, client: str, request: Request, reason: str) -> None:
        self.audit.append(
            "security",
            "request.blocked",
            "network:application-firewall",
            {
                "client": client,
                "method": request.method,
                "path": request.url.path[:500],
                "reason": reason,
            },
        )

    def counters(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counters)

    @staticmethod
    def os_firewall_status() -> str:
        system = platform.system().lower()
        try:
            if system == "windows":
                system_root = Path(os.getenv("SystemRoot", r"C:\Windows"))
                executable = system_root / "System32" / "netsh.exe"
                if not executable.is_file():
                    return "unknown"
                result = subprocess.run(
                    [str(executable), "advfirewall", "show", "allprofiles", "state"],
                    capture_output=True,
                    text=True,
                    timeout=4,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                states = re.findall(r"State\s+(ON|OFF)", result.stdout, re.IGNORECASE)
                if states and all(state.casefold() == "on" for state in states):
                    return "active"
                return "degraded" if states else "unknown"
            if system == "darwin":
                executable = Path("/usr/libexec/ApplicationFirewall/socketfilterfw")
                if not executable.is_file():
                    return "unknown"
                result = subprocess.run(
                    [str(executable), "--getglobalstate"],
                    capture_output=True,
                    text=True,
                    timeout=4,
                    check=False,
                )
                return "active" if "enabled" in result.stdout.casefold() else "degraded"
        except (OSError, subprocess.SubprocessError):
            return "unknown"
        return "unknown"

    def posture(
        self,
        *,
        federation: dict[str, Any],
        audit_valid: bool,
        angerona: dict[str, Any],
    ) -> dict[str, Any]:
        signing_ready = bool(federation.get("signing_key_configured"))
        transport_ready = bool(federation.get("transport_available"))
        capability_ready = bool(federation.get("capability_endpoint_ready"))
        peer_ready = bool(federation.get("ready"))
        layers = [
            {"id": "devices", "name": "Angerona Endpoint Defense", "status": "active" if angerona.get("mcp_ready") else "locked" if angerona.get("running") else "degraded", "detail": "Primary defensive layer; authenticated MCP observation plus exact, locally approved UI Automation", "metric": "connected" if angerona.get("mcp_ready") else "pair MCP" if angerona.get("running") else "offline"},
            {"id": "transport", "name": "Tailscale Transport", "status": "active" if peer_ready else "locked", "detail": "Encrypted tailnet path; direct internet exposure is denied", "metric": f"{len(federation.get('peer_urls', []))} peers" if transport_ready else "not installed"},
            {"id": "peer_auth", "name": "Peer Authentication", "status": "active" if capability_ready else "locked", "detail": "Ed25519-signed requests, stable peer identity, freshness, and replay rejection", "metric": "endpoint ready" if capability_ready else "peer keys required" if signing_ready else "keys required"},
            {"id": "app_firewall", "name": "Application Firewall", "status": "active", "detail": "Loopback allowlist, method/path/body limits, and security headers", "metric": f"{self._counters['blocked']} blocked"},
            {"id": "ids", "name": "IDS", "status": "active", "detail": "Observes anomalous paths, clients, methods, and request rates", "metric": f"{self._counters['suspicious']} suspicious"},
            {"id": "ips", "name": "IPS", "status": "active", "detail": "Blocks unsafe clients and throttles repeated requests", "metric": f"{self._counters['rate_limited']} throttled"},
            {"id": "capability", "name": "Capability Policy", "status": "active", "detail": "Typed allowlisted tools; no hidden or generic remote shell", "metric": "scoped"},
            {"id": "approval", "name": "Target Approval", "status": "active", "detail": "Privileged Mac/workstation actions require target-local approval", "metric": "single-use"},
            {"id": "action", "name": "Action / Compute / Data", "status": "active", "detail": "Bounded compute jobs and explicitly shared roots", "metric": "policy gated"},
            {"id": "audit", "name": "Hash-chained Audit", "status": "active" if audit_valid else "degraded", "detail": "Security decisions and actions are tamper-evident", "metric": "valid" if audit_valid else "verify"},
        ]
        os_firewall = self.os_firewall_status()
        return {
            "status": "active" if audit_valid and os_firewall != "degraded" and angerona.get("mcp_ready") else "degraded",
            "layers": layers,
            "flow": [
                {"from": source, "to": target, "label": label}
                for source, target, label in SECURITY_FLOW
            ],
            "counters": self.counters(),
            "policies": {
                "loopback_only": True,
                "request_size_limit": self.request_size_limit,
                "rate_limit_per_minute": self.rate_limit_per_minute,
                "block_seconds": self.block_seconds,
                "ids_enabled": True,
                "ips_enabled": True,
                "os_firewall": os_firewall,
                "primary_defense": "angerona",
                "angerona_running": bool(angerona.get("running")),
                "angerona_mcp_ready": bool(angerona.get("mcp_ready")),
            },
        }


class SecurityMiddleware(BaseHTTPMiddleware):
    """Apply defensive request policy and browser hardening headers."""

    def __init__(self, app: Any, monitor: SecurityMonitor) -> None:
        super().__init__(app)
        self.monitor = monitor

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        rejection = self.monitor.inspect(request)
        if rejection:
            status_code, detail = rejection
            response: Response = JSONResponse(status_code=status_code, content={"detail": detail})
        else:
            response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(self), geolocation=(), payment=(), usb=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response
