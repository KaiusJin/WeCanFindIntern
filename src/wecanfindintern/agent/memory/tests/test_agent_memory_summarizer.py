"""Rolling summary validation and rendering tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from wecanfindintern.agent.memory.models import MemoryMessage
from wecanfindintern.agent.memory.summarizer import (
    build_summary_prompt,
    normalize_summary,
    previous_important_ids,
    summary_text,
    validate_summary_payload,
)
from wecanfindintern.llm.gateway import parse_json


def _valid_summary() -> dict:
    return {
        "topicsCovered": ["job search"],
        "userGoals": ["find summer internship"],
        "establishedFacts": ["studies CS"],
        "preferencesStated": ["prefers Toronto"],
        "unresolvedQuestions": [],
        "importantMessageIds": [],
        "narrative": "User is looking for a summer internship.",
    }


def test_validate_summary_payload_accepts_valid():
    validate_summary_payload(_valid_summary(), set())


def test_validate_summary_payload_rejects_invented_ids():
    summary = _valid_summary()
    summary["importantMessageIds"] = ["does-not-exist"]
    with pytest.raises(ValueError):
        validate_summary_payload(summary, {"real-id"})


def test_validate_summary_payload_rejects_empty_narrative():
    with pytest.raises(ValueError):
        validate_summary_payload({"narrative": ""}, set())
    with pytest.raises(ValueError):
        validate_summary_payload({"topicsCovered": []}, set())


def test_validate_summary_payload_accepts_alias_keys_and_missing_lists():
    payload = {
        "preferences": ["Prefers Toronto"],
        "narrative": "User prefers Toronto jobs.",
    }
    validate_summary_payload(payload, set())
    normalized = normalize_summary(payload, set())
    assert normalized["preferencesStated"] == ["Prefers Toronto"]
    assert normalized["topicsCovered"] == []
    assert normalized["narrative"] == "User prefers Toronto jobs."


def test_validate_summary_payload_unwraps_nested_summary_object():
    payload = {
        "summary": {
            "preferences": ["Prefers Toronto"],
            "narrative": "User prefers Toronto jobs.",
        },
        "importantMessageIds": [],
    }
    validate_summary_payload(payload, set())
    normalized = normalize_summary(payload, set())
    assert normalized["preferencesStated"] == ["Prefers Toronto"]
    assert normalized["narrative"] == "User prefers Toronto jobs."


def test_validate_summary_payload_accepts_summary_as_narrative_string():
    payload = {"summary": "User prefers Toronto jobs.", "importantMessageIds": []}
    validate_summary_payload(payload, set())
    normalized = normalize_summary(payload, set())
    assert normalized["narrative"] == "User prefers Toronto jobs."


def test_normalize_summary_filters_unknown_ids():
    summary = _valid_summary()
    summary["importantMessageIds"] = ["known", "unknown"]
    normalized = normalize_summary(summary, {"known"})
    assert normalized["importantMessageIds"] == ["known"]


def test_previous_important_ids_parses_json():
    assert previous_important_ids(None) == set()
    payload = json.dumps({"importantMessageIds": ["a", "b"]})
    assert previous_important_ids(payload) == {"a", "b"}
    assert previous_important_ids("{not json") == set()


def test_summary_text_renders_nonempty_sections():
    text = summary_text(_valid_summary())
    assert "Narrative:" in text
    assert "prefers Toronto" in text
    assert "Unresolved questions:" not in text


def test_build_summary_prompt_contains_transcript_and_previous():
    message = MemoryMessage(
        id=uuid4(),
        session_id=uuid4(),
        role="user",
        content="hello agent",
        token_count=5,
        created_at=datetime.now(UTC),
    )
    prompt = build_summary_prompt('{"narrative":"old"}', [message])
    assert "hello agent" in prompt
    assert str(message.id) in prompt
    assert '{"narrative":"old"}' in prompt


def test_parse_json_takes_last_balanced_object_when_provider_adds_blocks():
    text = '{"reasoning":"thinking"}\n{"narrative":"final answer","topicsCovered":[]}'
    parsed = parse_json(text)
    assert parsed == {"narrative": "final answer", "topicsCovered": []}


def test_parse_json_rejects_text_without_json():
    import pytest

    with pytest.raises(ValueError):
        parse_json("no json here at all")
