"""Prompts for mock interview question generation and answer analysis."""

from __future__ import annotations


def build_questions_prompt(job_description: str) -> str:
    return f"""
You are a Staff Technical Hiring Manager designing a structured 3-stage behavioral
and technical interview.
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


def build_analysis_prompt(
    job_description: str,
    question_context: str,
    answer_text: str,
) -> str:
    return f"""
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
  "summary": "<2-3 sentence constructive summary>",
  "star_feedback": "<STAR method evaluation of structure, evidence and delivery>",
  "timeline": [
    {{"timestamp": "0:15", "type": "Opening", "observation": "<feedback on opening>"}},
    {{"timestamp": "0:45", "type": "Core argument", "observation": "<feedback on core argument>"}}
  ],
  "advice": ["<actionable improvement 1>", "<actionable improvement 2>"]
}}
"""
