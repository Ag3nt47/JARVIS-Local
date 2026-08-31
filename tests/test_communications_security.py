from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from jarvis.communications import (
    DISCORD_WEBHOOK_ENV,
    TELEGRAM_TOKEN_ENV,
    WEB_ROUTE_TOKEN_ENV,
    CommunicationRateLimitError,
    CommunicationsService,
    ProtectedSecretStore,
)


def _service(database, audit, settings) -> CommunicationsService:
    return CommunicationsService(database, audit, settings)


def test_public_state_masks_targets_and_never_persists_environment_secrets(
    database, audit, settings, monkeypatch
) -> None:
    telegram_token = f"12345678:{'A' * 40}"
    discord_webhook = f"https://discord.com/api/webhooks/12345678901234567/{'B' * 50}"
    web_token = "C" * 40
    monkeypatch.setenv(TELEGRAM_TOKEN_ENV, telegram_token)
    monkeypatch.setenv(DISCORD_WEBHOOK_ENV, discord_webhook)
    monkeypatch.setenv(WEB_ROUTE_TOKEN_ENV, web_token)
    service = _service(database, audit, settings)

    service.update_route(
        "signal",
        {"account": "+15551234567", "recipient": "+15557654321"},
    )
    service.update_route("telegram", {"chat_id": "1234567890123"})
    service.update_route("discord", {"mode": "webhook"})
    service.update_route(
        "web_app",
        {
            "base_url": "https://100.100.100.100:8443",
            "auth_enabled": True,
        },
    )

    snapshot_text = json.dumps(service.snapshot(), ensure_ascii=False)
    route_rows = json.dumps(database.query_all("SELECT * FROM communication_routes"))
    audit_rows = json.dumps(audit.list(100), ensure_ascii=False)
    combined = snapshot_text + route_rows + audit_rows

    assert telegram_token not in combined
    assert discord_webhook not in combined
    assert web_token not in combined
    assert "+15551234567" not in snapshot_text
    assert "+15557654321" not in snapshot_text
    assert "1234567890123" not in snapshot_text
    assert "account_masked" in snapshot_text
    assert "chat_id_masked" in snapshot_text


@pytest.mark.parametrize(
    "origin",
    [
        "https://example.com",
        "https://localhost.example.com",
        "https://user:password@127.0.0.1",
        "https://127.0.0.1/private",
        "https://127.0.0.1?next=http://169.254.169.254",
        "https://127.0.0.1#fragment",
        "https://127.0.0.1:99999",
        "http://192.168.1.20",
        "http://100.100.100.100",
        "https://169.254.169.254",
        "https://224.0.0.1",
    ],
)
def test_private_web_origin_policy_rejects_public_ambiguous_and_unsafe_destinations(
    database, audit, settings, origin: str
) -> None:
    service = _service(database, audit, settings)

    with pytest.raises(ValueError):
        service.update_route("web_app", {"base_url": origin, "auth_enabled": True})


@pytest.mark.parametrize(
    ("origin", "expected_scope"),
    [
        ("http://127.0.0.1:8765", "loopback"),
        ("https://192.168.1.20", "private_lan"),
        ("https://100.100.100.100", "tailscale"),
        ("https://[fd00::1]:8443", "private_lan"),
    ],
)
def test_private_web_origin_policy_accepts_only_explicit_private_origins(
    database, audit, settings, origin: str, expected_scope: str
) -> None:
    service = _service(database, audit, settings)

    route = service.update_route(
        "web_app",
        {"base_url": origin, "auth_enabled": expected_scope != "loopback"},
    )

    assert route["configuration"]["network_scope"] == expected_scope


def test_web_app_is_not_part_of_delivery_or_failover_order(database, audit, settings) -> None:
    service = _service(database, audit, settings)

    snapshot = service.reorder(["discord", "signal", "telegram"])

    assert snapshot["policy"]["delivery_order"] == ["discord", "signal", "telegram"]
    assert snapshot["policy"]["applies_to"] == ["signal", "telegram", "discord"]
    web_route = next(route for route in snapshot["routes"] if route["provider"] == "web_app")
    assert web_route["channel_kind"] == "interactive_access"
    assert web_route["delivery_supported"] is False
    assert web_route["priority"] is None


