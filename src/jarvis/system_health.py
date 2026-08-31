"""Read-only system metrics, threshold state, and bounded health-alert monitoring."""

from __future__ import annotations

import asyncio
import json
import math
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import psutil

from .audit import AuditLog, utc_now
from .communications import CommunicationsService
from .config import Settings
from .database import Database

METRICS: Final = (
    "cpu_percent",
    "memory_percent",
    "disk_percent",
    "temperature_celsius",
)
ONLINE_NOTIFICATION_ROUTES: Final = ("telegram", "discord")
DEFAULT_THRESHOLDS: Final[dict[str, dict[str, float | bool]]] = {
    "cpu_percent": {"enabled": True, "threshold": 85.0},
    "memory_percent": {"enabled": True, "threshold": 85.0},
    "disk_percent": {"enabled": True, "threshold": 90.0},
    "temperature_celsius": {"enabled": True, "threshold": 85.0},
}
_PERCENT_METRICS: Final = frozenset({"cpu_percent", "memory_percent", "disk_percent"})
_RECOVERY_HYSTERESIS: Final = {
    "cpu_percent": 3.0,
    "memory_percent": 3.0,
    "disk_percent": 3.0,
    "temperature_celsius": 3.0,
}


def _bounded_metric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _metric_in_range(value: Any, minimum: float, maximum: float) -> float | None:
    numeric = _bounded_metric(value)
    return numeric if numeric is not None and minimum <= numeric <= maximum else None


def _wmi_temperature_samples() -> tuple[list[dict[str, Any]], str | None]:
    """Read already-running Libre/OpenHardwareMonitor WMI providers without launching them."""

    if os.name != "nt":
        return [], None
    try:
        import pywintypes  # type: ignore[import-not-found]
        import win32com.client  # type: ignore[import-not-found]
    except ImportError:
        return [], None
    namespaces = (
        (r"root\LibreHardwareMonitor", "librehardwaremonitor_wmi"),
        (r"root\OpenHardwareMonitor", "openhardwaremonitor_wmi"),
    )
    for namespace, source in namespaces:
        try:
            locator = win32com.client.Dispatch("WbemScripting.SWbemLocator")
            service = locator.ConnectServer(".", namespace)
            records = service.ExecQuery(
                "SELECT Name, Identifier, Value FROM Sensor WHERE SensorType='Temperature'"
            )
            samples: list[dict[str, Any]] = []
            for record in records:
                current = _metric_in_range(getattr(record, "Value", None), -50.0, 200.0)
                if current is None:
                    continue
                samples.append(
                    {
                        "label": str(getattr(record, "Name", "Temperature"))[:200],
                        "identifier": str(getattr(record, "Identifier", ""))[:300],
                        "current_celsius": current,
                        "high_celsius": None,
                        "critical_celsius": None,
                    }
                )
                if len(samples) >= 64:
                    break
            if samples:
                return samples, source
        except (AttributeError, OSError, TypeError, ValueError, pywintypes.com_error):
            continue
    return [], None


