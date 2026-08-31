from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from jarvis.communications import TELEGRAM_TOKEN_ENV, CommunicationsService
from jarvis.system_health import ONLINE_NOTIFICATION_ROUTES, SystemHealthService


def _service(database, audit, settings) -> SystemHealthService:
    communications = CommunicationsService(database, audit, settings)
    return SystemHealthService(database, audit, settings, communications)


def _sample(
    *,
    cpu: float | None = 20.0,
    memory: float | None = 30.0,
    disk: float | None = 40.0,
    temperature: float | None = None,
) -> dict:
    def metric(value: float | None, unit: str = "percent") -> dict:
        return {
            "available": value is not None,
            "value": value,
            "unit": unit,
            "source": "test" if value is not None else None,
            "unavailable_reason": None if value is not None else "not reported",
        }

    return {
        "collected_at": datetime.now(UTC).isoformat(),
        "metrics": {
            "cpu_percent": metric(cpu),
            "memory_percent": metric(memory),
            "disk_percent": metric(disk),
            "temperature_celsius": metric(temperature, "celsius"),
            "uptime_seconds": metric(1000.0, "seconds"),
        },
    }


@pytest.mark.asyncio
async def test_online_delivery_is_disabled_by_default_even_with_a_selected_route(
    database, audit, settings, monkeypatch
) -> None:
    service = _service(database, audit, settings)
    service.update_settings({"notification_routes": ["telegram"]})
    monkeypatch.setattr(service, "collect", lambda: _sample(cpu=100.0, memory=100.0, disk=100.0))
    outbound_calls: list[tuple] = []

    async def unexpected_delivery(*args, **kwargs):
        outbound_calls.append((args, kwargs))
        return {"status": "delivered"}

    monkeypatch.setattr(service.communications, "deliver_health_alert", unexpected_delivery)
    result = await service.check()
    await service.communications.close()

    assert result["monitor_enabled"] is True
    assert service.get_settings()["online_notifications_enabled"] is False
    assert service.get_settings()["notification_routes"] == ["telegram"]
    assert result["alerts_created"]
    assert all(
        alert["delivery"]
        == [{"provider": "web_app", "status": "recorded_local", "remote_status": None}]
        for alert in result["alerts_created"]
    )
    assert service.list_alerts()
    assert outbound_calls == []


@pytest.mark.parametrize(
    "changes",
    [
        {"interval_seconds": True},
        {"interval_seconds": 9},
        {"interval_seconds": 3601},
        {"cooldown_seconds": False},
        {"cooldown_seconds": 59},
        {"cooldown_seconds": 86401},
        {"online_notifications_enabled": "yes"},
        {"thresholds": {}},
        {"thresholds": {"unknown": {"threshold": 50}}},
        {"thresholds": {"cpu_percent": {}}},
        {"thresholds": {"cpu_percent": {"threshold": True}}},
        {"thresholds": {"cpu_percent": {"threshold": float("nan")}}},
        {"thresholds": {"cpu_percent": {"threshold": float("inf")}}},
        {"thresholds": {"cpu_percent": {"threshold": 0}}},
        {"thresholds": {"cpu_percent": {"threshold": 101}}},
        {"thresholds": {"temperature_celsius": {"threshold": 19}}},
        {"thresholds": {"temperature_celsius": {"threshold": 121}}},
        {"notification_routes": ["telegram", "telegram"]},
        {"notification_routes": ["web_app"]},
        {"notification_routes": ["signal"]},
        {"notification_routes": ["https://example.com/hook"]},
        {"message": "arbitrary online content"},
        {"webhook_url": "https://example.com/hook"},
    ],
)
def test_health_policy_rejects_invalid_thresholds_and_arbitrary_delivery_fields(
    database, audit, settings, changes: dict
) -> None:
    service = _service(database, audit, settings)

    with pytest.raises((TypeError, ValueError)):
        service.update_settings(changes)


@pytest.mark.asyncio
async def test_threshold_is_inclusive_edge_triggered_and_cooldown_deduplicated(
    database, audit, settings, monkeypatch
) -> None:
    service = _service(database, audit, settings)
    service.update_settings(
        {
            "monitor_enabled": True,
            "cooldown_seconds": 60,
            "thresholds": {
                "cpu_percent": {"enabled": True, "threshold": 85},
                "memory_percent": {"enabled": False},
                "disk_percent": {"enabled": False},
                "temperature_celsius": {"enabled": False},
            },
        }
    )
    samples = iter(
        (
            _sample(cpu=84.9),
            _sample(cpu=85.0),
            _sample(cpu=99.0),
            _sample(cpu=81.0),
            _sample(cpu=85.0),
        )
    )
    monkeypatch.setattr(service, "collect", lambda: next(samples))

    below = await service.check()
    triggered = await service.check()
    repeated = await service.check()
    recovered = await service.check()
    rapid_rebreach = await service.check()

    assert below["alerts_created"] == []
    assert [item["event_type"] for item in triggered["alerts_created"]] == ["triggered"]
    assert repeated["alerts_created"] == []
    assert [item["event_type"] for item in recovered["alerts_created"]] == ["recovered"]
    assert rapid_rebreach["alerts_created"] == []
    assert [item["event_type"] for item in service.list_alerts()] == [
        "recovered",
        "triggered",
    ]
    await service.communications.close()