@pytest.mark.asyncio
async def test_provider_verification_is_get_only_and_atomic_rate_limited(
    database, audit, settings, monkeypatch
) -> None:
    settings.outbound_notifications_enabled = True
    token = f"12345678:{'A' * 40}"
    monkeypatch.setenv(TELEGRAM_TOKEN_ENV, token)
    service = _service(database, audit, settings)
    service.update_route("telegram", {"enabled": True, "chat_id": "1234567890123"})
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"id": 1}})

    service._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    )
    results = await asyncio.gather(
        service.test_route("telegram"),
        service.test_route("telegram"),
        return_exceptions=True,
    )
    await service.close()

    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.host == "api.telegram.org"
    assert requests[0].url.path.endswith("/getMe")
    assert sum(isinstance(result, CommunicationRateLimitError) for result in results) == 1
    successful = next(result for result in results if isinstance(result, dict))
    assert successful["status"] == "ready"
    assert successful["connectivity_checked"] is True
    assert token not in json.dumps(audit.list(100), ensure_ascii=False)


@pytest.mark.asyncio
async def test_web_policy_test_never_fetches_configured_origin(database, audit, settings) -> None:
    service = _service(database, audit, settings)
    service.update_route("web_app", {"enabled": True})

    def fail_on_request(_: httpx.Request) -> httpx.Response:
        raise AssertionError("private web policy verification must not make a network request")

    service._client = httpx.AsyncClient(transport=httpx.MockTransport(fail_on_request))
    result = await service.test_route("web_app")
    await service.close()

    assert result["status"] == "ready"
    assert result["connectivity_checked"] is False


def test_malformed_discord_webhook_fails_closed_without_url_parser_exception(
    database, audit, settings, monkeypatch
) -> None:
    monkeypatch.setenv(
        DISCORD_WEBHOOK_ENV,
        f"https://discord.com:99999/api/webhooks/12345678901234567/{'A' * 50}",
    )
    service = _service(database, audit, settings)

    route = service.update_route("discord", {"enabled": True, "mode": "webhook"})

    assert route["status"] == "setup_required"
    assert "malformed" in route["status_detail"].casefold()


def test_dpapi_credential_is_write_only_and_not_stored_as_plaintext(
    database, audit, settings, monkeypatch
) -> None:
    if not ProtectedSecretStore.available():
        pytest.skip("Windows DPAPI is not available")
    monkeypatch.delenv(TELEGRAM_TOKEN_ENV, raising=False)
    token = f"12345678:{'D' * 40}"
    service = _service(database, audit, settings)
    service.update_route("telegram", {"chat_id": "1234567890123"})

    public_route = service.replace_credential("telegram", token)
    protected = database.query_one(
        "SELECT protected_value FROM communication_secrets WHERE provider='telegram'"
    )["protected_value"]
    serialized_public = json.dumps(public_route, ensure_ascii=False)
    serialized_audit = json.dumps(audit.list(100), ensure_ascii=False)

    assert token.encode() not in bytes(protected)
    assert token not in serialized_public
    assert token not in serialized_audit
    assert service.secrets.read("telegram", "telegram_bot_token") == token
    assert any(
        item.get("source") == "protected_local"
        for item in public_route["prerequisites"]
        if item["id"].startswith("credential:")
    )


@pytest.mark.asyncio
async def test_api_redacts_secret_errors_rate_limits_tests_and_blocks_remote_clients(
    settings, monkeypatch
) -> None:
    from jarvis.api import create_app

    monkeypatch.delenv(TELEGRAM_TOKEN_ENV, raising=False)
    marker = "INVALID_CREDENTIAL_MUST_NEVER_BE_ECHOED_123456789"
    app = create_app(settings)
    local_transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 41000))
    remote_transport = httpx.ASGITransport(app=app, client=("198.51.100.10", 41001))

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=local_transport, base_url="http://127.0.0.1"
        ) as client:
            invalid_format = await client.post(
                "/api/communications/routes/telegram/credential",
                json={"credential": marker},
            )
            invalid_shape = await client.post(
                "/api/communications/routes/telegram/credential",
                json={"credential": marker, "unexpected": marker},
            )
            configured = await client.patch(
                "/api/communications/routes/telegram",
                json={"enabled": True, "chat_id": "1234567890123"},
            )
            first_test = await client.post("/api/communications/routes/telegram/test")
            second_test = await client.post("/api/communications/routes/telegram/test")

        async with httpx.AsyncClient(
            transport=remote_transport, base_url="http://jarvis.invalid"
        ) as remote:
            blocked = await remote.get("/api/communications")

    assert invalid_format.status_code == 409
    assert marker not in invalid_format.text
    assert invalid_shape.status_code == 422
    assert invalid_shape.json() == {"detail": "Request validation failed"}
    assert marker not in invalid_shape.text
    assert configured.status_code == 200
    assert first_test.status_code == 200
    assert first_test.json()["test"]["connectivity_checked"] is False
    assert second_test.status_code == 429
    assert second_test.headers["Retry-After"].isdigit()
    assert marker not in second_test.text
    assert blocked.status_code == 403
    assert "Remote traffic is disabled" in blocked.json()["detail"]
