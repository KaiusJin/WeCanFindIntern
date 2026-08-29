"""REST endpoints for the AI Agent section."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from wecanfindintern.agent.memory.manager import AgentMemoryManager
from wecanfindintern.agent.memory.preferences import PREFERENCE_KEYS
from wecanfindintern.agent.memory.store import AgentMemoryStore
from wecanfindintern.agent.models import (
    AgentApproval,
    AgentApprovalDecisionResult,
    AgentDecisionRequest,
    AgentMessage,
    AgentMessageRequest,
    AgentSession,
    AgentSessionResponse,
    AgentTurnResult,
)
from wecanfindintern.agent.orchestrator import AgentOrchestrator
from wecanfindintern.agent.repository import AgentRepository
from wecanfindintern.agent.tools import AgentDeps, LlmConfig, ToolError
from wecanfindintern.api.routes.profile import get_profile_repo
from wecanfindintern.api.routes.tracker import get_tracker_repo
from wecanfindintern.db.read_repository import JobReadRepository

agent_router = APIRouter(prefix="/api/v1/agent", tags=["AI Agent"])

SUPPORTED_PROVIDERS = {"Gemini", "OpenAI", "DeepSeek", "GLM", "Qwen", "Ollama"}


def get_agent_repo(request: Request) -> AgentRepository:
    return AgentRepository(request.app.state.database.pool)


AgentRepoDep = Annotated[AgentRepository, Depends(get_agent_repo)]


def _job_repo(request: Request) -> JobReadRepository:
    return JobReadRepository(request.app.state.database.pool)


def _memory_manager(request: Request) -> AgentMemoryManager:
    return AgentMemoryManager(
        store=AgentMemoryStore(request.app.state.database.pool)
    )


def _deps(request: Request, payload: AgentMessageRequest) -> AgentDeps:
    if not payload.provider or payload.provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=422, detail="Please select a supported AI provider.")
    if not payload.model_name:
        raise HTTPException(status_code=422, detail="Please select an AI model.")
    if not payload.api_key and payload.provider != "Ollama":
        raise HTTPException(
            status_code=422, detail=f"Missing {payload.provider} API key in AI Settings."
        )
    return AgentDeps(
        job_repo=_job_repo(request),
        tracker_repo=get_tracker_repo(request),
        profile_repo=get_profile_repo(request),
        waterlooworks=request.app.state.waterlooworks,
        llm_config=LlmConfig(
            provider=payload.provider,
            model_name=payload.model_name,
            api_key=payload.api_key,
            api_base=payload.api_base,
        ),
        memory=_memory_manager(request),
    )


def _execution_deps(request: Request) -> AgentDeps:
    """Dependencies for approval execution (no LLM round-trip needed)."""

    return AgentDeps(
        job_repo=_job_repo(request),
        tracker_repo=get_tracker_repo(request),
        profile_repo=get_profile_repo(request),
        waterlooworks=request.app.state.waterlooworks,
        llm_config=None,
        memory=_memory_manager(request),
    )


@agent_router.post("/sessions", response_model=AgentSessionResponse, status_code=201)
async def create_agent_session(
    repo: AgentRepoDep,
) -> AgentSessionResponse:
    session = await repo.create_session()
    return AgentSessionResponse(session=session)


@agent_router.get("/sessions")
async def list_agent_sessions(
    request: Request,
) -> list[dict[str, Any]]:
    return await _memory_manager(request).store().list_sessions_with_meta()


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


@agent_router.get("/sessions/{session_id}/memory")
async def agent_memory_status(
    session_id: UUID,
    request: Request,
) -> dict[str, Any]:
    memory = _memory_manager(request)
    store = memory.store()
    state = await store.load_session_state(session_id)
    preferences = await store.load_user_preferences()
    memories = await store.load_active_memories(50)
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
            session_id, payload.content, context=payload.context
        )
    except ToolError as error:
        status_code = (
            502
            if error.error_type in {"llm_failed", "llm_config_missing"}
            else 422
        )
        raise HTTPException(status_code=status_code, detail=str(error)) from error


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
