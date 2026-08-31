"""Local privacy helpers shared by memory and notification surfaces."""

from __future__ import annotations

import re

REDACTED = "[REDACTED]"

_ASSIGNMENT_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|bearer[_-]?token|token|"
    r"password|passwd|secret)\b(\s*[:=]\s*)([^\s,;]{6,})"
)
_BEARER_SECRET = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_KNOWN_TOKEN = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{12,})\b"
)
_JWT = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)


def redact_sensitive_text(value: str) -> str:
    """Mask common credential forms before text is persisted or logged."""

    redacted = _ASSIGNMENT_SECRET.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", value
    )
    redacted = _BEARER_SECRET.sub(f"Bearer {REDACTED}", redacted)
    redacted = _KNOWN_TOKEN.sub(REDACTED, redacted)
    return _JWT.sub(REDACTED, redacted)