@pytest.mark.asyncio
async def test_concurrent_checks_create_one_transition_and_no_duplicate_delivery(
    database, audit, settings, monkeypatch
) -> None:
    service = _service(database, audit, settings)
    service.update_settings(
        {
            "monitor_enabled": True,
            "online_notifications_enabled": True,
            "thresholds": {"cpu_percent": {"threshold": 80}},
            "notification_routes": ["telegram"],
        }
    )
    monkeypatch.setattr(service, "collect", lambda: _sample(cpu=90.0))
    deliveries: list[str] = []

    async def deliver(provider: str, **kwargs):
        await asyncio.sleep(0.01)
        deliveries.append(provider)
        return {"provider": provider, "status": "delivered", "remote_status": 200}

    monkeypatch.setattr(service.communications, "deliver_health_alert", deliver)
    results = await asyncio.gather(service.check(), service.check())

    assert sum(len(result["alerts_created"]) for result in results) == 1
    assert deliveries == ["telegram"]
    assert len(service.list_alerts()) == 1
    await service.communications.close()


@pytest.mark.asyncio
async def test_cooldown_survives_service_restart_and_future_timestamps_fail_closed(
    database, audit, settings, monkeypatch
) -> None:
    first = _service(database, audit, settings)
    first.update_settings(
        {
            "monitor_enabled": True,
            "cooldown_seconds": 60,
            "thresholds": {
                "cpu_percent": {"enabled": True, "threshold": 80},
                "memory_percent": {"enabled": False},
                "disk_percent": {"enabled": False},
                "temperature_celsius": {"enabled": False},
            },
        }
    )
    monkeypatch.setattr(first, "collect", lambda: _sample(cpu=90.0))
    initial = await first.check()

    second = _service(database, audit, settings)
    monkeypatch.setattr(second, "collect", lambda: _sample(cpu=90.0))
    after_restart = await second.check()
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    database.execute(
        "UPDATE system_health_metric_state SET last_alert_at=? WHERE metric='cpu_percent'",
        (future,),
    )
    future_timestamp = await second.check()
    await first.communications.close()
    await second.communications.close()

    assert [item["event_type"] for item in initial["alerts_created"]] == ["triggered"]
    assert after_restart["alerts_created"] == []
    assert future_timestamp["alerts_created"] == []
    assert len(second.list_alerts()) == 1


def test_temperature_provider_failure_is_metric_local_and_graceful(
    database, audit, settings, monkeypatch
) -> None:
    from jarvis import system_health as health_module

    service = _service(database, audit, settings)
    monkeypatch.setattr(
        health_module.psutil,
        "sensors_temperatures",
        lambda **_: {},
        raising=False,
    )

    def unavailable_provider():
        raise RuntimeError("Invalid WMI namespace")

    monkeypatch.setattr(health_module, "_wmi_temperature_samples", unavailable_provider)
    snapshot = service.collect()

    assert snapshot["metrics"]["temperature_celsius"]["available"] is False
    assert snapshot["metrics"]["temperature_celsius"]["value"] is None
    assert snapshot["metrics"]["cpu_percent"]["available"] is True
    assert snapshot["metrics"]["memory_percent"]["available"] is True


def test_impossible_and_nonfinite_temperature_samples_are_ignored(
    database, audit, settings, monkeypatch
) -> None:
    from jarvis import system_health as health_module

    service = _service(database, audit, settings)
    entries = [
        SimpleNamespace(label="bad-low", current=-500.0, high=None, critical=None),
        SimpleNamespace(label="bad-high", current=1000.0, high=None, critical=None),
        SimpleNamespace(label="not-a-number", current=float("nan"), high=None, critical=None),
    ]
    monkeypatch.setattr(
        health_module.psutil,
        "sensors_temperatures",
        lambda **_: {"test-sensors": entries},
        raising=False,
    )
    monkeypatch.setattr(health_module, "_wmi_temperature_samples", lambda: ([], None))

    temperature = service.collect()["metrics"]["temperature_celsius"]

    assert temperature["available"] is False
    assert temperature["value"] is None
    assert temperature["samples"] == []


