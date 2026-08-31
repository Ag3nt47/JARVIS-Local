"""SQLite persistence primitives for JARVIS."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    resource TEXT NOT NULL,
    details_json TEXT NOT NULL,
    previous_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    resolved_at TEXT,
    requester TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','approved','denied','expired','failed')),
    result_json TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL CHECK(state IN ('open','in_progress','blocked','done','cancelled')),
    priority INTEGER NOT NULL DEFAULT 2 CHECK(priority BETWEEN 1 AND 5),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    due_at TEXT,
    due_notified_at TEXT,
    last_activity_at TEXT NOT NULL,
    dedupe_key TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_state_activity ON tasks(state, last_activity_at);
CREATE INDEX IF NOT EXISTS idx_tasks_dedupe ON tasks(dedupe_key);
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 200),
    summary TEXT NOT NULL DEFAULT '' CHECK(length(summary) <= 10000),
    status TEXT NOT NULL CHECK(status IN ('planned','active','paused','completed','archived')),
    priority INTEGER NOT NULL DEFAULT 2 CHECK(priority BETWEEN 1 AND 5),
    progress INTEGER NOT NULL DEFAULT 0 CHECK(progress BETWEEN 0 AND 100),
    next_action TEXT NOT NULL DEFAULT '' CHECK(length(next_action) <= 500),
    tags_json TEXT NOT NULL DEFAULT '[]' CHECK(length(tags_json) <= 4096),
    accent TEXT NOT NULL DEFAULT 'cyan'
        CHECK(accent IN ('slate','blue','cyan','green','amber','orange','red','violet','pink')),
    pinned INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_activity_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_projects_status_updated ON projects(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_projects_pinned_priority
    ON projects(pinned DESC, priority DESC, updated_at DESC);
CREATE TABLE IF NOT EXISTS memory_items (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    embedding_json TEXT NOT NULL,
    embedding_model TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_created ON memory_items(created_at);
CREATE TABLE IF NOT EXISTS pinned_memory_items (
    key TEXT PRIMARY KEY CHECK(length(key) BETWEEN 1 AND 120),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source TEXT NOT NULL,
    content TEXT NOT NULL CHECK(length(content) <= 100000),
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS assistant_sessions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active','cancelled','completed')),
    channel TEXT NOT NULL CHECK(channel IN ('text','voice','phone','remote'))
);
CREATE TABLE IF NOT EXISTS assistant_turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES assistant_sessions(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN (
        'received','thinking','awaiting_approval','acting','verifying','speaking',
        'completed','cancelled','failed'
    )),
    skill_id TEXT NOT NULL,
    approval_id TEXT,
    error_code TEXT,
    result_json TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0,1))
);
CREATE INDEX IF NOT EXISTS idx_assistant_turns_session_created
    ON assistant_turns(session_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_assistant_turns_approval
    ON assistant_turns(approval_id) WHERE approval_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS assistant_turn_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id TEXT NOT NULL REFERENCES assistant_turns(id) ON DELETE CASCADE,
    timestamp TEXT NOT NULL,
    state TEXT NOT NULL,
    event_type TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_assistant_turn_events_turn
    ON assistant_turn_events(turn_id, id);
CREATE INDEX IF NOT EXISTS idx_pinned_memory_updated
    ON pinned_memory_items(updated_at DESC);
CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    level TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    dedupe_key TEXT,
    provider TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_notifications_dedupe ON notifications(dedupe_key, created_at);
CREATE TABLE IF NOT EXISTS communication_routes (
    provider TEXT PRIMARY KEY CHECK(provider IN ('signal','telegram','discord','web_app')),
    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
    priority INTEGER NOT NULL CHECK(priority BETWEEN 1 AND 4),
    config_json TEXT NOT NULL DEFAULT '{}',
    secret_refs_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_test_at TEXT,
    last_test_status TEXT CHECK(
        last_test_status IS NULL OR
        last_test_status IN (
            'disabled','setup_required','ready','unavailable','unverified','blocked','error'
        )
    ),
    last_test_detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_communication_routes_priority
    ON communication_routes(priority, provider);
CREATE TABLE IF NOT EXISTS communication_secrets (
    provider TEXT PRIMARY KEY CHECK(provider IN ('telegram','discord','web_app')),
    credential_kind TEXT NOT NULL CHECK(
        credential_kind IN ('telegram_bot_token','discord_webhook_url','discord_bot_token','web_token')
    ),
    protected_value BLOB NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS communication_policy (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    failover_enabled INTEGER NOT NULL DEFAULT 0 CHECK(failover_enabled IN (0,1)),
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS system_health_settings (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    monitor_enabled INTEGER NOT NULL DEFAULT 1 CHECK(monitor_enabled IN (0,1)),
    online_notifications_enabled INTEGER NOT NULL DEFAULT 0
        CHECK(online_notifications_enabled IN (0,1)),
    interval_seconds INTEGER NOT NULL DEFAULT 60 CHECK(interval_seconds BETWEEN 10 AND 3600),
    cooldown_seconds INTEGER NOT NULL DEFAULT 900 CHECK(cooldown_seconds BETWEEN 60 AND 86400),
    thresholds_json TEXT NOT NULL,
    delivery_routes_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS system_health_metric_state (
    metric TEXT PRIMARY KEY CHECK(
        metric IN ('cpu_percent','memory_percent','disk_percent','temperature_celsius')
    ),
    breached INTEGER NOT NULL DEFAULT 0 CHECK(breached IN (0,1)),
    last_value REAL,
    threshold REAL NOT NULL,
    changed_at TEXT NOT NULL,
    last_alert_at TEXT,
    last_recovery_at TEXT
);
CREATE TABLE IF NOT EXISTS system_health_alerts (
    id TEXT PRIMARY KEY,
    metric TEXT NOT NULL CHECK(
        metric IN ('cpu_percent','memory_percent','disk_percent','temperature_celsius')
    ),
    event_type TEXT NOT NULL CHECK(event_type IN ('triggered','ongoing','recovered')),
    severity TEXT NOT NULL CHECK(severity IN ('warning','info')),
    value REAL NOT NULL,
    threshold REAL NOT NULL,
    created_at TEXT NOT NULL,
    delivery_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_system_health_alerts_created
    ON system_health_alerts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_system_health_alerts_metric_created
    ON system_health_alerts(metric, created_at DESC);
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    capabilities_json TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS angerona_tactics (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE CHECK(length(name) BETWEEN 1 AND 120),
    description TEXT NOT NULL DEFAULT '' CHECK(length(description) <= 2000),
    steps_json TEXT NOT NULL CHECK(length(steps_json) <= 32768),
    preconditions_json TEXT NOT NULL DEFAULT '[]' CHECK(length(preconditions_json) <= 8192),
    success_evidence_json TEXT NOT NULL DEFAULT '[]'
        CHECK(length(success_evidence_json) <= 8192),
    version INTEGER NOT NULL DEFAULT 1 CHECK(version BETWEEN 1 AND 1000000),
    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_angerona_tactics_enabled_updated
    ON angerona_tactics(enabled, updated_at DESC);
CREATE TABLE IF NOT EXISTS phone_calls (
    call_sid TEXT PRIMARY KEY CHECK(length(call_sid) BETWEEN 10 AND 80),
    caller_hash TEXT NOT NULL,
    started_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    authenticated INTEGER NOT NULL DEFAULT 0 CHECK(authenticated IN (0,1)),
    failed_pins INTEGER NOT NULL DEFAULT 0 CHECK(failed_pins BETWEEN 0 AND 20),
    command_count INTEGER NOT NULL DEFAULT 0 CHECK(command_count BETWEEN 0 AND 100),
    status TEXT NOT NULL CHECK(status IN ('active','locked','ended'))
);
CREATE INDEX IF NOT EXISTS idx_phone_calls_started ON phone_calls(started_at DESC);
CREATE TABLE IF NOT EXISTS phone_webhook_receipts (
    digest TEXT PRIMARY KEY CHECK(length(digest) = 64),
    call_sid TEXT NOT NULL REFERENCES phone_calls(call_sid) ON DELETE CASCADE,
    path TEXT NOT NULL CHECK(length(path) BETWEEN 1 AND 200),
    received_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_phone_webhook_call
    ON phone_webhook_receipts(call_sid, received_at DESC);
"""


