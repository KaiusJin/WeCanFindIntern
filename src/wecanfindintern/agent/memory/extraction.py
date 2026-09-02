"""Long-term memory extraction from new conversation turns."""

from __future__ import annotations

from uuid import UUID

from wecanfindintern.agent.contracts import AgentDeps, ToolError
from wecanfindintern.agent.memory.config import settings
from wecanfindintern.agent.memory.models import (
    MEMORY_TYPES,
    MemoryCandidate,
    MemoryMessage,
)
from wecanfindintern.llm.gateway import complete_json


def validate_extraction_payload(parsed: dict, known_message_ids: set[str]) -> None:
    if not isinstance(parsed, dict) or "memories" not in parsed:
        raise ValueError("Extraction response must contain a memories array.")
    memories = parsed["memories"]
    if not isinstance(memories, list):
        raise ValueError("Extraction memories must be an array.")
    for index, item in enumerate(memories):
        if not isinstance(item, dict):
            raise ValueError(f"Extraction memory {index} must be an object.")
        if item.get("memoryType") not in MEMORY_TYPES:
            raise ValueError(f"Extraction memory {index} has a disallowed memoryType.")
        content = item.get("content")
        if isinstance(content, (dict, list)):
            raise ValueError(
                f"Extraction memory {index} content must be a string, got {type(content).__name__}."
            )
        if content is None or not str(content).strip():
            raise ValueError(f"Extraction memory {index} content must be a non-empty string.")
        confidence = item.get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1
        ):
            raise ValueError(f"Extraction memory {index} confidence must be in [0, 1].")
        source_id = item.get("sourceMessageId")
        if not isinstance(source_id, str):
            raise ValueError(f"Extraction memory {index} sourceMessageId must be a string.")
        if source_id and source_id not in known_message_ids:
            raise ValueError(
                f"Extraction memory {index} referenced a message id that does not exist."
            )
        if not isinstance(item.get("ttlDays"), int):
            raise ValueError(f"Extraction memory {index} ttlDays must be an integer.")


def build_extraction_prompt(
    messages: list[MemoryMessage],
    active_summary_text: str | None,
) -> str:
    transcript = "\n".join(
        f'<message id="{message.id}" role="{message.role}">\n{message.content}\n</message>'
        for message in messages
    )
    summary_block = active_summary_text or "(none)"
    type_list = ", ".join(sorted(MEMORY_TYPES))
    return (
        "You extract durable long-term memories about the user from a "
        "job-search assistant conversation in WeCanFindIntern.\n\n"
        "Return ONLY facts worth remembering across future sessions. An empty "
        "memories array is a correct answer when nothing qualifies.\n\n"
        f"Allowed memoryType values: {type_list}\n\n"
        "Type guidance:\n"
        "- USER_PREFERENCE: explicit preferences about jobs or workflow "
        "(locations, salary range, work mode, answer language, result style).\n"
        "- CAREER_CONTEXT: durable career facts (graduation term/year, program, "
        "target roles, availability).\n"
        "- JOB_TARGET: specific jobs/companies the user is actively targeting "
        "or has decided to apply to.\n"
        "- EXPLICIT_FACT: something the user explicitly asked you to remember.\n\n"
        "- SKILL_PROFILE: skills, tech stack, tools, experience level.\n"
        "- EDUCATION_PROFILE: school, program, degree, graduation timeline.\n"
        "- WORK_EXPERIENCE: past roles, employers, responsibilities.\n"
        "- APPLICATION_PLAN: concrete plans, deadlines, target counts, process "
        "preferences (e.g. weekly hours, how many applications).\n\n"
        "Positive examples:\n"
        '- USER_PREFERENCE: "Prefers jobs in Toronto and remote-friendly roles."\n'
        '- CAREER_CONTEXT: "Graduating in April 2027 from University of Waterloo CS."\n'
        '- JOB_TARGET: "Targeting the Innovax full-stack intern posting (ww:482179)."\n'
        '- SKILL_PROFILE: "Comfortable with Python, FastAPI, PostgreSQL, Docker, '
        'AWS, React and TypeScript."\n'
        '- APPLICATION_PLAN: "Plans to apply to 12-15 internships this summer, '
        'spending about 10 hours per week on applications."\n\n'
        "Hard constraints:\n"
        "- NEVER record inferred sensitive traits (health, ethnicity, religion, "
        "politics, sexuality, disability, immigration status, precise location "
        "beyond city-level work preference). If a candidate would reveal these, "
        "omit it entirely.\n"
        "- Do not store transient conversation state (what was just asked) or "
        "general job facts; those belong to retrieval, not user memory.\n"
        "- You decide where each fact belongs (best memoryType) and to what "
        "degree (confidence). Content may be one or several self-contained "
        "sentences, understandable without this conversation.\n"
        "- Split distinct facts into separate memories instead of lumping them; "
        "do not force a memory for every statement.\n"
        "- sourceMessageId must be the id of the message that best evidences "
        "the memory, chosen from the ids in the input.\n"
        "- confidence reflects how directly the user stated it: explicit "
        "statements near 1.0, weak inferences below 0.5.\n"
        "- ttlDays: 0 means no expiry; use a positive value only for time-bound "
        "facts (e.g. a target deadline passes).\n\n"
        "Conversation summary so far (context only, do not re-extract from it):\n"
        f"{summary_block}\n\n"
        f"New conversation turns:\n{transcript}\n"
    )


def extract_memory_candidates(
    deps: AgentDeps,
    messages: list[MemoryMessage],
    active_summary_text: str | None,
) -> list[MemoryCandidate]:
    """Extract durable user facts from new turns; [] when nothing qualifies."""

    if not messages:
        return []
    if deps.llm_config is None:
        raise ToolError("llm_config_missing", "AI model configuration is required.")
    known_ids = {str(message.id) for message in messages}
    base_prompt = build_extraction_prompt(messages, active_summary_text)
    last_error: Exception | None = None
    parsed = None
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
                "You extract durable user memories. Output ONLY JSON matching the "
                "requested schema. An empty memories array is correct when nothing "
                "qualifies. Never invent message ids."
            ),
            user_prompt=base_prompt + feedback,
        )
        parsed = result.data
        try:
            if not isinstance(parsed, dict):
                raise ValueError("Extraction response was not a JSON object.")
            validate_extraction_payload(parsed, known_ids)
            break
        except (ValueError, TypeError) as error:
            last_error = error
            parsed = None
    if parsed is None:
        raise ValueError(f"Extraction failed validation after retries: {last_error}")
    candidates: list[MemoryCandidate] = []
    for item in parsed["memories"]:
        confidence = float(item["confidence"])
        if confidence < settings.extraction_min_confidence:
            continue
        ttl_days = int(item["ttlDays"])
        source_id = item["sourceMessageId"]
        candidates.append(
            MemoryCandidate(
                memory_type=item["memoryType"],
                content=str(item["content"]).strip(),
                confidence=min(1.0, confidence),
                source_message_id=UUID(source_id) if source_id else None,
                ttl_days=ttl_days if ttl_days > 0 else None,
            )
        )
    return candidates
