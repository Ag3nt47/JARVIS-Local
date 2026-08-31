from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from jarvis.audit import AuditLog
from jarvis.config import Settings
from jarvis.database import Database
from jarvis.services import ApprovalService, NotificationService, TaskService
from jarvis.skills import CORE_SAFETY, SKILLS, SkillRegistry
from jarvis.tools import (
    ClickControlArgs,
    CommandRunner,
    DesktopAutomation,
    FilePolicy,
    FindFilesArgs,
    ReadFileArgs,
    ReplaceTextArgs,
    RunCommandArgs,
    SafeFileTools,
    ToolError,
    ToolRegistry,
)


class FakeMemory:
    async def search(self, query: str, limit: int) -> list[dict[str, Any]]:
        return [{"query": query, "limit": limit}]


class FakeTelemetry:
    def snapshot(self) -> dict[str, bool]:
        return {"healthy": True}


def test_desktop_click_resolves_one_exact_descendant(monkeypatch) -> None:
    class Element:
        automation_id = "settings-button"
        control_type = "Button"

    class Control:
        element_info = Element()
        clicked = False

        @staticmethod
        def window_text() -> str:
            return "Open settings"

        @staticmethod
        def is_visible() -> bool:
            return True

        @staticmethod
        def is_enabled() -> bool:
            return True

        def click_input(self) -> None:
            self.clicked = True

    control = Control()

    class Window:
        @staticmethod
        def descendants(**criteria):
            assert criteria == {"control_type": "Button"}
            return [control]

    class Desktop:
        @staticmethod
        def windows(**criteria):
            assert criteria == {
                "title": "JARVIS",
                "visible_only": True,
                "enabled_only": True,
            }
            return [Window()]

    monkeypatch.setattr(DesktopAutomation, "_desktop", staticmethod(lambda: Desktop()))
    result = DesktopAutomation().click_control(
        ClickControlArgs(
            window_title="JARVIS",
            control_name="Open settings",
            automation_id="settings-button",
            control_type="Button",
        )
    )

    assert control.clicked is True
    assert result["clicked"] is True
    assert result["control_type"] == "Button"


def make_tool_registry(
    settings: Settings, database: Database, audit: AuditLog
) -> tuple[ToolRegistry, ApprovalService]:
    approvals = ApprovalService(database, audit, settings.approval_ttl_seconds)
    return (
        ToolRegistry(
            settings,
            audit,
            approvals,
            FakeMemory(),
            TaskService(database, audit),
            NotificationService(database, settings),
            FakeTelemetry(),
        ),
        approvals,
    )


def test_file_policy_blocks_traversal_and_hidden_paths(settings: Settings) -> None:
    root = settings.allowed_roots[0]
    outside = root.parent / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    hidden = root / ".secret"
    hidden.write_text("private", encoding="utf-8")
    policy = FilePolicy(settings)

    with pytest.raises(ToolError, match="outside configured"):
        policy.resolve("../outside.txt")
    with pytest.raises(ToolError, match="Hidden paths"):
        policy.resolve(str(hidden))


def test_file_policy_blocks_symlink_escape(settings: Settings) -> None:
    root = settings.allowed_roots[0]
    outside = root.parent / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("private", encoding="utf-8")
    link = root / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable: {exc}")

    with pytest.raises(ToolError, match="outside configured"):
        FilePolicy(settings).resolve(str(link / "secret.txt"))


def test_safe_file_read_is_bounded_utf8_only(settings: Settings) -> None:
    root = settings.allowed_roots[0]
    text_file = root / "lines.txt"
    text_file.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    files = SafeFileTools(settings)

    result = files.read_file(ReadFileArgs(path=str(text_file), start_line=2, max_lines=2))

    assert result["content"] == "two\nthree"
    assert result["start_line"] == 2
    assert result["end_line"] == 3
    assert result["truncated"] is True

    binary = root / "binary.bin"
    binary.write_bytes(b"\xff\xfe")
    with pytest.raises(ToolError, match="UTF-8"):
        files.read_file(ReadFileArgs(path=str(binary)))


def test_spatial_file_discovery_is_name_only_root_bounded(settings: Settings) -> None:
    root = settings.allowed_roots[0]
    (root / "plans").mkdir()
    (root / "plans" / "angerona-tactic.json").write_text("secret body", encoding="utf-8")
    (root / "notes.txt").write_text("angerona-tactic appears only in content", encoding="utf-8")

    result = SafeFileTools(settings).find_files(
        FindFilesArgs(path=str(root), query="tactic", extension="json")
    )

    assert [item["name"] for item in result["matches"]] == ["angerona-tactic.json"]
    assert "secret body" not in str(result)
    with pytest.raises(ToolError, match="outside configured"):
        SafeFileTools(settings).find_files(
            FindFilesArgs(path=str(root.parent), query="anything")
        )


