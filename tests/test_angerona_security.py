from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from jarvis.angerona import AngeronaAdapter
from jarvis.api import create_app
from jarvis.config import Settings
from jarvis.preferences import PreferencesUpdate
from jarvis.tools import ToolError


@pytest.mark.asyncio
async def test_angerona_adapter_requires_token_and_redacts_event_details(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "AngeronaSuite"
    (root / "src" / "angerona").mkdir(parents=True)
    monkeypatch.delenv("ANGERONA_MCP_TOKEN", raising=False)
    adapter = AngeronaAdapter(root)

    status = await adapter.status()
    assert status["installed"] is True
    assert status["mcp_ready"] is False
    assert status["external_actions_supported"] is False
    with pytest.raises(ToolError, match="32-byte"):
        await adapter.recent_events()

    async def fake_call(name: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        assert name == "get_recent_alerts"
        assert arguments == {"limit": 3}
        return [
            {
                "id": "signed-event",
                "ts": 123.0,
                "module": "Process Monitor",
                "severity": "High",
                "message": "Blocked suspicious process",
                "details": {"secret_path": r"C:\private\payload.exe"},
                "mitre_tags": ["T1059"],
                "integrity_verified": True,
            }
        ]

    monkeypatch.setattr(adapter, "call_tool", fake_call)
    events = await adapter.recent_events(3)
    assert events[0]["integrity_verified"] is True
    assert "details" not in events[0]
    assert "private" not in str(events[0])


def test_peer_url_rejects_embedded_secret_material() -> None:
    with pytest.raises(ValueError, match="credentials"):
        PreferencesUpdate.model_validate(
            {"federation": {"peer_urls": ["http://user:secret@100.64.0.2:9443"]}}
        )
    with pytest.raises(ValueError, match="queries"):
        PreferencesUpdate.model_validate(
            {"federation": {"peer_urls": ["http://100.64.0.2:9443?token=secret"]}}
        )


@pytest.mark.asyncio
async def test_application_firewall_blocks_remote_clients_and_oversize_requests(
    tmp_path: Path,
) -> None:
    settings = Settings(
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        allowed_roots=(tmp_path,),
        quiet_hours_enabled=False,
    )
    settings.validate()
    app = create_app(settings)
    remote_transport = httpx.ASGITransport(app=app, client=("198.51.100.8", 43120))
    async with httpx.AsyncClient(
        transport=remote_transport, base_url="http://jarvis.local"
    ) as remote:
        blocked = await remote.get("/api/health")
    assert blocked.status_code == 403
    assert blocked.headers["x-content-type-options"] == "nosniff"

    local_transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 43121))
    async with httpx.AsyncClient(
        transport=local_transport, base_url="http://127.0.0.1"
    ) as local:
        oversize = await local.post(
            "/api/chat",
            content=b"{}",
            headers={"Content-Length": "1048577"},
        )
        root = await local.get("/api")
    assert oversize.status_code == 413
    assert root.status_code == 200
    assert "default-src 'self'" in root.headers["content-security-policy"]
