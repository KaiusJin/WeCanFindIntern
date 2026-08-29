"""Memory extraction validation tests."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from wecanfindintern.agent.memory.extraction import (
    build_extraction_prompt,
    validate_extraction_payload,
)
from wecanfindintern.agent.memory.models import MemoryMessage


def test_validate_extraction_payload_accepts_valid():
    payload = {
        "memories": [
            {
                "memoryType": "USER_PREFERENCE",
                "content": "Prefers Toronto jobs.",
                "confidence": 0.9,
                "sourceMessageId": "msg-1",
                "ttlDays": 0,
            }
        ]
    }
    validate_extraction_payload(payload, {"msg-1"})


def test_validate_extraction_payload_rejects_disallowed_type_and_ids():
    payload = {
        "memories": [
            {
                "memoryType": "SENSITIVE_TRAIT",
                "content": "x",
                "confidence": 1.0,
                "sourceMessageId": "nope",
                "ttlDays": 0,
            }
        ]
    }
    with pytest.raises(ValueError):
        validate_extraction_payload(payload, {"msg-1"})


def test_validate_extraction_payload_rejects_bad_confidence():
    payload = {
        "memories": [
            {
                "memoryType": "EXPLICIT_FACT",
                "content": "remember this",
                "confidence": 1.5,
                "sourceMessageId": "",
                "ttlDays": 0,
            }
        ]
    }
    with pytest.raises(ValueError):
        validate_extraction_payload(payload, set())


def test_build_extraction_prompt_contains_guidance_and_transcript():
    message = MemoryMessage(
        id=uuid4(),
        session_id=uuid4(),
        role="user",
        content="I prefer remote roles",
        token_count=5,
        created_at=datetime.now(UTC),
    )
    prompt = build_extraction_prompt([message], "summary so far")
    assert "USER_PREFERENCE" in prompt
    assert "I prefer remote roles" in prompt
    assert "summary so far" in prompt
    assert str(message.id) in prompt