def test_exact_text_edit_creates_rollback_backup_and_rejects_stale_input(
    settings: Settings,
) -> None:
    target = settings.allowed_roots[0] / "module.py"
    target.write_text("value = 1\n", encoding="utf-8")
    files = SafeFileTools(settings)

    result = files.replace_text(
        ReplaceTextArgs(path=str(target), old_text="value = 1", new_text="value = 2")
    )

    assert target.read_text(encoding="utf-8") == "value = 2\n"
    backup = Path(result["backup_path"])
    assert backup.read_text(encoding="utf-8") == "value = 1\n"
    assert result["before_sha256"] != result["after_sha256"]
    with pytest.raises(ToolError, match="precondition failed"):
        files.replace_text(
            ReplaceTextArgs(path=str(target), old_text="value = 1", new_text="value = 3")
        )


@pytest.mark.asyncio
async def test_atomic_write_requires_approval_and_cannot_replay(
    settings: Settings,
    database: Database,
    audit: AuditLog,
    monkeypatch,
) -> None:
    registry, approvals = make_tool_registry(settings, database, audit)
    target = settings.allowed_roots[0] / "approved.txt"
    replacements: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def recording_replace(source: str | Path, destination: str | Path) -> None:
        replacements.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr("jarvis.tools.os.replace", recording_replace)

    queued = await registry.execute(
        "write_file", {"path": str(target), "content": "approved", "overwrite": False}
    )

    assert queued["status"] == "approval_required"
    assert target.exists() is False
    request_id = queued["approval"]["id"]
    assert approvals.get(request_id)["status"] == "pending"

    completed = await registry.execute_approval(request_id, actor="user")

    assert completed["status"] == "completed"
    assert target.read_text(encoding="utf-8") == "approved"
    assert approvals.get(request_id)["status"] == "approved"
    assert len(replacements) == 1
    temporary, destination = replacements[0]
    assert temporary.parent == target.parent
    assert temporary.name.startswith(".jarvis-")
    assert destination == target
    assert temporary.exists() is False

    with pytest.raises(ValueError, match="already approved"):
        await registry.execute_approval(request_id, actor="user")


@pytest.mark.asyncio
async def test_exact_text_edit_requires_approval(
    settings: Settings, database: Database, audit: AuditLog
) -> None:
    registry, _ = make_tool_registry(settings, database, audit)
    target = settings.allowed_roots[0] / "approved.py"
    target.write_text("answer = 41\n", encoding="utf-8")

    queued = await registry.execute(
        "replace_text",
        {"path": str(target), "old_text": "41", "new_text": "42"},
        risk_tier="high",
    )

    assert queued["status"] == "approval_required"
    assert target.read_text(encoding="utf-8") == "answer = 41\n"
    completed = await registry.execute_approval(queued["approval"]["id"], actor="user")
    assert completed["result"]["replacements"] == 1
    assert target.read_text(encoding="utf-8") == "answer = 42\n"


@pytest.mark.asyncio
async def test_tool_registry_rejects_unknown_and_invalid_arguments(
    settings: Settings, database: Database, audit: AuditLog
) -> None:
    registry, _ = make_tool_registry(settings, database, audit)

    with pytest.raises(ToolError, match="not in the capability allowlist"):
        await registry.execute("shell", {})
    with pytest.raises(ToolError, match="Invalid arguments"):
        await registry.execute("read_file", {"path": "x", "unexpected": True})
    with pytest.raises(ToolError, match="JSON object"):
        await registry.execute("read_file", "not-an-object")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_guarded_skill_policy_approves_all_state_changes(
    settings: Settings, database: Database, audit: AuditLog
) -> None:
    registry, approvals = make_tool_registry(settings, database, audit)

    queued = await registry.execute(
        "create_task",
        {"title": "Guarded task"},
        risk_tier="guarded",
    )

    assert queued["status"] == "approval_required"
    assert "guarded risk policy" in queued["approval"]["reason"]
    assert database.query_all("SELECT * FROM tasks") == []

    completed = await registry.execute_approval(queued["approval"]["id"], actor="user")

    assert completed["status"] == "completed"
    assert approvals.get(queued["approval"]["id"])["status"] == "approved"
    assert database.query_one("SELECT title FROM tasks")["title"] == "Guarded task"


