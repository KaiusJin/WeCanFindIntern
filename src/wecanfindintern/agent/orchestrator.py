"""Agent orchestration: intent planning, confirmed write execution, replies."""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from wecanfindintern.agent.memory.config import settings as memory_settings
from wecanfindintern.agent.memory.manager import render_context_sections
from wecanfindintern.agent.memory.models import WorkingContext
from wecanfindintern.agent.memory.tokens import estimate_tokens
from wecanfindintern.agent.models import (
    AgentApproval,
    AgentApprovalDecisionResult,
    AgentMessage,
    AgentSession,
    AgentToolCall,
    AgentTurnResult,
)
from wecanfindintern.agent.repository import AgentRepository
from wecanfindintern.agent.tools import (
    AgentDeps,
    ToolError,
    is_write_tool,
    run_tool,
    summarize_for_llm,
)
from wecanfindintern.llm.gateway import LLMError, complete_json

APPROVE_PATTERNS = re.compile(
    r"^(yes|yeah|yep|y|confirm|confirmed|approve|go ahead|do it|ok|okay|sure|"
    r"fine|continue|执行|确认|同意|批准|好的|好|是|可以|继续|okay)\b.*$",
    re.IGNORECASE,
)


def _complete_json_retry(
    *,
    provider: str,
    model_name: str,
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    api_base: str | None = None,
    attempts: int = 3,
) -> Any:
    """Call complete_json with retries on transient provider failures."""

    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return complete_json(
                provider=provider,
                model_name=model_name,
                api_key=api_key,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                api_base=api_base,
            )
        except LLMError as error:
            last_error = error
            if attempt < attempts - 1:
                time.sleep(1.5 * (attempt + 1))
    raise last_error  # type: ignore[misc]
DENY_PATTERNS = re.compile(
    r"^(no|nope|n|deny|reject|cancel|stop|dont|don't|not now|算了|不了|"
    r"拒绝|取消|不要|不|否|停)\b.*$",
    re.IGNORECASE,
)


def _detect_decision(content: str) -> bool | None:
    text = content.strip().strip(".!?，。！？").strip()
    if not text:
        return None
    if APPROVE_PATTERNS.match(text) and len(text) <= 24:
        return True
    if DENY_PATTERNS.match(text) and len(text) <= 24:
        return False
    return None


def _history_text(messages: list[AgentMessage], limit: int = 8) -> str:
    lines: list[str] = []
    for message in messages[-limit:]:
        role = "user" if message.role == "user" else "assistant"
        lines.append(f"{role}: {message.content[:900]}")
    return "\n".join(lines)


def _context_text(context: dict[str, Any] | None) -> str:
    if not context:
        return "No job is open."
    job = context.get("job") or context
    if not isinstance(job, dict):
        return "No job is open."
    parts: list[str] = []
    if job.get("title"):
        parts.append(job["title"])
    if job.get("company"):
        parts.append(job["company"])
    if job.get("id"):
        parts.append(f"job id: {job['id']}")
    if job.get("jd"):
        parts.append(f"description: {job['jd'][:600]}")
    return " ".join(parts) if parts else "No job is open."