@pytest.mark.asyncio
async def test_structured_online_alert_uses_fixed_host_safe_content_and_no_redirects(
    database, audit, settings, monkeypatch
) -> None:
    settings.outbound_notifications_enabled = True
    token = f"12345678:{'A' * 40}"
    monkeypatch.setenv(TELEGRAM_TOKEN_ENV, token)
    communications = CommunicationsService(database, audit, settings)
    communications.update_route(
        "telegram",
        {"enabled": True, "chat_id": "1234567890123"},
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/secret"})

    communications._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    )
    outcome = await communications.deliver_health_alert(
        "telegram",
        metric="cpu_percent",
        event_type="triggered",
        value=91.234,
        threshold=90.0,
    )
    await communications.close()

    assert outcome == {"provider": "telegram", "status": "error", "remote_status": 302}
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.host == "api.telegram.org"
    assert requests[0].url.path.endswith("/sendMessage")
    payload = json.loads(requests[0].content)
    assert set(payload) == {"chat_id", "text", "disable_web_page_preview"}
    assert payload["text"].startswith("JARVIS system health: CPU usage crossed")
    assert "@" not in payload["text"]
    assert "http" not in payload["text"].casefold()
    assert token not in json.dumps(audit.list(100), ensure_ascii=False)


@pytest.mark.asyncio
async def test_structured_alert_rejects_untrusted_timestamp_and_impossible_values(
    database, audit, settings, monkeypatch
) -> None:
    settings.outbound_notifications_enabled = True
    token = f"12345678:{'A' * 40}"
    monkeypatch.setenv(TELEGRAM_TOKEN_ENV, token)
    communications = CommunicationsService(database, audit, settings)
    communications.update_route(
        "telegram",
        {"enabled": True, "chat_id": "1234567890123"},
    )
    requests: list[httpx.Request] = []
    communications._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: requests.append(request) or httpx.Response(200, json={"ok": True})
        )
    )

    with pytest.raises((TypeError, ValueError)):
        await communications.deliver_health_alert(
            "telegram",
            metric="cpu_percent",
            event_type="triggered",
            value=90.0,
            threshold=85.0,
            created_at="2026-08-21T20:00:00Z\n@everyone https://attacker.invalid",
        )
    with pytest.raises(ValueError):
        await communications.deliver_health_alert(
            "telegram",
            metric="cpu_percent",
            event_type="triggered",
            value=1000.0,
            threshold=85.0,
        )
    await communications.close()

    assert requests == []


def test_web_app_feed_is_local_and_not_an_online_delivery_route(
    database, audit, settings
) -> None:
    service = _service(database, audit, settings)

    assert ONLINE_NOTIFICATION_ROUTES == ("telegram", "discord")
    with pytest.raises(ValueError):
        service.update_settings({"notification_routes": ["web_app"]})
    assert service.get_settings()["notification_routes"] == []


@pytest.mark.asyncio
async def test_health_api_has_no_arbitrary_send_surface_and_remains_loopback_only(
    settings, monkeypatch
) -> None:
    from jarvis.api import create_app

    app = create_app(settings)
    services = app.state.services
    monkeypatch.setattr(services.system_health, "collect", lambda: _sample(cpu=10.0))
    local_transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 43000))
    remote_transport = httpx.ASGITransport(app=app, client=("198.51.100.20", 43001))
    marker = "ARBITRARY_HEALTH_MESSAGE_MUST_NOT_REACH_A_PROVIDER"

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=local_transport, base_url="http://127.0.0.1"
        ) as client:
            health = await client.get("/api/system-health")
            rejected_field = await client.patch(
                "/api/system-health/settings",
                json={"webhook_url": marker},
            )
            rejected_send = await client.post(
                "/api/system-health/notifications/send",
                json={"message": marker},
            )
            rejected_check = await client.post("/api/system-health/check")
            local_notification = await client.post(
                "/api/notifications",
                json={"title": "Local only", "message": marker},
            )
            audit_text = json.dumps(services.audit.list(200), ensure_ascii=False)

        async with httpx.AsyncClient(
            transport=remote_transport, base_url="http://jarvis.invalid"
        ) as remote:
            remote_health = await remote.get("/api/system-health")

    assert health.status_code == 200
    assert marker not in health.text
    assert rejected_field.status_code == 422
    assert rejected_field.json() == {"detail": "Request validation failed"}
    assert marker not in rejected_field.text
    assert rejected_send.status_code == 404
    assert rejected_check.status_code in {404, 405}
    assert local_notification.status_code == 201
    assert local_notification.json()["notification"]["provider"] == "local_log"
    assert "communications.health_alert_delivery" not in audit_text
    assert remote_health.status_code == 403
