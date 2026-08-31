"""Strictly allowlisted agent tools and filesystem policy enforcement."""

from __future__ import annotations

import base64
import ctypes
import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Awaitable, Callable
from ctypes import wintypes
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .audit import AuditLog
from .config import Settings
from .live_briefing import (
    LiveBriefingError,
    LiveBriefingService,
    current_time,
    format_json,
    system_info,
    word_count,
)
from .services import (
    ApprovalService,
    MemoryService,
    NotificationService,
    TaskService,
    TelemetryService,
)


class ToolError(RuntimeError):
    """A safe, user-presentable tool execution failure."""


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PathArgs(Arguments):
    path: str = Field(min_length=1, max_length=4096)


class ReadFileArgs(PathArgs):
    start_line: int = Field(default=1, ge=1, le=10_000_000)
    max_lines: int = Field(default=500, ge=1, le=2000)


class ListDirectoryArgs(PathArgs):
    max_entries: int = Field(default=200, ge=1, le=1000)


class SearchFilesArgs(PathArgs):
    query: str = Field(min_length=1, max_length=500)
    pattern: str = Field(default="*", min_length=1, max_length=100)
    case_sensitive: bool = False


class FindFilesArgs(PathArgs):
    query: str = Field(default="", max_length=300)
    extension: str | None = Field(default=None, pattern=r"^\.?[A-Za-z0-9_-]{1,20}$")
    max_results: int = Field(default=60, ge=1, le=100)


class RevealFileArgs(PathArgs):
    pass


class WriteFileArgs(PathArgs):
    content: str = Field(max_length=1_000_000)
    overwrite: bool = False


class ReplaceTextArgs(PathArgs):
    old_text: str = Field(min_length=1, max_length=262_144)
    new_text: str = Field(max_length=1_000_000)
    expected_replacements: int = Field(default=1, ge=1, le=100)


class MemorySearchArgs(Arguments):
    query: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=5, ge=1, le=20)


class CreateTaskArgs(Arguments):
    title: str = Field(min_length=1, max_length=300)
    details: str = Field(default="", max_length=10_000)
    priority: int = Field(default=2, ge=1, le=5)
    due_at: str | None = Field(default=None, max_length=64)
    dedupe_key: str | None = Field(default=None, max_length=200)


class ListTasksArgs(Arguments):
    state: str | None = Field(default=None, pattern="^(open|in_progress|blocked|done|cancelled)$")
    limit: int = Field(default=20, ge=1, le=100)


class NotifyArgs(Arguments):
    title: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=4000)
    level: str = Field(default="info", pattern="^(info|warning|error)$")
    dedupe_key: str | None = Field(default=None, max_length=200)


class NoArgs(Arguments):
    pass


class FormatJsonArgs(Arguments):
    data: str = Field(min_length=1, max_length=100_000)


class WordCountArgs(Arguments):
    text: str = Field(max_length=200_000)


class ListWindowsArgs(Arguments):
    max_windows: int = Field(default=50, ge=1, le=100)


class ReadClipboardArgs(Arguments):
    max_characters: int = Field(default=20_000, ge=1, le=100_000)


class ClickControlArgs(Arguments):
    window_title: str = Field(min_length=1, max_length=300)
    control_name: str = Field(min_length=1, max_length=300)
    automation_id: str | None = Field(default=None, min_length=1, max_length=300)
    control_type: str | None = Field(default=None, min_length=1, max_length=100)


class AngeronaInspectArgs(Arguments):
    window_title: str = Field(min_length=1, max_length=300)
    limit: int = Field(default=150, ge=1, le=300)


class AngeronaActionArgs(Arguments):
    window_title: str = Field(min_length=1, max_length=300)
    action: Literal["focus", "move_to_control", "invoke", "set_text", "send_keys", "scroll"]
    control_name: str | None = Field(default=None, min_length=1, max_length=300)
    automation_id: str | None = Field(default=None, min_length=1, max_length=300)
    control_type: str | None = Field(default=None, min_length=1, max_length=100)
    text: str | None = Field(default=None, max_length=2000)
    keys: str | None = Field(default=None, min_length=1, max_length=30)
    scroll_steps: int = Field(default=0, ge=-20, le=20)


class AngeronaTacticArgs(Arguments):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    steps: list[dict[str, Any]] = Field(min_length=1, max_length=20)
    preconditions: list[str] = Field(default_factory=list, max_length=20)
    success_evidence: list[str] = Field(default_factory=list, max_length=20)
    enabled: bool = False