def plan_turn(
    *,
    llm_config: AgentDeps,
    user_message: str,
    history: list[AgentMessage],
    context: dict[str, Any] | None,
    pending_approval: AgentApproval | None,
    working_context: WorkingContext | None = None,
) -> dict[str, Any]:
    """Ask the model to plan tool calls. Pure function for testability."""

    from wecanfindintern.agent.tools import TOOL_CATALOG

    if llm_config.llm_config is None:
        raise ToolError("llm_config_missing", "AI model configuration is required.")
    config = llm_config.llm_config
    tools_text = json.dumps(TOOL_CATALOG, ensure_ascii=False, indent=1)[:14000]
    pending_text = ""
    if pending_approval is not None:
        pending_text = (
            "\nThere is a PENDING approval waiting for the user: "
            f"tool={pending_approval.tool_name}, "
            f"arguments={json.dumps(pending_approval.arguments, ensure_ascii=False)[:500]}. "
            "Do NOT execute or re-plan it. Tell the user to confirm with the buttons "
            "or reply 'yes'/'确认', or cancel with 'no'/'取消'."
        )
    system_prompt = (
        "You are the AI Agent inside WeCanFindIntern, a single-user local job-search "
        "workspace. You help with: adding jobs to Interested, updating Tracker stages, "
        "removing Interested, filling the user's Profile, and recommending jobs.\n\n"
        f"Available tools:\n{tools_text}\n\n"
        "Rules:\n"
        "- Plan tool calls by choosing from the catalog. Never invent tools.\n"
        "- Read-only tools (get_profile, search_jobs, get_job_details, list_tracker, "
        "recommend_jobs, propose_profile_update) run immediately.\n"
        "- Write tools (add_interested, update_tracker_stage, remove_interested, "
        "update_profile) require confirmation: plan them, and the system will show a "
        "preview for the user to approve. Never run or claim to have run a write tool "
        "in this step.\n"
        "- When a user references a job by name/company, first use search_jobs or "
        "get_job_details to resolve it. If multiple jobs match, plan search only and "
        "ask the user to pick; do not guess.\n"
        "- Job references returned by search_jobs/get_job_details (source plus job_id) "
        "are enough to plan write tools like add_interested, update_tracker_stage and "
        "remove_interested. You do NOT need the job to be open in the UI, and you do "
        "NOT need to open anything.\n"
        "- For Tracker stage changes, use update_tracker_stage with application ids or "
        "job references. Supported stages: interested, applied, interview, offer, "
        "rejected.\n"
        "- Profile changes: when the user gives explicit field values (email, phone, "
        "city, region, country, linkedin_url, github_url, portfolio_url, education, "
        "work, projects, skills, etc.), plan update_profile with a partial "
        "profile.v1 payload containing only the requested fields. The payload has "
        "sections: basics, education, work_experience, projects, skills, "
        "certifications, languages, awards. Contact fields (email, phone, city, "
        "region, country, links) go inside 'basics', e.g. "
        "{\"payload\": {\"basics\": {\"email\": \"a@b.com\", \"city\": \"Toronto\"}}}. "
        "The server merges it with the current profile and shows a field-level diff "
        "for confirmation. "
        "When the user instead asks you to draft changes from resume text or general "
        "statements, plan propose_profile_update first and show the draft. Never "
        "invent personal facts; ask for evidence when values are missing.\n"
        "- Respond in the same language as the user (Chinese or English).\n"
        "- Output ONLY JSON: {\"reply\": string, \"tool_calls\": [{\"name\": string, "
        "\"arguments\": object}]}. tool_calls may be empty."
    )
    user_prompt = (
        f"Today: {datetime.now(UTC).date().isoformat()}\n\n"
        f"Open job context: {_context_text(context)}\n\n"
        + (
            "\n\n".join(render_context_sections(working_context))
            if working_context is not None
            else f"Recent conversation:\n{_history_text(history)}"
        )
        + "\n\n"
        f"User message: {user_message}{pending_text}"
    )
    result = _complete_json_retry(
        provider=config.provider,
        model_name=config.model_name,
        api_key=config.api_key,
        api_base=config.api_base,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )
    data = result.data
    if not isinstance(data, dict):
        raise ToolError("llm_failed", "Agent planner returned a non-object response.")
    reply = data.get("reply")
    tool_calls = data.get("tool_calls") or []
    if not isinstance(reply, str) or not isinstance(tool_calls, list):
        raise ToolError("llm_failed", "Agent planner response was missing reply/tool_calls.")
    return {"reply": reply, "tool_calls": tool_calls}


