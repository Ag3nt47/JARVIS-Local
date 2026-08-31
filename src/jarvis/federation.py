"""Authenticated, replay-resistant federation request primitives.

This module deliberately implements only authentication and typed request parsing.  It
does not open sockets, execute capabilities, store private keys, or log request bodies.
Peers sign a canonical envelope with Ed25519; receivers match the claimed peer/key to an
in-memory public-key allowlist before atomically applying replay and idempotency rules.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, ClassVar, Self, TypeAlias

try:  # Fail closed at the operation boundary when the optional dependency is absent.
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )

    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:  # pragma: no cover - which branch runs depends on the environment.
    InvalidSignature = Exception  # type: ignore[assignment,misc]
    Ed25519PrivateKey = Any  # type: ignore[assignment,misc]
    Ed25519PublicKey = Any  # type: ignore[assignment,misc]
    CRYPTOGRAPHY_AVAILABLE = False


PROTOCOL_VERSION = "jarvis-federation-v1"
DEFAULT_FRESHNESS_SECONDS = 60
MAX_BODY_BYTES = 2 * 1024 * 1024

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,62}[A-Za-z0-9])?$")
_METHOD_RE = re.compile(r"^[A-Z][A-Z0-9!#$%&'*+.^_`|~-]{0,31}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~-]{16,128}$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class FederationError(Exception):
    """Base class for federation errors safe for application-level classification."""


class FederationDependencyError(FederationError):
    """Raised when the mandatory Ed25519 implementation is not installed."""


class EnvelopeValidationError(FederationError, ValueError):
    """Raised when an envelope or capability does not match its strict schema."""


class AuthenticationError(FederationError):
    """Base class for failed peer authentication."""


class UnknownKeyError(AuthenticationError):
    """Raised when the peer/key pair is absent from the public-key allowlist."""


class RevokedKeyError(AuthenticationError):
    """Raised when a known public key has been revoked."""


class SignatureVerificationError(AuthenticationError):
    """Raised when an Ed25519 signature is invalid."""


class FreshnessError(AuthenticationError):
    """Raised when the signed timestamp is too old or too far in the future."""


class ReplayError(AuthenticationError):
    """Raised when a nonce has already been accepted for the same signing key."""


class IdempotencyConflictError(AuthenticationError):
    """Raised when a request ID is reused with a different raw-body digest."""


class ScopeDeniedError(AuthenticationError):
    """Raised when an authenticated key lacks a requested capability scope."""


def require_cryptography() -> None:
    """Require the audited Ed25519 backend instead of using a weaker fallback."""

    if not CRYPTOGRAPHY_AVAILABLE:
        raise FederationDependencyError(
            "Federation authentication requires the 'cryptography' package with Ed25519 support"
        )


def _validate_identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise EnvelopeValidationError(
            f"{field_name} must be 1-64 characters using letters, digits, '.', '_' or '-'"
        )
    return value


def _validate_token(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        raise EnvelopeValidationError(
            f"{field_name} must be a 16-128 character opaque URL-safe token"
        )
    return value


def _validate_path_query(value: str) -> str:
    if not isinstance(value, str) or not (1 <= len(value.encode("utf-8")) <= 2048):
        raise EnvelopeValidationError("path_query must be 1-2048 UTF-8 bytes")
    if not value.startswith("/") or value.startswith("//"):
        raise EnvelopeValidationError("path_query must be an origin-form absolute path")
    if "#" in value or "\r" in value or "\n" in value or "\x00" in value:
        raise EnvelopeValidationError("path_query contains a forbidden fragment or control byte")
    return value


def _validate_method(value: str) -> str:
    if not isinstance(value, str) or not _METHOD_RE.fullmatch(value):
        raise EnvelopeValidationError("method must be an uppercase HTTP token of at most 32 bytes")
    return value


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str, expected_size: int, field_name: str) -> bytes:
    if not isinstance(value, str) or "=" in value or not value:
        raise EnvelopeValidationError(f"{field_name} must be unpadded base64url")
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise EnvelopeValidationError(f"{field_name} must be valid base64url") from exc
    if len(raw) != expected_size or _b64url_encode(raw) != value:
        raise EnvelopeValidationError(
            f"{field_name} must canonically encode exactly {expected_size} bytes"
        )
    return raw


def sha256_body(raw_body: bytes) -> str:
    """Return the lowercase SHA-256 hex digest of an exact HTTP entity body."""

    if not isinstance(raw_body, bytes):
        raise TypeError("raw_body must be bytes")
    if len(raw_body) > MAX_BODY_BYTES:
        raise EnvelopeValidationError(f"request body exceeds {MAX_BODY_BYTES} bytes")
    return hashlib.sha256(raw_body).hexdigest()


@dataclass(frozen=True, slots=True)
class SignedRequestEnvelope:
    """Strict canonical metadata and Ed25519 signature for one HTTP request."""

    peer_id: str
    key_id: str
    method: str
    path_query: str
    timestamp: int
    nonce: str
    request_id: str
    body_sha256: str
    signature: str
    version: str = PROTOCOL_VERSION

    _FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "version",
            "peer_id",
            "key_id",
            "method",
            "path_query",
            "timestamp",
            "nonce",
            "request_id",
            "body_sha256",
            "signature",
        }
    )

    def __post_init__(self) -> None:
        if self.version != PROTOCOL_VERSION:
            raise EnvelopeValidationError(f"unsupported federation version: {self.version!r}")
        _validate_identifier(self.peer_id, "peer_id")
        _validate_identifier(self.key_id, "key_id")
        _validate_method(self.method)
        _validate_path_query(self.path_query)
        if isinstance(self.timestamp, bool) or not isinstance(self.timestamp, int):
            raise EnvelopeValidationError("timestamp must be an integer Unix timestamp in seconds")
        if self.timestamp < 0 or self.timestamp > 253402300799:
            raise EnvelopeValidationError("timestamp is outside the supported Unix time range")
        _validate_token(self.nonce, "nonce")
        _validate_token(self.request_id, "request_id")
        if not isinstance(self.body_sha256, str) or not _HEX_SHA256_RE.fullmatch(self.body_sha256):
            raise EnvelopeValidationError("body_sha256 must be a lowercase SHA-256 hex digest")
        _b64url_decode(self.signature, 64, "signature")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> SignedRequestEnvelope:
        """Parse an envelope while rejecting missing and extension fields."""

        if not isinstance(value, Mapping):
            raise EnvelopeValidationError("envelope must be an object")
        keys = set(value)
        missing = cls._FIELDS - keys
        unexpected = keys - cls._FIELDS
        if missing or unexpected:
            details = []
            if missing:
                details.append(f"missing={sorted(missing)}")
            if unexpected:
                details.append(f"unexpected={sorted(unexpected)}")
            raise EnvelopeValidationError("invalid envelope fields: " + ", ".join(details))
        try:
            return cls(**{key: value[key] for key in cls._FIELDS})
        except TypeError as exc:
            raise EnvelopeValidationError("invalid envelope field types") from exc

    def unsigned_mapping(self) -> dict[str, str | int]:
        """Return the exact fields covered by the signature."""

        return {
            "body_sha256": self.body_sha256,
            "key_id": self.key_id,
            "method": self.method,
            "nonce": self.nonce,
            "path_query": self.path_query,
            "peer_id": self.peer_id,
            "request_id": self.request_id,
            "timestamp": self.timestamp,
            "version": self.version,
        }

    def canonical_bytes(self) -> bytes:
        """Serialize the signed fields deterministically as UTF-8 JSON."""

        return json.dumps(
            self.unsigned_mapping(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def to_mapping(self) -> dict[str, str | int]:
        """Return a JSON-serializable transport object including the signature."""

        return {**self.unsigned_mapping(), "signature": self.signature}


def sign_request(
    private_key: Any,
    *,
    peer_id: str,
    key_id: str,
    method: str,
    path_query: str,
    timestamp: int,
    nonce: str,
    request_id: str,
    raw_body: bytes,
) -> SignedRequestEnvelope:
    """Create a signed envelope without serializing or retaining the private key."""

    require_cryptography()
    if not isinstance(private_key, Ed25519PrivateKey):
        raise TypeError("private_key must be an Ed25519PrivateKey")
    placeholder = _b64url_encode(b"\x00" * 64)
    envelope = SignedRequestEnvelope(
        peer_id=peer_id,
        key_id=key_id,
        method=method.upper(),
        path_query=path_query,
        timestamp=timestamp,
        nonce=nonce,
        request_id=request_id,
        body_sha256=sha256_body(raw_body),
        signature=placeholder,
    )
    return replace(envelope, signature=_b64url_encode(private_key.sign(envelope.canonical_bytes())))


CAPABILITY_SCOPES = frozenset(
    {"compute.chat", "compute.embed", "data.list", "data.read", "admin.request"}
)


@dataclass(frozen=True, slots=True)
class PeerPublicKey:
    """One allowlisted peer key and its exact fixed capability scopes."""

    peer_id: str
    key_id: str
    public_key: bytes = field(repr=False)
    scopes: frozenset[str]
    revoked: bool = False

    def __post_init__(self) -> None:
        _validate_identifier(self.peer_id, "peer_id")
        _validate_identifier(self.key_id, "key_id")
        if not isinstance(self.public_key, bytes) or len(self.public_key) != 32:
            raise EnvelopeValidationError("public_key must be exactly 32 raw Ed25519 bytes")
        normalized = frozenset(self.scopes)
        unknown = normalized - CAPABILITY_SCOPES
        if unknown:
            raise EnvelopeValidationError(f"unknown capability scopes: {sorted(unknown)}")
        object.__setattr__(self, "scopes", normalized)
        if not isinstance(self.revoked, bool):
            raise EnvelopeValidationError("revoked must be boolean")

    @classmethod
    def from_base64url(
        cls,
        *,
        peer_id: str,
        key_id: str,
        public_key: str,
        scopes: Iterable[str],
        revoked: bool = False,
    ) -> PeerPublicKey:
        """Build an allowlist record from a canonical base64url public key."""

        return cls(
            peer_id=peer_id,
            key_id=key_id,
            public_key=_b64url_decode(public_key, 32, "public_key"),
            scopes=frozenset(scopes),
            revoked=revoked,
        )


class PublicKeyAllowlist:
    """Thread-safe in-memory public-key allowlist with explicit revocation."""

    def __init__(self, keys: Iterable[PeerPublicKey] = ()) -> None:
        self._lock = threading.RLock()
        self._keys: dict[tuple[str, str], PeerPublicKey] = {}
        for key in keys:
            self.add(key)

    def add(self, key: PeerPublicKey) -> None:
        """Add a key, rejecting an accidental overwrite of a different key."""

        if not isinstance(key, PeerPublicKey):
            raise TypeError("key must be a PeerPublicKey")
        identity = (key.peer_id, key.key_id)
        with self._lock:
            existing = self._keys.get(identity)
            if existing is not None and existing.public_key != key.public_key:
                raise EnvelopeValidationError(
                    "peer_id/key_id already identifies another public key"
                )
            self._keys[identity] = key

    def revoke(self, peer_id: str, key_id: str) -> None:
        """Revoke a known key without removing its security classification."""

        with self._lock:
            identity = (peer_id, key_id)
            key = self._keys.get(identity)
            if key is None:
                raise UnknownKeyError("unknown federation peer or key")
            self._keys[identity] = replace(key, revoked=True)

    def resolve(self, peer_id: str, key_id: str) -> PeerPublicKey:
        """Resolve an active allowlisted key or raise a typed authentication error."""

        with self._lock:
            key = self._keys.get((peer_id, key_id))
        if key is None:
            raise UnknownKeyError("unknown federation peer or key")
        if key.revoked:
            raise RevokedKeyError("federation key has been revoked")
        return key


@dataclass(frozen=True, slots=True)
class ReplayDecision:
    """Result of atomically recording one authenticated envelope."""

    idempotent: bool


class SQLiteReplayStore:
    """Atomic SQLite nonce and request-ID replay ledger.

    Nonces are unique per signing key. Request IDs are unique per peer and bind to
    their first raw-body digest. Repetition with a new nonce and the same digest is an
    idempotent retry; a changed digest is a conflict. Conflict nonces are consumed too.
    """

    def __init__(self, database: str | Path | sqlite3.Connection = ":memory:") -> None:
        self._lock = threading.RLock()
        if isinstance(database, sqlite3.Connection):
            self._connection = database
            self._owns_connection = False
        else:
            self._connection = sqlite3.connect(
                str(database), isolation_level=None, check_same_thread=False, timeout=5.0
            )
            self._owns_connection = True
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    def _create_schema(self) -> None:
        with self._lock:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS federation_nonces (
                    peer_id TEXT NOT NULL,
                    key_id TEXT NOT NULL,
                    nonce TEXT NOT NULL,
                    accepted_at INTEGER NOT NULL,
                    PRIMARY KEY (peer_id, key_id, nonce)
                );
                CREATE TABLE IF NOT EXISTS federation_requests (
                    peer_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    body_sha256 TEXT NOT NULL CHECK(length(body_sha256) = 64),
                    accepted_at INTEGER NOT NULL,
                    PRIMARY KEY (peer_id, request_id)
                );
                """
            )

    def record(
        self,
        *,
        peer_id: str,
        key_id: str,
        nonce: str,
        request_id: str,
        body_sha256: str,
        accepted_at: int,
    ) -> ReplayDecision:
        """Atomically accept a nonce and apply the request-ID digest contract."""

        outcome: str
        with self._lock:
            cursor = self._connection.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                try:
                    cursor.execute(
                        """INSERT INTO federation_nonces
                           (peer_id, key_id, nonce, accepted_at) VALUES (?, ?, ?, ?)""",
                        (peer_id, key_id, nonce, accepted_at),
                    )
                except sqlite3.IntegrityError:
                    self._connection.rollback()
                    raise ReplayError("federation nonce has already been accepted") from None

                row = cursor.execute(
                    """SELECT body_sha256 FROM federation_requests
                       WHERE peer_id = ? AND request_id = ?""",
                    (peer_id, request_id),
                ).fetchone()
                if row is None:
                    cursor.execute(
                        """INSERT INTO federation_requests
                           (peer_id, request_id, body_sha256, accepted_at) VALUES (?, ?, ?, ?)""",
                        (peer_id, request_id, body_sha256, accepted_at),
                    )
                    outcome = "new"
                elif hmac.compare_digest(str(row[0]), body_sha256):
                    outcome = "idempotent"
                else:
                    outcome = "conflict"
                self._connection.commit()
            except (ReplayError, IdempotencyConflictError):
                raise
            except Exception:
                self._connection.rollback()
                raise
            finally:
                cursor.close()
        if outcome == "conflict":
            raise IdempotencyConflictError(
                "request_id was previously accepted with a different body digest"
            )
        return ReplayDecision(idempotent=outcome == "idempotent")

    def close(self) -> None:
        """Close the owned SQLite connection."""

        if self._owns_connection:
            with self._lock:
                self._connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class VerifiedRequest:
    """Effective authenticated identity and replay decision for application policy."""

    peer_id: str
    key_id: str
    scopes: frozenset[str]
    request_id: str
    body_sha256: str
    timestamp: int
    idempotent: bool


