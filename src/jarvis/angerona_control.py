"""Target-locked Windows UI Automation and defensive tactic persistence for Angerona."""

from __future__ import annotations

import importlib
import json
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any
from uuid import uuid4

import psutil

from .audit import AuditLog, utc_now
from .database import Database


class AngeronaControlError(RuntimeError):
    """A safe, user-presentable Angerona control failure."""


ANGERONA_ACTIONS = frozenset(
    {"focus", "move_to_control", "invoke", "set_text", "send_keys", "scroll"}
)
SAFE_KEYS = frozenset(
    {
        "ENTER",
        "ESC",
        "TAB",
        "SHIFT+TAB",
        "UP",
        "DOWN",
        "LEFT",
        "RIGHT",
        "PAGEUP",
        "PAGEDOWN",
        "HOME",
        "END",
        "CTRL+F",
        "CTRL+A",
    }
)


class AngeronaControlService:
    """Control only one verified Angerona window using exact accessibility selectors."""

    def __init__(self) -> None:
        self.root = Path(os.getenv("ANGERONA_ROOT", r"D:\local-security-ai\AngeronaSuite"))
        self.title_fragment = os.getenv("ANGERONA_WINDOW_TITLE", "Angerona").strip() or "Angerona"
        self._stopped = False
        self._action_lock = threading.RLock()
        self._recent_actions: deque[float] = deque()

    @staticmethod
    def _desktop() -> Any:
        if os.name != "nt":
            raise AngeronaControlError("Angerona desktop control is available only on Windows")
        try:
            importlib.import_module("comtypes.gen")
            from pywinauto import Desktop
        except ImportError as exc:
            raise AngeronaControlError(
                "Windows UI Automation is unavailable in this installation"
            ) from exc
        return Desktop(backend="uia")

    def emergency_stop(self) -> dict[str, Any]:
        self._stopped = True
        return {"stopped": True, "reason": "operator emergency stop"}

    def resume(self) -> dict[str, Any]:
        self._stopped = False
        return {"stopped": False}

    def _check_action_rate(self) -> None:
        now = time.monotonic()
        with self._action_lock:
            while self._recent_actions and self._recent_actions[0] <= now - 60:
                self._recent_actions.popleft()
            if len(self._recent_actions) >= 20:
                raise AngeronaControlError("Angerona input rate limit reached")
            self._recent_actions.append(now)

    def _verify_process(self, process_id: int) -> dict[str, Any]:
        try:
            process = psutil.Process(process_id)
            executable = Path(process.exe()).resolve()
            command_line = process.cmdline()
        except (psutil.Error, OSError, ValueError) as exc:
            raise AngeronaControlError("The Angerona process could not be verified") from exc
        try:
            root = self.root.expanduser().resolve(strict=True)
        except OSError as exc:
            raise AngeronaControlError("ANGERONA_ROOT is missing or inaccessible") from exc
        command_text = " ".join(command_line).casefold()
        executable_inside = executable == root or root in executable.parents
        root_mentioned = str(root).casefold() in command_text
        script_inside = False
        for argument in command_line[1:]:
            try:
                candidate = Path(argument).expanduser().resolve(strict=True)
                if candidate.is_file() and candidate.suffix.casefold() == ".py":
                    script_inside = candidate == root or root in candidate.parents
            except (OSError, ValueError):
                continue
            if script_inside:
                break
        module_launch = "-m angerona" in command_text or "angerona" in executable.name.casefold()
        if not executable_inside and not (root_mentioned and (module_launch or script_inside)):
            raise AngeronaControlError(
                "The selected window process is not locked to the configured Angerona root"
            )
        return {
            "process_id": process_id,
            "executable": executable.name,
            "root": str(root),
        }

    def _window(self, window_title: str) -> tuple[Any, dict[str, Any]]:
        if self._stopped:
            raise AngeronaControlError("Angerona input is paused by the emergency stop")
        if self.title_fragment.casefold() not in window_title.casefold():
            raise AngeronaControlError("The target title does not identify Angerona")
        try:
            matches = self._desktop().windows(
                title=window_title, visible_only=True, enabled_only=True
            )
        except Exception as exc:
            raise AngeronaControlError("Windows UI Automation could not inspect Angerona") from exc
        if len(matches) != 1:
            raise AngeronaControlError(
                "The exact Angerona window title did not resolve to one visible window"
            )
        window = matches[0]
        process = self._verify_process(int(window.element_info.process_id or 0))
        if window.window_text() != window_title:
            raise AngeronaControlError("Angerona changed focus or title before input")
        return window, process

    @staticmethod
    def _control(
        window: Any,
        *,
        control_name: str,
        automation_id: str | None,
        control_type: str | None,
    ) -> Any:
        candidates = window.descendants(
            **({"control_type": control_type} if control_type else {})
        )
        matches = []
        for candidate in candidates:
            try:
                element = candidate.element_info
                if candidate.window_text() != control_name:
                    continue
                if automation_id and element.automation_id != automation_id:
                    continue
                if control_type and element.control_type != control_type:
                    continue
                if candidate.is_visible() and candidate.is_enabled():
                    matches.append(candidate)
            except (AttributeError, RuntimeError, OSError, TypeError, ValueError):
                continue
        if len(matches) != 1:
            raise AngeronaControlError(
                "The exact Angerona control did not resolve to one visible, enabled target"
            )
        return matches[0]

    def inspect(self, window_title: str, limit: int = 200) -> dict[str, Any]:
        window, process = self._window(window_title)
        controls: list[dict[str, Any]] = []
        for candidate in window.descendants():
            if len(controls) >= limit:
                break
            try:
                element = candidate.element_info
                name = candidate.window_text().strip()
                if not name or not candidate.is_visible():
                    continue
                controls.append(
                    {
                        "name": name[:300],
                        "automation_id": (element.automation_id or "")[:300] or None,
                        "control_type": element.control_type,
                        "enabled": bool(candidate.is_enabled()),
                    }
                )
            except (AttributeError, RuntimeError, OSError, TypeError, ValueError):
                continue
        return {
            "window_title": window_title,
            "process": process,
            "controls": controls,
            "truncated": len(controls) >= limit,
            "mode": "observe",
        }

    def act(
        self,
        *,
        window_title: str,
        action: str,
        control_name: str | None = None,
        automation_id: str | None = None,
        control_type: str | None = None,
        text: str | None = None,
        keys: str | None = None,
        scroll_steps: int = 0,
    ) -> dict[str, Any]:
        if action not in ANGERONA_ACTIONS:
            raise AngeronaControlError("Unsupported Angerona UI action")
        self._check_action_rate()
        window, before_process = self._window(window_title)
        control = None
        if control_name:
            control = self._control(
                window,
                control_name=control_name,
                automation_id=automation_id,
                control_type=control_type,
            )
        if action not in {"focus", "send_keys"} and control is None:
            raise AngeronaControlError("This action requires one exact Angerona control")
        try:
            if action == "focus":
                window.set_focus()
            elif action == "move_to_control":
                from pywinauto import mouse

                rectangle = control.rectangle()
                mouse.move(coords=(rectangle.mid_point().x, rectangle.mid_point().y))
            elif action == "invoke":
                try:
                    control.invoke()
                except (AttributeError, RuntimeError):
                    control.click_input()
            elif action == "set_text":
                if text is None or len(text) > 2000 or "\x00" in text:
                    raise AngeronaControlError("Angerona text must be present and bounded")
                control.set_edit_text(text)
            elif action == "send_keys":
                normalized = (keys or "").upper()
                if normalized not in SAFE_KEYS:
                    raise AngeronaControlError("Keyboard input is outside the Angerona key allowlist")
                from pywinauto.keyboard import send_keys

                key_map = {
                    "ENTER": "{ENTER}", "ESC": "{ESC}", "TAB": "{TAB}",
                    "SHIFT+TAB": "+{TAB}", "UP": "{UP}", "DOWN": "{DOWN}",
                    "LEFT": "{LEFT}", "RIGHT": "{RIGHT}", "PAGEUP": "{PGUP}",
                    "PAGEDOWN": "{PGDN}", "HOME": "{HOME}", "END": "{END}",
                    "CTRL+F": "^f", "CTRL+A": "^a",
                }
                window.set_focus()
                send_keys(key_map[normalized], pause=0.03, with_spaces=True)
            elif action == "scroll":
                if not -20 <= scroll_steps <= 20 or scroll_steps == 0:
                    raise AngeronaControlError("Scroll steps must be between -20 and 20, excluding zero")
                rectangle = control.rectangle()
                control.scroll(
                    wheel_dist=scroll_steps,
                    coords=(rectangle.mid_point().x, rectangle.mid_point().y),
                )
        except AngeronaControlError:
            raise
        except Exception as exc:
            raise AngeronaControlError("The approved Angerona action failed safely") from exc
        _, after_process = self._window(window_title)
        if before_process["process_id"] != after_process["process_id"]:
            raise AngeronaControlError("Angerona target identity changed during the action")
        return {
            "window_title": window_title,
            "process_id": before_process["process_id"],
            "action": action,
            "control_name": control_name,
            "verified_after": True,
            "mode": "approved_execute",
        }


