"""REST endpoints for the AI Agent section."""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from wecanfindintern.agent.contracts import AgentDeps, LlmConfig, ToolError
from wecanfindintern.agent.memory.manager import AgentMemoryManager
from wecanfindintern.agent.memory.preferences import PREFERENCE_KEYS
from wecanfindintern.agent.models import (
    AgentApproval,
    AgentApprovalDecisionResult,
    AgentDecisionRequest,
    AgentMessage,
    AgentMessageRequest,
    AgentSession,
    AgentSessionResponse,
    AgentToolCall,
    AgentTurnResult,
)
from wecanfindintern.agent.orchestrator import AgentOrchestrator
from wecanfindintern.agent.recommend.embeddings import EmbeddingConfig
from wecanfindintern.agent.recommend.repository import RecommendationRepository
from wecanfindintern.agent.repository import AgentRepository
from wecanfindintern.api.dependencies import (
    get_job_repository,
    get_profile_repository,
    get_tracker_repository,
)
from wecanfindintern.llm.providers import SUPPORTED_LLM_PROVIDERS

agent_router = APIRouter(prefix="/api/v1/agent", tags=["AI Agent"])
logger = logging.getLogger(__name__)

def _public_agent_error(error: ToolError) -> tuple[int, str]:
    """Keep provider, parser, and planner internals out of API responses."""

    if error.error_type in {"llm_failed", "planner_invalid_output"}:
        logger.warning(
            "AI Agent request failed internally: error_type=%s error=%s",
            error.error_type,
            error,
        )
        return 502, "The AI model could not complete this request. Please try again."
    if error.error_type == "llm_config_missing":
        return 422, "Please select an AI model and configure its API key in Settings."
    return 422, str(error)


def get_agent_repo(request: Request) -> AgentRepository:
    return AgentRepository(request.app.state.database.pool)


AgentRepoDep = Annotated[AgentRepository, Depends(get_agent_repo)]


def _memory_manager(request: Request) -> AgentMemoryManager:
    return request.app.state.agent_memory


def _build_agent_deps(
    request: Request,
    *,
    llm_config: LlmConfig | None,
    embedding_config: EmbeddingConfig | None = None,
) -> AgentDeps:
    return AgentDeps(
        job_repo=get_job_repository(request),
        tracker_repo=get_tracker_repository(request),
        profile_repo=get_profile_repository(request),
        waterlooworks=request.app.state.waterlooworks,
        llm_config=llm_config,
        embedding_config=embedding_config,
        memory=_memory_manager(request),
        recommendation_repo=RecommendationRepository(request.app.state.database.pool),
    )


