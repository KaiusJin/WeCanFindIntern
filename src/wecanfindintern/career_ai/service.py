"""Core AI logic and integrations for ATS Review, Interview Coach, and Cover Letter."""

from __future__ import annotations

import io
import json
import os
import re
from io import BytesIO
from typing import Any

from gtts import gTTS
from pypdf import PdfReader

from wecanfindintern.career_ai.models import (
    AtsReviewResponse,
    CoverLetterResponse,
    InterviewAnalyzeResponse,
    InterviewQuestionItem,
    InterviewQuestionsResponse,
    TimelineEvent,
    UserProfile,
)


def clean_json_text(text: str) -> str:
    """Extract and sanitize JSON block from LLM output."""
    if not text:
        return ""
    # Strip markdown code blocks
    cleaned = re.sub(r"^```(?:json)?", "", text.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"```$", "", cleaned.strip(), flags=re.MULTILINE).strip()
    match = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)
    if match:
        return match.group(1).strip()
    return cleaned


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract text from uploaded PDF bytes."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text_parts = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(text_parts).strip()
    except Exception as exc:
        raise ValueError(f"Failed to extract text from PDF: {exc}") from exc


def match_level(score: int) -> str:
    if score >= 80:
        return "高匹配 (High Match)"
    if score >= 55:
        return "中匹配 (Medium Match)"
    return "基础匹配 (Low Match)"


def _resolve_api_key(provider: str, api_key: str | None = None) -> str:
    if api_key and api_key.strip():
        return api_key.strip()
    raise ValueError(f"Missing {provider} API key. Please enter your API key in Settings.")


def _call_openai_compatible(
    api_key: str,
    model_name: str | None,
    messages: list[dict[str, str]],
    base_url: str | None = None,
    response_format: dict[str, str] | None = None,
) -> tuple[str, int]:
    if not model_name or not model_name.strip():
        raise ValueError("No AI model selected. Please select a model in Settings.")
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url)
    kwargs: dict[str, Any] = {}
    if response_format:
        kwargs["response_format"] = response_format
    resp = client.chat.completions.create(
        model=model_name.strip(),
        messages=messages,
        **kwargs,
    )
    content = resp.choices[0].message.content or "{}"
    tokens = resp.usage.total_tokens if resp.usage else 0
    return content, tokens


def _call_gemini_with_fallback(
    api_key: str,
    prompt: Any,
    requested_model: str | None = None,
) -> str:
    """Execute Gemini generate_content using strictly the user-requested model."""
    if not requested_model or not requested_model.strip():
        raise ValueError("No AI model selected. Please select a model in Settings.")
    import google.generativeai as genai
    genai.configure(api_key=api_key)

    target_model = requested_model.strip().replace("models/", "")
    model = genai.GenerativeModel(target_model)
    resp = model.generate_content(prompt)
    if resp and resp.text:
        return resp.text
    raise RuntimeError(f"Gemini model {target_model} returned empty response.")


# --- 1. ATS Resume Review ---

