"""Opt-in application service for authenticated JARVIS federation requests.

The HTTP listener remains loopback-only.  This service supplies the authenticated,
typed application boundary that a separately deployed Tailscale reverse proxy can
eventually target; it does not expose a network socket or change firewall policy.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .audit import AuditLog, utc_now
from .config import Settings
from .database import Database
from .federation import (
    AdminRequest,
    ComputeChatRequest,
    ComputeEmbedRequest,
    DataListRequest,
    DataReadRequest,
    FederationVerifier,
    ParsedCapability,
    PeerPublicKey,
    PublicKeyAllowlist,
    SignedRequestEnvelope,
    SQLiteReplayStore,
    VerifiedRequest,
    verify_capability_request,
)
from .providers import OllamaError, OllamaProvider
from .services import ApprovalService

FEDERATION_ENDPOINT = "/api/federation/v1/capabilities"
FEDERATION_ENVELOPE_HEADER = "X-Jarvis-Federation-Envelope"
FEDERATION_ADMIN_APPROVAL_TOOL = "federation_admin_proposal"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,62}[A-Za-z0-9])?$")
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


class FederationConfigurationError(ValueError):
    """Raised when an explicitly enabled federation configuration is unsafe."""


class FederationDisabledError(RuntimeError):
    """Raised when a request reaches the disabled-by-default capability endpoint."""


class FederationRequestStateError(RuntimeError):
    """Raised when an idempotent retry cannot safely return its original outcome."""


class FederationCapabilityError(RuntimeError):
    """Safe capability execution error with an HTTP classification."""

    def __init__(self, detail: str, *, status_code: int = 400) -> None:
        super().__init__(detail)
        self.status_code = status_code


def _enabled_from_environment() -> bool:
    return os.getenv("JARVIS_FEDERATION_ENABLED", "").strip().casefold() in _TRUE_VALUES


def _load_json_environment(name: str, default: Any) -> Any:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FederationConfigurationError(f"{name} must contain valid JSON") from exc


def _node_id_from_environment() -> str:
    node_id = os.getenv("JARVIS_FEDERATION_NODE_ID", "").strip()
    if not _IDENTIFIER_RE.fullmatch(node_id):
        raise FederationConfigurationError(
            "JARVIS_FEDERATION_NODE_ID must be a valid 1-64 character identifier"
        )
    return node_id


def _peer_keys_from_environment() -> tuple[PeerPublicKey, ...]:
    records = _load_json_environment("JARVIS_FEDERATION_PEERS", [])
    if not isinstance(records, list) or not (1 <= len(records) <= 64):
        raise FederationConfigurationError(
            "JARVIS_FEDERATION_PEERS must contain between 1 and 64 peer key records"
        )
    keys: list[PeerPublicKey] = []
    allowed_fields = {"peer_id", "key_id", "public_key", "scopes", "revoked"}
    required_fields = {"peer_id", "key_id", "public_key", "scopes"}
    for record in records:
        if not isinstance(record, Mapping):
            raise FederationConfigurationError("each federation peer key must be an object")
        fields = set(record)
        if not required_fields <= fields or not fields <= allowed_fields:
            raise FederationConfigurationError("a federation peer key has invalid fields")
        scopes = record["scopes"]
        if not isinstance(scopes, list) or not scopes:
            raise FederationConfigurationError("each federation peer key needs fixed scopes")
        keys.append(
            PeerPublicKey.from_base64url(
                peer_id=record["peer_id"],
                key_id=record["key_id"],
                public_key=record["public_key"],
                scopes=scopes,
                revoked=record.get("revoked", False),
            )
        )
    return tuple(keys)


def _named_roots_from_environment(settings: Settings) -> dict[str, Path]:
    records = _load_json_environment("JARVIS_FEDERATION_ROOTS", {})
    if not isinstance(records, Mapping) or len(records) > 16:
        raise FederationConfigurationError(
            "JARVIS_FEDERATION_ROOTS must be an object containing at most 16 roots"
        )
    roots: dict[str, Path] = {}
    for name, value in records.items():
        if not isinstance(name, str) or not _IDENTIFIER_RE.fullmatch(name):
            raise FederationConfigurationError("federation root names must be identifiers")
        if not isinstance(value, str) or not value.strip():
            raise FederationConfigurationError("federation root paths must be non-empty strings")
        resolved = Path(value).expanduser().resolve()
        if not any(
            resolved == allowed or resolved.is_relative_to(allowed)
            for allowed in settings.allowed_roots
        ):
            raise FederationConfigurationError(
                "every federation root must stay within an application allowed root"
            )
        roots[name] = resolved
    return roots


class FederationResultStore:
    """Persist completed responses so request-ID retries never repeat effects."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self._lock = threading.RLock()
        with self.database.transaction() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS federation_results (
                    peer_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('processing','completed','failed')),
                    response_json TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    PRIMARY KEY (peer_id, request_id)
                )"""
            )

    def claim(self, identity: VerifiedRequest, capability: str) -> None:
        try:
            with self._lock, self.database.transaction() as connection:
                connection.execute(
                    """INSERT INTO federation_results
                    (peer_id, request_id, capability, state, created_at)
                    VALUES (?, ?, ?, 'processing', ?)""",
                    (identity.peer_id, identity.request_id, capability, utc_now()),
                )
        except sqlite3.IntegrityError as exc:
            raise FederationRequestStateError(
                "federation request ID already has a durable outcome claim"
            ) from exc

    def finish(
        self,
        identity: VerifiedRequest,
        response: dict[str, Any],
        *,
        failed: bool = False,
    ) -> None:
        encoded = json.dumps(response, sort_keys=True, separators=(",", ":"))
        with self._lock, self.database.transaction() as connection:
            cursor = connection.execute(
                """UPDATE federation_results
                SET state=?, response_json=?, completed_at=?
                WHERE peer_id=? AND request_id=? AND state='processing'""",
                (
                    "failed" if failed else "completed",
                    encoded,
                    utc_now(),
                    identity.peer_id,
                    identity.request_id,
                ),
            )
        if cursor.rowcount != 1:
            raise FederationRequestStateError(
                "federation request outcome was not in the expected processing state"
            )

    def get(self, identity: VerifiedRequest) -> tuple[str, dict[str, Any] | None] | None:
        row = self.database.query_one(
            """SELECT state, response_json FROM federation_results
            WHERE peer_id=? AND request_id=?""",
            (identity.peer_id, identity.request_id),
        )
        if row is None:
            return None
        response = json.loads(row["response_json"]) if row["response_json"] else None
        return str(row["state"]), response


class FederationService:
    """Authenticate and dispatch the five fixed federation capabilities."""

    def __init__(
        self,
        *,
        enabled: bool,
        node_id: str | None,
        verifier: FederationVerifier | None,
        peer_keys: tuple[PeerPublicKey, ...],
        named_roots: dict[str, Path],
        replay_store: SQLiteReplayStore | None,
        result_store: FederationResultStore | None,
        provider: OllamaProvider,
        approvals: ApprovalService,
        audit: AuditLog,
        settings: Settings,
        configuration_error: str | None = None,
    ) -> None:
        self.enabled = enabled
        self.node_id = node_id
        self.verifier = verifier
        self.peer_keys = peer_keys
        self.named_roots = named_roots
        self.replay_store = replay_store
        self.result_store = result_store
        self.provider = provider
        self.approvals = approvals
        self.audit = audit
        self.settings = settings
        self.configuration_error = configuration_error

    @classmethod
    def from_environment(
        cls,
        settings: Settings,
        database: Database,
        provider: OllamaProvider,
        approvals: ApprovalService,
        audit: AuditLog,
    ) -> FederationService:
        """Build an opt-in service without making invalid config remotely reachable."""

        if not _enabled_from_environment():
            return cls(
                enabled=False,
                node_id=None,
                verifier=None,
                peer_keys=(),
                named_roots={},
                replay_store=None,
                result_store=None,
                provider=provider,
                approvals=approvals,
                audit=audit,
                settings=settings,
            )
        replay_store: SQLiteReplayStore | None = None
        try:
            node_id = _node_id_from_environment()
            keys = _peer_keys_from_environment()
            roots = _named_roots_from_environment(settings)
            replay_store = SQLiteReplayStore(settings.data_dir / "federation-replay.sqlite3")
            verifier = FederationVerifier(PublicKeyAllowlist(keys), replay_store)
            result_store = FederationResultStore(database)
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            if replay_store is not None:
                replay_store.close()
            return cls(
                enabled=False,
                node_id=None,
                verifier=None,
                peer_keys=(),
                named_roots={},
                replay_store=None,
                result_store=None,
                provider=provider,
                approvals=approvals,
                audit=audit,
                settings=settings,
                configuration_error=type(exc).__name__,
            )
        return cls(
            enabled=True,
            node_id=node_id,
            verifier=verifier,
            peer_keys=keys,
            named_roots=roots,
            replay_store=replay_store,
            result_store=result_store,
            provider=provider,
            approvals=approvals,
            audit=audit,
            settings=settings,
        )

    def status(self) -> dict[str, Any]:
        """Return readiness without exposing peer IDs, keys, paths, or request data."""

        active_keys = tuple(key for key in self.peer_keys if not key.revoked)
        scopes = sorted({scope for key in active_keys for scope in key.scopes})
        return {
            "enabled": self.enabled,
            "ready": self.enabled and self.verifier is not None,
            "protocol": "jarvis-federation-v1",
            "endpoint": FEDERATION_ENDPOINT,
            "listener": "loopback-only",
            "transport_exposed": False,
            "peer_key_count": len(active_keys),
            "scopes": scopes,
            "named_roots": sorted(self.named_roots),
            "configuration": "invalid" if self.configuration_error else "valid",
            "configuration_error": self.configuration_error,
            "requires_external_transport_deployment": True,
            "external_proxy_must_expose_only": FEDERATION_ENDPOINT,
        }

    def close(self) -> None:
        if self.replay_store is not None:
            self.replay_store.close()

    def parse_envelope_header(self, value: str | None) -> SignedRequestEnvelope:
        if value is None or not value or len(value.encode("utf-8")) > 8192:
            raise FederationCapabilityError("signed federation envelope is required")
        try:
            mapping = json.loads(value)
        except json.JSONDecodeError as exc:
            raise FederationCapabilityError("signed federation envelope is invalid") from exc
        return SignedRequestEnvelope.from_mapping(mapping)

    async def handle(
        self,
        envelope: SignedRequestEnvelope,
        *,
        method: str,
        path_query: str,
        raw_body: bytes,
    ) -> dict[str, Any]:
        if not self.enabled or self.verifier is None or self.result_store is None:
            raise FederationDisabledError("federation is disabled or not safely configured")
        identity, capability = verify_capability_request(
            self.verifier,
            envelope,
            method=method,
            path_query=path_query,
            raw_body=raw_body,
        )
        if identity.idempotent:
            return self._idempotent_response(identity, capability)

        self.result_store.claim(identity, capability.name)
        actor = f"federation:{identity.peer_id}"
        self.audit.append(
            actor,
            "federation.request.accepted",
            f"federation:{capability.name}",
            {"request_id": identity.request_id, "key_id": identity.key_id},
        )
        try:
            response = await self._execute(identity, capability)
        except (FederationCapabilityError, OllamaError) as exc:
            error_status = exc.status_code if isinstance(exc, FederationCapabilityError) else 503
            safe_response = {
                "status": "failed",
                "capability": capability.name,
                "request_id": identity.request_id,
                "detail": "capability execution failed safely",
                "error_status": error_status,
            }
            self.result_store.finish(identity, safe_response, failed=True)
            self.audit.append(
                actor,
                "federation.request.failed",
                f"federation:{capability.name}",
                {"request_id": identity.request_id, "error_type": type(exc).__name__},
            )
            raise
        except Exception as exc:
            safe_response = {
                "status": "failed",
                "capability": capability.name,
                "request_id": identity.request_id,
                "detail": "capability execution failed safely",
                "error_status": 500,
            }
            self.result_store.finish(identity, safe_response, failed=True)
            self.audit.append(
                actor,
                "federation.request.failed",
                f"federation:{capability.name}",
                {"request_id": identity.request_id, "error_type": type(exc).__name__},
            )
            raise FederationCapabilityError(
                "capability execution failed safely", status_code=500
            ) from exc
        self.result_store.finish(identity, response)
        self.audit.append(
            actor,
            "federation.request.completed",
            f"federation:{capability.name}",
            {"request_id": identity.request_id},
        )
        return response

    def _idempotent_response(
        self, identity: VerifiedRequest, capability: ParsedCapability
    ) -> dict[str, Any]:
        if self.result_store is None:
            raise FederationRequestStateError("federation result store is unavailable")
        cached = self.result_store.get(identity)
        if cached is None or cached[1] is None:
            raise FederationRequestStateError(
                "the original request is still processing or its outcome is unavailable"
            )
        state, response = cached
        assert response is not None
        self.audit.append(
            f"federation:{identity.peer_id}",
            "federation.request.idempotent",
            f"federation:{capability.name}",
            {"request_id": identity.request_id, "state": state},
        )
        if state == "failed":
            raise FederationCapabilityError(
                str(response.get("detail", "capability execution failed safely")),
                status_code=int(response.get("error_status", 500)),
            )
        return {**response, "idempotent": True}

    async def _execute(
        self, identity: VerifiedRequest, capability: ParsedCapability
    ) -> dict[str, Any]:
        payload = capability.payload
        if isinstance(payload, ComputeChatRequest):
            result = await self.provider.chat(
                [
                    {"role": message.role, "content": message.content}
                    for message in payload.messages
                ],
                model=payload.model,
                tools=None,
            )
            message = result.get("message", {})
            content = str(message.get("content", ""))
            content = content[: payload.max_tokens * 8]
            return {
                "status": "completed",
                "capability": capability.name,
                "request_id": identity.request_id,
                "model": str(result.get("model", payload.model)),
                "message": {"role": "assistant", "content": content},
            }
        if isinstance(payload, ComputeEmbedRequest):
            embeddings = await self.provider.embed(payload.inputs, model=payload.model)
            return {
                "status": "completed",
                "capability": capability.name,
                "request_id": identity.request_id,
                "model": payload.model,
                "embeddings": embeddings,
            }
        if isinstance(payload, DataListRequest):
            return self._list_data(identity, payload)
        if isinstance(payload, DataReadRequest):
            return self._read_data(identity, payload)
        if isinstance(payload, AdminRequest):
            return self._queue_admin(identity, payload)
        raise FederationCapabilityError("unsupported federation capability")

    def _resolve_data_path(self, root_name: str, relative_path: str) -> tuple[Path, Path]:
        root = self.named_roots.get(root_name)
        if root is None:
            raise FederationCapabilityError(
                "the requested named root is unavailable", status_code=404
            )
        try:
            root = root.resolve(strict=True)
            path = (root / relative_path).resolve(strict=True) if relative_path else root
        except (OSError, RuntimeError) as exc:
            raise FederationCapabilityError(
                "the requested data path is unavailable", status_code=404
            ) from exc
        if path != root and not path.is_relative_to(root):
            raise FederationCapabilityError("the requested data path escaped its named root")
        relative = path.relative_to(root)
        if self.settings.deny_hidden_paths and any(part.startswith(".") for part in relative.parts):
            raise FederationCapabilityError("hidden data paths are blocked by policy")
        return root, path

    def _list_data(self, identity: VerifiedRequest, payload: DataListRequest) -> dict[str, Any]:
        root, path = self._resolve_data_path(payload.root, payload.prefix)
        if not path.is_dir():
            raise FederationCapabilityError("the requested data path is not a directory")
        entries: list[dict[str, Any]] = []
        for child in sorted(path.iterdir(), key=lambda item: item.name.casefold()):
            if len(entries) >= payload.limit:
                break
            if self.settings.deny_hidden_paths and child.name.startswith("."):
                continue
            try:
                resolved = child.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if resolved != root and not resolved.is_relative_to(root):
                continue
            entries.append(
                {
                    "path": resolved.relative_to(root).as_posix(),
                    "type": "directory" if resolved.is_dir() else "file",
                    "size_bytes": resolved.stat().st_size if resolved.is_file() else None,
                }
            )
        return {
            "status": "completed",
            "capability": "data.list",
            "request_id": identity.request_id,
            "root": payload.root,
            "prefix": payload.prefix,
            "entries": entries,
            "truncated": len(entries) >= payload.limit,
        }

    def _read_data(self, identity: VerifiedRequest, payload: DataReadRequest) -> dict[str, Any]:
        _root, path = self._resolve_data_path(payload.root, payload.relative_path)
        if not path.is_file():
            raise FederationCapabilityError("the requested data path is not a file")
        size = path.stat().st_size
        if size > payload.max_bytes:
            raise FederationCapabilityError("the requested file exceeds the signed byte limit")
        try:
            with path.open("rb") as handle:
                content = handle.read(payload.max_bytes + 1)
        except OSError as exc:
            raise FederationCapabilityError("the requested file could not be read") from exc
        if len(content) > payload.max_bytes:
            raise FederationCapabilityError("the requested file exceeds the signed byte limit")
        return {
            "status": "completed",
            "capability": "data.read",
            "request_id": identity.request_id,
            "root": payload.root,
            "relative_path": payload.relative_path,
            "encoding": "base64",
            "content": base64.b64encode(content).decode("ascii"),
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }

    def _queue_admin(self, identity: VerifiedRequest, payload: AdminRequest) -> dict[str, Any]:
        if payload.target_peer_id != self.node_id:
            raise FederationCapabilityError(
                "administrative proposals must target this local node", status_code=409
            )
        approval = self.approvals.create(
            f"federation:{identity.peer_id}",
            FEDERATION_ADMIN_APPROVAL_TOOL,
            {
                "action": payload.action,
                "target_peer_id": payload.target_peer_id,
                "reason": payload.reason,
                "federation_request_id": identity.request_id,
                "requesting_peer_id": identity.peer_id,
            },
            "A signed remote administrative proposal requires approval on this target",
        )
        self.audit.append(
            f"federation:{identity.peer_id}",
            "federation.admin.queued",
            f"approval:{approval['id']}",
            {"action": payload.action, "request_id": identity.request_id},
        )
        return {
            "status": "approval_required",
            "capability": "admin.request",
            "request_id": identity.request_id,
            "approval": {
                "id": approval["id"],
                "status": approval["status"],
                "expires_at": approval["expires_at"],
            },
            "executed": False,
        }

    def approve_admin_proposal(self, request_id: str, actor: str) -> dict[str, Any]:
        """Acknowledge a proposal after target-local approval without executing it."""

        pending = self.approvals.get(request_id)
        if pending is None:
            raise KeyError("Approval request not found")
        if pending.get("tool_name") != FEDERATION_ADMIN_APPROVAL_TOOL:
            raise FederationRequestStateError("approval is not a federation admin proposal")
        request = self.approvals.claim_for_execution(request_id, actor)
        if request.get("tool_name") != FEDERATION_ADMIN_APPROVAL_TOOL:
            raise FederationRequestStateError("approval is not a federation admin proposal")
        result = {
            "status": "approved_proposal",
            "executed": False,
            "message": "Proposal approved for separate local implementation; no action executed",
        }
        self.approvals.record_execution(
            request_id,
            succeeded=True,
            actor=actor,
            result=result,
        )
        self.audit.append(
            actor,
            "federation.admin.approved_no_execution",
            f"approval:{request_id}",
            {"action": request.get("arguments", {}).get("action")},
        )
        return result


__all__ = [
    "FEDERATION_ADMIN_APPROVAL_TOOL",
    "FEDERATION_ENDPOINT",
    "FEDERATION_ENVELOPE_HEADER",
    "FederationCapabilityError",
    "FederationConfigurationError",
    "FederationDisabledError",
    "FederationRequestStateError",
    "FederationService",
]
