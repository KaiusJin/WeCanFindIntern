"""Mock interview question generation and answer evaluation service."""

from __future__ import annotations

import json
import os
from typing import Any

from wecanfindintern.interview.models import (
    InterviewAnalyzeResponse,
    InterviewQuestionItem,
    InterviewQuestionsResponse,
)
from wecanfindintern.llm.client import (
    call_gemini,
    call_openai_compatible,
    clean_json_text,
    resolve_api_key,
)


def generate_interview_questions(
    job_description: str,
    provider: str = "Gemini",
    model_name: str | None = None,
    api_key: str | None = None,
) -> InterviewQuestionsResponse:
    """Generate 3-stage tailored mock interview loop."""
    if not job_description.strip():
        return InterviewQuestionsResponse(ok=False, error="Job description cannot be empty.")
    if not model_name or not model_name.strip():
        return InterviewQuestionsResponse(ok=False, error="No AI model selected. Please select a model in Settings.")

    try:
        resolved_key = resolve_api_key(provider, api_key)
    except ValueError as exc:
        return InterviewQuestionsResponse(ok=False, error=str(exc))

    prompt = f"""
You are a Staff Technical Hiring Manager designing a structured 3-stage behavioral and technical interview.
Based on the following Job Description, generate exactly 3 highly realistic interview questions:
1. Warm-up / Self-Introduction & Background
2. Technical / Problem Solving / Architecture
3. Behavioral / Collaboration / Conflict / Ownership

Job Description:
{job_description}

Return a VALID JSON array of 3 objects ONLY:
[
  {{
    "id": 1,
    "category": "icebreaker",
    "category_label": "1. Warm-up & Intro",
    "question": "<Tailored question>",
    "eval_criteria": ["<Point 1>", "<Point 2>"]
  }},
  {{
    "id": 2,
    "category": "technical",
    "category_label": "2. Technical & Project Deep-Dive",
    "question": "<Tailored technical question>",
    "eval_criteria": ["<Point 1>", "<Point 2>"]
  }},
  {{
    "id": 3,
    "category": "behavioral",
    "category_label": "3. Behavioral & Ownership",
    "question": "<Tailored behavioral question>",
    "eval_criteria": ["<Point 1>", "<Point 2>"]
  }}
]
"""

    if provider in ("DeepSeek", "OpenAI"):
        try:
            base_url = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com") if provider == "DeepSeek" else None
            raw_content, total_tokens = call_openai_compatible(
                api_key=resolved_key,
                model_name=model_name,
                messages=[
                    {"role": "system", "content": "You are a professional technical interviewer. Output valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                base_url=base_url,
            )
            raw = clean_json_text(raw_content)
            data = json.loads(raw)
            if isinstance(data, dict) and "questions" in data:
                data = data["questions"]
            questions = [InterviewQuestionItem.model_validate(q) for q in data]
            return InterviewQuestionsResponse(ok=True, questions=questions, usage={"total_tokens": total_tokens})
        except Exception as exc:
            return InterviewQuestionsResponse(ok=False, error=f"{provider} questions error: {exc}")

    # Gemini
    try:
        resp_text = call_gemini(resolved_key, prompt, model_name)
        raw_json = clean_json_text(resp_text)
        data = json.loads(raw_json)
        if isinstance(data, dict) and "questions" in data:
            data = data["questions"]
        questions = [InterviewQuestionItem.model_validate(q) for q in data]
        return InterviewQuestionsResponse(ok=True, questions=questions)
    except Exception as exc:
        return InterviewQuestionsResponse(ok=False, error=f"Gemini questions error: {exc}")


def analyze_interview_performance(
    job_description: str,
    question_context: str,
    answer_text: str = "",
    video_bytes: bytes | None = None,
    provider: str = "Gemini",
    model_name: str | None = None,
    api_key: str | None = None,
) -> InterviewAnalyzeResponse:
    """Analyze mock interview answer performance."""
    if not model_name or not model_name.strip():
        return InterviewAnalyzeResponse(ok=False, error="No AI model selected. Please select a model in Settings.")

    try:
        resolved_key = resolve_api_key(provider, api_key)
    except ValueError as exc:
        return InterviewAnalyzeResponse(ok=False, error=str(exc))

    prompt_text = f"""
You are a Principal Interview Coach evaluating a candidate's answer.

Role Job Description:
{job_description}

Interview Question:
{question_context}

Candidate Answer / Transcript:
{answer_text or '[See attached video/audio recording]'}

Evaluate the response across:
1. Relevance & Clarity
2. Technical accuracy & STAR methodology
3. Delivery, confidence, and structure

Return a VALID JSON object ONLY:
{{
  "score": <int 0-100>,
  "feedback": "<Overall constructive summary>",
  "strengths": ["<strength 1>", "<strength 2>"],
  "improvements": ["<improvement 1>", "<improvement 2>"],
  "model_answer": "<High-scoring example answer following the STAR format>",
  "timeline": [
    {{"time": "0:15", "comment": "<feedback on opening>"}},
    {{"time": "0:45", "comment": "<feedback on core argument>"}}
  ]
}}
"""

    if video_bytes and provider == "Gemini":
        try:
            import google.generativeai as genai
            clean_key = resolve_api_key("Gemini", api_key)
            genai.configure(api_key=clean_key, transport="rest")
            target_model = model_name.strip().replace("models/", "")
            model = genai.GenerativeModel(target_model)
            contents = [
                {"mime_type": "video/webm", "data": video_bytes},
                prompt_text,
            ]
            resp = model.generate_content(contents)
            raw = clean_json_text(resp.text)
            data = json.loads(raw)
            return InterviewAnalyzeResponse(
                ok=True,
                score=int(data.get("score", 75)),
                feedback=data.get("feedback", ""),
                strengths=data.get("strengths", []),
                improvements=data.get("improvements", []),
                model_answer=data.get("model_answer", ""),
                timeline=data.get("timeline", []),
            )
        except Exception as exc:
            return InterviewAnalyzeResponse(ok=False, error=f"Gemini video analysis error: {exc}")

    # Fallback / Text analysis
    if provider in ("DeepSeek", "OpenAI"):
        try:
            base_url = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com") if provider == "DeepSeek" else None
            raw_content, total_tokens = call_openai_compatible(
                api_key=resolved_key,
                model_name=model_name,
                messages=[
                    {"role": "system", "content": "You are a professional interview coach. Output valid JSON."},
                    {"role": "user", "content": prompt_text},
                ],
                base_url=base_url,
                response_format={"type": "json_object"},
            )
            raw = clean_json_text(raw_content)
            data = json.loads(raw)
            return InterviewAnalyzeResponse(
                ok=True,
                score=int(data.get("score", 75)),
                feedback=data.get("feedback", ""),
                strengths=data.get("strengths", []),
                improvements=data.get("improvements", []),
                model_answer=data.get("model_answer", ""),
                timeline=data.get("timeline", []),
                usage={"total_tokens": total_tokens},
            )
        except Exception as exc:
            return InterviewAnalyzeResponse(ok=False, error=f"{provider} answer analysis error: {exc}")

    # Gemini text analysis
    try:
        resp_text = call_gemini(resolved_key, prompt_text, model_name)
        raw = clean_json_text(resp_text)
        data = json.loads(raw)
        return InterviewAnalyzeResponse(
            ok=True,
            score=int(data.get("score", 75)),
            feedback=data.get("feedback", ""),
            strengths=data.get("strengths", []),
            improvements=data.get("improvements", []),
            model_answer=data.get("model_answer", ""),
            timeline=data.get("timeline", []),
        )
    except Exception as exc:
        return InterviewAnalyzeResponse(ok=False, error=f"Gemini answer analysis error: {exc}")
