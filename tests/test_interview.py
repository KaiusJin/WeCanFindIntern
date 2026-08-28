"""Unit tests for Mock Interview coaching module."""

from wecanfindintern.interview.models import (
    InterviewQuestionItem,
    InterviewQuestionsResponse,
)
from wecanfindintern.interview.service import generate_interview_questions
from wecanfindintern.interview.tts import generate_tts_audio


def test_interview_models():
    q_resp = InterviewQuestionsResponse(
        ok=True,
        questions=[
            InterviewQuestionItem(
                id=1,
                category="icebreaker",
                category_label="Icebreaker",
                question="Tell me about yourself.",
            )
        ],
    )
    assert len(q_resp.questions) == 1
    assert q_resp.questions[0].category == "icebreaker"


def test_interview_missing_api_key():
    resp = generate_interview_questions(
        job_description="Software engineer intern",
        provider="OpenAI",
        model_name="gpt-4o",
        api_key=None,
    )
    assert not resp.ok
    assert "Missing OpenAI API key" in resp.error


def test_tts_audio_empty():
    audio = generate_tts_audio("")
    assert audio == b""
