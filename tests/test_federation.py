"""Security and schema tests for the standalone federation authentication core."""

from __future__ import annotations

import json

import pytest

from jarvis.federation import (
    CRYPTOGRAPHY_AVAILABLE,
    AdminRequest,
    ComputeChatRequest,
    DataReadRequest,
    EnvelopeValidationError,
    FederationDependencyError,
    FederationVerifier,
    FreshnessError,
    IdempotencyConflictError,
    PeerPublicKey,
    PublicKeyAllowlist,
    ReplayError,
    RevokedKeyError,
    SignatureVerificationError,
    SQLiteReplayStore,
    UnknownKeyError,
    parse_capability,
    require_cryptography,
    sign_request,
)

if CRYPTOGRAPHY_AVAILABLE:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


NOW = 1_800_000_000
PEER_ID = "peer.alpha"
KEY_ID = "key.primary"
PATH = "/v1/federation/capabilities?mode=sync"


def _body(capability: str = "compute.chat", payload: dict[str, object] | None = None) -> bytes:
    if payload is None:
        payload = {
            "model": "llama3:latest",
            "messages": [{"role": "user", "content": "Return a concise health summary."}],
        }
    return json.dumps(
        {"capability": capability, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def test_dependency_fails_closed_when_cryptography_is_missing() -> None:
    if CRYPTOGRAPHY_AVAILABLE:
        require_cryptography()
    else:
        with pytest.raises(FederationDependencyError, match="cryptography"):
            require_cryptography()


def test_capabilities_are_strict_and_paths_are_confined_to_named_roots() -> None:
    parsed = parse_capability(_body())
    assert parsed.name == "compute.chat"
    assert isinstance(parsed.payload, ComputeChatRequest)
    assert parsed.payload.messages[0].role == "user"

    read = parse_capability(
        _body(
            "data.read",
            {"root": "documents", "relative_path": "reports/status.txt", "max_bytes": 4096},
        )
    )
    assert isinstance(read.payload, DataReadRequest)
    assert read.payload.root == "documents"

    for unsafe_path in ("../secret.txt", "/Windows/system.ini", "C:/secret.txt", "a\\b"):
        with pytest.raises(EnvelopeValidationError):
            parse_capability(
                _body("data.read", {"root": "documents", "relative_path": unsafe_path})
            )

    with pytest.raises(EnvelopeValidationError):
        parse_capability(
            _body(
                "compute.chat",
                {
                    "model": "llama3:latest",
                    "messages": [{"role": "user", "content": "hello"}],
                    "command": "whoami",
                },
            )
        )
    with pytest.raises(EnvelopeValidationError):
        parse_capability(_body("shell.execute", {"command": "whoami"}))


def test_admin_model_accepts_only_fixed_proposal_actions() -> None:
    parsed = parse_capability(
        _body(
            "admin.request",
            {
                "action": "key.rotate.request",
                "target_peer_id": "peer.beta",
                "reason": "Routine scheduled key rotation",
            },
        )
    )
    assert isinstance(parsed.payload, AdminRequest)
    with pytest.raises(EnvelopeValidationError):
        parse_capability(
            _body(
                "admin.request",
                {
                    "action": "script.execute",
                    "target_peer_id": "peer.beta",
                    "reason": "This must never be accepted",
                },
            )
        )


pytestmark_crypto = pytest.mark.skipif(
    not CRYPTOGRAPHY_AVAILABLE, reason="cryptography Ed25519 dependency is not installed"
)


@pytest.fixture
def private_key():
    if not CRYPTOGRAPHY_AVAILABLE:
        pytest.skip("cryptography Ed25519 dependency is not installed")
    return Ed25519PrivateKey.generate()


@pytest.fixture
def public_record(private_key) -> PeerPublicKey:
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return PeerPublicKey(
        peer_id=PEER_ID,
        key_id=KEY_ID,
        public_key=public_bytes,
        scopes=frozenset({"compute.chat", "compute.embed", "data.read"}),
    )


@pytest.fixture
def verifier(public_record) -> FederationVerifier:
    return FederationVerifier(
        PublicKeyAllowlist([public_record]),
        SQLiteReplayStore(),
        clock=lambda: NOW,
    )


def _sign(private_key, body: bytes, *, nonce: str, request_id: str, **changes):
    values = {
        "peer_id": PEER_ID,
        "key_id": KEY_ID,
        "method": "POST",
        "path_query": PATH,
        "timestamp": NOW,
        "nonce": nonce,
        "request_id": request_id,
        "raw_body": body,
    }
    values.update(changes)
    return sign_request(private_key, **values)


@pytestmark_crypto
def test_verified_request_returns_effective_peer_scopes_and_idempotency(
    private_key, verifier
) -> None:
    body = _body()
    first = _sign(
        private_key,
        body,
        nonce="nonce-token-00001",
        request_id="request-token-001",
    )
    identity = verifier.verify(
        first, method="POST", path_query=PATH, raw_body=body, required_scope="compute.chat"
    )
    assert identity.peer_id == PEER_ID
    assert identity.key_id == KEY_ID
    assert identity.scopes == frozenset({"compute.chat", "compute.embed", "data.read"})
    assert identity.idempotent is False

    retry = _sign(
        private_key,
        body,
        nonce="nonce-token-00002",
        request_id="request-token-001",
    )
    retried = verifier.verify(retry, method="POST", path_query=PATH, raw_body=body)
    assert retried.idempotent is True


@pytestmark_crypto
@pytest.mark.parametrize(
    ("actual_path", "actual_body"),
    [
        (PATH + "&altered=true", _body()),
        (PATH, _body("compute.embed", {"model": "nomic-embed-text:latest", "inputs": ["x"]})),
    ],
)
def test_altered_body_or_path_is_rejected(private_key, verifier, actual_path, actual_body) -> None:
    signed_body = _body()
    envelope = _sign(
        private_key,
        signed_body,
        nonce="nonce-token-00003",
        request_id="request-token-002",
    )
    with pytest.raises(SignatureVerificationError):
        verifier.verify(envelope, method="POST", path_query=actual_path, raw_body=actual_body)


@pytestmark_crypto
@pytest.mark.parametrize("timestamp", [NOW - 61, NOW + 61])
def test_stale_and_future_timestamps_are_rejected(private_key, verifier, timestamp) -> None:
    body = _body()
    envelope = _sign(
        private_key,
        body,
        nonce=f"nonce-token-{timestamp}",
        request_id=f"request-token-{timestamp}",
        timestamp=timestamp,
    )
    with pytest.raises(FreshnessError):
        verifier.verify(envelope, method="POST", path_query=PATH, raw_body=body)


@pytestmark_crypto
def test_reused_nonce_is_rejected(private_key, verifier) -> None:
    body = _body()
    first = _sign(
        private_key,
        body,
        nonce="nonce-token-00004",
        request_id="request-token-004",
    )
    verifier.verify(first, method="POST", path_query=PATH, raw_body=body)
    replay = _sign(
        private_key,
        body,
        nonce="nonce-token-00004",
        request_id="request-token-005",
    )
    with pytest.raises(ReplayError):
        verifier.verify(replay, method="POST", path_query=PATH, raw_body=body)


@pytestmark_crypto
def test_conflicting_request_id_is_rejected_and_its_nonce_is_consumed(
    private_key, verifier
) -> None:
    first_body = _body()
    first = _sign(
        private_key,
        first_body,
        nonce="nonce-token-00005",
        request_id="request-token-006",
    )
    verifier.verify(first, method="POST", path_query=PATH, raw_body=first_body)

    changed_body = _body(
        "compute.embed", {"model": "nomic-embed-text:latest", "inputs": ["status"]}
    )
    conflict = _sign(
        private_key,
        changed_body,
        nonce="nonce-token-00006",
        request_id="request-token-006",
    )
    with pytest.raises(IdempotencyConflictError):
        verifier.verify(conflict, method="POST", path_query=PATH, raw_body=changed_body)
    with pytest.raises(ReplayError):
        verifier.verify(conflict, method="POST", path_query=PATH, raw_body=changed_body)


@pytestmark_crypto
def test_unknown_and_revoked_keys_are_rejected_before_replay(private_key, public_record) -> None:
    body = _body()
    envelope = _sign(
        private_key,
        body,
        nonce="nonce-token-00007",
        request_id="request-token-007",
    )
    unknown = FederationVerifier(PublicKeyAllowlist(), SQLiteReplayStore(), clock=lambda: NOW)
    with pytest.raises(UnknownKeyError):
        unknown.verify(envelope, method="POST", path_query=PATH, raw_body=body)

    allowlist = PublicKeyAllowlist([public_record])
    allowlist.revoke(PEER_ID, KEY_ID)
    revoked = FederationVerifier(allowlist, SQLiteReplayStore(), clock=lambda: NOW)
    with pytest.raises(RevokedKeyError):
        revoked.verify(envelope, method="POST", path_query=PATH, raw_body=body)
