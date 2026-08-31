"""Application wiring tests for the opt-in signed federation boundary."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from jarvis.api import create_app
from jarvis.config import Settings
from jarvis.federation import sign_request
from jarvis.federation_service import FEDERATION_ENDPOINT, FEDERATION_ENVELOPE_HEADER


def _public_key(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _configure_environment(
    monkeypatch: pytest.MonkeyPatch,
    private_key: Ed25519PrivateKey,
    root: Path,
) -> None:
    monkeypatch.setenv("JARVIS_FEDERATION_ENABLED", "true")
    monkeypatch.setenv("JARVIS_FEDERATION_NODE_ID", "windows.node")
    monkeypatch.setenv(
        "JARVIS_FEDERATION_PEERS",
        json.dumps(
            [
                {
                    "peer_id": "macbook.peer",
                    "key_id": "macbook.primary",
                    "public_key": _public_key(private_key),
                    "scopes": [
                        "compute.chat",
                        "data.list",
                        "data.read",
                        "admin.request",
                    ],
                }
            ]
        ),
    )
    monkeypatch.setenv("JARVIS_FEDERATION_ROOTS", json.dumps({"workspace": str(root)}))


def _body(capability: str, payload: dict[str, Any]) -> bytes:
    return json.dumps(
        {"capability": capability, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _signed_headers(
    private_key: Ed25519PrivateKey,
    body: bytes,
    *,
    nonce: str,
    request_id: str,
) -> dict[str, str]:
    envelope = sign_request(
        private_key,
        peer_id="macbook.peer",
        key_id="macbook.primary",
        method="POST",
        path_query=FEDERATION_ENDPOINT,
        timestamp=int(time.time()),
        nonce=nonce,
        request_id=request_id,
        raw_body=body,
    )
    return {
        "Content-Type": "application/json",
        FEDERATION_ENVELOPE_HEADER: json.dumps(envelope.to_mapping(), separators=(",", ":")),
    }


@pytest.mark.asyncio
async def test_federation_is_disabled_and_loopback_only_by_default(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "JARVIS_FEDERATION_ENABLED",
        "JARVIS_FEDERATION_NODE_ID",
        "JARVIS_FEDERATION_PEERS",
        "JARVIS_FEDERATION_ROOTS",
    ):
        monkeypatch.delenv(name, raising=False)
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 44000))
    async with app.router.lifespan_context(app):  # noqa: SIM117
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            status = (await client.get("/api/federation/status")).json()
            assert status["enabled"] is False
            assert status["ready"] is False
            assert status["listener"] == "loopback-only"
            assert status["transport_exposed"] is False
            response = await client.post(FEDERATION_ENDPOINT, content=b"{}")
            assert response.status_code == 503


@pytest.mark.asyncio
async def test_signed_capabilities_are_scoped_idempotent_and_admin_is_target_approved(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_key = Ed25519PrivateKey.generate()
    root = settings.allowed_roots[0]
    (root / "shared.bin").write_bytes(b"federated data")
    _configure_environment(monkeypatch, private_key, root)
    app = create_app(settings)
    services = app.state.services
    calls = 0

    async def chat(messages, *, model=None, tools=None):
        nonlocal calls
        calls += 1
        assert tools is None
        assert messages == [{"role": "user", "content": "status"}]
        return {
            "model": model,
            "message": {"role": "assistant", "content": "ready"},
        }

    monkeypatch.setattr(services.provider, "chat", chat)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 44001))
    async with app.router.lifespan_context(app):  # noqa: SIM117
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            status_response = await client.get("/api/federation/status")
            assert status_response.status_code == 200
            status = status_response.json()
            assert status["ready"] is True
            assert status["peer_key_count"] == 1
            assert status["named_roots"] == ["workspace"]
            serialized_status = status_response.text
            assert "macbook.peer" not in serialized_status
            assert _public_key(private_key) not in serialized_status
            assert str(root) not in serialized_status

            chat_body = _body(
                "compute.chat",
                {
                    "model": "qwen3:4b",
                    "messages": [{"role": "user", "content": "status"}],
                },
            )
            first = await client.post(
                FEDERATION_ENDPOINT,
                content=chat_body,
                headers=_signed_headers(
                    private_key,
                    chat_body,
                    nonce="nonce-chat-000001",
                    request_id="request-chat-0001",
                ),
            )
            assert first.status_code == 200
            assert first.json()["message"]["content"] == "ready"

            retry = await client.post(
                FEDERATION_ENDPOINT,
                content=chat_body,
                headers=_signed_headers(
                    private_key,
                    chat_body,
                    nonce="nonce-chat-000002",
                    request_id="request-chat-0001",
                ),
            )
            assert retry.status_code == 200
            assert retry.json()["idempotent"] is True
            assert calls == 1

            replay_headers = _signed_headers(
                private_key,
                chat_body,
                nonce="nonce-chat-000003",
                request_id="request-chat-0002",
            )
            assert (
                await client.post(FEDERATION_ENDPOINT, content=chat_body, headers=replay_headers)
            ).status_code == 200
            assert (
                await client.post(FEDERATION_ENDPOINT, content=chat_body, headers=replay_headers)
            ).status_code == 409

            embed_body = _body(
                "compute.embed",
                {"model": "nomic-embed-text:latest", "inputs": ["status"]},
            )
            denied = await client.post(
                FEDERATION_ENDPOINT,
                content=embed_body,
                headers=_signed_headers(
                    private_key,
                    embed_body,
                    nonce="nonce-embed-00001",
                    request_id="request-embed-001",
                ),
            )
            assert denied.status_code == 403

            read_body = _body(
                "data.read",
                {
                    "root": "workspace",
                    "relative_path": "shared.bin",
                    "max_bytes": 1024,
                },
            )
            read = await client.post(
                FEDERATION_ENDPOINT,
                content=read_body,
                headers=_signed_headers(
                    private_key,
                    read_body,
                    nonce="nonce-read-0000001",
                    request_id="request-read-0001",
                ),
            )
            assert read.status_code == 200
            assert base64.b64decode(read.json()["content"]) == b"federated data"

            admin_body = _body(
                "admin.request",
                {
                    "action": "policy.change.request",
                    "target_peer_id": "windows.node",
                    "reason": "Request a reviewed local policy change",
                },
            )
            admin = await client.post(
                FEDERATION_ENDPOINT,
                content=admin_body,
                headers=_signed_headers(
                    private_key,
                    admin_body,
                    nonce="nonce-admin-000001",
                    request_id="request-admin-001",
                ),
            )
            assert admin.status_code == 200
            approval_id = admin.json()["approval"]["id"]
            assert admin.json()["executed"] is False
            approval = services.approvals.get(approval_id)
            assert approval is not None
            assert approval["status"] == "pending"
            assert approval["tool_name"] == "federation_admin_proposal"

            admin_retry = await client.post(
                FEDERATION_ENDPOINT,
                content=admin_body,
                headers=_signed_headers(
                    private_key,
                    admin_body,
                    nonce="nonce-admin-000002",
                    request_id="request-admin-001",
                ),
            )
            assert admin_retry.json()["approval"]["id"] == approval_id
            assert len(services.approvals.list()) == 1

            approved = await client.post(
                f"/api/approvals/{approval_id}/approve",
                json={"actor": "target:local-user"},
            )
            assert approved.status_code == 200
            assert approved.json()["outcome"]["executed"] is False
            assert approved.json()["approval"]["status"] == "approved"
            assert approved.json()["approval"]["result"]["executed"] is False

            audit = services.audit.list(100)
            actions = {entry["action"] for entry in audit}
            assert "federation.admin.queued" in actions
            assert "federation.admin.approved_no_execution" in actions
            assert all("signature" not in json.dumps(entry) for entry in audit)


@pytest.mark.asyncio
async def test_invalid_opt_in_configuration_fails_closed(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JARVIS_FEDERATION_ENABLED", "true")
    monkeypatch.setenv("JARVIS_FEDERATION_NODE_ID", "windows.node")
    monkeypatch.setenv("JARVIS_FEDERATION_PEERS", "not-json")
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 44002))
    async with app.router.lifespan_context(app):  # noqa: SIM117
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            status = (await client.get("/api/federation/status")).json()
            assert status["enabled"] is False
            assert status["ready"] is False
            assert status["configuration"] == "invalid"
            assert (await client.post(FEDERATION_ENDPOINT, content=b"{}")).status_code == 503


@pytest.mark.asyncio
async def test_enabling_federation_does_not_weaken_the_non_loopback_firewall(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_key = Ed25519PrivateKey.generate()
    _configure_environment(monkeypatch, private_key, settings.allowed_roots[0])
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app, client=("100.64.0.8", 44003))
    async with app.router.lifespan_context(app):  # noqa: SIM117
        async with httpx.AsyncClient(transport=transport, base_url="http://100.64.0.1") as client:
            response = await client.get("/api/federation/status")
            assert response.status_code == 403
            assert "Remote traffic is disabled" in response.json()["detail"]