def compose_reply(
    *,
    llm_config: AgentDeps,
    user_message: str,
    tool_summaries: list[str],
    context: dict[str, Any] | None,
    awaiting_approval: bool,
    working_context: WorkingContext | None = None,
) -> str:
    """Generate the final assistant reply from executed read-tool results."""

    if llm_config.llm_config is None:
        raise ToolError("llm_config_missing", "AI model configuration is required.")
    config = llm_config.llm_config
    system_prompt = (
        "You are the AI Agent inside WeCanFindIntern. Summarize tool results for the "
        "user in a concise, friendly way. Be honest about limitations: if a job "
        "description or profile field was missing, say it. Respond in the same language "
        "as the user's message. Output ONLY JSON: {\"reply\": string}."
    )
    user_prompt = (
        f"Open job context: {_context_text(context)}\n\n"
        + (
            "\n\n".join(render_context_sections(working_context))
            if working_context is not None
            else ""
        )
        + "\n\n"
        f"User message: {user_message}\n\n"
        "Tool results:\n"
        + ("\n".join(f"- {item}" for item in tool_summaries) or "- (none)")
        + (
            "\n\nA confirmation is pending for write actions. Tell the user what will "
            "happen and how to confirm (buttons or 'yes'), or cancel ('no')."
            if awaiting_approval
            else ""
        )
    )
    result = _complete_json_retry(
        provider=config.provider,
        model_name=config.model_name,
        api_key=config.api_key,
        api_base=config.api_base,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )
    reply = result.data.get("reply") if isinstance(result.data, dict) else None
    if not isinstance(reply, str) or not reply.strip():
        raise ToolError("llm_failed", "Agent reply composer returned an empty response.")
    return reply


def format_execution_reply(tool_name: str, result: dict[str, Any]) -> str:
    """Deterministic confirmation result message (no extra LLM round-trip)."""

    summary = result.get("summary") or "Done."
    data = result.get("data") or {}
    results = data.get("results")
    if isinstance(results, list) and results:
        lines = [summary, ""]
        for item in results[:25]:
            if not isinstance(item, dict):
                continue
            label = item.get("title") or item.get("job_id") or item.get("application_id")
            status = item.get("status", "ok")
            detail = item.get("error") or item.get("stage") or ""
            lines.append(f"- {label}: {status}" + (f" ({detail})" if detail else ""))
        return "\n".join(lines)
    changes = data.get("changes")
    if isinstance(changes, list) and changes:
        lines = [summary, ""]
        for change in changes[:25]:
            section = change.get("section", "")
            field = change.get("field", "")
            old = change.get("old")
            new = change.get("new")
            lines.append(f"- {section}.{field}: {old!r} → {new!r}")
        return "\n".join(lines)
    return summary