def generate_ats_review(
    resume_text: str,
    job_description: str,
    provider: str = "Gemini",
    model_name: str | None = None,
    api_key: str | None = None,
) -> AtsReviewResponse:
    if not resume_text.strip():
        return AtsReviewResponse(ok=False, error="Resume text cannot be empty.")
    if not job_description.strip():
        return AtsReviewResponse(ok=False, error="Job description cannot be empty.")
    if not model_name or not model_name.strip():
        return AtsReviewResponse(ok=False, error="No AI model selected. Please select a model in Settings.")

    try:
        resolved_key = _resolve_api_key(provider, api_key)
    except ValueError as exc:
        return AtsReviewResponse(ok=False, error=str(exc))

    prompt = f"""
You are an expert ATS (Applicant Tracking System) and Senior Technical Recruiter.
Analyze the provided Candidate Resume against the Target Job Description.

Candidate Resume:
{resume_text}

Target Job Description:
{job_description}

Provide an objective evaluation. Return a VALID JSON object ONLY:
{{
  "score": <int 0-100>,
  "summary": "<1-2 concise sentences summarizing overall fit and match quality>",
  "strengths": ["<strength 1 with specific skill/experience>", "<strength 2>", "<strength 3>"],
  "gaps": ["<missing required skill or qualification 1>", "<gap 2>", "<gap 3>"],
  "suggestions": ["<actionable resume bullet edit 1>", "<actionable resume bullet edit 2>", "<actionable resume bullet edit 3>"]
}}
"""

    if provider in ("DeepSeek", "OpenAI"):
        try:
            base_url = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com") if provider == "DeepSeek" else None
            raw_content, total_tokens = _call_openai_compatible(
                api_key=resolved_key,
                model_name=model_name,
                messages=[
                    {"role": "system", "content": "You are a senior recruiter. Output strictly valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                base_url=base_url,
                response_format={"type": "json_object"},
            )
            raw = clean_json_text(raw_content)
            data = json.loads(raw)
            score = max(0, min(100, int(data.get("score", 0))))
            return AtsReviewResponse(
                ok=True,
                score=score,
                level=match_level(score),
                summary=data.get("summary", ""),
                strengths=data.get("strengths", []),
                gaps=data.get("gaps", []),
                suggestions=data.get("suggestions", []),
                usage={"total_tokens": total_tokens},
            )
        except Exception as exc:
            return AtsReviewResponse(ok=False, error=f"{provider} ATS Review error: {exc}")

    # Gemini
    try:
        resp_text = _call_gemini_with_fallback(resolved_key, prompt, model_name)
        raw_json = clean_json_text(resp_text)
        data = json.loads(raw_json)
        score = max(0, min(100, int(data.get("score", 0))))
        return AtsReviewResponse(
            ok=True,
            score=score,
            level=match_level(score),
            summary=data.get("summary", ""),
            strengths=data.get("strengths", []),
            gaps=data.get("gaps", []),
            suggestions=data.get("suggestions", []),
        )
    except Exception as exc:
        return AtsReviewResponse(ok=False, error=f"Gemini ATS Review error: {exc}")


# --- 2. AI Interview Coach ---

def generate_interview_questions(
    job_description: str,
    provider: str = "Gemini",
    model_name: str | None = None,
    api_key: str | None = None,
) -> InterviewQuestionsResponse:
    if not job_description.strip():
        return InterviewQuestionsResponse(ok=False, error="Job description cannot be empty.")
    if not model_name or not model_name.strip():
        return InterviewQuestionsResponse(ok=False, error="No AI model selected. Please select a model in Settings.")

    try:
        resolved_key = _resolve_api_key(provider, api_key)
    except ValueError as exc:
        return InterviewQuestionsResponse(ok=False, error=str(exc))

    prompt = f"""
Design a 3-Question Mock Interview Loop tailored specifically to this Job Description:
{job_description}

Structure:
1. Icebreaker: Short introduction or 'Why this company/role?'
2. Behavioral (STAR): A core 'Tell me about a time...' testing a key skill mentioned in the JD.
3. Situational / Deep Dive: A harder scenario testing technical depth, edge cases, or project trade-offs.

Return a VALID JSON array of 3 objects:
[
  {{"id": 1, "category": "icebreaker", "category_label": "Icebreaker & Motivation", "question": "<Q1 text>"}},
  {{"id": 2, "category": "behavioral", "category_label": "Behavioral (STAR)", "question": "<Q2 text>"}},
  {{"id": 3, "category": "situational", "category_label": "Situational & Technical Depth", "question": "<Q3 text>"}}
]
"""

    if provider in ("DeepSeek", "OpenAI"):
        try:
            base_url = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com") if provider == "DeepSeek" else None
            raw_content, _ = _call_openai_compatible(
                api_key=resolved_key,
                model_name=model_name,
                messages=[
                    {"role": "system", "content": "You are an expert interviewer. Output strictly valid JSON array or object containing questions."},
                    {"role": "user", "content": prompt},
                ],
                base_url=base_url,
                response_format={"type": "json_object"},
            )
            raw = clean_json_text(raw_content)
            parsed = json.loads(raw)
            items_raw = parsed if isinstance(parsed, list) else parsed.get("questions", [])
            questions = [InterviewQuestionItem.model_validate(item) for item in items_raw[:3]]
            return InterviewQuestionsResponse(ok=True, questions=questions)
        except Exception as exc:
            return InterviewQuestionsResponse(ok=False, error=f"{provider} question error: {exc}")

    # Gemini
    try:
        resp_text = _call_gemini_with_fallback(resolved_key, prompt, model_name)
        raw_json = clean_json_text(resp_text)
        items_raw = json.loads(raw_json)
        if isinstance(items_raw, dict) and "questions" in items_raw:
            items_raw = items_raw["questions"]
        questions = [InterviewQuestionItem.model_validate(item) for item in items_raw[:3]]
        return InterviewQuestionsResponse(ok=True, questions=questions)
    except Exception as exc:
        return InterviewQuestionsResponse(ok=False, error=f"Gemini question error: {exc}")


def generate_tts_audio(text: str) -> bytes | None:
    """Generate audio MP3 bytes for interview question."""
    try:
        if not text or not text.strip():
            return None
        tts = gTTS(text=text.strip(), lang="en")
        audio_io = BytesIO()
        tts.write_to_fp(audio_io)
        audio_io.seek(0)
        return audio_io.getvalue()
    except Exception:
        return None


def analyze_interview_performance(
    job_description: str,
    question_context: str | None = None,
    answer_text: str | None = None,
    video_bytes: bytes | None = None,
    provider: str = "Gemini",
    model_name: str | None = None,
    api_key: str | None = None,
) -> InterviewAnalyzeResponse:
    if not model_name or not model_name.strip():
        return InterviewAnalyzeResponse(ok=False, error="No AI model selected. Please select a model in Settings.")

    try:
        resolved_key = _resolve_api_key(provider, api_key)
    except ValueError as exc:
        return InterviewAnalyzeResponse(ok=False, error=str(exc))

    prompt = f"""
You are an Elite Behavioral & Technical Interview Coach.
Analyze the candidate's interview answer to the question below:

Job Description Context:
{job_description}

Specific Question Asked:
{question_context or 'General self-introduction or behavioral question.'}

Candidate Answer / Transcript:
{answer_text or 'Video/Audio answer provided.'}

Evaluation criteria:
1. CONTENT & STAR METHOD: Did the candidate directly answer the prompt? Did they use Situation, Task, Action, Result?
2. RELEVANCE & IMPACT: Did they demonstrate concrete achievements, technical aptitude, or collaboration skills required by the JD?
3. CLARITY & CONCISENESS: Tone, structure, filler words, or rambling.

Return a VALID JSON object ONLY:
{{
  "score": <int 0-100>,
  "summary": "<2-3 sentence assessment of the response strength and professionalism>",
  "star_feedback": "<evaluation on whether STAR method was effectively used>",
  "timeline": [
    {{"timestamp": "00:15", "type": "Content", "observation": "<Observation 1>"}},
    {{"timestamp": "00:45", "type": "Audio", "observation": "<Observation 2>"}},
    {{"timestamp": "01:20", "type": "Visual", "observation": "<Observation 3>"}}
  ],
  "advice": ["<Actionable improvement tip 1>", "<Actionable improvement tip 2>", "<Actionable improvement tip 3>"]
}}
"""

    if provider in ("DeepSeek", "OpenAI"):
        try:
            base_url = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com") if provider == "DeepSeek" else None
            raw_content, _ = _call_openai_compatible(
                api_key=resolved_key,
                model_name=model_name,
                messages=[
                    {"role": "system", "content": "You are a master interview coach. Output strictly valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                base_url=base_url,
                response_format={"type": "json_object"},
            )
            raw = clean_json_text(raw_content)
            data = json.loads(raw)
            score = max(0, min(100, int(data.get("score", 0))))
            timeline_items = [
                TimelineEvent.model_validate(e) for e in data.get("timeline", [])
            ]
            return InterviewAnalyzeResponse(
                ok=True,
                score=score,
                summary=data.get("summary", ""),
                star_feedback=data.get("star_feedback"),
                timeline=timeline_items,
                advice=data.get("advice", []),
            )
        except Exception as exc:
            return InterviewAnalyzeResponse(ok=False, error=f"{provider} analysis error: {exc}")

    # Gemini
    try:
        resp_text = _call_gemini_with_fallback(resolved_key, prompt, model_name)
        raw_json = clean_json_text(resp_text)
        data = json.loads(raw_json)
        score = max(0, min(100, int(data.get("score", 0))))
        timeline_items = [
            TimelineEvent.model_validate(e) for e in data.get("timeline", [])
        ]
        return InterviewAnalyzeResponse(
            ok=True,
            score=score,
            summary=data.get("summary", ""),
            star_feedback=data.get("star_feedback"),
            timeline=timeline_items,
            advice=data.get("advice", []),
        )
    except Exception as exc:
        return InterviewAnalyzeResponse(ok=False, error=f"Gemini analysis error: {exc}")


# --- 3. Cover Letter Generator ---

def generate_cover_letter(
    resume_text: str,
    job_description: str,
    user_info: UserProfile,
    date_str: str = "[Date]",
    provider: str = "Gemini",
    model_name: str | None = None,
    api_key: str | None = None,
) -> CoverLetterResponse:
    if not resume_text.strip():
        return CoverLetterResponse(ok=False, error="Resume text cannot be empty.")
    if not job_description.strip():
        return CoverLetterResponse(ok=False, error="Job description cannot be empty.")
    if not model_name or not model_name.strip():
        return CoverLetterResponse(ok=False, error="No AI model selected. Please select a model in Settings.")

    try:
        resolved_key = _resolve_api_key(provider, api_key)
    except ValueError as exc:
        return CoverLetterResponse(ok=False, error=str(exc))

    prompt = f"""
You are a Senior Copywriter and Executive Career Coach.
Task: Write a concise, hyper-personalized, and compelling cover letter.

Target Job Description:
{job_description}

Candidate Resume Context:
{resume_text}

Candidate Profile Details:
- Name: {user_info.full_name or 'Applicant'}
- Email: {user_info.email or ''}
- Phone: {user_info.phone or ''}
- LinkedIn: {user_info.linkedin or ''}
- Address: {user_info.address or ''}

Date: {date_str}

STYLE & CONTENT RULES:
1. 2 to 3 focused paragraphs. Target 200–280 words.
2. Weave the candidate's concrete projects and quantified achievements into the key responsibilities of the role.
3. Use active voice, professional tone, and avoid buzzword clichés.
4. Output strictly formatted letter text starting with the date and recipient block.

Return a VALID JSON object ONLY:
{{
  "hr_info": {{
    "company": "<Company Name>",
    "manager": "<Hiring Manager Name or 'Hiring Team'>",
    "address": "<Company Location or 'Headquarters'>"
  }},
  "cover_letter": "<Full formatted text of the cover letter with paragraphs>"
}}
"""

    if provider in ("DeepSeek", "OpenAI"):
        try:
            base_url = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com") if provider == "DeepSeek" else None
            raw_content, total_tokens = _call_openai_compatible(
                api_key=resolved_key,
                model_name=model_name,
                messages=[
                    {"role": "system", "content": "You are a professional cover letter writer. Output valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                base_url=base_url,
                response_format={"type": "json_object"},
            )
            raw = clean_json_text(raw_content)
            data = json.loads(raw)
            return CoverLetterResponse(
                ok=True,
                text=data.get("cover_letter", ""),
                hr_info=data.get("hr_info", {}),
                usage={"total_tokens": total_tokens},
            )
        except Exception as exc:
            return CoverLetterResponse(ok=False, error=f"{provider} cover letter error: {exc}")

    # Gemini
    try:
        resp_text = _call_gemini_with_fallback(resolved_key, prompt, model_name)
        raw_json = clean_json_text(resp_text)
        data = json.loads(raw_json)
        return CoverLetterResponse(
            ok=True,
            text=data.get("cover_letter", ""),
            hr_info=data.get("hr_info", {}),
        )
    except Exception as exc:
        return CoverLetterResponse(ok=False, error=f"Gemini cover letter error: {exc}")