class RunCommandArgs(Arguments):
    executable: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    arguments: list[str] = Field(default_factory=list, max_length=24)
    cwd: str = Field(min_length=1, max_length=4096)
    timeout_seconds: int = Field(default=120, ge=1, le=600)


class DesktopAutomation:
    """Exact UI Automation targets; never accepts screen coordinates or image clicks."""

    @staticmethod
    def _desktop() -> Any:
        try:
            importlib.import_module("comtypes.gen")
            from pywinauto import Desktop
        except ImportError as exc:
            raise ToolError("Windows UI Automation is unavailable in this installation") from exc
        return Desktop(backend="uia")

    def list_windows(self, arguments: ListWindowsArgs) -> dict[str, Any]:
        windows: list[dict[str, Any]] = []
        try:
            candidates = self._desktop().windows(visible_only=True, enabled_only=True)
        except Exception as exc:
            raise ToolError("Windows UI Automation could not enumerate windows") from exc
        for candidate in candidates:
            if len(windows) >= arguments.max_windows:
                break
            try:
                title = candidate.window_text().strip()
                if not title:
                    continue
                element = candidate.element_info
                windows.append(
                    {
                        "title": title[:300],
                        "handle": int(element.handle or 0),
                        "process_id": int(element.process_id or 0),
                    }
                )
            except (AttributeError, RuntimeError, OSError, TypeError, ValueError):
                continue
        return {"windows": windows, "truncated": len(candidates) > len(windows)}

    def _click_control_exact(self, arguments: ClickControlArgs) -> dict[str, Any]:
        try:
            desktop = self._desktop()
            matches = desktop.windows(
                title=arguments.window_title,
                visible_only=True,
                enabled_only=True,
            )
            if len(matches) != 1:
                raise ToolError(
                    "The exact window title did not resolve to one visible, enabled window"
                )
            window = matches[0]
            candidates = window.descendants(
                **({"control_type": arguments.control_type} if arguments.control_type else {})
            )
            controls = []
            for candidate in candidates:
                element = candidate.element_info
                if candidate.window_text() != arguments.control_name:
                    continue
                if arguments.automation_id and element.automation_id != arguments.automation_id:
                    continue
                if arguments.control_type and element.control_type != arguments.control_type:
                    continue
                controls.append(candidate)
            if len(controls) != 1:
                raise ToolError(
                    "The exact control name did not resolve to one target in the selected window"
                )
            control = controls[0]
            if not control.is_visible() or not control.is_enabled():
                raise ToolError("The exact control is not currently visible and enabled")
            control.click_input()
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError("The approved control click failed safely") from exc
        return {
            "window_title": arguments.window_title,
            "control_name": arguments.control_name,
            "automation_id": arguments.automation_id,
            "control_type": control.element_info.control_type,
            "clicked": True,
        }

    def click_control(self, arguments: ClickControlArgs) -> dict[str, Any]:
        if not getattr(sys, "frozen", False):
            return self._click_control_exact(arguments)
        system_root = Path(os.getenv("SystemRoot", r"C:\Windows")).resolve()
        powershell = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
        helper = bundle_root / "desktop" / "uia_click.ps1"
        if not powershell.is_file() or not helper.is_file():
            raise ToolError("The approved Windows UI Automation helper is unavailable")

        def encode(value: str | None) -> str:
            return base64.b64encode((value or "").encode("utf-8")).decode("ascii")

        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            completed = subprocess.run(
                [
                    str(powershell),
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(helper),
                    "-WindowTitleBase64",
                    encode(arguments.window_title),
                    "-ControlNameBase64",
                    encode(arguments.control_name),
                    "-AutomationIdBase64",
                    encode(arguments.automation_id),
                    "-ControlTypeBase64",
                    encode(arguments.control_type),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=25,
                check=False,
                shell=False,
                creationflags=creation_flags,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ToolError("The approved click helper failed safely") from exc
        if completed.returncode != 0:
            raise ToolError("The exact UI Automation target could not be invoked")
        try:
            result = json.loads(completed.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ToolError("The approved click helper returned an invalid result") from exc
        if not isinstance(result, dict) or result.get("clicked") is not True:
            raise ToolError("The approved click helper returned an invalid result")
        return result


class ClipboardTools:
    """Read bounded Unicode clipboard text without persisting clipboard history."""

    CF_UNICODETEXT = 13

    @staticmethod
    def read(arguments: ReadClipboardArgs) -> dict[str, Any]:
        if os.name != "nt":
            raise ToolError("Clipboard reading is supported only on Windows")
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = wintypes.HANDLE
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = wintypes.BOOL
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = wintypes.LPVOID
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        if not user32.OpenClipboard(None):
            raise ToolError("Windows clipboard is busy; try again")
        handle = None
        pointer = None
        try:
            handle = user32.GetClipboardData(ClipboardTools.CF_UNICODETEXT)
            if not handle:
                return {"text": "", "characters": 0, "truncated": False}
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                raise ToolError("Windows clipboard text could not be read")
            text = ctypes.wstring_at(pointer)
        finally:
            if pointer and handle:
                kernel32.GlobalUnlock(handle)
            user32.CloseClipboard()
        bounded = text[: arguments.max_characters]
        return {
            "text": bounded,
            "characters": len(text),
            "truncated": len(bounded) < len(text),
        }


class CommandRunner:
    """Run an approved executable directly, without a shell or free-form command string."""

    ALLOWED_EXECUTABLES: ClassVar[frozenset[str]] = frozenset({
        "git",
        "git.exe",
        "go",
        "go.exe",
        "java",
        "java.exe",
        "javac",
        "javac.exe",
        "node",
        "node.exe",
        "ollama",
        "ollama.exe",
        "py",
        "py.exe",
        "pytest",
        "pytest.exe",
        "python",
        "python.exe",
        "python3",
        "python3.exe",
        "ruff",
        "ruff.exe",
        "rustc",
        "rustc.exe",
        "cargo",
        "cargo.exe",
        "dotnet",
        "dotnet.exe",
    })
    ALLOWED_GIT_SUBCOMMANDS: ClassVar[frozenset[str]] = frozenset({
        "add",
        "commit",
        "diff",
        "grep",
        "init",
        "log",
        "ls-files",
        "rev-parse",
        "show",
        "status",
    })
    OUTPUT_LIMIT = 32_000

    def __init__(self, settings: Settings) -> None:
        self.policy = FilePolicy(settings)

    @staticmethod
    def _resolve_executable(name: str) -> str | None:
        candidates: list[Path] = []
        discovered = shutil.which(name)
        if discovered:
            candidates.append(Path(discovered))
        if not getattr(sys, "frozen", False) and Path(sys.executable).name.casefold().startswith(
            "python"
        ):
            if name.casefold().startswith(("py", "python")):
                candidates.append(Path(sys.executable))
            else:
                candidates.append(Path(sys.executable).parent / f"{name.removesuffix('.exe')}.exe")
        local_app_data = os.getenv("LOCALAPPDATA")
        if local_app_data and name.casefold().startswith(("py", "python")):
            candidates.append(Path(local_app_data) / "Python" / "bin" / "python.exe")
        for candidate in candidates:
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                continue
            if "microsoft\\windowsapps" in str(resolved).casefold():
                continue
            if resolved.is_file():
                return str(resolved)
        return None

    def _validate_python(self, executable: str, arguments: list[str]) -> None:
        if not executable.casefold().startswith(("py", "python")):
            return
        forbidden = {"-c", "-m", "-", "/c"}
        if any(argument.casefold() in forbidden for argument in arguments):
            raise ToolError("Python inline code, modules, and standard-input execution are blocked")
        positional = [
            argument
            for argument in arguments
            if not argument.startswith("-") and not argument.startswith("/")
        ]
        if positional:
            script = self.policy.resolve(positional[0])
            if not script.is_file() or script.suffix.casefold() != ".py":
                raise ToolError("Python may only run an existing .py file in an allowed root")

    @staticmethod
    def _validate_arguments(executable: str, arguments: list[str]) -> None:
        for argument in arguments:
            if len(argument) > 512 or "\x00" in argument or "\r" in argument or "\n" in argument:
                raise ToolError("Command arguments must be short, single-line values")
        if executable.startswith("git"):
            if not arguments:
                raise ToolError("Git requires an explicitly allowlisted subcommand")
            subcommand = arguments[0].casefold()
            if subcommand == "--version":
                return
            if subcommand not in CommandRunner.ALLOWED_GIT_SUBCOMMANDS:
                raise ToolError("Git subcommand is outside the approved coding capability list")

    def run(self, arguments: RunCommandArgs) -> dict[str, Any]:
        executable_name = arguments.executable.casefold()
        if executable_name not in self.ALLOWED_EXECUTABLES:
            raise ToolError("Executable is outside the approved command capability list")
        command_arguments = list(arguments.arguments)
        self._validate_python(executable_name, command_arguments)
        self._validate_arguments(executable_name, command_arguments)
        cwd = self.policy.resolve(arguments.cwd)
        if not cwd.is_dir():
            raise ToolError("Command working directory is not a directory")
        resolved_executable = self._resolve_executable(arguments.executable)
        if resolved_executable is None:
            raise ToolError("Approved executable is not installed or is not on PATH")
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            completed = subprocess.run(
                [resolved_executable, *command_arguments],
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=arguments.timeout_seconds,
                check=False,
                shell=False,
                creationflags=creation_flags,
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolError("The approved command exceeded its timeout and was stopped") from exc
        except OSError as exc:
            raise ToolError("The approved command could not start") from exc
        stdout = completed.stdout[-self.OUTPUT_LIMIT :]
        stderr = completed.stderr[-self.OUTPUT_LIMIT :]
        return {
            "executable": Path(resolved_executable).name,
            "arguments": command_arguments,
            "cwd": str(cwd),
            "exit_code": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "output_truncated": len(completed.stdout) > len(stdout)
            or len(completed.stderr) > len(stderr),
        }


class FilePolicy:
    """Resolve paths only inside configured roots, including through symlinks."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def resolve(self, requested: str, *, must_exist: bool = True) -> Path:
        raw = Path(requested).expanduser()
        if not raw.is_absolute():
            raw = self.settings.allowed_roots[0] / raw
        try:
            resolved = raw.resolve(strict=must_exist)
        except (OSError, RuntimeError) as exc:
            raise ToolError(f"Cannot resolve path: {requested}") from exc
        matching_root = next(
            (root for root in self.settings.allowed_roots if resolved == root or resolved.is_relative_to(root)),
            None,
        )
        if matching_root is None:
            raise ToolError("Path is outside configured workspace roots")
        if self.settings.deny_hidden_paths:
            relative = resolved.relative_to(matching_root)
            if any(part.startswith(".") for part in relative.parts):
                raise ToolError("Hidden paths are blocked by policy")
        return resolved


class SafeFileTools:
    """Bounded text-only file operations with atomic approved writes."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.policy = FilePolicy(settings)

    def _backup(self, path: Path) -> Path:
        backup_root = self.settings.data_dir / "code_backups"
        backup_root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        path_digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:12]
        backup = backup_root / f"{timestamp}-{path_digest}-{uuid4().hex[:8]}-{path.name}.bak"
        try:
            shutil.copy2(path, backup)
        except OSError as exc:
            raise ToolError("A rollback backup could not be created; no edit was applied") from exc
        return backup

    @staticmethod
    def _atomic_write(path: Path, encoded: bytes, error_message: str) -> None:
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=path.parent, prefix=".jarvis-", suffix=".tmp", delete=False
            ) as handle:
                temporary_name = handle.name
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
        except OSError as exc:
            if temporary_name:
                try:
                    Path(temporary_name).unlink(missing_ok=True)
                except OSError:
                    pass
            raise ToolError(error_message) from exc

    def read_file(self, arguments: ReadFileArgs) -> dict[str, Any]:
        path = self.policy.resolve(arguments.path)
        if not path.is_file():
            raise ToolError("Path is not a file")
        size = path.stat().st_size
        if size > self.settings.max_file_bytes:
            raise ToolError(f"File exceeds the {self.settings.max_file_bytes}-byte read limit")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError("Only UTF-8 text files can be read") from exc
        lines = text.splitlines()
        start = arguments.start_line - 1
        selected = lines[start : start + arguments.max_lines]
        return {
            "path": str(path),
            "content": "\n".join(selected),
            "start_line": arguments.start_line,
            "end_line": start + len(selected),
            "total_lines": len(lines),
            "truncated": start + len(selected) < len(lines),
        }

    def list_directory(self, arguments: ListDirectoryArgs) -> dict[str, Any]:
        path = self.policy.resolve(arguments.path)
        if not path.is_dir():
            raise ToolError("Path is not a directory")
        entries: list[dict[str, Any]] = []
        for child in sorted(path.iterdir(), key=lambda item: item.name.casefold()):
            if self.settings.deny_hidden_paths and child.name.startswith("."):
                continue
            if len(entries) >= arguments.max_entries:
                break
            try:
                resolved = self.policy.resolve(str(child))
                stat = resolved.stat()
            except (OSError, ToolError):
                continue
            entries.append(
                {
                    "name": child.name,
                    "path": str(resolved),
                    "type": "directory" if resolved.is_dir() else "file",
                    "size_bytes": stat.st_size if resolved.is_file() else None,
                }
            )
        return {"path": str(path), "entries": entries, "truncated": len(entries) >= arguments.max_entries}

    def search_files(self, arguments: SearchFilesArgs) -> dict[str, Any]:
        root = self.policy.resolve(arguments.path)
        if not root.is_dir():
            raise ToolError("Search path is not a directory")
        needle = arguments.query if arguments.case_sensitive else arguments.query.casefold()
        matches: list[dict[str, Any]] = []
        try:
            candidates = root.rglob(arguments.pattern)
            for candidate in candidates:
                if len(matches) >= self.settings.max_search_results:
                    break
                try:
                    path = self.policy.resolve(str(candidate))
                    if not path.is_file() or path.stat().st_size > self.settings.max_file_bytes:
                        continue
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError, ToolError):
                    continue
                for line_number, line in enumerate(text.splitlines(), 1):
                    haystack = line if arguments.case_sensitive else line.casefold()
                    if needle in haystack:
                        matches.append(
                            {"path": str(path), "line": line_number, "text": line[:500]}
                        )
                        if len(matches) >= self.settings.max_search_results:
                            break
        except (OSError, ValueError) as exc:
            raise ToolError("Invalid or inaccessible search pattern") from exc
        return {
            "query": arguments.query,
            "matches": matches,
            "truncated": len(matches) >= self.settings.max_search_results,
        }

    def find_files(self, arguments: FindFilesArgs) -> dict[str, Any]:
        """Find file and directory names without reading file contents."""

        root = self.policy.resolve(arguments.path)
        if not root.is_dir():
            raise ToolError("File discovery path is not a directory")
        query = arguments.query.casefold().strip()
        extension = arguments.extension
        if extension:
            extension = extension.casefold()
            if not extension.startswith("."):
                extension = f".{extension}"
        matches: list[dict[str, Any]] = []
        scanned = 0
        scan_limit = 10_000
        try:
            for candidate in root.rglob("*"):
                scanned += 1
                if scanned > scan_limit or len(matches) >= arguments.max_results:
                    break
                try:
                    path = self.policy.resolve(str(candidate))
                    relative = path.relative_to(root)
                    if query and query not in str(relative).casefold():
                        continue
                    if extension and (not path.is_file() or path.suffix.casefold() != extension):
                        continue
                    stat = path.stat()
                except (OSError, ToolError, ValueError):
                    continue
                matches.append(
                    {
                        "name": path.name,
                        "path": str(path),
                        "relative_path": str(relative),
                        "type": "directory" if path.is_dir() else "file",
                        "extension": path.suffix.casefold() if path.is_file() else None,
                        "size_bytes": stat.st_size if path.is_file() else None,
                        "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                    }
                )
        except OSError as exc:
            raise ToolError("File discovery could not traverse the selected root") from exc
        return {
            "root": str(root),
            "query": arguments.query,
            "matches": matches,
            "scanned": min(scanned, scan_limit),
            "truncated": scanned > scan_limit or len(matches) >= arguments.max_results,
        }

    def reveal_file(self, arguments: RevealFileArgs) -> dict[str, Any]:
        """Reveal one exact allowed path in Explorer after approval."""

        path = self.policy.resolve(arguments.path)
        if os.name != "nt":
            raise ToolError("File reveal is currently available only on Windows")
        system_root = Path(os.getenv("SystemRoot", r"C:\Windows")).resolve()
        try:
            explorer = (system_root / "explorer.exe").resolve(strict=True)
        except OSError as exc:
            raise ToolError("Windows Explorer could not be verified") from exc
        command = (
            [str(explorer), "/select,", str(path)]
            if path.is_file()
            else [str(explorer), str(path)]
        )
        try:
            subprocess.Popen(command, shell=False, close_fds=True)
        except OSError as exc:
            raise ToolError("The approved file reveal failed safely") from exc
        return {"path": str(path), "revealed": True}

    def write_file(self, arguments: WriteFileArgs) -> dict[str, Any]:
        path = self.policy.resolve(arguments.path, must_exist=False)
        parent = self.policy.resolve(str(path.parent))
        if not parent.is_dir():
            raise ToolError("Parent directory does not exist")
        encoded = arguments.content.encode("utf-8")
        if len(encoded) > self.settings.max_file_write_bytes:
            raise ToolError(
                f"Content exceeds the {self.settings.max_file_write_bytes}-byte write limit"
            )
        if path.exists() and not arguments.overwrite:
            raise ToolError("File exists and overwrite was not explicitly requested")
        existed = path.exists()
        backup = self._backup(path) if existed else None
        self._atomic_write(path, encoded, "Approved file write failed")
        return {
            "path": str(path),
            "bytes_written": len(encoded),
            "overwritten": existed,
            "backup_path": str(backup) if backup else None,
        }

    def replace_text(self, arguments: ReplaceTextArgs) -> dict[str, Any]:
        path = self.policy.resolve(arguments.path)
        if not path.is_file():
            raise ToolError("Path is not a file")
        if path.stat().st_size > self.settings.max_file_bytes:
            raise ToolError(f"File exceeds the {self.settings.max_file_bytes}-byte read limit")
        try:
            original = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError("Only UTF-8 text files can be edited") from exc
        matches = original.count(arguments.old_text)
        if matches != arguments.expected_replacements:
            raise ToolError(
                "Exact edit precondition failed: "
                f"expected {arguments.expected_replacements} match(es), found {matches}"
            )
        updated = original.replace(
            arguments.old_text,
            arguments.new_text,
            arguments.expected_replacements,
        )
        encoded = updated.encode("utf-8")
        if len(encoded) > self.settings.max_file_write_bytes:
            raise ToolError(
                f"Edited file exceeds the {self.settings.max_file_write_bytes}-byte write limit"
            )
        original_bytes = original.encode("utf-8")
        backup = self._backup(path)
        self._atomic_write(path, encoded, "Approved exact text edit failed")
        return {
            "path": str(path),
            "replacements": matches,
            "bytes_written": len(encoded),
            "before_sha256": hashlib.sha256(original_bytes).hexdigest(),
            "after_sha256": hashlib.sha256(encoded).hexdigest(),
            "backup_path": str(backup),
        }


Handler = Callable[[Arguments], Any | Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    arguments_model: type[Arguments]
    handler: Handler
    requires_approval: bool = False
    changes_state: bool = False

    def ollama_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.arguments_model.model_json_schema(),
            },
        }


class ToolRegistry:
    """Validate every structured call against a fixed capability table."""

    def __init__(
        self,
        settings: Settings,
        audit: AuditLog,
        approvals: ApprovalService,
        memory: MemoryService,
        tasks: TaskService,
        notifications: NotificationService,
        telemetry: TelemetryService,
        angerona_control: Any | None = None,
        angerona_tactics: Any | None = None,
    ) -> None:
        files = SafeFileTools(settings)
        desktop = DesktopAutomation()
        clipboard = ClipboardTools()
        commands = CommandRunner(settings)
        live = LiveBriefingService()
        self.settings = settings
        self.live = live
        self.files = files
        self.audit = audit
        self.approvals = approvals
        self._specs: dict[str, ToolSpec] = {
            "read_file": ToolSpec("read_file", "Read a bounded UTF-8 file within an allowed workspace root.", ReadFileArgs, files.read_file),
            "list_directory": ToolSpec("list_directory", "List a directory within an allowed workspace root.", ListDirectoryArgs, files.list_directory),
            "search_files": ToolSpec("search_files", "Find literal text in bounded workspace files.", SearchFilesArgs, files.search_files),
            "find_files": ToolSpec("find_files", "Find file and directory names within an allowed root without reading their contents.", FindFilesArgs, files.find_files),
            "reveal_file": ToolSpec("reveal_file", "Reveal one exact allowed path in Windows Explorer after human approval.", RevealFileArgs, files.reveal_file, True, True),
            "write_file": ToolSpec("write_file", "Atomically write a UTF-8 file after human approval.", WriteFileArgs, files.write_file, True, True),
            "replace_text": ToolSpec("replace_text", "Replace an exact, expected UTF-8 text fragment after human approval, with a rollback backup and content hashes.", ReplaceTextArgs, files.replace_text, True, True),
            "memory_search": ToolSpec("memory_search", "Search private local memory by semantic similarity.", MemorySearchArgs, lambda args: memory.search(args.query, args.limit)),
            "create_task": ToolSpec("create_task", "Create or deduplicate a local tracked task.", CreateTaskArgs, lambda args: tasks.create(**args.model_dump(), actor="assistant")[0], changes_state=True),
            "list_tasks": ToolSpec("list_tasks", "List locally tracked tasks.", ListTasksArgs, lambda args: tasks.list(args.state, args.limit)),
            "notify_local": ToolSpec("notify_local", "Write a local/log notification; never sends externally.", NotifyArgs, lambda args: notifications.send_local(**args.model_dump()), changes_state=True),
            "system_health": ToolSpec("system_health", "Return read-only local CPU, memory, disk, and process telemetry.", NoArgs, lambda _args: telemetry.snapshot()),
            "get_current_time": ToolSpec("get_current_time", "Return the current local date, time, and timezone.", NoArgs, lambda _args: current_time()),
            "get_system_info": ToolSpec("get_system_info", "Return basic read-only host operating system and runtime information.", NoArgs, lambda _args: system_info()),
            "format_json": ToolSpec("format_json", "Validate and pretty-print bounded JSON text.", FormatJsonArgs, lambda args: format_json(args.data)),
            "word_count": ToolSpec("word_count", "Count characters, words, and lines in bounded text.", WordCountArgs, lambda args: word_count(args.text)),
            "get_world_news": ToolSpec("get_world_news", "Fetch a bounded live headline briefing from fixed approved world-news RSS feeds.", NoArgs, lambda _args: live.world_news()),
            "get_world_finance_news": ToolSpec("get_world_finance_news", "Fetch a bounded live market briefing from fixed approved finance RSS feeds.", NoArgs, lambda _args: live.finance_news()),
            "open_world_monitor": ToolSpec("open_world_monitor", "Open the fixed World Monitor dashboard in the system browser after human approval.", NoArgs, lambda _args: live.open_world_monitor(), True, True),
            "open_finance_world_monitor": ToolSpec("open_finance_world_monitor", "Open the fixed finance World Monitor dashboard in the system browser after human approval.", NoArgs, lambda _args: live.open_finance_monitor(), True, True),
            "list_windows": ToolSpec("list_windows", "List visible Windows application titles for exact UI Automation targeting.", ListWindowsArgs, desktop.list_windows),
            "read_clipboard": ToolSpec("read_clipboard", "Read bounded Unicode clipboard text after explicit approval without storing clipboard history.", ReadClipboardArgs, clipboard.read, True, False),
            "click_control": ToolSpec("click_control", "Click one exact named control in one exact visible Windows application after approval. Coordinates and image matching are not accepted.", ClickControlArgs, desktop.click_control, True, True),
            "run_command": ToolSpec("run_command", "Run an allowlisted executable with a structured argument list and no command shell after approval.", RunCommandArgs, commands.run, True, True),
        }
        def unavailable_angerona(_arguments: Arguments) -> dict[str, Any]:
            raise ToolError("Angerona control is unavailable in this runtime")

        self._specs.update(
            {
                    "angerona_inspect": ToolSpec(
                        "angerona_inspect",
                        "Inspect accessible controls in one exact, process-verified Angerona window.",
                        AngeronaInspectArgs,
                        (
                            lambda args: angerona_control.inspect(args.window_title, args.limit)
                            if angerona_control is not None
                            else unavailable_angerona(args)
                        ),
                    ),
                    "angerona_action": ToolSpec(
                        "angerona_action",
                        "Perform one exact target-locked Angerona UI action after local approval.",
                        AngeronaActionArgs,
                        (
                            lambda args: angerona_control.act(**args.model_dump())
                            if angerona_control is not None
                            else unavailable_angerona(args)
                        ),
                        True,
                        True,
                    ),
                    "angerona_tactics": ToolSpec(
                        "angerona_tactics",
                        "List versioned defensive Angerona tactics without executing them.",
                        NoArgs,
                        (
                            lambda args: {"tactics": angerona_tactics.list()}
                            if angerona_tactics is not None
                            else unavailable_angerona(args)
                        ),
                    ),
                    "angerona_save_tactic": ToolSpec(
                        "angerona_save_tactic",
                        "Save or version a bounded Angerona tactic after local approval.",
                        AngeronaTacticArgs,
                        (
                            lambda args: angerona_tactics.save(
                                **args.model_dump(), actor="assistant"
                            )
                            if angerona_tactics is not None
                            else unavailable_angerona(args)
                        ),
                        True,
                        True,
                    ),
            }
        )

    def catalog(self) -> list[dict[str, Any]]:
        """Expose only the operator-facing action capabilities used by the HUD."""

        action_names = ("list_windows", "read_clipboard", "click_control", "run_command")
        risk_by_action = {
            "list_windows": "low",
            "read_clipboard": "guarded",
            "click_control": "guarded",
            "run_command": "high",
        }
        return [
            {
                "name": name,
                "description": self._specs[name].description,
                "requires_approval": self._specs[name].requires_approval,
                "changes_state": self._specs[name].changes_state,
                "risk": risk_by_action[name],
                "parameters": self._specs[name].arguments_model.model_json_schema(),
            }
            for name in action_names
            if name in self._specs
        ]

    def schemas_for(self, allowed_tools: tuple[str, ...]) -> list[dict[str, Any]]:
        return [self._specs[name].ollama_schema() for name in allowed_tools if name in self._specs]

    def coding_status(self) -> dict[str, Any]:
        roots = [str(root) for root in self.settings.allowed_roots]
        return {
            "enabled": "replace_text" in self._specs and "run_command" in self._specs,
            "approval_required": True,
            "allowed_roots": roots,
            "edit_tools": ["write_file", "replace_text"],
            "allowed_executables": sorted(CommandRunner.ALLOWED_EXECUTABLES),
            "allowed_git_subcommands": sorted(CommandRunner.ALLOWED_GIT_SUBCOMMANDS),
            "backup_directory": str(self.settings.data_dir / "code_backups"),
        }

    async def execute(
        self,
        name: str,
        raw_arguments: dict[str, Any],
        *,
        actor: str = "assistant",
        approved: bool = False,
        risk_tier: str = "low",
        correlation: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        spec = self._specs.get(name)
        if spec is None:
            raise ToolError("Tool is not in the capability allowlist")
        if not isinstance(raw_arguments, dict):
            raise ToolError("Tool arguments must be a JSON object")
        try:
            arguments = spec.arguments_model.model_validate(raw_arguments)
        except ValidationError as exc:
            raise ToolError(f"Invalid arguments for {name}: {exc.errors(include_url=False)}") from exc
        guarded_mutation = risk_tier in {"guarded", "high"} and spec.changes_state
        if (spec.requires_approval or guarded_mutation) and not approved:
            approval_arguments = arguments.model_dump(mode="json")
            approval_reason = (
                f"{name} changes local state under the {risk_tier} risk policy and requires "
                "human confirmation"
                if spec.changes_state
                else f"{name} can expose sensitive local data and requires human confirmation"
            )
            request = self.approvals.create(
                actor,
                name,
                approval_arguments,
                approval_reason,
            )
            return {"status": "approval_required", "approval": request, "correlation": correlation or {}}
        try:
            result = spec.handler(arguments)
            if hasattr(result, "__await__"):
                result = await result
        except ToolError:
            self.audit.append(actor, "tool.failed", f"tool:{name}", {})
            raise
        except LiveBriefingError as exc:
            self.audit.append(actor, "tool.failed", f"tool:{name}", {})
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            self.audit.append(actor, "tool.failed", f"tool:{name}", {"error_type": type(exc).__name__})
            raise ToolError(f"{name} failed safely") from exc
        evidence = {"approved": approved, **(correlation or {})}
        if name == "angerona_action" and isinstance(result, dict):
            evidence.update(
                {
                    "process_id": result.get("process_id"),
                    "verified_after": bool(result.get("verified_after")),
                    "action": result.get("action"),
                }
            )
        self.audit.append(actor, "tool.executed", f"tool:{name}", evidence)
        verification = self._verify_postcondition(name, result, spec.changes_state)
        return {"status": "completed", "result": result, "verification": verification}

    def _verify_postcondition(
        self, name: str, result: Any, changes_state: bool
    ) -> dict[str, Any]:
        """Verify concrete effects when a capability exposes safe local evidence."""
        if not changes_state:
            return {"required": False, "verified": True, "method": "read_only"}
        if not isinstance(result, dict):
            return {"required": True, "verified": False, "method": "missing_result"}
        try:
            if name == "write_file":
                path = self.files.policy.resolve(result.get("path", ""))
                verified = path.is_file() and path.stat().st_size == result.get("bytes_written")
                return {"required": True, "verified": verified, "method": "file_size"}
            if name == "replace_text":
                path = self.files.policy.resolve(result.get("path", ""))
                digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
                return {
                    "required": True,
                    "verified": digest == result.get("after_sha256"),
                    "method": "sha256",
                }
            evidence_keys = {
                "reveal_file": "revealed",
                "click_control": "clicked",
                "angerona_action": "verified_after",
            }
            key = evidence_keys.get(name)
            if key:
                return {"required": True, "verified": result.get(key) is True, "method": key}
        except (OSError, ToolError):
            return {"required": True, "verified": False, "method": "local_evidence_error"}
        return {"required": True, "verified": None, "method": "adapter_not_available"}

    async def execute_approval(self, request_id: str, actor: str = "user") -> dict[str, Any]:
        request = self.approvals.claim_for_execution(request_id, actor)
        try:
            outcome = await self.execute(
                request["tool_name"], request["arguments"], actor=actor, approved=True
            )
        except Exception as exc:
            self.approvals.record_execution(
                request_id,
                succeeded=False,
                actor=actor,
                result={"error": str(exc), "error_type": type(exc).__name__},
            )
            raise
        self.approvals.record_execution(
            request_id,
            succeeded=True,
            actor=actor,
            result=outcome,
        )
        return outcome
