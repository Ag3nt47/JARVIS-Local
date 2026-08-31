from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jarvis.audit import AuditLog
from jarvis.config import Settings
from jarvis.database import Database
from jarvis.privacy import REDACTED, redact_sensitive_text
from jarvis.services import ApprovalService, NotificationService, Scheduler, TaskService


def test_approval_request_approve_and_replay_are_audited(
    database: Database, audit: AuditLog
) -> None:
    approvals = ApprovalService(database, audit, ttl_seconds=300)
    request = approvals.create(
        "assistant", "write_file", {"path": "note.txt", "content": "safe"}, "write"
    )

    assert request["status"] == "pending"
    assert request["arguments"]["path"] == "note.txt"

    approved = approvals.resolve(request["id"], "approved", "user", {"ok": True})
    assert approved["status"] == "approved"
    assert approved["result"] == {"ok": True}

    with pytest.raises(ValueError, match="already approved"):
        approvals.resolve(request["id"], "approved", "user")

    actions = [entry["action"] for entry in reversed(audit.list())]
    assert actions == ["approval.requested", "approval.approved"]


def test_approval_deny_and_expiry_cannot_be_replayed(
    database: Database, audit: AuditLog
) -> None:
    approvals = ApprovalService(database, audit, ttl_seconds=300)
    denied_request = approvals.create("assistant", "write_file", {"path": "x"}, "write")

    denied = approvals.resolve(denied_request["id"], "denied", "user")
    assert denied["status"] == "denied"
    with pytest.raises(ValueError, match="already denied"):
        approvals.resolve(denied_request["id"], "approved", "user")

    expired_service = ApprovalService(database, audit, ttl_seconds=-1)
    expired = expired_service.create("assistant", "write_file", {"path": "old"}, "write")
    assert expired["status"] == "expired"
    with pytest.raises(ValueError, match="already expired"):
        expired_service.resolve(expired["id"], "approved", "user")


def test_approval_execution_claim_is_atomic_and_single_use(
    database: Database, audit: AuditLog
) -> None:
    approvals = ApprovalService(database, audit, ttl_seconds=300)
    request = approvals.create("assistant", "run_command", {"executable": "python"}, "run")

    claimed = approvals.claim_for_execution(request["id"], "user")

    assert claimed["status"] == "approved"
    with pytest.raises(ValueError, match="already approved"):
        approvals.claim_for_execution(request["id"], "user")
    completed = approvals.record_execution(
        request["id"],
        succeeded=True,
        actor="user",
        result={"status": "completed", "exit_code": 0},
    )
    assert completed["result"]["exit_code"] == 0


def test_task_deduplication_update_and_inactivity(database: Database, audit: AuditLog) -> None:
    tasks = TaskService(database, audit)
    first, created = tasks.create("Investigate", priority=3, dedupe_key="incident-42")
    duplicate, duplicate_created = tasks.create(
        "Should not replace", priority=5, dedupe_key="incident-42"
    )

    assert created is True
    assert duplicate_created is False
    assert duplicate["id"] == first["id"]
    assert duplicate["title"] == "Investigate"

    updated = tasks.update(first["id"], {"state": "in_progress", "priority": 4})
    assert updated["state"] == "in_progress"
    assert updated["priority"] == 4
    assert updated["last_activity_at"] >= first["last_activity_at"]
    with pytest.raises(ValueError, match="Unsupported task fields"):
        tasks.update(first["id"], {"owner": "nobody"})
    with pytest.raises(KeyError, match="Task not found"):
        tasks.update("missing", {"state": "done"})

    stale = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    database.execute(
        "UPDATE tasks SET last_activity_at=? WHERE id=?", (stale, first["id"])
    )
    assert [task["id"] for task in tasks.inactive(120)] == [first["id"]]

    tasks.update(first["id"], {"state": "done"})
    database.execute(
        "UPDATE tasks SET last_activity_at=? WHERE id=?", (stale, first["id"])
    )
    assert tasks.inactive(120) == []

    replacement, replacement_created = tasks.create(
        "New active task", dedupe_key="incident-42"
    )
    assert replacement_created is True
    assert replacement["id"] != first["id"]


@pytest.mark.asyncio
async def test_due_task_notifies_once_and_rearms_when_deadline_changes(
    database: Database, audit: AuditLog, settings: Settings
) -> None:
    tasks = TaskService(database, audit)
    notifications = NotificationService(database, settings)
    scheduler = Scheduler(settings, tasks, notifications)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    task, _ = tasks.create("Review briefing", due_at=past)

    assert await scheduler.run_once() == 1
    assert await scheduler.run_once() == 0
    assert notifications.list()[0]["title"] == "Task due"
    assert tasks.get(task["id"])["due_notified_at"] is not None

    revised = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    tasks.update(task["id"], {"due_at": revised})
    assert tasks.get(task["id"])["due_notified_at"] is None
    assert await scheduler.run_once() == 1


def test_notification_deduplication_and_outbound_disabled(
    database: Database, settings: Settings
) -> None:
    notifications = NotificationService(database, settings)
    first = notifications.send_local("Alert", "Something happened", "warning", "same")
    duplicate = notifications.send_local("Changed", "Do not create", "error", "same")

    assert duplicate["id"] == first["id"]
    assert duplicate["deduplicated"] is True
    assert len(notifications.list()) == 1
    assert first["provider"] == "local_log"
    assert first["delivered"] == 1

    with pytest.raises(PermissionError, match="disabled"):
        notifications.send_outbound("external")


def test_sensitive_text_is_masked_before_local_persistence(
    database: Database, settings: Settings
) -> None:
    notifications = NotificationService(database, settings)
    saved = notifications.send_local(
        "Credential",
        "api_key=sk-exampleexampleexample and Authorization: Bearer abcdefghijklmno",
    )

    assert "sk-example" not in saved["message"]
    assert "abcdefgh" not in saved["message"]
    assert saved["message"].count(REDACTED) == 2
    assert redact_sensitive_text("ordinary planning note") == "ordinary planning note"

    settings.outbound_notifications_enabled = True
    with pytest.raises(NotImplementedError, match="No outbound"):
        notifications.send_outbound("external")
