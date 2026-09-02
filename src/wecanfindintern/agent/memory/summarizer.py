"""Rolling conversation summary: merge previous state with evicted turns."""

from __future__ import annotations

import json

from wecanfindintern.agent.contracts import AgentDeps, ToolError
from wecanfindintern.agent.memory.config import settings
from wecanfindintern.agent.memory.models import MemoryMessage
from wecanfindintern.agent.memory.tokens import estimate_tokens
from wecanfindintern.llm.gateway import complete_json

SUMMARY_KEYS = {
    "topicsCovered",
    "userGoals",
    "establishedFacts",
    "preferencesStated",
    "unresolvedQuestions",
    "importantMessageIds",
    "narrative",
}

KEY_ALIASES: dict[str, set[str]] = {
    "topicsCovered": {"topicsCovered", "topics"},
    "userGoals": {"userGoals", "goals"},
    "establishedFacts": {"establishedFacts", "facts"},
    "preferencesStated": {"preferencesStated", "preferences"},
    "unresolvedQuestions": {"unresolvedQuestions", "questions"},
    "importantMessageIds": {"importantMessageIds"},
    "narrative": {"narrative"},
}


def previous_important_ids(previous_summary_json: str | None) -> set[str]:
    if not previous_summary_json:
        return set()
    try:
        parsed = json.loads(previous_summary_json)
    except json.JSONDecodeError:
        return set()
    ids = parsed.get("importantMessageIds") if isinstance(parsed, dict) else None
    return {str(item) for item in ids} if isinstance(ids, list) else set()


def validate_summary_payload(parsed: dict, known_message_ids: set[str]) -> None:
    parsed = _unwrap_summary(parsed)
    if not isinstance(parsed, dict) or not parsed:
        raise ValueError("Summary response is missing required fields.")
    narrative = parsed.get("narrative")
    if isinstance(narrative, (dict, list)) or narrative is None or not str(narrative).strip():
        raise ValueError("Summary narrative must be a non-empty string.")
    for key in SUMMARY_KEYS - {"narrative"}:
        value = _canonical_value(parsed, key)
        if value is None:
            continue  # optional lists default to []
        if not isinstance(value, list) or any(
            not isinstance(item, str) for item in value
        ):
            raise ValueError(f"Summary field {key} must be an array of strings.")
        if key == "importantMessageIds":
            invented = [item for item in value if item not in known_message_ids]
            if invented:
                raise ValueError(
                    f"Summary referenced message ids that do not exist: {invented[:5]}"
                )


def normalize_summary(parsed: dict, known_message_ids: set[str]) -> dict:
    parsed = _unwrap_summary(parsed)
    normalized = {
        key: _canonical_value(parsed, key) or []
        for key in SUMMARY_KEYS - {"narrative"}
    }
    normalized["narrative"] = str(parsed.get("narrative", ""))
    normalized["importantMessageIds"] = [
        item
        for item in dict.fromkeys(normalized["importantMessageIds"])
        if item in known_message_ids
    ]
    return normalized


def _unwrap_summary(parsed: dict) -> dict:
    """Handle providers that wrap the schema in varying shapes.

    Observed variants: ``{"summary": "<narrative string>"}`` and
    ``{"summary": {narrative, ...}, ...}``.
    """

    nested = parsed.get("summary") if isinstance(parsed, dict) else None
    if isinstance(nested, str) and nested.strip():
        merged = dict(parsed)
        merged["narrative"] = nested
        merged.pop("summary", None)
        return merged
    if isinstance(nested, dict) and "narrative" in nested:
        merged = {**nested}
        for key, value in parsed.items():
            if key != "summary":
                merged.setdefault(key, value)
        return merged
    return parsed


def _canonical_value(parsed: dict, key: str) -> list[str] | str | None:
    for alias in KEY_ALIASES[key]:
        if alias in parsed:
            return parsed[alias]
    return None


def summary_text(summary: dict) -> str:
    sections = [
        ("Narrative", summary.get("narrative", "")),
        ("Topics covered", "; ".join(summary.get("topicsCovered", []))),
        ("User goals", "; ".join(summary.get("userGoals", []))),
        ("Established facts", "; ".join(summary.get("establishedFacts", []))),
        (
            "Preferences stated",
            "; ".join(summary.get("preferencesStated", [])),
        ),
        ("Unresolved questions", "; ".join(summary.get("unresolvedQuestions", []))),
    ]
    return "\n".join(
        f"{title}: {value}" for title, value in sections if value.strip()
    )


def build_summary_prompt(
    previous_summary_json: str | None,
    evicted_messages: list[MemoryMessage],
) -> str:
    transcript = "\n".join(
        f'<message id="{message.id}" role="{message.role}">\n{message.content}\n</message>'
        for message in evicted_messages
    )
    previous = previous_summary_json or "null"
    return (
        "You maintain the rolling summary of a job-search assistant "
        "conversation for WeCanFindIntern.\n\n"
        "Merge the previous summary state with the new conversation turns "
        "below into ONE updated summary.\n\n"
        "Rules:\n"
        "- Preserve still-relevant facts from the previous summary; drop "
        "resolved or obsolete items.\n"
        "- The summary is conversational state, NOT job data or source material.\n"
        "- importantMessageIds may only contain ids that literally appear in "
        "the input.\n"
        f"- Keep the narrative under {settings.summary_max_tokens} tokens; be "
        "dense, factual, and neutral.\n"
        "- Do not invent topics, goals, facts, or preferences that are not "
        "grounded in the input.\n"
        "- The conversation is in Chinese or English; keep the narrative in the "
        "language of the turns.\n\n"
        "Previous summary state (JSON or null):\n"
        f"{previous}\n\n"
        f"New conversation turns to fold in:\n{transcript}\n"
    )


def build_rolling_summary(
    deps: AgentDeps,
    previous_summary_json: str | None,
    evicted_messages: list[MemoryMessage],
) -> dict:
    """Fold evicted turns into the rolling summary (incremental by design)."""

    if not evicted_messages:
        raise ValueError("Rolling summary requires at least one evicted message.")
    if deps.llm_config is None:
        raise ToolError("llm_config_missing", "AI model configuration is required.")
    known_ids = {str(message.id) for message in evicted_messages} | previous_important_ids(
        previous_summary_json
    )
    base_prompt = build_summary_prompt(previous_summary_json, evicted_messages)
    last_error: Exception | None = None
    for attempt in range(3):
        feedback = (
            ""
            if attempt == 0
            else f"\n\nPrevious response failed validation: {last_error}. "
            "Fix the output and try again."
        )
        result = complete_json(
            provider=deps.llm_config.provider,
            model_name=deps.llm_config.model_name,
            api_key=deps.llm_config.api_key,
            system_prompt=(
                "You produce structured conversation summaries. Output ONLY JSON "
                "matching the requested schema. Never invent message ids or facts."
            ),
            user_prompt=base_prompt + feedback,
        )
        parsed = result.data
        try:
            if not isinstance(parsed, dict):
                raise ValueError("Summary response was not a JSON object.")
            validate_summary_payload(parsed, known_ids)
            return normalize_summary(parsed, known_ids)
        except (ValueError, TypeError) as error:
            last_error = error
    raise ValueError(f"Summary failed validation after retries: {last_error}")


def summary_token_count(text: str) -> int:
    return estimate_tokens(text)