class FederationVerifier:
    """Verify signed HTTP requests against an allowlist and replay ledger."""

    def __init__(
        self,
        allowlist: PublicKeyAllowlist,
        replay_store: SQLiteReplayStore,
        *,
        freshness_seconds: int = DEFAULT_FRESHNESS_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(freshness_seconds, int) or not (1 <= freshness_seconds <= 300):
            raise ValueError("freshness_seconds must be an integer between 1 and 300")
        self.allowlist = allowlist
        self.replay_store = replay_store
        self.freshness_seconds = freshness_seconds
        self.clock = clock

    def verify(
        self,
        envelope: SignedRequestEnvelope | Mapping[str, Any],
        *,
        method: str,
        path_query: str,
        raw_body: bytes,
        required_scope: str | None = None,
    ) -> VerifiedRequest:
        """Authenticate an exact HTTP request and return its effective peer identity."""

        require_cryptography()
        if not isinstance(envelope, SignedRequestEnvelope):
            envelope = SignedRequestEnvelope.from_mapping(envelope)
        actual_method = method.upper() if isinstance(method, str) else method
        _validate_method(actual_method)
        _validate_path_query(path_query)
        if envelope.method != actual_method or envelope.path_query != path_query:
            raise SignatureVerificationError("signed method or path_query does not match request")
        actual_digest = sha256_body(raw_body)
        if not hmac.compare_digest(envelope.body_sha256, actual_digest):
            raise SignatureVerificationError("signed body digest does not match request body")

        now = int(self.clock())
        age = now - envelope.timestamp
        if age > self.freshness_seconds:
            raise FreshnessError("signed request timestamp is stale")
        if age < -self.freshness_seconds:
            raise FreshnessError("signed request timestamp is too far in the future")

        key = self.allowlist.resolve(envelope.peer_id, envelope.key_id)
        try:
            Ed25519PublicKey.from_public_bytes(key.public_key).verify(
                _b64url_decode(envelope.signature, 64, "signature"),
                envelope.canonical_bytes(),
            )
        except InvalidSignature as exc:
            raise SignatureVerificationError("invalid federation request signature") from exc

        if required_scope is not None:
            if required_scope not in CAPABILITY_SCOPES:
                raise EnvelopeValidationError("required_scope is not a fixed federation capability")
            if required_scope not in key.scopes:
                raise ScopeDeniedError("federation key is not authorized for this capability")

        decision = self.replay_store.record(
            peer_id=envelope.peer_id,
            key_id=envelope.key_id,
            nonce=envelope.nonce,
            request_id=envelope.request_id,
            body_sha256=envelope.body_sha256,
            accepted_at=now,
        )
        return VerifiedRequest(
            peer_id=key.peer_id,
            key_id=key.key_id,
            scopes=key.scopes,
            request_id=envelope.request_id,
            body_sha256=envelope.body_sha256,
            timestamp=envelope.timestamp,
            idempotent=decision.idempotent,
        )


def _strict_object(
    value: Any, *, allowed: set[str], required: set[str], label: str
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EnvelopeValidationError(f"{label} must be an object")
    keys = set(value)
    if not required <= keys or not keys <= allowed:
        raise EnvelopeValidationError(
            f"invalid {label} fields; required={sorted(required)}, allowed={sorted(allowed)}"
        )
    return value


def _bounded_text(value: Any, field_name: str, *, minimum: int, maximum: int) -> str:
    if not isinstance(value, str) or not (minimum <= len(value) <= maximum):
        raise EnvelopeValidationError(
            f"{field_name} must contain between {minimum} and {maximum} characters"
        )
    if "\x00" in value:
        raise EnvelopeValidationError(f"{field_name} contains a NUL character")
    return value


def _model_name(value: Any) -> str:
    if not isinstance(value, str) or not _MODEL_RE.fullmatch(value):
        raise EnvelopeValidationError("model must be a 1-128 character model identifier")
    return value


def _named_root(value: Any) -> str:
    return _validate_identifier(value, "root")


def _relative_path(value: Any, *, allow_empty: bool) -> str:
    if not isinstance(value, str) or len(value) > 512:
        raise EnvelopeValidationError("relative path must be a string of at most 512 characters")
    if not value and allow_empty:
        return value
    if not value or value.startswith(("/", "\\")) or "\\" in value or ":" in value:
        raise EnvelopeValidationError("relative path must stay beneath its named root")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise EnvelopeValidationError("relative path contains an empty or traversal component")
    if any(any(ord(character) < 32 for character in part) for part in parts):
        raise EnvelopeValidationError("relative path contains control characters")
    return value


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """A bounded plain-text chat message; tool-call structures are intentionally absent."""

    role: str
    content: str

    @classmethod
    def from_mapping(cls, value: Any) -> ChatMessage:
        item = _strict_object(
            value, allowed={"role", "content"}, required={"role", "content"}, label="message"
        )
        role = item["role"]
        if role not in {"system", "user", "assistant"}:
            raise EnvelopeValidationError("message role must be system, user, or assistant")
        return cls(
            role=role, content=_bounded_text(item["content"], "content", minimum=1, maximum=16_384)
        )


@dataclass(frozen=True, slots=True)
class ComputeChatRequest:
    """Typed payload for bounded remote model inference without native tool execution."""

    scope: ClassVar[str] = "compute.chat"
    model: str
    messages: tuple[ChatMessage, ...]
    max_tokens: int = 1024
    temperature: float = 0.2

    @classmethod
    def from_mapping(cls, value: Any) -> ComputeChatRequest:
        item = _strict_object(
            value,
            allowed={"model", "messages", "max_tokens", "temperature"},
            required={"model", "messages"},
            label=cls.scope,
        )
        raw_messages = item["messages"]
        if not isinstance(raw_messages, list) or not (1 <= len(raw_messages) <= 64):
            raise EnvelopeValidationError("messages must contain between 1 and 64 items")
        max_tokens = item.get("max_tokens", 1024)
        if (
            isinstance(max_tokens, bool)
            or not isinstance(max_tokens, int)
            or not 1 <= max_tokens <= 8192
        ):
            raise EnvelopeValidationError("max_tokens must be an integer between 1 and 8192")
        temperature = item.get("temperature", 0.2)
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
            raise EnvelopeValidationError("temperature must be numeric")
        temperature = float(temperature)
        if not 0.0 <= temperature <= 2.0:
            raise EnvelopeValidationError("temperature must be between 0 and 2")
        return cls(
            model=_model_name(item["model"]),
            messages=tuple(ChatMessage.from_mapping(message) for message in raw_messages),
            max_tokens=max_tokens,
            temperature=temperature,
        )


@dataclass(frozen=True, slots=True)
class ComputeEmbedRequest:
    """Typed payload for bounded embedding generation."""

    scope: ClassVar[str] = "compute.embed"
    model: str
    inputs: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: Any) -> ComputeEmbedRequest:
        item = _strict_object(
            value,
            allowed={"model", "inputs"},
            required={"model", "inputs"},
            label=cls.scope,
        )
        inputs = item["inputs"]
        if not isinstance(inputs, list) or not (1 <= len(inputs) <= 128):
            raise EnvelopeValidationError("inputs must contain between 1 and 128 strings")
        return cls(
            model=_model_name(item["model"]),
            inputs=tuple(
                _bounded_text(text, "input", minimum=1, maximum=16_384) for text in inputs
            ),
        )


@dataclass(frozen=True, slots=True)
class DataListRequest:
    """Typed listing request confined to one server-configured named root."""

    scope: ClassVar[str] = "data.list"
    root: str
    prefix: str = ""
    limit: int = 100

    @classmethod
    def from_mapping(cls, value: Any) -> DataListRequest:
        item = _strict_object(
            value,
            allowed={"root", "prefix", "limit"},
            required={"root"},
            label=cls.scope,
        )
        limit = item.get("limit", 100)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise EnvelopeValidationError("limit must be an integer between 1 and 1000")
        return cls(
            root=_named_root(item["root"]),
            prefix=_relative_path(item.get("prefix", ""), allow_empty=True),
            limit=limit,
        )


@dataclass(frozen=True, slots=True)
class DataReadRequest:
    """Typed read request confined to a relative path under a named root."""

    scope: ClassVar[str] = "data.read"
    root: str
    relative_path: str
    max_bytes: int = 262_144

    @classmethod
    def from_mapping(cls, value: Any) -> DataReadRequest:
        item = _strict_object(
            value,
            allowed={"root", "relative_path", "max_bytes"},
            required={"root", "relative_path"},
            label=cls.scope,
        )
        max_bytes = item.get("max_bytes", 262_144)
        if (
            isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or not 1 <= max_bytes <= 1_048_576
        ):
            raise EnvelopeValidationError("max_bytes must be an integer between 1 and 1048576")
        return cls(
            root=_named_root(item["root"]),
            relative_path=_relative_path(item["relative_path"], allow_empty=False),
            max_bytes=max_bytes,
        )


ADMIN_ACTIONS = frozenset(
    {"peer.enroll.request", "peer.revoke.request", "key.rotate.request", "policy.change.request"}
)


@dataclass(frozen=True, slots=True)
class AdminRequest:
    """Typed administrative proposal; this model never performs the requested action."""

    scope: ClassVar[str] = "admin.request"
    action: str
    target_peer_id: str
    reason: str

    @classmethod
    def from_mapping(cls, value: Any) -> AdminRequest:
        item = _strict_object(
            value,
            allowed={"action", "target_peer_id", "reason"},
            required={"action", "target_peer_id", "reason"},
            label=cls.scope,
        )
        action = item["action"]
        if action not in ADMIN_ACTIONS:
            raise EnvelopeValidationError(f"action must be one of {sorted(ADMIN_ACTIONS)}")
        return cls(
            action=action,
            target_peer_id=_validate_identifier(item["target_peer_id"], "target_peer_id"),
            reason=_bounded_text(item["reason"], "reason", minimum=8, maximum=1024),
        )


CapabilityPayload: TypeAlias = (
    ComputeChatRequest | ComputeEmbedRequest | DataListRequest | DataReadRequest | AdminRequest
)

_CAPABILITY_MODELS: dict[str, type[Any]] = {
    ComputeChatRequest.scope: ComputeChatRequest,
    ComputeEmbedRequest.scope: ComputeEmbedRequest,
    DataListRequest.scope: DataListRequest,
    DataReadRequest.scope: DataReadRequest,
    AdminRequest.scope: AdminRequest,
}


@dataclass(frozen=True, slots=True)
class ParsedCapability:
    """A fixed capability name paired with its validated typed payload."""

    name: str
    payload: CapabilityPayload


def parse_capability(raw_body: bytes) -> ParsedCapability:
    """Parse strict UTF-8 JSON into one of the five fixed capability models."""

    sha256_body(raw_body)  # Enforce the shared body-size ceiling before JSON parsing.
    try:
        value = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EnvelopeValidationError("capability body must be valid UTF-8 JSON") from exc
    outer = _strict_object(
        value,
        allowed={"capability", "payload"},
        required={"capability", "payload"},
        label="capability request",
    )
    name = outer["capability"]
    model = _CAPABILITY_MODELS.get(name) if isinstance(name, str) else None
    if model is None:
        raise EnvelopeValidationError("unknown or unsupported federation capability")
    return ParsedCapability(name=name, payload=model.from_mapping(outer["payload"]))


def verify_capability_request(
    verifier: FederationVerifier,
    envelope: SignedRequestEnvelope | Mapping[str, Any],
    *,
    method: str,
    path_query: str,
    raw_body: bytes,
) -> tuple[VerifiedRequest, ParsedCapability]:
    """Parse a fixed capability and authenticate it with the matching exact scope."""

    capability = parse_capability(raw_body)
    identity = verifier.verify(
        envelope,
        method=method,
        path_query=path_query,
        raw_body=raw_body,
        required_scope=capability.name,
    )
    return identity, capability


__all__ = [
    "ADMIN_ACTIONS",
    "CAPABILITY_SCOPES",
    "CRYPTOGRAPHY_AVAILABLE",
    "PROTOCOL_VERSION",
    "AdminRequest",
    "AuthenticationError",
    "ChatMessage",
    "ComputeChatRequest",
    "ComputeEmbedRequest",
    "DataListRequest",
    "DataReadRequest",
    "EnvelopeValidationError",
    "FederationDependencyError",
    "FederationError",
    "FederationVerifier",
    "FreshnessError",
    "IdempotencyConflictError",
    "ParsedCapability",
    "PeerPublicKey",
    "PublicKeyAllowlist",
    "ReplayError",
    "RevokedKeyError",
    "SQLiteReplayStore",
    "ScopeDeniedError",
    "SignatureVerificationError",
    "SignedRequestEnvelope",
    "UnknownKeyError",
    "VerifiedRequest",
    "parse_capability",
    "require_cryptography",
    "sha256_body",
    "sign_request",
    "verify_capability_request",
]