class Database:
    """Small thread-safe SQLite wrapper using explicit transactions."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._local = threading.local()
        self._write_lock = threading.RLock()

    def connect(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                self.path, timeout=10, isolation_level=None, check_same_thread=False
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=10000")
            self._local.connection = connection
        return connection

    def initialize(self) -> None:
        with self._write_lock:
            connection = self.connect()
            connection.executescript(SCHEMA)
            health_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(system_health_settings)")
            }
            if "online_notifications_enabled" not in health_columns:
                connection.execute(
                    "ALTER TABLE system_health_settings "
                    "ADD COLUMN online_notifications_enabled INTEGER NOT NULL DEFAULT 0 "
                    "CHECK(online_notifications_enabled IN (0,1))"
                )
            task_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(tasks)")
            }
            if "due_notified_at" not in task_columns:
                connection.execute("ALTER TABLE tasks ADD COLUMN due_notified_at TEXT")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a serialized write transaction and roll back on failure."""
        with self._write_lock:
            connection = self.connect()
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")

    def execute(self, sql: str, parameters: Iterable[Any] = ()) -> sqlite3.Cursor:
        return self.connect().execute(sql, tuple(parameters))

    def query_all(self, sql: str, parameters: Iterable[Any] = ()) -> list[dict[str, Any]]:
        return [dict(row) for row in self.execute(sql, parameters).fetchall()]

    def query_one(self, sql: str, parameters: Iterable[Any] = ()) -> dict[str, Any] | None:
        row = self.execute(sql, parameters).fetchone()
        return dict(row) if row is not None else None

    def close(self) -> None:
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
            self._local.connection = None