class SystemHealthService:
    """Collect metrics and manage edge-triggered, cooldown-deduplicated alerts."""

    def __init__(
        self,
        database: Database,
        audit: AuditLog,
        settings: Settings,
        communications: CommunicationsService,
    ) -> None:
        self.database = database
        self.audit = audit
        self.settings = settings
        self.communications = communications
        self._runner: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._ensure_defaults()

    def _ensure_defaults(self) -> None:
        now = utc_now()
        serialized = json.dumps(DEFAULT_THRESHOLDS, sort_keys=True, separators=(",", ":"))
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO system_health_settings
                (id,monitor_enabled,online_notifications_enabled,interval_seconds,
                 cooldown_seconds,thresholds_json,delivery_routes_json,updated_at)
                 VALUES (1,1,0,60,900,?,'[]',?)""",
                (serialized, now),
            )
            for metric, policy in DEFAULT_THRESHOLDS.items():
                connection.execute(
                    """INSERT OR IGNORE INTO system_health_metric_state
                    (metric,breached,threshold,changed_at) VALUES (?,0,?,?)""",
                    (metric, policy["threshold"], now),
                )

    @staticmethod
    def _temperature_metrics() -> dict[str, Any]:
        samples: list[dict[str, Any]] = []
        source: str | None = None
        getter = getattr(psutil, "sensors_temperatures", None)
        if callable(getter):
            try:
                groups = getter(fahrenheit=False) or {}
                for group_name, entries in groups.items():
                    for entry in entries:
                        current = _metric_in_range(
                            getattr(entry, "current", None), -50.0, 200.0
                        )
                        if current is None:
                            continue
                        samples.append(
                            {
                                "label": str(getattr(entry, "label", "") or group_name)[:200],
                                "identifier": str(group_name)[:300],
                                "current_celsius": current,
                                "high_celsius": _metric_in_range(
                                    getattr(entry, "high", None), -50.0, 200.0
                                ),
                                "critical_celsius": _metric_in_range(
                                    getattr(entry, "critical", None), -50.0, 200.0
                                ),
                            }
                        )
                        if len(samples) >= 64:
                            break
                    if len(samples) >= 64:
                        break
                if samples:
                    source = "psutil"
            except (AttributeError, NotImplementedError, OSError, RuntimeError, psutil.Error):
                samples = []
        if not samples:
            try:
                samples, source = _wmi_temperature_samples()
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                samples, source = [], None
        maximum = max((sample["current_celsius"] for sample in samples), default=None)
        if samples:
            return {
                "available": True,
                "value": maximum,
                "unit": "celsius",
                "source": source,
                "samples": samples,
                "unavailable_reason": None,
            }
        reason = (
            "No psutil or Libre/OpenHardwareMonitor temperature sensors were reported"
            if os.name == "nt"
            else "No temperature sensors were reported by psutil"
        )
        return {
            "available": False,
            "value": None,
            "unit": "celsius",
            "source": None,
            "samples": [],
            "unavailable_reason": reason,
        }

    def _disk_path(self) -> Path:
        candidate = self.settings.data_dir
        while not candidate.exists() and candidate != candidate.parent:
            candidate = candidate.parent
        return candidate

    def collect(self) -> dict[str, Any]:
        """Collect a nonblocking, read-only system snapshot from local providers."""

        collected_at = utc_now()
        try:
            cpu = _metric_in_range(psutil.cpu_percent(interval=None), 0.0, 100.0)
        except (OSError, RuntimeError, psutil.Error):
            cpu = None
        try:
            memory = _metric_in_range(psutil.virtual_memory().percent, 0.0, 100.0)
        except (OSError, RuntimeError, psutil.Error):
            memory = None
        try:
            disk = _metric_in_range(
                psutil.disk_usage(str(self._disk_path())).percent, 0.0, 100.0
            )
            disk_reason = None if disk is not None else "Disk usage provider returned no value"
        except (OSError, RuntimeError, psutil.Error):
            disk = None
            disk_reason = "Disk usage is unavailable for the JARVIS data volume"
        try:
            uptime = _bounded_metric(time.time() - float(psutil.boot_time()))
            if uptime is not None and uptime < 0:
                uptime = None
        except (OSError, ValueError, TypeError, psutil.Error):
            uptime = None
        return {
            "collected_at": collected_at,
            "metrics": {
                "cpu_percent": {
                    "available": cpu is not None,
                    "value": cpu,
                    "unit": "percent",
                    "source": "psutil" if cpu is not None else None,
                    "unavailable_reason": None if cpu is not None else "CPU usage is unavailable",
                },
                "memory_percent": {
                    "available": memory is not None,
                    "value": memory,
                    "unit": "percent",
                    "source": "psutil" if memory is not None else None,
                    "unavailable_reason": (
                        None if memory is not None else "Memory usage is unavailable"
                    ),
                },
                "disk_percent": {
                    "available": disk is not None,
                    "value": disk,
                    "unit": "percent",
                    "source": "psutil" if disk is not None else None,
                    "path_scope": "jarvis_data_volume",
                    "unavailable_reason": disk_reason,
                },
                "uptime_seconds": {
                    "available": uptime is not None,
                    "value": uptime,
                    "unit": "seconds",
                    "source": "psutil" if uptime is not None else None,
                    "unavailable_reason": (
                        None if uptime is not None else "System uptime is unavailable"
                    ),
                },
                "temperature_celsius": self._temperature_metrics(),
            },
        }

    @staticmethod
    def _decode_settings(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "monitor_enabled": bool(row["monitor_enabled"]),
            "online_notifications_enabled": bool(row["online_notifications_enabled"]),
            "interval_seconds": row["interval_seconds"],
            "cooldown_seconds": row["cooldown_seconds"],
            "thresholds": json.loads(row["thresholds_json"]),
            "notification_routes": json.loads(row["delivery_routes_json"]),
            "updated_at": row["updated_at"],
        }

    def get_settings(self) -> dict[str, Any]:
        row = self.database.query_one("SELECT * FROM system_health_settings WHERE id=1")
        if row is None:
            raise RuntimeError("System health settings are unavailable")
        return self._decode_settings(row)

    @staticmethod
    def _validate_thresholds(value: Any) -> dict[str, dict[str, float | bool]]:
        if not isinstance(value, dict):
            raise TypeError("Health thresholds must be an object")
        unknown = set(value) - set(METRICS)
        if unknown:
            raise ValueError(f"Unknown health metrics: {', '.join(sorted(unknown))}")
        validated: dict[str, dict[str, float | bool]] = {}
        for metric, policy in value.items():
            if not isinstance(policy, dict) or set(policy) - {"enabled", "threshold"}:
                raise ValueError(f"Invalid threshold policy for {metric}")
            item: dict[str, float | bool] = {}
            if "enabled" in policy:
                if not isinstance(policy["enabled"], bool):
                    raise TypeError(f"{metric} enabled must be boolean")
                item["enabled"] = policy["enabled"]
            if "threshold" in policy:
                threshold = _bounded_metric(policy["threshold"])
                minimum, maximum = (1.0, 100.0) if metric in _PERCENT_METRICS else (20.0, 120.0)
                if threshold is None or not minimum <= threshold <= maximum:
                    raise ValueError(f"{metric} threshold must be between {minimum:g} and {maximum:g}")
                item["threshold"] = threshold
            if not item:
                raise ValueError(f"Threshold update for {metric} is empty")
            validated[metric] = item
        if not validated:
            raise ValueError("Health threshold update cannot be empty")
        return validated

    def update_settings(self, changes: dict[str, Any], actor: str = "user:hud") -> dict[str, Any]:
        """Persist a strict monitoring policy without accepting messages or URLs."""

        allowed = {
            "monitor_enabled",
            "online_notifications_enabled",
            "interval_seconds",
            "cooldown_seconds",
            "thresholds",
            "notification_routes",
        }
        invalid = set(changes) - allowed
        if invalid:
            raise ValueError(f"Unsupported health settings: {', '.join(sorted(invalid))}")
        if not changes:
            raise ValueError("At least one health setting is required")
        current = self.get_settings()
        updated = dict(current)
        for field in ("monitor_enabled", "online_notifications_enabled"):
            if field in changes:
                if not isinstance(changes[field], bool):
                    raise TypeError(f"{field} must be boolean")
                updated[field] = changes[field]
        for field, minimum, maximum in (
            ("interval_seconds", 10, 3600),
            ("cooldown_seconds", 60, 86400),
        ):
            if field in changes:
                value = changes[field]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or not minimum <= value <= maximum
                ):
                    raise ValueError(f"{field} must be between {minimum} and {maximum}")
                updated[field] = value
        if "notification_routes" in changes:
            routes = changes["notification_routes"]
            if not isinstance(routes, list) or len(routes) > len(ONLINE_NOTIFICATION_ROUTES):
                raise ValueError("Health notification routes must be a bounded list")
            if len(set(routes)) != len(routes) or any(
                route not in ONLINE_NOTIFICATION_ROUTES for route in routes
            ):
                raise ValueError("Health online notifications support only Telegram and Discord")
            updated["notification_routes"] = routes
        if "thresholds" in changes:
            thresholds = json.loads(json.dumps(current["thresholds"]))
            for metric, policy in self._validate_thresholds(changes["thresholds"]).items():
                thresholds[metric].update(policy)
            updated["thresholds"] = thresholds

        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE system_health_settings SET
                monitor_enabled=?,online_notifications_enabled=?,interval_seconds=?,
                cooldown_seconds=?,thresholds_json=?,delivery_routes_json=?,updated_at=?
                WHERE id=1""",
                (
                    int(updated["monitor_enabled"]),
                    int(updated["online_notifications_enabled"]),
                    updated["interval_seconds"],
                    updated["cooldown_seconds"],
                    json.dumps(updated["thresholds"], sort_keys=True, separators=(",", ":")),
                    json.dumps(updated["notification_routes"], separators=(",", ":")),
                    now,
                ),
            )
            for metric, policy in updated["thresholds"].items():
                connection.execute(
                    """UPDATE system_health_metric_state SET threshold=?,
                    breached=CASE WHEN ?=0 THEN 0 ELSE breached END,
                    changed_at=CASE WHEN ?=0 AND breached=1 THEN ? ELSE changed_at END
                    WHERE metric=?""",
                    (
                        policy["threshold"],
                        int(bool(policy["enabled"])),
                        int(bool(policy["enabled"])),
                        now,
                        metric,
                    ),
                )
        self.audit.append(
            actor,
            "system_health.settings_updated",
            "system_health:settings",
            {"fields": sorted(changes)},
        )
        self._wake.set()
        return self.get_settings()

    def alert_state(self) -> dict[str, Any]:
        settings = self.get_settings()
        rows = self.database.query_all(
            "SELECT * FROM system_health_metric_state ORDER BY metric ASC"
        )
        states = []
        for row in rows:
            policy = settings["thresholds"][row["metric"]]
            states.append(
                {
                    "metric": row["metric"],
                    "enabled": bool(policy["enabled"]),
                    "breached": bool(row["breached"]),
                    "last_value": row["last_value"],
                    "threshold": row["threshold"],
                    "changed_at": row["changed_at"],
                    "last_alert_at": row["last_alert_at"],
                    "last_recovery_at": row["last_recovery_at"],
                }
            )
        return {
            "metrics": states,
            "active_count": sum(state["breached"] for state in states if state["enabled"]),
        }

    def list_alerts(
        self,
        limit: int = 100,
        metric: str | None = None,
        event_type: str | None = None,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("Health alert limit must be between 1 and 500")
        if metric is not None and metric not in METRICS:
            raise ValueError("Unknown health metric")
        if event_type is not None and event_type not in {"triggered", "ongoing", "recovered"}:
            raise ValueError("Unknown health alert event type")
        clauses: list[str] = []
        parameters: list[Any] = []
        if metric:
            clauses.append("metric=?")
            parameters.append(metric)
        if event_type:
            clauses.append("event_type=?")
            parameters.append(event_type)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        rows = self.database.query_all(
            f"SELECT * FROM system_health_alerts{where} "
            "ORDER BY created_at DESC,rowid DESC LIMIT ?",
            parameters,
        )
        for row in rows:
            row["delivery"] = json.loads(row.pop("delivery_json"))
        return rows

    @staticmethod
    def _values(sample: dict[str, Any]) -> dict[str, float | None]:
        metrics = sample["metrics"]
        return {
            metric: metrics[metric]["value"] if metrics[metric]["available"] else None
            for metric in METRICS
        }

    @staticmethod
    def _cooldown_elapsed(value: Any, now: datetime, cooldown_seconds: int) -> bool:
        """Treat invalid timestamps as expired and future timestamps as not elapsed."""

        if not isinstance(value, str) or not value:
            return True
        try:
            previous = datetime.fromisoformat(value)
            if previous.tzinfo is None:
                return True
            elapsed = (now - previous.astimezone(UTC)).total_seconds()
        except (OverflowError, TypeError, ValueError):
            return True
        return 0 <= elapsed and elapsed >= cooldown_seconds

    def _evaluate(
        self, sample: dict[str, Any], settings: dict[str, Any]
    ) -> list[dict[str, Any]]:
        now = datetime.now(UTC)
        now_text = now.isoformat()
        values = self._values(sample)
        created: list[dict[str, Any]] = []
        with self.database.transaction() as connection:
            for metric in METRICS:
                policy = settings["thresholds"][metric]
                value = values[metric]
                if not policy["enabled"] or value is None:
                    continue
                row = connection.execute(
                    "SELECT * FROM system_health_metric_state WHERE metric=?", (metric,)
                ).fetchone()
                if row is None:
                    continue
                was_breached = bool(row["breached"])
                threshold = float(policy["threshold"])
                recovery_boundary = threshold - _RECOVERY_HYSTERESIS[metric]
                is_breached = value >= (recovery_boundary if was_breached else threshold)
                event_type: str | None = None
                if is_breached and not was_breached:
                    if self._cooldown_elapsed(
                        row["last_alert_at"], now, settings["cooldown_seconds"]
                    ):
                        event_type = "triggered"
                elif is_breached and was_breached and row["last_alert_at"]:
                    if self._cooldown_elapsed(
                        row["last_alert_at"], now, settings["cooldown_seconds"]
                    ):
                        event_type = "ongoing"
                elif not is_breached and was_breached:
                    event_type = "recovered"

                changed_at = now_text if is_breached != was_breached else row["changed_at"]
                last_alert_at = (
                    now_text
                    if event_type in {"triggered", "ongoing"}
                    else row["last_alert_at"]
                )
                last_recovery_at = (
                    now_text if event_type == "recovered" else row["last_recovery_at"]
                )
                connection.execute(
                    """UPDATE system_health_metric_state SET
                    breached=?,last_value=?,threshold=?,changed_at=?,last_alert_at=?,last_recovery_at=?
                    WHERE metric=?""",
                    (
                        int(is_breached),
                        value,
                        policy["threshold"],
                        changed_at,
                        last_alert_at,
                        last_recovery_at,
                        metric,
                    ),
                )
                if event_type:
                    alert = {
                        "id": str(uuid.uuid4()),
                        "metric": metric,
                        "event_type": event_type,
                        "severity": "info" if event_type == "recovered" else "warning",
                        "value": value,
                        "threshold": threshold,
                        "created_at": now_text,
                        "delivery": [],
                    }
                    connection.execute(
                        """INSERT INTO system_health_alerts
                        (id,metric,event_type,severity,value,threshold,created_at,delivery_json)
                        VALUES (?,?,?,?,?,?,?,'[]')""",
                        (
                            alert["id"],
                            metric,
                            event_type,
                            alert["severity"],
                            value,
                            alert["threshold"],
                            now_text,
                        ),
                    )
                    created.append(alert)
        return created

    async def _deliver_online(
        self, alert: dict[str, Any], notification_routes: list[str]
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = [
            {"provider": "web_app", "status": "recorded_local", "remote_status": None}
        ]
        for provider in notification_routes:
            result = await self.communications.deliver_health_alert(
                provider,
                metric=alert["metric"],
                event_type=alert["event_type"],
                value=alert["value"],
                threshold=alert["threshold"],
            )
            results.append(result)
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE system_health_alerts SET delivery_json=? WHERE id=?",
                (json.dumps(results, sort_keys=True, separators=(",", ":")), alert["id"]),
            )
        return results

    async def check(self) -> dict[str, Any]:
        """Sample and evaluate once; a disabled monitor never mutates alert state."""

        sample = self.collect()
        settings = self.get_settings()
        created: list[dict[str, Any]] = []
        if settings["monitor_enabled"]:
            created = self._evaluate(sample, settings)
            for alert in created:
                if settings["online_notifications_enabled"]:
                    alert["delivery"] = await self._deliver_online(
                        alert, settings["notification_routes"]
                    )
                else:
                    alert["delivery"] = [
                        {
                            "provider": "web_app",
                            "status": "recorded_local",
                            "remote_status": None,
                        }
                    ]
                    with self.database.transaction() as connection:
                        connection.execute(
                            "UPDATE system_health_alerts SET delivery_json=? WHERE id=?",
                            (
                                json.dumps(alert["delivery"], separators=(",", ":")),
                                alert["id"],
                            ),
                        )
                self.audit.append(
                    "system:health-monitor",
                    f"system_health.alert_{alert['event_type']}",
                    f"system_health:{alert['metric']}",
                    {
                        "alert_id": alert["id"],
                        "metric": alert["metric"],
                        "event_type": alert["event_type"],
                    },
                )
        return {
            "monitor_enabled": settings["monitor_enabled"],
            "sample": sample,
            "alerts_created": created,
            "alert_state": self.alert_state(),
        }

    def delivery_status(self) -> list[dict[str, Any]]:
        settings = self.get_settings()
        return [
            {
                "provider": "web_app",
                "supported": True,
                "channel_kind": "local_feed",
                "selected": True,
                "status": "ready",
                "detail": "Alerts are recorded in the local authenticated system-health feed",
            },
            {
                "provider": "telegram",
                "supported": True,
                "channel_kind": "online_notification",
                "selected": "telegram" in settings["notification_routes"],
                **self.communications.health_alert_readiness("telegram"),
            },
            {
                "provider": "discord",
                "supported": True,
                "channel_kind": "online_notification",
                "selected": "discord" in settings["notification_routes"],
                **self.communications.health_alert_readiness("discord"),
            },
            {
                "provider": "signal",
                "supported": False,
                "channel_kind": "online_notification",
                "selected": False,
                "status": "unavailable",
                "detail": "Structured Signal health delivery is not implemented",
            },
        ]

    def snapshot(self) -> dict[str, Any]:
        sample = self.collect()
        settings = self.get_settings()
        unavailable = [
            metric for metric, value in sample["metrics"].items() if not value["available"]
        ]
        breached = []
        values = self._values(sample)
        for metric, policy in settings["thresholds"].items():
            if policy["enabled"] and values[metric] is not None and values[metric] >= policy["threshold"]:
                breached.append(metric)
        if not settings["monitor_enabled"]:
            status = "monitor_disabled"
            evaluation = "monitoring_is_disabled"
        elif breached:
            status = "warning"
            evaluation = "thresholds_exceeded"
        elif unavailable:
            status = "partial"
            evaluation = "some_metrics_unavailable"
        else:
            status = "normal"
            evaluation = "within_thresholds_for_available_metrics"
        return {
            "status": status,
            "evaluation": evaluation,
            "breached_metrics": breached,
            "unavailable_metrics": unavailable,
            "sample": sample,
            "settings": settings,
            "alert_state": self.alert_state(),
            "delivery": self.delivery_status(),
        }

    def start(self) -> None:
        if self._runner is None or self._runner.done():
            self._runner = asyncio.create_task(self._loop(), name="jarvis-system-health-monitor")

    async def stop(self) -> None:
        if self._runner is None:
            return
        self._runner.cancel()
        try:
            await self._runner
        except asyncio.CancelledError:
            pass
        self._runner = None

    async def _loop(self) -> None:
        while True:
            settings = self.get_settings()
            if settings["monitor_enabled"]:
                try:
                    await self.check()
                except (OSError, RuntimeError, ValueError, psutil.Error) as exc:
                    self.audit.append(
                        "system:health-monitor",
                        "system_health.check_failed",
                        "system_health:monitor",
                        {"error_type": type(exc).__name__},
                    )
            try:
                await asyncio.wait_for(
                    self._wake.wait(), timeout=float(settings["interval_seconds"])
                )
                self._wake.clear()
            except TimeoutError:
                pass


__all__ = [
    "DEFAULT_THRESHOLDS",
    "METRICS",
    "ONLINE_NOTIFICATION_ROUTES",
    "SystemHealthService",
]