class AngeronaTacticService:
    """Store versioned, bounded defensive UI tactics without executing them automatically."""

    MAX_STEPS = 20

    def __init__(self, database: Database, audit: AuditLog) -> None:
        self.database = database
        self.audit = audit

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(row)
        payload["steps"] = json.loads(payload.pop("steps_json"))
        payload["preconditions"] = json.loads(payload.pop("preconditions_json"))
        payload["success_evidence"] = json.loads(payload.pop("success_evidence_json"))
        payload["enabled"] = bool(payload["enabled"])
        return payload

    @classmethod
    def validate_steps(cls, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not 1 <= len(steps) <= cls.MAX_STEPS:
            raise ValueError(f"Angerona tactics require 1 to {cls.MAX_STEPS} steps")
        safe: list[dict[str, Any]] = []
        allowed_fields = {
            "action", "control_name", "automation_id", "control_type", "text", "keys",
            "scroll_steps",
        }
        for step in steps:
            if not isinstance(step, dict) or set(step) - allowed_fields:
                raise ValueError("Angerona tactic contains unsupported fields")
            if step.get("action") not in ANGERONA_ACTIONS:
                raise ValueError("Angerona tactic contains an unsupported action")
            encoded = json.dumps(step, ensure_ascii=False)
            if len(encoded) > 3000:
                raise ValueError("Angerona tactic step is too large")
            safe.append(dict(step))
        if len(json.dumps(safe, ensure_ascii=False)) > 30_000:
            raise ValueError("Angerona tactic steps are too large")
        return safe

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.database.query_all(
            "SELECT * FROM angerona_tactics ORDER BY enabled DESC, updated_at DESC LIMIT ?",
            (limit,),
        )
        return [self._decode(row) for row in rows]

    def save(
        self,
        *,
        name: str,
        description: str,
        steps: list[dict[str, Any]],
        preconditions: list[str],
        success_evidence: list[str],
        enabled: bool = False,
        actor: str = "user:hud",
    ) -> dict[str, Any]:
        cleaned_name = name.strip()
        if not cleaned_name or len(cleaned_name) > 120:
            raise ValueError("Angerona tactic name must be between 1 and 120 characters")
        if len(description) > 2000:
            raise ValueError("Angerona tactic description is too long")
        safe_steps = self.validate_steps(steps)
        for items in (preconditions, success_evidence):
            if len(items) > 20 or any(not item.strip() or len(item) > 300 for item in items):
                raise ValueError("Angerona tactic evidence lists are invalid")
        now = utc_now()
        existing = self.database.query_one(
            "SELECT id,version,created_at FROM angerona_tactics WHERE name=?", (cleaned_name,)
        )
        tactic_id = existing["id"] if existing else str(uuid4())
        version = int(existing["version"]) + 1 if existing else 1
        created_at = existing["created_at"] if existing else now
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO angerona_tactics
                (id,name,description,steps_json,preconditions_json,success_evidence_json,
                 version,enabled,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(name) DO UPDATE SET description=excluded.description,
                steps_json=excluded.steps_json,preconditions_json=excluded.preconditions_json,
                success_evidence_json=excluded.success_evidence_json,version=excluded.version,
                enabled=excluded.enabled,updated_at=excluded.updated_at""",
                (
                    tactic_id, cleaned_name, description, json.dumps(safe_steps, sort_keys=True),
                    json.dumps(preconditions), json.dumps(success_evidence), version, int(enabled),
                    created_at, now,
                ),
            )
        self.audit.append(
            actor,
            "angerona.tactic.saved",
            f"angerona_tactic:{tactic_id}",
            {"name": cleaned_name, "version": version, "enabled": enabled},
        )
        return next(item for item in self.list() if item["id"] == tactic_id)
