"""Durable assistant turn lifecycle and correlation primitives."""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from .audit import AuditLog, utc_now
from .database import Database

TurnState = Literal[
    "received",
    "thinking",
    "awaiting_approval",
    "acting",
    "verifying",
    "speaking",
    "completed",
    "cancelled",
    "failed",
]

TERMINAL_STATES = {"completed", "cancelled", "failed"}
TRANSITIONS: dict[str, set[str]] = {
    "received": {"thinking", "cancelled", "failed"},
    "thinking": {"awaiting_approval", "acting", "speaking", "completed", "cancelled", "failed"},
    "awaiting_approval": {"acting", "cancelled", "failed"},
    "acting": {"awaiting_approval", "verifying", "speaking", "completed", "cancelled", "failed"},
    "verifying": {"speaking", "completed", "cancelled", "failed"},
    "speaking": {"completed", "cancelled", "failed"},
}


class TurnCancelled(RuntimeError):
    """Raised at a safe cancellation checkpoint."""


class OrchestrationService:
    """Persist state before work so turns can be inspected and safely resumed."""

    def __init__(self, database: Database, audit: AuditLog) -> None:
        self.database = database
        self.audit = audit

    def begin(
        self, skill_id: str, *, session_id: str | None = None, channel: str = "text"
    ) -> dict[str, Any]:
        if channel not in {"text", "voice", "phone", "remote"}:
            raise ValueError("Unknown assistant channel")
        now = utc_now()
        session_id = session_id or str(uuid.uuid4())
        turn_id = str(uuid.uuid4())
        with self.database.transaction() as connection:
            session = connection.execute(
                "SELECT status FROM assistant_sessions WHERE id=?", (session_id,)
            ).fetchone()
            if session is None:
                connection.execute(
                    "INSERT INTO assistant_sessions (id,created_at,updated_at,status,channel) VALUES (?,?,?,'active',?)",
                    (session_id, now, now, channel),
                )
            elif session["status"] != "active":
                raise ValueError("Assistant session is not active")
            connection.execute(
                "INSERT INTO assistant_turns (id,session_id,created_at,updated_at,state,skill_id) VALUES (?,?,?,?,?,?)",
                (turn_id, session_id, now, now, "received", skill_id),
            )
            connection.execute(
                "INSERT INTO assistant_turn_events (turn_id,timestamp,state,event_type) VALUES (?,?,?,?)",
                (turn_id, now, "received", "turn.started"),
            )
        self.audit.append(
            "assistant",
            "turn.started",
            f"turn:{turn_id}",
            {"session_id": session_id, "skill_id": skill_id, "channel": channel},
        )
        return self.get(turn_id) or {}

    def get(self, turn_id: str, *, include_events: bool = True) -> dict[str, Any] | None:
        row = self.database.query_one("SELECT * FROM assistant_turns WHERE id=?", (turn_id,))
        if row is None:
            return None
        row["cancel_requested"] = bool(row["cancel_requested"])
        row["result"] = json.loads(row.pop("result_json")) if row.get("result_json") else None
        if include_events:
            events = self.database.query_all(
                "SELECT id,timestamp,state,event_type,details_json FROM assistant_turn_events WHERE turn_id=? ORDER BY id",
                (turn_id,),
            )
            for event in events:
                event["details"] = json.loads(event.pop("details_json"))
            row["events"] = events
        return row

    def transition(
        self,
        turn_id: str,
        state: TurnState,
        *,
        event_type: str | None = None,
        details: dict[str, Any] | None = None,
        approval_id: str | None = None,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            current = connection.execute(
                "SELECT state FROM assistant_turns WHERE id=?", (turn_id,)
            ).fetchone()
            if current is None:
                raise KeyError("Assistant turn not found")
            if state != current["state"] and state not in TRANSITIONS.get(current["state"], set()):
                raise ValueError(f"Invalid turn transition: {current['state']} -> {state}")
            connection.execute(
                "UPDATE assistant_turns SET state=?,updated_at=?,approval_id=COALESCE(?,approval_id),result_json=COALESCE(?,result_json),error_code=COALESCE(?,error_code) WHERE id=?",
                (
                    state,
                    now,
                    approval_id,
                    json.dumps(result, sort_keys=True) if result is not None else None,
                    error_code,
                    turn_id,
                ),
            )
            connection.execute(
                "INSERT INTO assistant_turn_events (turn_id,timestamp,state,event_type,details_json) VALUES (?,?,?,?,?)",
                (
                    turn_id,
                    now,
                    state,
                    event_type or f"turn.{state}",
                    json.dumps(details or {}, sort_keys=True),
                ),
            )
        self.audit.append(
            "assistant", event_type or f"turn.{state}", f"turn:{turn_id}", details or {}
        )
        return self.get(turn_id) or {}

    def request_cancel(self, turn_id: str, actor: str = "user") -> dict[str, Any]:
        turn = self.get(turn_id, include_events=False)
        if turn is None:
            raise KeyError("Assistant turn not found")
        if turn["state"] in TERMINAL_STATES:
            return self.get(turn_id) or {}
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE assistant_turns SET cancel_requested=1,updated_at=? WHERE id=?",
                (utc_now(), turn_id),
            )
        self.audit.append(
            actor, "turn.cancel_requested", f"turn:{turn_id}", {"state": turn["state"]}
        )
        return self.get(turn_id) or {}

    def checkpoint(self, turn_id: str) -> None:
        turn = self.get(turn_id, include_events=False)
        if turn is None:
            raise KeyError("Assistant turn not found")
        if turn["cancel_requested"]:
            if turn["state"] not in TERMINAL_STATES:
                self.transition(turn_id, "cancelled", event_type="turn.cancelled")
            raise TurnCancelled("Assistant turn was cancelled")

    def find_by_approval(self, approval_id: str) -> dict[str, Any] | None:
        row = self.database.query_one(
            "SELECT id FROM assistant_turns WHERE approval_id=?", (approval_id,)
        )
        return self.get(row["id"]) if row else None

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.database.query_all(
            "SELECT id FROM assistant_turns ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [self.get(row["id"], include_events=False) or {} for row in rows]

    def metrics(self) -> dict[str, Any]:
        states = self.database.query_all(
            "SELECT state,COUNT(*) AS count FROM assistant_turns GROUP BY state"
        )
        pending = self.database.query_one(
            "SELECT COUNT(*) AS count FROM assistant_turns WHERE state='awaiting_approval'"
        )
        return {
            "turns_by_state": {row["state"]: row["count"] for row in states},
            "awaiting_approval": pending["count"] if pending else 0,
        }

    def resume_approval(
        self, approval_id: str, outcome: dict[str, Any], *, succeeded: bool
    ) -> dict[str, Any] | None:
        turn = self.find_by_approval(approval_id)
        if turn is None or turn["state"] != "awaiting_approval":
            return turn
        self.checkpoint(turn["id"])
        self.transition(
            turn["id"],
            "acting",
            event_type="turn.approval_resumed",
            details={"approval_id": approval_id},
        )
        if not succeeded:
            return self.transition(
                turn["id"], "failed", error_code="approval_execution_failed", result=outcome
            )
        self.transition(turn["id"], "verifying", event_type="turn.verification_started")
        verification = outcome.get("verification", {})
        verification_result = verification.get("verified")
        passed = outcome.get("status") == "completed" and verification_result is not False
        return self.transition(
            turn["id"],
            "completed" if passed else "failed",
            event_type="turn.verification_completed",
            details={"verified": verification_result, "verification": verification},
            result={"approval_id": approval_id, "outcome": outcome, "verified": verification_result},
            error_code=None if passed else "postcondition_not_verified",
        )
