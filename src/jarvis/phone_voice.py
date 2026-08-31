"""Authenticated Twilio voice gateway state and TwiML helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape
from typing import Any
from urllib.parse import parse_qsl

from .audit import AuditLog, utc_now
from .database import Database


class PhoneVoiceError(RuntimeError):
    """A safe phone-gateway request failure carrying an HTTP status."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def hash_phone_pin(pin: str, *, iterations: int = 310_000, salt: bytes | None = None) -> str:
    """Return a portable PBKDF2 record; the plain PIN is never persisted."""

    if not pin.isdigit() or len(pin) != 6:
        raise ValueError("Phone PIN must contain exactly 6 digits")
    actual_salt = salt or os.urandom(18)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), actual_salt, iterations)
    return "$".join(
        (
            "pbkdf2_sha256",
            str(iterations),
            base64.urlsafe_b64encode(actual_salt).decode().rstrip("="),
            base64.urlsafe_b64encode(digest).decode().rstrip("="),
        )
    )


def verify_phone_pin(pin: str, record: str) -> bool:
    try:
        algorithm, iterations_text, salt_text, digest_text = record.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        if not 200_000 <= iterations <= 2_000_000:
            return False
        salt = base64.urlsafe_b64decode(salt_text + "=" * (-len(salt_text) % 4))
        expected = base64.urlsafe_b64decode(digest_text + "=" * (-len(digest_text) % 4))
        actual = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def twilio_signature(auth_token: str, url: str, parameters: list[tuple[str, str]]) -> str:
    """Compute Twilio's form webhook HMAC-SHA1 over the exact configured URL."""

    material = url + "".join(key + value for key, value in sorted(parameters))
    digest = hmac.new(auth_token.encode(), material.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def twiml_say(message: str, *, redirect: str | None = None, hangup: bool = False) -> str:
    body = f'<Say voice="Polly.Matthew">{escape(message[:3800])}</Say>'
    if redirect:
        body += f'<Redirect method="POST">{escape(redirect)}</Redirect>'
    if hangup:
        body += "<Hangup/>"
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>'


def twiml_gather(
    message: str,
    *,
    action: str,
    input_modes: str,
    timeout: int = 6,
    num_digits: int | None = None,
) -> str:
    digits = f' numDigits="{num_digits}"' if num_digits is not None else ""
    gather = (
        f'<Gather input="{input_modes}" action="{escape(action)}" method="POST" '
        f'timeout="{timeout}" speechTimeout="auto"{digits}>'
        f'<Say voice="Polly.Matthew">{escape(message[:2000])}</Say></Gather>'
    )
    fallback = '<Say voice="Polly.Matthew">I did not receive a response.</Say><Hangup/>'
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{gather}{fallback}</Response>'


@dataclass(frozen=True, slots=True)
class PhoneVoiceConfig:
    enabled: bool
    public_base_url: str
    auth_token: str
    allowed_callers: frozenset[str]
    pin_hash: str
    session_minutes: int = 10
    max_commands: int = 10
    max_failed_pins: int = 3
    max_calls_per_window: int = 5

    @classmethod
    def from_environment(cls) -> PhoneVoiceConfig:
        enabled = os.getenv("JARVIS_PHONE_ENABLED", "").strip().casefold() in {
            "1", "true", "yes", "on"
        }
        base_url = os.getenv("JARVIS_PHONE_PUBLIC_BASE_URL", "").strip().rstrip("/")
        callers = frozenset(
            part.strip() for part in os.getenv("JARVIS_PHONE_ALLOWED_CALLERS", "").split(",")
            if part.strip()
        )
        return cls(
            enabled=enabled,
            public_base_url=base_url,
            auth_token=os.getenv("TWILIO_AUTH_TOKEN", "").strip(),
            allowed_callers=callers,
            pin_hash=os.getenv("JARVIS_PHONE_PIN_HASH", "").strip(),
        )

    @property
    def ready(self) -> bool:
        return bool(
            self.enabled
            and self.public_base_url.startswith("https://")
            and self.auth_token
            and self.allowed_callers
            and self.pin_hash
        )


class PhoneVoiceService:
    """Enforce signature, caller, PIN, replay, session, and command boundaries."""

    def __init__(self, database: Database, audit: AuditLog, config: PhoneVoiceConfig) -> None:
        self.database = database
        self.audit = audit
        self.config = config

    @classmethod
    def from_environment(cls, database: Database, audit: AuditLog) -> PhoneVoiceService:
        return cls(database, audit, PhoneVoiceConfig.from_environment())

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "ready": self.config.ready,
            "https_configured": self.config.public_base_url.startswith("https://"),
            "caller_allowlist_configured": bool(self.config.allowed_callers),
            "pin_hash_configured": bool(self.config.pin_hash),
            "approval_policy": "phone_sessions_cannot_approve_guarded_actions",
            "audio_storage": "disabled",
        }

    def verify_webhook(
        self, *, path: str, raw_body: bytes, supplied_signature: str | None
    ) -> dict[str, str]:
        if not self.config.ready:
            raise PhoneVoiceError("Phone voice gateway is disabled or incomplete", 503)
        if not supplied_signature:
            raise PhoneVoiceError("Missing provider signature", 403)
        try:
            pairs = parse_qsl(raw_body.decode("utf-8"), keep_blank_values=True, strict_parsing=True)
        except (UnicodeDecodeError, ValueError) as exc:
            raise PhoneVoiceError("Malformed voice webhook", 400) from exc
        expected = twilio_signature(
            self.config.auth_token, f"{self.config.public_base_url}{path}", pairs
        )
        if not hmac.compare_digest(expected, supplied_signature.strip()):
            self.audit.append("phone:provider", "phone.signature_rejected", "phone:webhook", {})
            raise PhoneVoiceError("Invalid provider signature", 403)
        parameters: dict[str, str] = {}
        for key, value in pairs:
            parameters[key] = value
        return parameters

    @staticmethod
    def _caller_hash(caller: str) -> str:
        return hashlib.sha256(caller.encode()).hexdigest()

    @staticmethod
    def _validate_call_sid(call_sid: str) -> None:
        if not (10 <= len(call_sid) <= 80 and call_sid.isalnum()):
            raise PhoneVoiceError("Invalid call identifier", 400)

    def begin(self, call_sid: str, caller: str) -> dict[str, Any]:
        self._validate_call_sid(call_sid)
        if caller not in self.config.allowed_callers:
            self.audit.append("phone:caller", "phone.caller_rejected", "phone:call", {})
            raise PhoneVoiceError("Caller is not authorized", 403)
        now = datetime.now(UTC)
        cutoff = (now - timedelta(minutes=10)).isoformat()
        recent = self.database.query_one(
            "SELECT COUNT(*) AS count FROM phone_calls WHERE started_at>=?", (cutoff,)
        )
        existing = self.database.query_one("SELECT * FROM phone_calls WHERE call_sid=?", (call_sid,))
        if existing is None and int((recent or {}).get("count", 0)) >= self.config.max_calls_per_window:
            raise PhoneVoiceError("Phone call rate limit reached", 429)
        caller_hash = self._caller_hash(caller)
        if existing is not None:
            if existing["caller_hash"] != caller_hash:
                raise PhoneVoiceError("Call identity mismatch", 403)
            return existing
        stamp = now.isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO phone_calls
                (call_sid,caller_hash,started_at,last_seen_at,authenticated,failed_pins,
                 command_count,status) VALUES (?,?,?,?,0,0,0,'active')""",
                (call_sid, caller_hash, stamp, stamp),
            )
        self.audit.append("phone:caller", "phone.call_started", f"phone_call:{call_sid}", {})
        return self.database.query_one("SELECT * FROM phone_calls WHERE call_sid=?", (call_sid,)) or {}

    def _active(self, call_sid: str, *, authenticated: bool = False) -> dict[str, Any]:
        self._validate_call_sid(call_sid)
        row = self.database.query_one("SELECT * FROM phone_calls WHERE call_sid=?", (call_sid,))
        if row is None or row["status"] != "active":
            raise PhoneVoiceError("Phone session is not active", 403)
        started = datetime.fromisoformat(row["started_at"])
        if datetime.now(UTC) - started > timedelta(minutes=self.config.session_minutes):
            with self.database.transaction() as connection:
                connection.execute("UPDATE phone_calls SET status='ended' WHERE call_sid=?", (call_sid,))
            raise PhoneVoiceError("Phone session expired", 403)
        if authenticated and not bool(row["authenticated"]):
            raise PhoneVoiceError("Phone session is not authenticated", 403)
        return row

    def authenticate(self, call_sid: str, pin: str) -> bool:
        row = self._active(call_sid)
        if bool(row["authenticated"]):
            return True
        valid = verify_phone_pin(pin, self.config.pin_hash)
        failed = int(row["failed_pins"]) + (0 if valid else 1)
        locked = not valid and failed >= self.config.max_failed_pins
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE phone_calls SET authenticated=?,failed_pins=?,status=?,last_seen_at=?
                WHERE call_sid=?""",
                (int(valid), failed, "locked" if locked else "active", utc_now(), call_sid),
            )
        self.audit.append(
            "phone:caller",
            "phone.pin_accepted" if valid else "phone.pin_rejected",
            f"phone_call:{call_sid}",
            {"locked": locked},
        )
        return valid

    def claim_webhook_once(self, call_sid: str, path: str, raw_body: bytes) -> bool:
        """Return false for an exact signed webhook retry so commands cannot replay."""

        self._active(call_sid)
        digest = hashlib.sha256(path.encode() + b"\0" + raw_body).hexdigest()
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT 1 FROM phone_webhook_receipts WHERE digest=?", (digest,)
            ).fetchone()
            if existing is not None:
                return False
            connection.execute(
                """INSERT INTO phone_webhook_receipts
                (digest,call_sid,path,received_at) VALUES (?,?,?,?)""",
                (digest, call_sid, path, utc_now()),
            )
        return True

    def accept_command(self, call_sid: str) -> dict[str, Any]:
        row = self._active(call_sid, authenticated=True)
        count = int(row["command_count"])
        if count >= self.config.max_commands:
            raise PhoneVoiceError("Phone command limit reached", 429)
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE phone_calls SET command_count=command_count+1,last_seen_at=? WHERE call_sid=?",
                (utc_now(), call_sid),
            )
        return {"command_number": count + 1, "remaining": self.config.max_commands - count - 1}

    def end(self, call_sid: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE phone_calls SET status='ended',last_seen_at=? WHERE call_sid=?",
                (utc_now(), call_sid),
            )
        self.audit.append("phone:caller", "phone.call_ended", f"phone_call:{call_sid}", {})
