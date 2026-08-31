"""Tamper-evident, append-only audit log."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime
from typing import Any

from .database import Database

GENESIS_HASH = "0" * 64


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class AuditLog:
    """Append security events to a SHA-256 hash chain.

    If ``JARVIS_AUDIT_SECRET`` is set, entries use HMAC-SHA256, protecting the
    chain against an attacker who can rewrite the database but does not have the
    process secret.
    """

    def __init__(self, database: Database, secret: str | None = None) -> None:
        self.database = database
        self.secret = (secret if secret is not None else os.getenv("JARVIS_AUDIT_SECRET", "")).encode()

    def _digest(self, payload: bytes) -> str:
        if self.secret:
            return hmac.new(self.secret, payload, hashlib.sha256).hexdigest()
        return hashlib.sha256(payload).hexdigest()

    def append(
        self,
        actor: str,
        action: str,
        resource: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        timestamp = utc_now()
        details_json = json.dumps(details or {}, sort_keys=True, separators=(",", ":"))
        with self.database.transaction() as connection:
            previous = connection.execute(
                "SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
            previous_hash = previous[0] if previous else GENESIS_HASH
            canonical = json.dumps(
                {
                    "timestamp": timestamp,
                    "actor": actor,
                    "action": action,
                    "resource": resource,
                    "details": json.loads(details_json),
                    "previous_hash": previous_hash,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            entry_hash = self._digest(canonical)
            cursor = connection.execute(
                """INSERT INTO audit_log
                (timestamp, actor, action, resource, details_json, previous_hash, entry_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (timestamp, actor, action, resource, details_json, previous_hash, entry_hash),
            )
            entry_id = cursor.lastrowid
        return {
            "id": entry_id,
            "timestamp": timestamp,
            "actor": actor,
            "action": action,
            "resource": resource,
            "details": json.loads(details_json),
            "previous_hash": previous_hash,
            "entry_hash": entry_hash,
        }

    def list(self, limit: int = 100, before_id: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM audit_log"
        params: list[Any] = []
        if before_id is not None:
            sql += " WHERE id < ?"
            params.append(before_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = self.database.query_all(sql, params)
        for row in rows:
            row["details"] = json.loads(row.pop("details_json"))
        return rows

    def verify(self) -> dict[str, Any]:
        rows = self.database.query_all("SELECT * FROM audit_log ORDER BY id ASC")
        expected_previous = GENESIS_HASH
        for row in rows:
            canonical = json.dumps(
                {
                    "timestamp": row["timestamp"],
                    "actor": row["actor"],
                    "action": row["action"],
                    "resource": row["resource"],
                    "details": json.loads(row["details_json"]),
                    "previous_hash": row["previous_hash"],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            if row["previous_hash"] != expected_previous or not hmac.compare_digest(
                row["entry_hash"], self._digest(canonical)
            ):
                return {"valid": False, "entries": len(rows), "broken_at_id": row["id"]}
            expected_previous = row["entry_hash"]
        return {"valid": True, "entries": len(rows), "head_hash": expected_previous}