def test_command_runner_is_allowlisted_scoped_and_shell_free(
    settings: Settings, monkeypatch
) -> None:
    runner = CommandRunner(settings)
    calls: list[dict[str, Any]] = []

    def completed(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return subprocess.CompletedProcess(command, 0, stdout="Python test\n", stderr="")

    monkeypatch.setattr(
        CommandRunner,
        "_resolve_executable",
        staticmethod(lambda name: rf"C:\Tools\{name}.exe"),
    )
    monkeypatch.setattr("jarvis.tools.subprocess.run", completed)
    outcome = runner.run(
        RunCommandArgs(
            executable="python",
            arguments=["--version"],
            cwd=str(settings.allowed_roots[0]),
        )
    )

    assert outcome["exit_code"] == 0
    assert calls[0]["shell"] is False
    assert calls[0]["command"] == [r"C:\Tools\python.exe", "--version"]
    with pytest.raises(ToolError, match="inline code"):
        runner.run(
            RunCommandArgs(
                executable="python",
                arguments=["-c", "print('unsafe')"],
                cwd=str(settings.allowed_roots[0]),
            )
        )
    with pytest.raises(ToolError, match="capability list"):
        runner.run(
            RunCommandArgs(
                executable="powershell",
                arguments=["-Command", "Get-Process"],
                cwd=str(settings.allowed_roots[0]),
            )
        )
    runner.run(
        RunCommandArgs(
            executable="git",
            arguments=["status", "--short"],
            cwd=str(settings.allowed_roots[0]),
        )
    )
    with pytest.raises(ToolError, match="Git subcommand"):
        runner.run(
            RunCommandArgs(
                executable="git",
                arguments=["reset", "--hard"],
                cwd=str(settings.allowed_roots[0]),
            )
        )


@pytest.mark.asyncio
async def test_command_and_click_require_explicit_approval(
    settings: Settings, database: Database, audit: AuditLog
) -> None:
    registry, _ = make_tool_registry(settings, database, audit)

    command = await registry.execute(
        "run_command",
        {
            "executable": "python",
            "arguments": ["--version"],
            "cwd": str(settings.allowed_roots[0]),
        },
    )
    click = await registry.execute(
        "click_control",
        {"window_title": "Calculator", "control_name": "One"},
    )
    monitor = await registry.execute("open_world_monitor", {})

    assert command["status"] == "approval_required"
    assert click["status"] == "approval_required"
    assert monitor["status"] == "approval_required"
    assert registry.catalog()[0]["name"] == "list_windows"


@pytest.mark.asyncio
async def test_clipboard_read_is_bounded_and_requires_explicit_approval(
    settings: Settings, database: Database, audit: AuditLog
) -> None:
    registry, _ = make_tool_registry(settings, database, audit)

    queued = await registry.execute("read_clipboard", {"max_characters": 500})

    assert queued["status"] == "approval_required"
    assert "sensitive local data" in queued["approval"]["reason"]
    clipboard = next(item for item in registry.catalog() if item["name"] == "read_clipboard")
    assert clipboard["requires_approval"] is True
    assert clipboard["changes_state"] is False


def test_skill_catalog_has_unique_ids_and_constrained_tool_scopes(
    settings: Settings, database: Database, audit: AuditLog
) -> None:
    registry = SkillRegistry()
    tools, _ = make_tool_registry(settings, database, audit)
    skill_ids = [skill.id for skill in SKILLS]

    assert len(skill_ids) == len(set(skill_ids))
    assert "untrusted data" in CORE_SAFETY
    assert "approval" in CORE_SAFETY
    assert "write_file" not in registry.get("engineering_debug").allowed_tools
    assert "write_file" not in registry.get("defensive_sentry").allowed_tools
    assert "write_file" in registry.get("safe_automation").allowed_tools
    assert "click_control" in registry.get("safe_automation").allowed_tools
    assert "read_clipboard" in registry.get("safe_automation").allowed_tools
    assert "run_command" in registry.get("safe_automation").allowed_tools
    assert registry.get("safe_automation").risk_tier == "guarded"
    software_engineer = registry.get("software_engineer")
    assert "replace_text" in software_engineer.allowed_tools
    assert "write_file" in software_engineer.allowed_tools
    assert "run_command" in software_engineer.allowed_tools
    assert software_engineer.risk_tier == "high"
    live_briefing = registry.get("live_briefing")
    assert "get_world_news" in live_briefing.allowed_tools
    assert "open_world_monitor" in live_briefing.allowed_tools
    assert live_briefing.risk_tier == "guarded"

    for skill in SKILLS:
        exposed_names = {
            schema["function"]["name"] for schema in tools.schemas_for(skill.allowed_tools)
        }
        assert exposed_names == set(skill.allowed_tools)

    with pytest.raises(KeyError, match="Unknown skill"):
        registry.get("untrusted_plugin")