def _deps(request: Request, payload: AgentMessageRequest) -> AgentDeps:
    if not payload.provider or payload.provider not in SUPPORTED_LLM_PROVIDERS:
        raise HTTPException(status_code=422, detail="Please select a supported AI provider.")
    if not payload.model_name:
        raise HTTPException(status_code=422, detail="Please select an AI model.")
    if not payload.api_key and payload.provider != "Ollama":
        raise HTTPException(
            status_code=422, detail=f"Missing {payload.provider} API key in AI Settings."
        )
    try:
        embedding_config = EmbeddingConfig.from_values(
            provider=payload.embedding_provider,
            model=payload.embedding_model,
            dimensions=payload.embedding_dimensions,
            api_key=payload.embedding_api_key,
            api_base=payload.embedding_api_base,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _build_agent_deps(
        request,
        llm_config=LlmConfig(
            provider=payload.provider,
            model_name=payload.model_name,
            api_key=payload.api_key,
            api_base=payload.api_base,
        ),
        embedding_config=embedding_config,
    )


def _execution_deps(request: Request) -> AgentDeps:
    """Dependencies for approval execution (no LLM round-trip needed)."""

    return _build_agent_deps(
        request,
        llm_config=None,
    )


@agent_router.post("/sessions", response_model=AgentSessionResponse, status_code=201)
async def create_agent_session(
    repo: AgentRepoDep,
) -> AgentSessionResponse:
    session = await repo.create_session()
    return AgentSessionResponse(session=session)


@agent_router.get("/sessions")
async def list_agent_sessions(
    repo: AgentRepoDep,
) -> list[dict[str, Any]]:
    return await repo.list_sessions_with_meta()


@agent_router.patch("/sessions/{session_id}", response_model=AgentSession)
async def rename_agent_session(
    session_id: UUID,
    payload: dict[str, str],
    repo: AgentRepoDep,
) -> AgentSession:
    title = (payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="Title must not be empty")
    session = await repo.update_session_title(session_id, title[:200])
    if session is None:
        raise HTTPException(status_code=404, detail="Agent session not found")
    return session


@agent_router.delete("/sessions/{session_id}")
async def delete_agent_session(
    session_id: UUID,
    repo: AgentRepoDep,
) -> dict[str, bool]:
    deleted = await repo.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Agent session not found")
    return {"deleted": True}


@agent_router.get("/sessions/{session_id}/memory")
async def agent_memory_status(
    session_id: UUID,
    request: Request,
) -> dict[str, Any]:
    memory = _memory_manager(request)
    store = memory.store()
    state = await store.load_session_state(session_id)
    preferences = await store.load_user_preferences()
    memories = await store.load_active_memories(200)
    summary_backlog = await store.unsummarized_token_count(
        session_id, state.summary_covers_through_message_id
    )
    extraction_backlog = await store.unsummarized_token_count(
        session_id, state.extraction_covers_through_message_id
    )
    return {
        "session_id": str(session_id),
        "summary": {
            "version": state.summary_version,
            "token_count": state.summary_token_count,
            "has_summary": bool(state.summary_text),
            "unsummarized_tokens": summary_backlog,
        },
        "long_term_memory": {
            "active_count": len(memories),
            "enabled": preferences.get("LONG_TERM_MEMORY", "ENABLED") != "DISABLED",
        },
        "extraction_backlog_tokens": extraction_backlog,
        "memories": [
            {
                "id": str(item.id),
                "memory_type": item.memory_type,
                "content": item.content,
                "confidence": item.confidence,
            }
            for item in memories
        ],
    }


@agent_router.get("/preferences")
async def list_agent_preferences(request: Request) -> dict[str, str]:
    return await _memory_manager(request).get_preferences()


@agent_router.put("/preferences/{key}")
async def set_agent_preference(
    key: str,
    payload: dict[str, str],
    request: Request,
) -> dict[str, str]:
    try:
        value = await _memory_manager(request).set_preference(
            key, payload.get("value", "")
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {key: value}


@agent_router.delete("/preferences/{key}")
async def delete_agent_preference(
    key: str,
    request: Request,
) -> dict[str, bool]:
    if key not in PREFERENCE_KEYS:
        raise HTTPException(status_code=422, detail=f"Unknown preference key: {key}")
    deleted = await _memory_manager(request).clear_preference(key)
    return {"deleted": deleted}


@agent_router.delete("/memories/{memory_id}")
async def delete_agent_memory(
    memory_id: UUID,
    request: Request,
) -> dict[str, bool]:
    deleted = await _memory_manager(request).store().delete_memory(memory_id)
    return {"deleted": deleted}


@agent_router.get("/sessions/{session_id}", response_model=AgentSession)
async def get_agent_session(session_id: UUID, repo: AgentRepoDep) -> AgentSession:
    session = await repo.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Agent session not found")
    return session


@agent_router.get(
    "/sessions/{session_id}/messages", response_model=list[AgentMessage]
)
async def list_agent_messages(
    session_id: UUID, repo: AgentRepoDep
) -> list[AgentMessage]:
    if await repo.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Agent session not found")
    return await repo.list_messages(session_id)


@agent_router.get(
    "/sessions/{session_id}/tool-calls", response_model=list[AgentToolCall]
)
async def list_agent_tool_calls(
    session_id: UUID, repo: AgentRepoDep
) -> list[AgentToolCall]:
    """Return structured results so historical conversations can restore job cards."""

    if await repo.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Agent session not found")
    return await repo.list_tool_calls(session_id)


@agent_router.post(
    "/sessions/{session_id}/messages", response_model=AgentTurnResult
)
async def send_agent_message(
    session_id: UUID,
    payload: AgentMessageRequest,
    repo: AgentRepoDep,
    request: Request,
) -> AgentTurnResult:
    session = await repo.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Agent session not found")
    orchestrator = AgentOrchestrator(repo, _deps(request, payload))
    try:
        return await orchestrator.process_message(
            session_id,
            payload.content,
            context=(payload.context.model_dump(exclude_none=True) if payload.context else None),
        )
    except ToolError as error:
        status_code, detail = _public_agent_error(error)
        raise HTTPException(status_code=status_code, detail=detail) from error


@agent_router.post("/sessions/{session_id}/messages/stream")
async def send_agent_message_stream(
    session_id: UUID,
    payload: AgentMessageRequest,
    repo: AgentRepoDep,
    request: Request,
):
    """SSE variant of the message endpoint: tool, approval, text and done
    events are pushed as they happen instead of after the whole turn."""

    session = await repo.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Agent session not found")
    orchestrator = AgentOrchestrator(repo, _deps(request, payload))

    async def event_stream():
        try:
            async for event in orchestrator.process_message_stream(
                session_id,
                payload.content,
                context=(
                    payload.context.model_dump(exclude_none=True)
                    if payload.context
                    else None
                ),
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except ToolError as error:
            status, detail = _public_agent_error(error)
            # Deliberately NOT named `payload`: that would shadow the request
            # parameter and make it a local of this generator.
            error_event = {"type": "error", "status": status, "detail": detail}
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"
        except Exception as error:  # pragma: no cover - defensive
            logger.exception("Unhandled AI Agent streaming failure", exc_info=error)
            error_event = {
                "type": "error",
                "status": 502,
                "detail": "The AI Agent could not complete this request. Please try again.",
            }
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@agent_router.get(
    "/sessions/{session_id}/approvals", response_model=list[AgentApproval]
)
async def list_agent_approvals(
    session_id: UUID, repo: AgentRepoDep
) -> list[AgentApproval]:
    if await repo.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Agent session not found")
    return await repo.list_pending_approvals(session_id)


@agent_router.post(
    "/approvals/{approval_id}/decision",
    response_model=AgentApprovalDecisionResult,
)
async def decide_agent_approval(
    approval_id: UUID,
    payload: AgentDecisionRequest,
    repo: AgentRepoDep,
    request: Request,
) -> AgentApprovalDecisionResult:
    approval = await repo.get_approval(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    orchestrator = AgentOrchestrator(repo, _execution_deps(request))
    try:
        return await orchestrator.decide_approval(approval_id, payload.approved)
    except ToolError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
