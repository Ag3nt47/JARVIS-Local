from __future__ import annotations

import pytest

from jarvis.orchestration import OrchestrationService, TurnCancelled


def test_turn_lifecycle_is_durable_and_transition_checked(database, audit) -> None:
    service = OrchestrationService(database, audit)
    turn = service.begin("executive_assistant", channel="voice")
    service.transition(turn["id"], "thinking")
    service.transition(turn["id"], "speaking")
    completed = service.transition(turn["id"], "completed", result={"message": "ready"})

    assert completed["state"] == "completed"
    assert completed["result"] == {"message": "ready"}
    assert [event["state"] for event in completed["events"]] == [
        "received",
        "thinking",
        "speaking",
        "completed",
    ]
    with pytest.raises(ValueError, match="Invalid turn transition"):
        service.transition(turn["id"], "thinking")


def test_cancellation_is_requested_then_consumed_at_checkpoint(database, audit) -> None:
    service = OrchestrationService(database, audit)
    turn = service.begin("executive_assistant")
    service.transition(turn["id"], "thinking")
    requested = service.request_cancel(turn["id"])
    assert requested["cancel_requested"] is True

    with pytest.raises(TurnCancelled):
        service.checkpoint(turn["id"])
    assert service.get(turn["id"])["state"] == "cancelled"


def test_approval_resume_runs_verification_hook(database, audit) -> None:
    service = OrchestrationService(database, audit)
    turn = service.begin("safe_automation")
    service.transition(turn["id"], "thinking")
    service.transition(turn["id"], "awaiting_approval", approval_id="approval-1")

    resumed = service.resume_approval(
        "approval-1", {"status": "completed", "result": {"ok": True}}, succeeded=True
    )

    assert resumed is not None
    assert resumed["state"] == "completed"
    assert resumed["result"]["verified"] is None
    assert service.metrics()["turns_by_state"]["completed"] == 1
