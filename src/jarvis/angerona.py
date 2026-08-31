"""Authenticated, read-only adapter for Angerona's supported loopback MCP API."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlparse

import httpx
import psutil

from .tools import ToolError


class AngeronaAdapter:
    """Observe Angerona without importing its internals or crossing its authority boundary."""

    ALLOWED_TOOLS: ClassVar[frozenset[str]] = frozenset(
        {
            "get_recent_alerts",
            "get_module_status",
            "get_attack_heatmap",
            "get_incidents",
            "get_remediation_log",
            "search_events",
            "get_enterprise_readiness",
            "get_causal_incident_graph",
        }
    )

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(
            os.getenv("ANGERONA_ROOT", r"D:\local-security-ai\AngeronaSuite")
        )
        try:
            port = int(os.getenv("ANGERONA_MCP_PORT", "47923"))
        except ValueError:
            port = 47923
        self.port = port if 1 <= port <= 65_535 else 47923
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.token = os.getenv("ANGERONA_MCP_TOKEN", "")

    @staticmethod
    def _running() -> bool:
        for process in psutil.process_iter(("cmdline",)):
            try:
                command = [str(value).casefold() for value in (process.info["cmdline"] or [])]
                if any(
                    value == "-m" and command[index + 1] == "angerona"
                    for index, value in enumerate(command[:-1])
                ):
                    return True
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError, TypeError, ValueError):
                continue
        return False

    def _headers(self) -> dict[str, str]:
        headers = {"Host": "127.0.0.1"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _mcp_ready(self) -> bool:
        if len(self.token.encode("utf-8")) < 32:
            return False
        try:
            async with httpx.AsyncClient(timeout=1.5, trust_env=False) as client:
                response = await client.get(f"{self.base_url}/health", headers=self._headers())
            payload = response.json() if response.status_code == 200 else {}
            return payload == {"status": "ok", "server": "angerona-mcp"}
        except (httpx.HTTPError, ValueError, TypeError):
            return False

    async def status(self) -> dict[str, Any]:
        installed = (self.root / "src" / "angerona").is_dir()
        running = self._running()
        token_configured = len(self.token.encode("utf-8")) >= 32
        mcp_ready = await self._mcp_ready()
        return {
            "name": "Angerona",
            "role": "primary_endpoint_defense",
            "installed": installed,
            "running": running,
            "mcp_ready": mcp_ready,
            "token_configured": token_configured,
            "endpoint": self.base_url,
            "root": str(self.root),
            "status": "active" if mcp_ready else "locked" if running else "offline",
            "control_mode": "read_only_mcp",
            "external_actions_supported": False,
            "operator_start_required": True,
        }

    @staticmethod
    def _decode_sse(data_lines: list[str]) -> dict[str, Any]:
        try:
            payload = json.loads("\n".join(data_lines))
        except (json.JSONDecodeError, TypeError) as exc:
            raise ToolError("Angerona returned an invalid MCP event") from exc
        if not isinstance(payload, dict):
            raise ToolError("Angerona returned an invalid MCP payload")
        return payload

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name not in self.ALLOWED_TOOLS:
            raise ToolError("Angerona MCP tool is outside the read-only allowlist")
        if len(self.token.encode("utf-8")) < 32:
            raise ToolError("Angerona MCP requires a 32-byte-or-longer bearer token")
        timeout = httpx.Timeout(connect=2, read=6, write=2, pool=2)
        try:
            async with (
                httpx.AsyncClient(timeout=timeout, trust_env=False) as client,
                client.stream(
                    "GET", f"{self.base_url}/sse", headers=self._headers()
                ) as stream,
            ):
                    stream.raise_for_status()
                    event_name = ""
                    data_lines: list[str] = []
                    requested = False
                    async for line in stream.aiter_lines():
                        if line.startswith("event:"):
                            event_name = line[6:].strip()
                        elif line.startswith("data:"):
                            data_lines.append(line[5:].strip())
                        elif not line and data_lines:
                            payload = self._decode_sse(data_lines)
                            data_lines = []
                            if event_name == "endpoint":
                                endpoint = str(payload.get("uri", ""))
                                parsed = urlparse(endpoint)
                                session_id = parse_qs(parsed.query).get("sessionId", [""])[0]
                                if (
                                    parsed.scheme != "http"
                                    or parsed.hostname != "127.0.0.1"
                                    or parsed.port != self.port
                                    or parsed.path != "/message"
                                    or not session_id
                                ):
                                    raise ToolError("Angerona returned an unsafe MCP endpoint")
                                request = {
                                    "jsonrpc": "2.0",
                                    "id": 1,
                                    "method": "tools/call",
                                    "params": {"name": name, "arguments": arguments},
                                }
                                response = await client.post(
                                    endpoint,
                                    headers=self._headers(),
                                    json=request,
                                )
                                response.raise_for_status()
                                requested = True
                            elif event_name == "message" and requested:
                                if payload.get("id") != 1:
                                    continue
                                if "error" in payload:
                                    raise ToolError("Angerona rejected the read-only MCP request")
                                content = payload.get("result", {}).get("content", [])
                                text = content[0].get("text") if content else None
                                if not isinstance(text, str) or len(text) > 1_000_000:
                                    raise ToolError("Angerona returned an invalid MCP result")
                                return json.loads(text)
                            event_name = ""
        except ToolError:
            raise
        except (httpx.HTTPError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ToolError("Angerona read-only MCP is unavailable") from exc
        raise ToolError("Angerona MCP ended before returning a result")

    async def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        result = await self.call_tool("get_recent_alerts", {"limit": max(1, min(limit, 200))})
        if not isinstance(result, list):
            raise ToolError("Angerona returned an invalid alert list")
        events: list[dict[str, Any]] = []
        for raw in result[:200]:
            if not isinstance(raw, dict):
                continue
            events.append(
                {
                    "id": str(raw.get("id", ""))[:100],
                    "timestamp": raw.get("ts"),
                    "module": str(raw.get("module", "unknown"))[:200],
                    "severity": str(raw.get("severity", "unknown"))[:50],
                    "message": str(raw.get("message", ""))[:1000],
                    "mitre_tags": [str(tag)[:50] for tag in raw.get("mitre_tags", [])[:20]]
                    if isinstance(raw.get("mitre_tags"), list)
                    else [],
                    "integrity_verified": raw.get("integrity_verified") is True,
                }
            )
        return events
