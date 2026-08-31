from __future__ import annotations

from urllib.parse import urlencode

import pytest

from jarvis.phone_voice import (
    PhoneVoiceConfig,
    PhoneVoiceError,
    PhoneVoiceService,
    hash_phone_pin,
    twilio_signature,
    twiml_say,
    verify_phone_pin,
)


def phone_service(database, audit) -> PhoneVoiceService:
    pin_hash = hash_phone_pin("123456", iterations=200_000, salt=b"fixed-unit-test-salt")
    config = PhoneVoiceConfig(
        enabled=True,
        public_base_url="https://phone.example.test",
        auth_token="test-token",
        allowed_callers=frozenset({"+15551234567", "+15557654321"}),
        pin_hash=pin_hash,
        max_commands=2,
    )
    return PhoneVoiceService(database, audit, config)


def test_pin_hash_is_salted_and_constant_time_verifiable() -> None:
    record = hash_phone_pin("123456", iterations=200_000, salt=b"fixed-unit-test-salt")

    assert "123456" not in record
    assert verify_phone_pin("123456", record) is True
    assert verify_phone_pin("654321", record) is False
    with pytest.raises(ValueError, match="exactly 6"):
        hash_phone_pin("1234")


def test_twilio_webhook_requires_exact_url_and_signature(database, audit) -> None:
    service = phone_service(database, audit)
    pairs = [("CallSid", "CA1234567890"), ("From", "+15551234567")]
    raw = urlencode(pairs).encode()
    signature = twilio_signature(
        "test-token", "https://phone.example.test/api/phone/voice", pairs
    )

    parameters = service.verify_webhook(
        path="/api/phone/voice", raw_body=raw, supplied_signature=signature
    )

    assert parameters["CallSid"] == "CA1234567890"
    with pytest.raises(PhoneVoiceError, match="Invalid provider signature"):
        service.verify_webhook(
            path="/api/phone/pin", raw_body=raw, supplied_signature=signature
        )


def test_phone_session_enforces_caller_pin_lockout_and_command_limit(database, audit) -> None:
    service = phone_service(database, audit)
    call_sid = "CA123456789012345"

    service.begin(call_sid, "+15551234567")
    assert service.authenticate(call_sid, "123456") is True
    assert service.claim_webhook_once(call_sid, "/api/phone/command", b"one") is True
    assert service.claim_webhook_once(call_sid, "/api/phone/command", b"one") is False
    assert service.claim_webhook_once(call_sid, "/api/phone/command", b"two") is True
    assert service.accept_command(call_sid)["remaining"] == 1
    assert service.accept_command(call_sid)["remaining"] == 0
    with pytest.raises(PhoneVoiceError, match="command limit"):
        service.accept_command(call_sid)
    with pytest.raises(PhoneVoiceError, match="identity mismatch"):
        service.begin(call_sid, "+15557654321")

    rejected_sid = "CA999999999999999"
    service.begin(rejected_sid, "+15551234567")
    assert service.authenticate(rejected_sid, "000000") is False
    assert service.authenticate(rejected_sid, "000000") is False
    assert service.authenticate(rejected_sid, "000000") is False
    with pytest.raises(PhoneVoiceError, match="not active"):
        service.authenticate(rejected_sid, "123456")


def test_twiml_escapes_spoken_text() -> None:
    xml = twiml_say("JARVIS <ready> & secure", hangup=True)

    assert "&lt;ready&gt; &amp; secure" in xml
    assert "<Hangup/>" in xml
