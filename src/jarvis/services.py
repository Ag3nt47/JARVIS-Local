"""Persistence-backed application services."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import uuid
from datetime import UTC, datetime, timedelta
from datetime import time as dt_time
from typing import Any, ClassVar

import psutil

from .audit import AuditLog, utc_now
from .config import Settings
from .database import Database
from .privacy import redact_sensitive_text
from .providers import OllamaProvider

LOGGER = logging.getLogger("jarvis")


def _decode_json_fields(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    for field in fields:
        if field in row:
            raw_value = row.pop(field)
            row[field.removesuffix("_json")] = (
                json.loads(raw_value) if raw_value is not None else None
            )
    return row


class ApprovalService:
    """Create and resolve explicit human approval requests."""

    def __init__(self, database: Database, audit: AuditLog, ttl_seconds: int = 900) -> None:
        self.database = database
        self.audit = audit
        self.ttl_seconds = ttl_seconds

    def create(
        self,
        requester: str,
        tool_name: str,
        arguments: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=self.ttl_seconds)
        arguments_json = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO approvals
                (id, created_at, expires_at, requester, tool_name, arguments_json, reason, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')""",
                (
                    request_id,
                    now.isoformat(),
                    expires_at.isoformat(),
                    requester,
                    tool_name,
                    arguments_json,
                    reason,
                ),
            )
        self.audit.append(
            requester,
            "approval.requested",
            f"approval:{request_id}",
            {"tool_name": tool_name, "reason": reason},
        )
        return self.get(request_id) or {}

    def _expire_if_needed(self, row: dict[str, Any]) -> dict[str, Any]:
        if row["status"] == "pending" and datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC):
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE approvals SET status='expired', resolved_at=? WHERE id=? AND status='pending'",
                    (utc_now(), row["id"]),
                )
            row["status"] = "expired"
            row["resolved_at"] = utc_now()
        return row

    def get(self, request_id: str) -> dict[str, Any] | None:
        row = self.database.query_one("SELECT * FROM approvals WHERE id=?", (request_id,))
        if row is None:
            return None
        row = self._expire_if_needed(row)
        return _decode_json_fields(row, ("arguments_json", "result_json"))

    def list(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if status:
            rows = self.database.query_all(
                "SELECT * FROM approvals WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            rows = self.database.query_all(
                "SELECT * FROM approvals ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        return [
            _decode_json_fields(self._expire_if_needed(row), ("arguments_json", "result_json"))
            for row in rows
        ]

    def resolve(
        self,
        request_id: str,
        decision: str,
        actor: str,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = self.get(request_id)
        if row is None:
            raise KeyError("Approval request not found")
        if row["status"] != "pending":
            raise ValueError(f"Approval is already {row['status']}")
        if decision not in {"approved", "denied", "failed"}:
            raise ValueError("Invalid approval decision")
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE approvals SET status=?, resolved_at=?, result_json=? WHERE id=?",
                (
                    decision,
                    utc_now(),
                    json.dumps(result, sort_keys=True) if result is not None else None,
                    request_id,
                ),
            )
        self.audit.append(
            actor,
            f"approval.{decision}",
            f"approval:{request_id}",
            {"tool_name": row["tool_name"]},
        )
        return self.get(request_id) or {}

    def claim_for_execution(self, request_id: str, actor: str) -> dict[str, Any]:
        """Atomically consume one pending approval before its effect can run."""

        resolved_at = utc_now()
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """UPDATE approvals
                SET status='approved', resolved_at=?
                WHERE id=? AND status='pending' AND expires_at>?""",
                (resolved_at, request_id, resolved_at),
            )
        if cursor.rowcount != 1:
            current = self.get(request_id)
            if current is None:
                raise KeyError("Approval request not found")
            raise ValueError(f"Approval is already {current['status']}")
        claimed = self.get(request_id) or {}
        self.audit.append(
            actor,
            "approval.approved",
            f"approval:{request_id}",
            {"tool_name": claimed.get("tool_name")},
        )
        return claimed

    def record_execution(
        self,
        request_id: str,
        *,
        succeeded: bool,
        actor: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Attach the claimed action result, or mark its execution as failed."""

        status = "approved" if succeeded else "failed"
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """UPDATE approvals SET status=?, result_json=?
                WHERE id=? AND status='approved'""",
                (status, json.dumps(result, sort_keys=True), request_id),
            )
        if cursor.rowcount != 1:
            raise ValueError("Approval execution was not in the claimed state")
        if not succeeded:
            self.audit.append(
                actor,
                "approval.failed",
                f"approval:{request_id}",
                {"error_type": result.get("error_type", "unknown")},
            )
        return self.get(request_id) or {}


class TaskService:
    """Task storage with activity timestamps and active-task deduplication."""

    ACTIVE_STATES = ("open", "in_progress", "blocked")

    @staticmethod
    def _normalize_due_at(value: str | None) -> str | None:
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("Task due time must be an ISO-8601 date and time") from exc
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        return parsed.astimezone(UTC).isoformat()

    def __init__(self, database: Database, audit: AuditLog) -> None:
        self.database = database
        self.audit = audit

    def create(
        self,
        title: str,
        details: str = "",
        priority: int = 2,
        due_at: str | None = None,
        dedupe_key: str | None = None,
        actor: str = "user",
    ) -> tuple[dict[str, Any], bool]:
        due_at = self._normalize_due_at(due_at)
        if dedupe_key:
            existing = self.database.query_one(
                """SELECT * FROM tasks WHERE dedupe_key=?
                AND state IN ('open','in_progress','blocked') ORDER BY created_at DESC LIMIT 1""",
                (dedupe_key,),
            )
            if existing:
                return existing, False
        task_id = str(uuid.uuid4())
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO tasks
                (id,title,details,state,priority,created_at,updated_at,due_at,last_activity_at,dedupe_key)
                VALUES (?,?,?,'open',?,?,?,?,?,?)""",
                (task_id, title, details, priority, now, now, due_at, now, dedupe_key),
            )
        self.audit.append(actor, "task.created", f"task:{task_id}", {"title": title})
        return self.get(task_id) or {}, True

    def get(self, task_id: str) -> dict[str, Any] | None:
        return self.database.query_one("SELECT * FROM tasks WHERE id=?", (task_id,))

    def list(self, state: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if state:
            return self.database.query_all(
                "SELECT * FROM tasks WHERE state=? ORDER BY priority DESC, updated_at DESC LIMIT ?",
                (state, limit),
            )
        return self.database.query_all(
            "SELECT * FROM tasks ORDER BY priority DESC, updated_at DESC LIMIT ?", (limit,)
        )

    def update(self, task_id: str, changes: dict[str, Any], actor: str = "user") -> dict[str, Any]:
        allowed = {"title", "details", "state", "priority", "due_at", "dedupe_key"}
        invalid = set(changes) - allowed
        if invalid:
            raise ValueError(f"Unsupported task fields: {', '.join(sorted(invalid))}")
        if not changes:
            current = self.get(task_id)
            if current is None:
                raise KeyError("Task not found")
            return current
        changes = dict(changes)
        if "due_at" in changes:
            changes["due_at"] = self._normalize_due_at(changes["due_at"])
            changes["due_notified_at"] = None
        now = utc_now()
        changes["updated_at"] = now
        changes["last_activity_at"] = now
        assignments = ", ".join(f"{key}=?" for key in changes)
        values = list(changes.values()) + [task_id]
        with self.database.transaction() as connection:
            cursor = connection.execute(f"UPDATE tasks SET {assignments} WHERE id=?", values)
            if cursor.rowcount != 1:
                raise KeyError("Task not found")
        self.audit.append(actor, "task.updated", f"task:{task_id}", {"fields": sorted(changes)})
        return self.get(task_id) or {}

    def inactive(self, minutes: int) -> list[dict[str, Any]]:
        cutoff = (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()
        return self.database.query_all(
            """SELECT * FROM tasks WHERE state IN ('open','in_progress','blocked')
            AND last_activity_at <= ? ORDER BY last_activity_at ASC""",
            (cutoff,),
        )

    def due_unnotified(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return active tasks whose due time has arrived and has not been announced."""

        return self.database.query_all(
            """SELECT * FROM tasks
            WHERE state IN ('open','in_progress','blocked')
            AND due_at IS NOT NULL AND due_at <= ? AND due_notified_at IS NULL
            ORDER BY due_at ASC LIMIT ?""",
            (utc_now(), limit),
        )

    def mark_due_notified(self, task_id: str, due_at: str) -> bool:
        """Mark the exact current deadline as announced without racing an edit."""

        with self.database.transaction() as connection:
            cursor = connection.execute(
                """UPDATE tasks SET due_notified_at=?
                WHERE id=? AND due_at=? AND due_notified_at IS NULL""",
                (utc_now(), task_id, due_at),
            )
        return cursor.rowcount == 1


class ProjectService:
    """Persist safe project metadata and constrained lifecycle transitions.

    Projects intentionally expose no command, script, URL, or filesystem-path fields.
    Free text is display-only; file-tool roots remain separately configured and are
    never derived from project metadata.
    """

    STATUSES = frozenset({"planned", "active", "paused", "completed", "archived"})
    ACCENTS = frozenset(
        {"slate", "blue", "cyan", "green", "amber", "orange", "red", "violet", "pink"}
    )
    TRANSITIONS: ClassVar[dict[str, frozenset[str]]] = {
        "planned": frozenset({"active", "completed", "archived"}),
        "active": frozenset({"paused", "completed", "archived"}),
        "paused": frozenset({"active", "completed", "archived"}),
        "completed": frozenset({"active", "archived"}),
        "archived": frozenset({"planned"}),
    }
    _TAG_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9 _.-]{0,38}[A-Za-z0-9])?$")
    _UPDATE_FIELDS = frozenset(
        {
            "title",
            "summary",
            "status",
            "priority",
            "progress",
            "next_action",
            "tags",
            "accent",
            "pinned",
        }
    )

    def __init__(self, database: Database, audit: AuditLog) -> None:
        self.database = database
        self.audit = audit

    @staticmethod
    def _text(
        value: Any,
        field: str,
        maximum: int,
        *,
        allow_empty: bool = False,
        multiline: bool = False,
    ) -> str:
        if not isinstance(value, str):
            raise TypeError(f"Project {field} must be text")
        normalized = value.strip()
        if not normalized and not allow_empty:
            raise ValueError(f"Project {field} cannot be empty")
        if len(normalized) > maximum:
            raise ValueError(f"Project {field} exceeds {maximum} characters")
        allowed_controls = "\n\t" if multiline else ""
        if any(
            ord(character) < 32 and character not in allowed_controls for character in normalized
        ):
            raise ValueError(f"Project {field} contains control characters")
        return normalized

    @classmethod
    def _tags(cls, value: Any) -> list[str]:
        if not isinstance(value, (list, tuple)) or len(value) > 20:
            raise ValueError("Project tags must be a list containing at most 20 items")
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_tag in value:
            if not isinstance(raw_tag, str):
                raise TypeError("Project tags must be text")
            tag = raw_tag.strip()
            if not cls._TAG_RE.fullmatch(tag):
                raise ValueError(
                    "Project tags must use 1-40 letters, digits, spaces, '.', '_' or '-'"
                )
            folded = tag.casefold()
            if folded not in seen:
                normalized.append(tag)
                seen.add(folded)
        return normalized

    @classmethod
    def _validated_changes(cls, changes: dict[str, Any]) -> dict[str, Any]:
        invalid = set(changes) - cls._UPDATE_FIELDS
        if invalid:
            raise ValueError(f"Unsupported project fields: {', '.join(sorted(invalid))}")
        validated: dict[str, Any] = {}
        for field, value in changes.items():
            if field == "title":
                validated[field] = cls._text(value, field, 200)
            elif field == "summary":
                validated[field] = cls._text(value, field, 10_000, allow_empty=True, multiline=True)
            elif field == "next_action":
                validated[field] = cls._text(value, field, 500, allow_empty=True)
            elif field == "status":
                if value not in cls.STATUSES:
                    raise ValueError("Unsupported project status")
                validated[field] = value
            elif field in {"priority", "progress"}:
                minimum, maximum = (1, 5) if field == "priority" else (0, 100)
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or not minimum <= value <= maximum
                ):
                    raise ValueError(f"Project {field} must be between {minimum} and {maximum}")
                validated[field] = value
            elif field == "tags":
                validated["tags_json"] = json.dumps(
                    cls._tags(value), ensure_ascii=False, separators=(",", ":")
                )
            elif field == "accent":
                if value not in cls.ACCENTS:
                    raise ValueError("Unsupported project accent")
                validated[field] = value
            elif field == "pinned":
                if not isinstance(value, bool):
                    raise ValueError("Project pinned must be boolean")
                validated[field] = int(value)
        return validated

    @classmethod
    def _decode(cls, row: dict[str, Any]) -> dict[str, Any]:
        decoded = dict(row)
        tags = json.loads(decoded.pop("tags_json"))
        if not isinstance(tags, list):
            raise TypeError("Stored project tags are invalid")
        decoded["tags"] = tags
        decoded["pinned"] = bool(decoded["pinned"])
        decoded["allowed_transitions"] = sorted(cls.TRANSITIONS[decoded["status"]])
        return decoded

    @classmethod
    def _check_transition(cls, current: str, target: str) -> None:
        if target != current and target not in cls.TRANSITIONS[current]:
            raise ValueError(f"Project cannot transition from {current} to {target}")

    def create(
        self,
        title: str,
        summary: str = "",
        status: str = "planned",
        priority: int = 2,
        progress: int = 0,
        next_action: str = "",
        tags: list[str] | tuple[str, ...] = (),
        accent: str = "cyan",
        pinned: bool = False,
        actor: str = "user",
    ) -> dict[str, Any]:
        """Create one project after defense-in-depth service validation."""

        values = self._validated_changes(
            {
                "title": title,
                "summary": summary,
                "status": status,
                "priority": priority,
                "progress": progress,
                "next_action": next_action,
                "tags": tags,
                "accent": accent,
                "pinned": pinned,
            }
        )
        if values["status"] == "completed":
            values["progress"] = 100
        project_id = str(uuid.uuid4())
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO projects
                (id,title,summary,status,priority,progress,next_action,tags_json,accent,pinned,
                 created_at,updated_at,last_activity_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    project_id,
                    values["title"],
                    values["summary"],
                    values["status"],
                    values["priority"],
                    values["progress"],
                    values["next_action"],
                    values["tags_json"],
                    values["accent"],
                    values["pinned"],
                    now,
                    now,
                    now,
                ),
            )
        self.audit.append(
            actor,
            "project.created",
            f"project:{project_id}",
            {"title": values["title"], "status": values["status"]},
        )
        return self.get(project_id) or {}

    def get(self, project_id: str) -> dict[str, Any] | None:
        row = self.database.query_one("SELECT * FROM projects WHERE id=?", (project_id,))
        return self._decode(row) if row is not None else None

    def list(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """List pinned projects first with a bounded, deterministic ordering."""

        if status is not None and status not in self.STATUSES:
            raise ValueError("Unsupported project status")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("Project list limit must be between 1 and 500")
        if status is None:
            rows = self.database.query_all(
                """SELECT * FROM projects
                ORDER BY pinned DESC, priority DESC, updated_at DESC, id ASC LIMIT ?""",
                (limit,),
            )
        else:
            rows = self.database.query_all(
                """SELECT * FROM projects WHERE status=?
                ORDER BY pinned DESC, priority DESC, updated_at DESC, id ASC LIMIT ?""",
                (status, limit),
            )
        return [self._decode(row) for row in rows]

    def update(
        self, project_id: str, changes: dict[str, Any], actor: str = "user"
    ) -> dict[str, Any]:
        """Update only allowlisted metadata and enforce lifecycle transitions."""

        if not changes:
            current = self.get(project_id)
            if current is None:
                raise KeyError("Project not found")
            return current
        validated = self._validated_changes(dict(changes))
        now = utc_now()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if row is None:
                raise KeyError("Project not found")
            current = dict(row)
            if "status" in validated:
                self._check_transition(current["status"], validated["status"])
                if validated["status"] == "completed":
                    validated["progress"] = 100
            resulting_status = validated.get("status", current["status"])
            resulting_progress = validated.get("progress", current["progress"])
            if resulting_status == "completed" and resulting_progress != 100:
                raise ValueError("Completed projects must have 100 percent progress")
            validated["updated_at"] = now
            validated["last_activity_at"] = now
            assignments = ", ".join(f"{field}=?" for field in validated)
            connection.execute(
                f"UPDATE projects SET {assignments} WHERE id=?",
                (*validated.values(), project_id),
            )
        public_fields = sorted(set(changes) & self._UPDATE_FIELDS)
        self.audit.append(
            actor,
            "project.updated",
            f"project:{project_id}",
            {"fields": public_fields},
        )
        return self.get(project_id) or {}


class NotificationService:
    """Persist and log local notifications; outbound adapters remain disabled."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        logger: logging.Logger | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.logger = logger or LOGGER

    def send_local(
        self,
        title: str,
        message: str,
        level: str = "info",
        dedupe_key: str | None = None,
    ) -> dict[str, Any]:
        title = redact_sensitive_text(title)
        message = redact_sensitive_text(message)
        if dedupe_key:
            cutoff = (
                datetime.now(UTC) - timedelta(minutes=self.settings.notification_dedupe_minutes)
            ).isoformat()
            existing = self.database.query_one(
                """SELECT * FROM notifications WHERE dedupe_key=? AND created_at>=?
                ORDER BY created_at DESC LIMIT 1""",
                (dedupe_key, cutoff),
            )
            if existing:
                existing["deduplicated"] = True
                return existing
        notification_id = str(uuid.uuid4())
        created_at = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO notifications
                (id,created_at,level,title,message,dedupe_key,provider,delivered)
                VALUES (?,?,?,?,?,?,'local_log',1)""",
                (notification_id, created_at, level, title, message, dedupe_key),
            )
        self.logger.log(
            {"info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}.get(
                level, logging.INFO
            ),
            "%s: %s",
            title,
            message,
        )
        return self.database.query_one(
            "SELECT * FROM notifications WHERE id=?", (notification_id,)
        ) or {}

    def send_outbound(self, *_: Any, **__: Any) -> None:
        if not self.settings.outbound_notifications_enabled:
            raise PermissionError("Outbound notification adapters are disabled")
        raise NotImplementedError("No outbound notification adapter is configured")

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.database.query_all(
            "SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?", (limit,)
        )


class TelemetryService:
    """Read-only local system telemetry backed by psutil."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def snapshot(self) -> dict[str, Any]:
        process = psutil.Process()
        disks: list[dict[str, Any]] = []
        seen: set[str] = set()
        for root in self.settings.allowed_roots:
            anchor = root.anchor or str(root)
            if anchor in seen:
                continue
            seen.add(anchor)
            usage = psutil.disk_usage(anchor)
            disks.append(
                {
                    "path": anchor,
                    "total_bytes": usage.total,
                    "used_bytes": usage.used,
                    "free_bytes": usage.free,
                    "percent": usage.percent,
                }
            )
        virtual_memory = psutil.virtual_memory()
        return {
            "timestamp": utc_now(),
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory": {
                "total_bytes": virtual_memory.total,
                "available_bytes": virtual_memory.available,
                "percent": virtual_memory.percent,
            },
            "disks": disks,
            "process": {
                "pid": process.pid,
                "rss_bytes": process.memory_info().rss,
                "threads": process.num_threads(),
                "uptime_seconds": max(
                    0, datetime.now(UTC).timestamp() - process.create_time()
                ),
            },
        }


class MemoryService:
    """Embedding-backed local memory with exact SQLite cosine ranking."""

    def __init__(self, database: Database, provider: OllamaProvider) -> None:
        self.database = database
        self.provider = provider

    async def add(
        self,
        content: str,
        source: str = "user",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        content = redact_sensitive_text(content)
        vector = (await self.provider.embed(content))[0]
        item_id = str(uuid.uuid4())
        created_at = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO memory_items
                (id,created_at,source,content,metadata_json,embedding_json,embedding_model)
                VALUES (?,?,?,?,?,?,?)""",
                (
                    item_id,
                    created_at,
                    source,
                    content,
                    json.dumps(metadata or {}, sort_keys=True),
                    json.dumps(vector, separators=(",", ":")),
                    self.provider.embed_model,
                ),
            )
        return {
            "id": item_id,
            "created_at": created_at,
            "source": source,
            "content": content,
            "metadata": metadata or {},
            "embedding_model": self.provider.embed_model,
        }

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if len(left) != len(right) or not left:
            return -1.0
        numerator = sum(a * b for a, b in zip(left, right, strict=True))
        denominator = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
        return numerator / denominator if denominator else 0.0

    async def search(self, query: str, limit: int = 8, min_score: float = -1.0) -> list[dict[str, Any]]:
        query_vector = (await self.provider.embed(query))[0]
        rows = self.database.query_all("SELECT * FROM memory_items")
        ranked: list[dict[str, Any]] = []
        for row in rows:
            score = self._cosine(query_vector, json.loads(row.pop("embedding_json")))
            if score < min_score:
                continue
            row["metadata"] = json.loads(row.pop("metadata_json"))
            row["score"] = score
            ranked.append(row)
        ranked.sort(key=lambda item: item["score"], reverse=True)
        return ranked[:limit]

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.database.query_all(
            """SELECT id,created_at,source,content,metadata_json,embedding_model
            FROM memory_items ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        )
        return [_decode_json_fields(row, ("metadata_json",)) for row in rows]

    def pin(
        self,
        key: str,
        content: str,
        *,
        source: str = "system",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Upsert a restart-safe memory that does not depend on an embedding provider."""

        normalized_key = key.strip()
        if not normalized_key or len(normalized_key) > 120:
            raise ValueError("Pinned memory key must be between 1 and 120 characters")
        if not content.strip() or len(content) > 100_000:
            raise ValueError("Pinned memory content must be between 1 and 100000 characters")
        now = utc_now()
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT created_at FROM pinned_memory_items WHERE key=?", (normalized_key,)
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            connection.execute(
                """INSERT INTO pinned_memory_items
                (key,created_at,updated_at,source,content,metadata_json)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(key) DO UPDATE SET updated_at=excluded.updated_at,
                source=excluded.source,content=excluded.content,
                metadata_json=excluded.metadata_json""",
                (
                    normalized_key,
                    created_at,
                    now,
                    source,
                    content,
                    json.dumps(metadata or {}, sort_keys=True),
                ),
            )
        return self.get_pinned(normalized_key) or {}

    def get_pinned(self, key: str) -> dict[str, Any] | None:
        row = self.database.query_one("SELECT * FROM pinned_memory_items WHERE key=?", (key,))
        return _decode_json_fields(row, ("metadata_json",)) if row else None

    def list_pinned(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.database.query_all(
            "SELECT * FROM pinned_memory_items ORDER BY updated_at DESC LIMIT ?", (limit,)
        )
        return [_decode_json_fields(row, ("metadata_json",)) for row in rows]

    def delete(self, item_id: str) -> bool:
        with self.database.transaction() as connection:
            cursor = connection.execute("DELETE FROM memory_items WHERE id=?", (item_id,))
        return cursor.rowcount == 1


class NodeService:
    """Local registry for explicitly configured JARVIS nodes; discovery is off."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def list(self) -> list[dict[str, Any]]:
        rows = self.database.query_all("SELECT * FROM nodes ORDER BY name")
        return [_decode_json_fields(row, ("capabilities_json",)) for row in rows]

    def heartbeat(
        self, node_id: str, name: str, url: str, capabilities: list[str]
    ) -> dict[str, Any]:
        now = utc_now()
        capabilities_json = json.dumps(sorted(set(capabilities)))
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO nodes (id,name,url,capabilities_json,last_seen_at,status)
                VALUES (?,?,?,?,?,'online')
                ON CONFLICT(id) DO UPDATE SET name=excluded.name,url=excluded.url,
                capabilities_json=excluded.capabilities_json,last_seen_at=excluded.last_seen_at,
                status='online'""",
                (node_id, name, url, capabilities_json, now),
            )
        row = self.database.query_one("SELECT * FROM nodes WHERE id=?", (node_id,)) or {}
        return _decode_json_fields(row, ("capabilities_json",))


class Scheduler:
    """Minimal cooperative scheduler for inactivity checks."""

    def __init__(
        self,
        settings: Settings,
        tasks: TaskService,
        notifications: NotificationService,
        logger: logging.Logger | None = None,
    ) -> None:
        self.settings = settings
        self.tasks = tasks
        self.notifications = notifications
        self.logger = logger or LOGGER
        self._runner: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    def _is_quiet_time(self) -> bool:
        if not self.settings.quiet_hours_enabled:
            return False
        now = datetime.now(UTC).astimezone().time().replace(tzinfo=None)
        start = dt_time.fromisoformat(self.settings.quiet_hours_start)
        end = dt_time.fromisoformat(self.settings.quiet_hours_end)
        if start <= end:
            return start <= now < end
        return now >= start or now < end

    async def run_once(self) -> int:
        if self._is_quiet_time():
            return 0
        inactive = self.tasks.inactive(self.settings.inactivity_minutes)
        for task in inactive:
            self.notifications.send_local(
                "Inactive task",
                f"{task['title']} has had no activity since {task['last_activity_at']}",
                "warning",
                f"inactive:{task['id']}",
            )
        due = self.tasks.due_unnotified()
        for task in due:
            self.notifications.send_local(
                "Task due",
                task["title"],
                "warning",
                f"due:{task['id']}:{task['due_at']}",
            )
            self.tasks.mark_due_notified(task["id"], task["due_at"])
        return len(inactive) + len(due)

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.run_once()
            except Exception:
                self.logger.exception("Scheduled inactivity scan failed")
            try:
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=self.settings.scheduler_interval_seconds
                )
            except TimeoutError:
                continue

    def start(self) -> None:
        if self._runner is None:
            self._stopping.clear()
            self._runner = asyncio.create_task(self._loop(), name="jarvis-scheduler")

    async def stop(self) -> None:
        if self._runner is not None:
            self._stopping.set()
            await self._runner
            self._runner = None
