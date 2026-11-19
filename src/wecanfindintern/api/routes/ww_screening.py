"""Local file import and bounded DeepSeek screening endpoints."""
from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field, SecretStr

from wecanfindintern.waterlooworks.screening import Job, classify, parse_export


async def local_request(request: Request) -> None:
    if request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(403, "Local access only")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host"):
        raise HTTPException(403, "Same-origin access only")


router = APIRouter(prefix="/api/v1/ww-screening", dependencies=[Depends(local_request)])


class ScreeningRequest(BaseModel):
    job: Job
    api_key: SecretStr
    model: str = Field(default="deepseek-v4-flash", min_length=1, max_length=100)


@router.post("/import")
async def import_jobs(file: UploadFile) -> dict:
    content = await file.read(40 * 1024 * 1024 + 1)
    if len(content) > 40 * 1024 * 1024:
        raise HTTPException(413, "文件超过 40 MB。")
    try:
        jobs = parse_export(content, file.filename or "")
    except (ValueError, UnicodeError) as error:
        # Validation errors may contain JD snippets; do not echo them.
        raise HTTPException(400, "文件格式无效、缺少 ID、字段过长或重复 ID 内容不一致。") from error
    return {"jobs": [job.model_dump() for job in jobs]}


@router.post("/classify")
async def classify_job(payload: ScreeningRequest, request: Request) -> dict:
    if not payload.api_key.get_secret_value().strip():
        raise HTTPException(400, "请输入 DeepSeek API Key。")
    if not hasattr(request.app.state, "ww_screening_slots"):
        request.app.state.ww_screening_slots = asyncio.Semaphore(15)
    async with request.app.state.ww_screening_slots:
        # Keep the slot until the synchronous provider finishes, even on disconnect.
        task = asyncio.create_task(asyncio.to_thread(
            classify, payload.job, payload.api_key.get_secret_value(), payload.model
        ))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise
        except Exception:
            # Never expose upstream exception strings, which may contain credentials.
            return {"id": payload.job.id, "decision": "error", "evidence": "",
                    "reason": "调用失败或返回格式无效。请检查 Key、余额、模型和网络，再重试。"}
