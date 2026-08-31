"""Security, persistence, readiness, and API tests for Communications setup."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from jarvis.audit import AuditLog
from jarvis.communications import (
    DISCORD_WEBHOOK_ENV,
    TELEGRAM_TOKEN_ENV,
    CommunicationRateLimitError,
    CommunicationsService,
    ProtectedSecretStore,
)
from jarvis.config import Settings
from jarvis.database import Database

TELEGRAM_TOKEN = "12345678:" + "A" * 35
DISCORD_WEBHOOK = "https://discord.com/api/webhooks/12345678901234567/" + "B" * 48


def test_disabled_defaults_and_delivery_policy_are_persistent(
    database: Database, audit: AuditLog, settings: Settings
) -> None:
    communications = CommunicationsService(database, audit, settings)
    snapshot = communications.snapshot()

    assert [route["provider"] for route in snapshot["routes"]] == [
        "signal",
        "telegram",
        "discord",
        "web_app",
    ]
    assert all(route["enabled"] is False for route in snapshot["routes"])
    assert snapshot["policy"] == {
        "failover_enabled": False,
        "delivery_order": ["signal", "telegram", "discord"],
        "applies_to": ["signal", "telegram", "discord"],
        "configured_only": True,
        "semantics": (
            "Priority/failover preference only; no outbound send endpoint is exposed. "
            "Future delivery must stop on authentication, target, payload, rate-limit, "
            "or ambiguous-timeout failures."
        ),
    }
    web = snapshot["routes"][-1]
    assert web["channel_kind"] == "interactive_access"
    assert web["delivery_supported"] is False
    assert web["priority"] is None
    assert web["configuration"]["base_url"] == f"http://127.0.0.1:{settings.port}"
    assert database.query_all("SELECT * FROM communication_secrets") == []

    reordered = communications.reorder(["discord", "signal", "telegram"], actor="tester")
    assert reordered["policy"]["delivery_order"] == ["discord", "signal", "telegram"]
    assert reordered["routes"][-1]["provider"] == "web_app"
    assert (
        communications.update_failover(True, actor="tester")["policy"]["failover_enabled"] is True
    )

    second = CommunicationsService(database, audit, settings)
    assert second.snapshot()["policy"]["delivery_order"] == ["discord", "signal", "telegram"]
    assert second.snapshot()["policy"]["failover_enabled"] is True


def test_route_configuration_masks_identifiers_and_blocks_unsafe_fields(
    database: Database, audit: AuditLog, settings: Settings, monkeypatch
) -> None:
    communications = CommunicationsService(database, audit, settings)
    monkeypatch.setattr(communications, "_signal_cli_available", lambda: True)

    signal = communications.update_route(
        "signal",
        {
            "enabled": True,
            "account": "+15551234567",
            "recipient": "+15557654321",
        },
        actor="tester",
    )
    assert signal["status"] == "unverified"
    assert signal["configuration"] == {
        "account_configured": True,
        "account_masked": "+1••••••4567",
        "recipient_configured": True,
        "recipient_masked": "+1••••••4321",
    }
    assert "+15551234567" not in json.dumps(signal)

    telegram = communications.update_route(
        "telegram", {"enabled": True, "chat_id": "-1001234567890"}, actor="tester"
    )
    assert telegram["configuration"]["chat_id_configured"] is True
    assert "-1001234567890" not in json.dumps(telegram)

    discord = communications.update_route(
        "discord",
        {"enabled": True, "mode": "bot", "channel_id": "12345678901234567"},
        actor="tester",
    )
    assert discord["configuration"]["mode"] == "bot"
    assert "12345678901234567" not in json.dumps(discord)

    loopback = communications.update_route(
        "web_app", {"enabled": True, "base_url": "http://127.0.0.1:8765"}
    )
    assert loopback["status"] == "ready"
    assert loopback["configuration"]["network_scope"] == "loopback"

    for unsafe_url in (
        "https://example.com",
        "http://192.168.1.5:8765",
        "https://private.example.test",
        "https://user:password@192.168.1.5",
        "https://192.168.1.5/app",
        "https://192.168.1.5?probe=1",
    ):
        with pytest.raises(ValueError):
            communications.update_route("web_app", {"base_url": unsafe_url, "auth_enabled": False})
    private = communications.update_route(
        "web_app",
        {
            "base_url": "https://192.168.1.5:8765",
            "auth_enabled": True,
        },
    )
    assert private["configuration"]["network_scope"] == "private_lan"
    assert private["status"] == "setup_required"

    for forbidden in ("command", "script", "executable", "path", "webhook_url", "token"):
        with pytest.raises(ValueError, match="Unsupported"):
            communications.update_route("telegram", {forbidden: "not-allowed"})

    audit_json = json.dumps(audit.list())
    assert "+15551234567" not in audit_json
    assert "-1001234567890" not in audit_json
    assert "12345678901234567" not in audit_json
    assert audit.verify()["valid"] is True


@pytest.mark.skipif(not ProtectedSecretStore.available(), reason="Windows DPAPI is unavailable")
def test_dpapi_credential_is_write_only_encrypted_and_clearable(
    database: Database, audit: AuditLog, settings: Settings, monkeypatch
) -> None:
    monkeypatch.delenv(TELEGRAM_TOKEN_ENV, raising=False)
    settings.outbound_notifications_enabled = True
    communications = CommunicationsService(database, audit, settings)
    communications.update_route(
        "telegram", {"enabled": True, "chat_id": "1234567890"}, actor="tester"
    )

    route = communications.replace_credential("telegram", TELEGRAM_TOKEN, actor="tester")
    assert route["status"] == "ready"
    assert TELEGRAM_TOKEN not in json.dumps(route)
    protected = database.query_one(
        "SELECT credential_kind,protected_value FROM communication_secrets WHERE provider='telegram'"
    )
    assert protected["credential_kind"] == "telegram_bot_token"
    assert TELEGRAM_TOKEN.encode() not in bytes(protected["protected_value"])
    assert TELEGRAM_TOKEN.encode() not in settings.database_path.read_bytes()
    assert TELEGRAM_TOKEN not in json.dumps(audit.list())
    prerequisite = next(
        item for item in route["prerequisites"] if item["id"] == "credential:telegram_bot_token"
    )
    assert prerequisite["source"] == "protected_local"
    assert prerequisite["ready"] is True

    monkeypatch.setenv(TELEGRAM_TOKEN_ENV, "87654321:" + "Z" * 35)
    overridden = communications.snapshot()["routes"][1]
    credential = next(
        item for item in overridden["prerequisites"] if item["id"].startswith("credential:")
    )
    assert credential["source"] == "environment"
    monkeypatch.delenv(TELEGRAM_TOKEN_ENV)

    cleared = communications.clear_credential("telegram", actor="tester")
    assert cleared["status"] == "setup_required"
    assert (
        database.query_one("SELECT * FROM communication_secrets WHERE provider='telegram'") is None
    )


@pytest.mark.asyncio
async def test_identity_test_is_fixed_host_atomic_rate_limited_and_sends_nothing(
    database: Database, audit: AuditLog, settings: Settings, monkeypatch
) -> None:
    settings.outbound_notifications_enabled = False
    monkeypatch.setenv(TELEGRAM_TOKEN_ENV, TELEGRAM_TOKEN)
    communications = CommunicationsService(database, audit, settings)
    communications.update_route(
        "telegram", {"enabled": True, "chat_id": "1234567890"}, actor="tester"
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"id": 1}})

    communications._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False, trust_env=False
    )
    result = await communications.test_route("telegram", actor="tester")
    assert result == {
        "provider": "telegram",
        "status": "blocked",
        "detail": "Provider identity verified; outbound delivery remains blocked by local policy",
        "tested_at": result["tested_at"],
        "connectivity_checked": True,
        "credential_verified": True,
    }
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.host == "api.telegram.org"
    assert requests[0].url.path.endswith("/getMe")
    assert "sendMessage" not in str(requests[0].url)

    with pytest.raises(CommunicationRateLimitError) as replay:
        await communications.test_route("telegram", actor="tester")
    assert 1 <= replay.value.retry_after <= 120
    assert len(requests) == 1
    assert TELEGRAM_TOKEN not in json.dumps(audit.list())
    await communications.close()


@pytest.mark.asyncio
async def test_concurrent_tests_share_one_atomic_lease(
    database: Database, audit: AuditLog, settings: Settings, monkeypatch
) -> None:
    settings.outbound_notifications_enabled = True
    monkeypatch.setenv(TELEGRAM_TOKEN_ENV, TELEGRAM_TOKEN)
    communications = CommunicationsService(database, audit, settings)
    communications.update_route("telegram", {"enabled": True, "chat_id": "1234567890"})

    async def request_once(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.02)
        return httpx.Response(200, json={"ok": True}, request=request)

    communications._client = httpx.AsyncClient(transport=httpx.MockTransport(request_once))
    outcomes = await asyncio.gather(
        communications.test_route("telegram"),
        communications.test_route("telegram"),
        return_exceptions=True,
    )
    assert sum(isinstance(item, CommunicationRateLimitError) for item in outcomes) == 1
    assert sum(isinstance(item, dict) for item in outcomes) == 1
    await communications.close()


def test_malformed_discord_environment_never_breaks_status(
    database: Database, audit: AuditLog, settings: Settings, monkeypatch
) -> None:
    monkeypatch.setenv(
        DISCORD_WEBHOOK_ENV,
        "https://discord.com:invalid/api/webhooks/12345678901234567/" + "X" * 48,
    )
    communications = CommunicationsService(database, audit, settings)
    route = communications.update_route("discord", {"enabled": True, "mode": "webhook"})
    assert route["status"] == "setup_required"
    credential = next(
        item for item in route["prerequisites"] if item["id"].startswith("credential:")
    )
    assert credential["configured"] is True
    assert credential["ready"] is False


@pytest.mark.asyncio
async def test_communications_api_contract_and_secret_validation_redaction(
    tmp_path: Path, monkeypatch
) -> None:
    from jarvis.api import create_app

    monkeypatch.delenv(TELEGRAM_TOKEN_ENV, raising=False)
    root = tmp_path / "project"
    root.mkdir()
    settings = Settings(
        project_root=root,
        data_dir=tmp_path / "data",
        allowed_roots=(root,),
        quiet_hours_enabled=False,
        outbound_notifications_enabled=False,
    )
    settings.validate()
    app = create_app(settings)
    services = app.state.services
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 45500))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        snapshot = await client.get("/api/communications")
        assert snapshot.status_code == 200
        assert snapshot.json()["status_values"] == [
            "disabled",
            "setup_required",
            "ready",
            "unavailable",
            "unverified",
            "blocked",
            "error",
        ]
        assert snapshot.json()["policy"]["delivery_order"] == [
            "signal",
            "telegram",
            "discord",
        ]

        telegram = await client.patch(
            "/api/communications/routes/telegram",
            json={"enabled": True, "chat_id": "1234567890"},
        )
        assert telegram.status_code == 200
        assert telegram.json()["route"]["status"] == "setup_required"
        assert (await client.get("/api/communications/routes/telegram")).json()["route"][
            "provider"
        ] == "telegram"

        unsafe_secret = "plaintext-must-not-return"
        invalid = await client.patch(
            "/api/communications/routes/telegram",
            json={"enabled": True, "token": unsafe_secret},
        )
        assert invalid.status_code == 422
        assert invalid.json() == {"detail": "Request validation failed"}
        assert unsafe_secret not in invalid.text
        assert (
            await client.patch("/api/communications/routes/telegram", json={})
        ).status_code == 422

        public_web = await client.patch(
            "/api/communications/routes/web_app",
            json={"enabled": True, "base_url": "https://example.com", "auth_enabled": True},
        )
        assert public_web.status_code == 409

        reordered = await client.patch(
            "/api/communications/order",
            json={"provider_order": ["discord", "telegram", "signal"]},
        )
        assert reordered.status_code == 200
        assert reordered.json()["policy"]["delivery_order"] == [
            "discord",
            "telegram",
            "signal",
        ]
        assert (
            await client.patch(
                "/api/communications/order",
                json={"provider_order": ["web_app", "telegram", "signal"]},
            )
        ).status_code == 422
        failover = await client.patch(
            "/api/communications/failover", json={"failover_enabled": True}
        )
        assert failover.json()["policy"]["failover_enabled"] is True

        if ProtectedSecretStore.available():
            credential = await client.post(
                "/api/communications/routes/telegram/credential",
                json={"credential": TELEGRAM_TOKEN},
            )
            assert credential.status_code == 200
            assert TELEGRAM_TOKEN not in credential.text
            assert (
                await client.delete("/api/communications/routes/telegram/credential")
            ).status_code == 200

        assert (
            await client.post(
                "/api/communications/routes/signal/credential",
                json={"credential": TELEGRAM_TOKEN},
            )
        ).status_code == 422
        assert (await client.post("/api/communications/routes/unknown/test")).status_code == 422
        assert (
            await client.post("/api/communications/routes/telegram/send", json={})
        ).status_code == 404

    await services.communications.close()
    await services.provider.close()
    services.federation.close()
    services.database.close()
