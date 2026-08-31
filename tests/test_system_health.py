from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from jarvis.communications import CommunicationsService
from jarvis.database import Database
from jarvis.system_health import SystemHealthService


def _service(database, audit, settings) -> SystemHealthService:
    return SystemHealthService(
        database,
        audit,
        settings,
        CommunicationsService(database, audit, settings),
    )


def _sample(cpu: float = 10.0) -> dict:
    def available(value: float, unit: str) -> dict:
        return {
            "available": True,
            "value": value,
            "unit": unit,
            "source": "test",
            "unavailable_reason": None,
        }

    return {
        "collected_at": datetime.now(UTC).isoformat(),
        "metrics": {
            "cpu_percent": available(cpu, "percent"),
            "memory_percent": available(20.0, "percent"),
            "disk_percent": available(30.0, "percent"),
            "uptime_seconds": available(1_000.0, "seconds"),
            "temperature_celsius": {
                "available": False,
                "value": None,
                "unit": "celsius",
                "source": None,
                "samples": [],
                "unavailable_reason": "not reported",
            },
        },
    }


def test_defaults_enable_only_local_monitoring(database, audit, settings) -> None:
    service = _service(database, audit, settings)

    policy = service.get_settings()
    delivery = {item["provider"]: item for item in service.delivery_status()}

    assert policy["monitor_enabled"] is True
    assert policy["online_notifications_enabled"] is False
    assert policy["notification_routes"] == []
    assert delivery["web_app"]["status"] == "ready"
    assert delivery["web_app"]["channel_kind"] == "local_feed"
    assert delivery["signal"]["supported"] is False
    assert delivery["signal"]["status"] == "unavailable"


def test_database_migrates_existing_health_policy_without_enabling_online(
    tmp_path: Path,
) -> None:
    path = tmp_path / "old.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE system_health_settings (
        id INTEGER PRIMARY KEY, monitor_enabled INTEGER NOT NULL,
        interval_seconds INTEGER NOT NULL, cooldown_seconds INTEGER NOT NULL,
        thresholds_json TEXT NOT NULL, delivery_routes_json TEXT NOT NULL,
        updated_at TEXT NOT NULL)"""
    )
    connection.commit()
    connection.close()

    database = Database(path)
    database.initialize()
    columns = {
        row["name"]
        for row in database.query_all("PRAGMA table_info(system_health_settings)")
    }
    database.close()

    assert "online_notifications_enabled" in columns


@pytest.mark.asyncio
async def test_cooldown_allows_ongoing_alert_and_corrupt_timestamp_does_not_crash(
    database, audit, settings, monkeypatch
) -> None:
    service = _service(database, audit, settings)
    service.update_settings(
        {
            "cooldown_seconds": 60,
            "thresholds": {
                "cpu_percent": {"threshold": 80.0},
                "memory_percent": {"enabled": False},
                "disk_percent": {"enabled": False},
                "temperature_celsius": {"enabled": False},
            },
        }
    )
    monkeypatch.setattr(service, "collect", lambda: _sample(90.0))

    first = await service.check()
    assert [alert["event_type"] for alert in first["alerts_created"]] == ["triggered"]

    old = (datetime.now(UTC) - timedelta(seconds=61)).isoformat()
    database.execute(
        "UPDATE system_health_metric_state SET last_alert_at=? WHERE metric='cpu_percent'",
        (old,),
    )
    ongoing = await service.check()
    assert [alert["event_type"] for alert in ongoing["alerts_created"]] == ["ongoing"]

    database.execute(
        "UPDATE system_health_metric_state SET last_alert_at='not-a-date' "
        "WHERE metric='cpu_percent'"
    )
    healed = await service.check()
    assert [alert["event_type"] for alert in healed["alerts_created"]] == ["ongoing"]
    await service.communications.close()


@pytest.mark.asyncio
async def test_system_health_api_contract_and_lifecycle(settings, monkeypatch) -> None:
    from jarvis.api import create_app

    app = create_app(settings)
    services = app.state.services
    monkeypatch.setattr(services.system_health, "collect", lambda: _sample())
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 45500))

    async with app.router.lifespan_context(app):
        assert services.system_health._runner is not None
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1"
        ) as client:
            snapshot = await client.get("/api/system-health")
            assert snapshot.status_code == 200
            assert snapshot.json()["status"] == "partial"
            assert snapshot.json()["unavailable_metrics"] == ["temperature_celsius"]

            policy = await client.get("/api/system-health/settings")
            assert policy.status_code == 200
            assert policy.json()["settings"]["monitor_enabled"] is True
            assert policy.json()["settings"]["online_notifications_enabled"] is False

            updated = await client.patch(
                "/api/system-health/settings",
                json={
                    "interval_seconds": 30,
                    "cooldown_seconds": 120,
                    "thresholds": {"cpu_percent": {"threshold": 75}},
                    "online_notifications_enabled": True,
                    "notification_routes": ["telegram", "discord"],
                },
            )
            assert updated.status_code == 200
            assert updated.json()["settings"]["notification_routes"] == [
                "telegram",
                "discord",
            ]
            assert updated.json()["delivery"][0]["provider"] == "web_app"

            alerts = await client.get("/api/system-health/alerts?limit=10")
            assert alerts.status_code == 200
            assert isinstance(alerts.json()["alerts"], list)
            state = await client.get("/api/system-health/alerts/state")
            assert state.status_code == 200
            assert set(state.json()) == {"metrics", "active_count"}

            invalid_route = await client.patch(
                "/api/system-health/settings",
                json={"notification_routes": ["signal"]},
            )
            assert invalid_route.status_code == 422
            arbitrary = await client.patch(
                "/api/system-health/settings",
                json={"message": "send anything"},
            )
            assert arbitrary.status_code == 422
            assert (
                await client.post(
                    "/api/system-health/notifications/send",
                    json={"message": "send anything"},
                )
            ).status_code == 404

    assert services.system_health._runner is None