class AgentOrchestrator:
    def __init__(self, repo: AgentRepository, deps: AgentDeps) -> None:
        self.repo = repo
        self.deps = deps

    async def process_message(
        self,
        session_id: UUID,
        content: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> AgentTurnResult:
        session = await self._require_session(session_id)
        user_message = await self.repo.add_message(
            session_id, "user", content, token_count=estimate_tokens(content)
        )
        await self.repo.touch_session(session_id)
        if session.title == "New conversation":
            title = re.sub(r"\s+", " ", content.strip())[:60]
            session = (await self.repo.update_session_title(session_id, title)) or session

        pending = await self.repo.list_pending_approvals(session_id)
        decision = _detect_decision(content) if pending else None
        if decision is not None:
            return await self._decide(
                pending[0].id,
                decision,
                user_message=user_message,
                session=session,
            )

        history = await self.repo.list_messages(session_id, limit=40)
        working_context = None
        if self.deps.memory is not None:
            working_context = await self.deps.memory.build_context(session_id, content)
        try:
            plan = await asyncio.to_thread(
                plan_turn,
                llm_config=self.deps,
                user_message=content,
                history=history,
                context=context,
                pending_approval=pending[0] if pending else None,
                working_context=working_context,
            )
        except LLMError as error:
            raise ToolError("llm_failed", f"AI model error: {error}") from error

        tool_call_records: list[dict[str, Any]] = []
        pending_approval: AgentApproval | None = None
        tool_summaries: list[str] = []
        for planned in plan["tool_calls"]:
            if not isinstance(planned, dict):
                continue
            name = str(planned.get("name", ""))
            arguments = planned.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {}
            try:
                if is_write_tool(name):
                    result = await run_tool(name, arguments, self.deps, phase="plan")
                    if result.get("requires_approval"):
                        pending_approval = await self.repo.create_approval(
                            session_id=session_id,
                            tool_name=name,
                            arguments=arguments,
                            preview=result.get("preview", {}),
                        )
                        tool_summaries.append(
                            f"{name}: {result.get('summary', '')} (awaiting approval)"
                        )
                        tool_call_records.append(
                            {
                                "tool_name": name,
                                "arguments": arguments,
                                "status": "awaiting_approval",
                                "result": result.get("preview", {}),
                            }
                        )
                        continue
                else:
                    result = await run_tool(name, arguments, self.deps, phase="plan")
                tool_summaries.append(
                    f"{name}: {result.get('summary', '')} | {summarize_for_llm(result)}"
                )
                tool_call_records.append(
                    {
                        "tool_name": name,
                        "arguments": arguments,
                        "status": "succeeded",
                        "result": result,
                    }
                )
            except ToolError as error:
                tool_call_records.append(
                    {
                        "tool_name": name,
                        "arguments": arguments,
                        "status": "failed",
                        "error": f"{error.error_type}: {error}",
                    }
                )
                tool_summaries.append(f"{name}: failed ({error.error_type}: {error})")
            except Exception as error:  # pragma: no cover - defensive
                tool_call_records.append(
                    {
                        "tool_name": name,
                        "arguments": arguments,
                        "status": "failed",
                        "error": str(error),
                    }
                )
                tool_summaries.append(f"{name}: failed ({error})")

        if pending_approval is not None:
            try:
                reply = await asyncio.to_thread(
                    compose_reply,
                    llm_config=self.deps,
                    user_message=content,
                    tool_summaries=tool_summaries,
                    context=context,
                    awaiting_approval=True,
                    working_context=working_context,
                )
            except (ToolError, LLMError):
                reply = (
                    "I've prepared the following change for your confirmation. "
                    "Please review the preview and confirm or cancel."
                )
        elif tool_summaries:
            try:
                reply = await asyncio.to_thread(
                    compose_reply,
                    llm_config=self.deps,
                    user_message=content,
                    tool_summaries=tool_summaries,
                    context=context,
                    awaiting_approval=False,
                    working_context=working_context,
                )
            except (ToolError, LLMError):
                reply = plan["reply"]
        else:
            reply = plan["reply"]

        assistant = await self.repo.add_message(
            session_id, "assistant", reply, token_count=estimate_tokens(reply)
        )
        await self._record_turn_and_maintenance(session_id)
        tool_calls: list[AgentToolCall] = []
        for record in tool_call_records:
            tool_calls.append(
                await self.repo.add_tool_call(
                    session_id=session_id,
                    message_id=assistant.id,
                    tool_name=record["tool_name"],
                    arguments=record["arguments"],
                    status=record["status"],
                    result=record.get("result"),
                    error=record.get("error"),
                )
            )
        await self.repo.append_audit(
            session_id=session_id,
            user_intent=content[:500],
            tool_name=",".join(
                str(c.get("name", "")) for c in plan["tool_calls"] if isinstance(c, dict)
            )
            or None,
            arguments_summary=json.dumps(
                [c.get("arguments") for c in plan["tool_calls"] if isinstance(c, dict)],
                ensure_ascii=False,
            )[:500],
            approval_status=pending_approval.status if pending_approval else None,
            result_summary="; ".join(tool_summaries)[:500],
        )
        return AgentTurnResult(
            message=assistant,
            tool_calls=tool_calls,
            pending_approval=pending_approval,
            session=session,
        )

    async def _record_turn_and_maintenance(self, session_id: UUID) -> None:
        if self.deps.memory is None:
            return
        maintenance_due = await self.deps.memory.record_turn(session_id)
        if not maintenance_due:
            return
        if self.deps.llm_config is None:
            return
        if memory_settings.maintenance_inline:
            await self.deps.memory.run_maintenance(session_id, self.deps)
        else:
            self.deps.memory.schedule_maintenance(session_id, self.deps)

    async def decide_approval(
        self, approval_id: UUID, approved: bool
    ) -> AgentApprovalDecisionResult:
        return await self._decide(approval_id, approved, user_message=None, session=None)

    async def _decide(
        self,
        approval_id: UUID,
        approved: bool,
        *,
        user_message: AgentMessage | None,
        session: AgentSession | None,
    ) -> AgentApprovalDecisionResult:
        approval = await self.repo.get_approval(approval_id)
        if approval is None or approval.status != "pending":
            raise ToolError("approval_not_pending", "This approval is no longer pending.")
        session = session or await self._require_session(approval.session_id)
        if not approved:
            decided = await self.repo.decide_approval(approval_id, "denied")
            assert decided is not None
            reply = "取消成功。没有做任何更改。"
            assistant = await self.repo.add_message(
                session.id, "assistant", reply, token_count=estimate_tokens(reply)
            )
            await self._record_turn_and_maintenance(session.id)
            await self.repo.append_audit(
                session_id=session.id,
                user_intent=user_message.content[:500] if user_message else "deny",
                tool_name=approval.tool_name,
                arguments_summary=json.dumps(approval.arguments, ensure_ascii=False)[:500],
                approval_status="denied",
            )
            return AgentApprovalDecisionResult(
                message=assistant,
                tool_calls=[],
                approval=decided,
                session=session,
            )

        decided = await self.repo.decide_approval(approval_id, "approved")
        assert decided is not None
        try:
            result = await run_tool(
                approval.tool_name, approval.arguments, self.deps, phase="execute"
            )
            tool_error = None
        except ToolError as error:
            result = {"ok": False, "summary": f"{error.error_type}: {error}"}
            tool_error = f"{error.error_type}: {error}"
        except Exception as error:  # pragma: no cover - defensive
            result = {"ok": False, "summary": str(error)}
            tool_error = str(error)

        tool_call = await self.repo.add_tool_call(
            session_id=session.id,
            message_id=None,
            tool_name=approval.tool_name,
            arguments=approval.arguments,
            status="failed" if tool_error else "succeeded",
            result=result if not tool_error else None,
            error=tool_error,
        )
        reply = (
            format_execution_reply(approval.tool_name, result)
            if not tool_error
            else f"操作失败：{tool_error}"
        )
        assistant = await self.repo.add_message(
            session.id, "assistant", reply, token_count=estimate_tokens(reply)
        )
        await self._record_turn_and_maintenance(session.id)
        await self.repo.append_audit(
            session_id=session.id,
            user_intent=user_message.content[:500] if user_message else "approve",
            tool_name=approval.tool_name,
            arguments_summary=json.dumps(approval.arguments, ensure_ascii=False)[:500],
            approval_status="approved",
            result_summary=(result.get("summary") or "")[:500],
            error=tool_error,
        )
        return AgentApprovalDecisionResult(
            message=assistant,
            tool_calls=[tool_call],
            approval=decided,
            session=session,
        )

    async def _require_session(self, session_id: UUID) -> AgentSession:
        session = await self.repo.get_session(session_id)
        if session is None:
            raise ToolError("session_not_found", "Agent session not found.")
        return session
