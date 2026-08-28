"""Prompts for ATS resume review."""

from __future__ import annotations

ATS_SYSTEM_PROMPT = "You are a professional ATS system. Output strictly valid JSON."


def build_ats_prompt(resume_text: str, job_description: str) -> str:
    return f"""
You are an expert Applicant Tracking System (ATS) and Technical Recruiter.
Evaluate how well the following candidate resume matches the target job description.

Target Job Description:
{job_description}

Candidate Resume Context:
{resume_text}

Evaluation Guidelines:
1. Score from 0 to 100 based strictly on skill relevance, keyword alignment,
   experience scope, and role fit.
2. List key matching strengths (technologies, methodologies, relevant scope).
3. List critical missing keywords, experience gaps, or qualification mismatches.
4. Give 3-5 concrete, actionable bullet improvement suggestions for the candidate.

Return a VALID JSON object ONLY with this exact schema:
{{
  "score": <int 0-100>,
  "summary": "<2-3 sentence overview of candidate match>",
  "strengths": ["<strength 1>", "<strength 2>", ...],
  "gaps": ["<gap/missing keyword 1>", "<gap/missing keyword 2>", ...],
  "suggestions": ["<actionable advice 1>", "<actionable advice 2>", ...]
}}
"""
