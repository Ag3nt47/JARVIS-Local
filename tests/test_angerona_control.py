from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.angerona_control import (
    AngeronaControlError,
    AngeronaControlService,
    AngeronaTacticService,
)


def test_angerona_tactics_are_versioned_disabled_by_default_and_bounded(database, audit) -> None:
    tactics = AngeronaTacticService(database, audit)
    first = tactics.save(
        name="Review quarantine",
        description="Inspect the quarantine pane.",
        steps=[{"action": "focus"}, {"action": "send_keys", "keys": "CTRL+F"}],
        preconditions=["Angerona window is verified"],
        success_evidence=["Quarantine pane is visible"],
    )
    second = tactics.save(
        name="Review quarantine",
        description="Inspect and search the quarantine pane.",
        steps=[{"action": "focus"}],
        preconditions=[],
        success_evidence=[],
        enabled=True,
    )

    assert first["enabled"] is False
    assert second["id"] == first["id"]
    assert second["version"] == 2
    assert second["enabled"] is True
    assert "steps_json" not in second
    with pytest.raises(ValueError, match="unsupported fields"):
        tactics.save(
            name="Unsafe",
            description="",
            steps=[{"action": "invoke", "coordinates": [1, 2]}],
            preconditions=[],
            success_evidence=[],
        )


def test_angerona_window_requires_exact_title_and_process_inside_root(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "AngeronaSuite"
    root.mkdir()
    executable = root / "angerona.exe"
    executable.write_bytes(b"")
    service = AngeronaControlService()
    service.root = root

    class Element:
        process_id = 4147

    class Window:
        element_info = Element()

        @staticmethod
        def window_text() -> str:
            return "Angerona Defense"

        @staticmethod
        def descendants():
            return []

    class Desktop:
        @staticmethod
        def windows(**criteria):
            assert criteria["title"] == "Angerona Defense"
            return [Window()]

    class Process:
        @staticmethod
        def exe() -> str:
            return str(executable)

        @staticmethod
        def cmdline() -> list[str]:
            return [str(executable)]

    monkeypatch.setattr(service, "_desktop", lambda: Desktop())
    monkeypatch.setattr("jarvis.angerona_control.psutil.Process", lambda _pid: Process())

    result = service.inspect("Angerona Defense")

    assert result["process"]["process_id"] == 4147
    with pytest.raises(AngeronaControlError, match="does not identify"):
        service.inspect("Calculator")
    service.emergency_stop()
    with pytest.raises(AngeronaControlError, match="emergency stop"):
        service.inspect("Angerona Defense")
