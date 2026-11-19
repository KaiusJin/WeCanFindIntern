"""Unit tests for Mock Interview coaching module."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from wecanfindintern.interview.models import (
    InterviewQuestionItem,
    InterviewQuestionsResponse,
)
from wecanfindintern.interview.service import (
    analyze_interview_performance,
    generate_interview_questions,
)
from wecanfindintern.interview.stt import STTError, Transcript, transcribe_audio
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
        resume_text="Alex Chen — Python, FastAPI.",
        provider="OpenAI",
        model_name="gpt-4o",
        api_key=None,
    )
    assert not resp.ok
    assert "Missing OpenAI API key" in resp.error


def test_questions_require_candidate_context():
    resp = generate_interview_questions(
        job_description="Software engineer intern",
        resume_text="   ",
        provider="OpenAI",
        model_name="gpt-4o",
        api_key="key",
    )
    assert not resp.ok
    assert "Candidate context is required" in resp.error


def test_questions_prompt_embeds_resume_and_fixed_structure():
    captured = {}

    def fake_complete_json(**kwargs):
        captured["user_prompt"] = kwargs["user_prompt"]
        return SimpleNamespace(
            data=[
                {
                    "id": index + 1,
                    "category": "intro",
                    "category_label": f"{index + 1}. Q",
                    "question": f"Q{index + 1}",
                    "eval_criteria": ["c"],
                }
                for index in range(7)
            ],
            usage={},
        )

    with patch(
        "wecanfindintern.interview.service.complete_json",
        side_effect=fake_complete_json,
    ):
        resp = generate_interview_questions(
            job_description="Backend intern at Acme.",
            resume_text="Alex Chen | Python | Billing service at Shopify.",
            provider="OpenAI",
            model_name="gpt-4o",
            api_key="key",
        )
    assert resp.ok
    assert len(resp.questions) == 7
    prompt = captured["user_prompt"]
    assert "EXACTLY 7" in prompt
    assert "Self-introduction" in prompt
    assert "Work experience follow-up" in prompt
    assert "Project follow-up" in prompt
    assert "Do NOT include behavioral or HR" in prompt
    assert "Alex Chen | Python | Billing service at Shopify." in prompt


def test_build_resume_text_flattens_profile():
    from wecanfindintern.interview.service import build_resume_text
    from wecanfindintern.profile.models import (
        ProfileBasics,
        ProjectEntry,
        SkillEntry,
        UserProfile,
        WorkEntry,
    )

    profile = UserProfile(
        id=uuid4(),
        schema_version="profile.v1",
        basics=ProfileBasics(full_name="Alex Chen", email="alex@example.com"),
        work_experience=[
            WorkEntry(
                company="Shopify",
                title="Backend Intern",
                description="Built billing APIs.",
                skills=["python"],
            )
        ],
        projects=[
            ProjectEntry(name="Billing Service", description="FastAPI + Postgres.")
        ],
        skills=[SkillEntry(name="python"), SkillEntry(name="fastapi")],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    text = build_resume_text(profile)
    assert "Alex Chen" in text
    assert "Work Experience" in text
    assert "Shopify" in text and "Built billing APIs." in text
    assert "Projects" in text and "Billing Service" in text
    assert "Skills" in text and "fastapi" in text


def test_tts_audio_empty():
    audio = generate_tts_audio("")
    assert audio.data == b""


# ---------------------------------------------------------------------------
# TTS backends
# ---------------------------------------------------------------------------


def test_tts_default_backend_is_gtts(monkeypatch):
    from wecanfindintern.interview import tts

    monkeypatch.delenv(tts.TTS_BACKEND_ENV, raising=False)
    assert tts.selected_backend() == "gtts"


def test_tts_local_backend_produces_wav(monkeypatch, tmp_path):
    from wecanfindintern.interview import tts

    monkeypatch.setenv(tts.TTS_BACKEND_ENV, "local")
    tts._synthesize_local.cache_clear()

    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        wav_path = command[2]
        tmp_path.joinpath("out.wav").write_bytes(b"RIFF-fake-wav")
        # say writes to the requested path; emulate by copying our fake bytes.
        import shutil

        shutil.copyfile(tmp_path / "out.wav", wav_path)

    monkeypatch.setattr(tts.subprocess, "run", fake_run)
    audio = tts.generate_tts_audio("Tell me about yourself.")
    assert audio.data == b"RIFF-fake-wav"
    assert audio.media_type == "audio/wav"
    assert captured["command"][0] == "say"
    tts._synthesize_local.cache_clear()


def test_tts_unknown_backend_falls_back_to_default(monkeypatch):
    from wecanfindintern.interview import tts

    monkeypatch.setenv(tts.TTS_BACKEND_ENV, "does-not-exist")
    assert tts.selected_backend() == "gtts"


# ---------------------------------------------------------------------------
# Practice trend aggregation
# ---------------------------------------------------------------------------


def test_summarize_trend_computes_improvement():
    from datetime import datetime
    from uuid import uuid4

    from wecanfindintern.interview.repository import summarize_trend

    def row(score, answers, hours):
        return {
            "id": uuid4(),
            "created_at": datetime(2026, 8, 29, 10 + hours, 0),
            "avg_score": score,
            "answer_count": answers,
        }

    trend = summarize_trend(
        [row(60, 3, 0), row(0, 0, 1), row(75, 3, 2)]
    )
    assert trend["session_count"] == 3
    assert trend["answered_sessions"] == 2
    assert trend["answer_count"] == 6
    assert trend["first_session_score"] == 60
    assert trend["latest_session_score"] == 75
    assert trend["improvement"] == 15
    assert trend["average_score"] == 68  # (60+75)/2 rounded
    assert len(trend["trend"]) == 3


def test_summarize_trend_empty():
    from wecanfindintern.interview.repository import summarize_trend

    trend = summarize_trend([])
    assert trend["session_count"] == 0
    assert trend["improvement"] == 0
    assert trend["trend"] == []


# ---------------------------------------------------------------------------
# Local speech-to-text
# ---------------------------------------------------------------------------


class FakeWhisperModel:
    def __init__(self, segments, language="en", duration=42.0):
        self._segments = segments
        self._language = language
        self._duration = duration

    def transcribe(self, path, vad_filter=True):
        segments = [
            SimpleNamespace(text=f" {segment} ") for segment in self._segments
        ]
        info = SimpleNamespace(language=self._language, duration=self._duration)
        return iter(segments), info


def test_transcribe_audio_joins_segments_and_reports_meta(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERVIEW_STT_MODEL", "tiny")
    monkeypatch.setattr(
        "wecanfindintern.interview.stt._load_model",
        lambda size: FakeWhisperModel(["I led the team", "to deliver on time."]),
    )
    transcript = transcribe_audio(b"fake-bytes", mime="audio/webm")
    assert transcript.text == "I led the team to deliver on time."
    assert transcript.language == "en"
    assert transcript.duration_seconds == 42.0


def test_transcribe_audio_rejects_empty_upload():
    with pytest.raises(STTError):
        transcribe_audio(b"")


def test_transcribe_audio_rejects_silence(monkeypatch):
    monkeypatch.setattr(
        "wecanfindintern.interview.stt._load_model",
        lambda size: FakeWhisperModel(["", "  "]),
    )
    with pytest.raises(STTError):
        transcribe_audio(b"fake-bytes")


# ---------------------------------------------------------------------------
# Provider-agnostic analysis path
# ---------------------------------------------------------------------------


def _fake_llm_result(**extra):
    payload = {
        "score": 82,
        "summary": "Strong answer.",
        "star_feedback": "Good situation and result.",
        "timeline": [{"timestamp": "00:10", "type": "pace", "observation": "steady"}],
        "advice": ["Quantify impact."],
    }
    payload.update(extra)
    return SimpleNamespace(data=payload, usage={"total_tokens": 100})


def test_audio_answer_is_transcribed_then_analyzed_locally():
    captured = {}

    def fake_complete_json(**kwargs):
        captured["user_prompt"] = kwargs["user_prompt"]
        return _fake_llm_result()

    with (
        patch(
            "wecanfindintern.interview.service.transcribe_audio",
            return_value=Transcript(
                text="I migrated the billing service to Go.", language="en", duration_seconds=35.0
            ),
        ),
        patch(
            "wecanfindintern.interview.service.complete_json",
            side_effect=fake_complete_json,
        ),
    ):
        response = analyze_interview_performance(
            job_description="Backend intern role",
            question_context="Describe a migration you led.",
            audio_bytes=b"fake-audio",
            provider="OpenAI",
            model_name="gpt-4o",
            api_key="key",
        )
    assert response.ok
    assert response.transcript == "I migrated the billing service to Go."
    assert response.transcript_language == "en"
    assert response.answer_duration_seconds == 35.0
    assert "I migrated the billing service to Go." in captured["user_prompt"]


def test_typed_answer_takes_precedence_over_transcript():
    captured = {}

    def fake_complete_json(**kwargs):
        captured["user_prompt"] = kwargs["user_prompt"]
        return _fake_llm_result()

    with (
        patch(
            "wecanfindintern.interview.service.transcribe_audio",
            return_value=Transcript(text="audio words", language="en", duration_seconds=10.0),
        ),
        patch(
            "wecanfindintern.interview.service.complete_json",
            side_effect=fake_complete_json,
        ),
    ):
        response = analyze_interview_performance(
            job_description="Backend intern role",
            question_context="q",
            answer_text="Typed correction of the answer.",
            audio_bytes=b"fake-audio",
            provider="OpenAI",
            model_name="gpt-4o",
            api_key="key",
        )
    assert response.transcript == "audio words"
    assert "Typed correction of the answer." in captured["user_prompt"]


def test_analysis_without_answer_or_audio_is_rejected():
    response = analyze_interview_performance(
        job_description="Backend intern role",
        question_context="q",
        answer_text="",
        audio_bytes=None,
        provider="OpenAI",
        model_name="gpt-4o",
        api_key="key",
    )
    assert not response.ok
    assert "audio" in response.error.lower() or "answer" in response.error.lower()


def test_transcription_failure_returns_actionable_error():
    with patch(
        "wecanfindintern.interview.service.transcribe_audio",
        side_effect=STTError("No speech was detected in the recording."),
    ):
        response = analyze_interview_performance(
            job_description="Backend intern role",
            question_context="q",
            audio_bytes=b"fake-audio",
            provider="DeepSeek",
            model_name="deepseek-chat",
            api_key="key",
        )
    assert not response.ok
    assert "No speech was detected" in response.error


# ---------------------------------------------------------------------------
# Rubric-based scoring with evaluation criteria
# ---------------------------------------------------------------------------


def test_analysis_prompt_embeds_criteria_and_honest_timeline():
    from wecanfindintern.llm.prompts.interview import build_analysis_prompt

    prompt = build_analysis_prompt(
        "Backend intern at Acme.",
        "Describe a migration you led.",
        "I migrated billing to Go.",
        "- Naming and structure\n- Rollout safety",
    )
    assert "Evaluation Criteria for this question" in prompt
    assert "- Naming and structure" in prompt
    assert "90-100" in prompt and "0-39" in prompt
    assert "NEVER invent clock timestamps" in prompt
    assert '"section"' in prompt and '"timestamp"' not in prompt


def test_analysis_without_criteria_omits_criteria_block():
    from wecanfindintern.llm.prompts.interview import build_analysis_prompt

    prompt = build_analysis_prompt("jd", "q", "answer")
    assert "Evaluation Criteria for this question" not in prompt


def test_criteria_results_are_parsed_and_validated():
    with patch(
        "wecanfindintern.interview.service.complete_json",
        return_value=_fake_llm_result(
            criteria_results=[
                {
                    "criterion": "Rollout safety",
                    "verdict": "met",
                    "note": "Described canary rollout.",
                },
                {"criterion": "Naming", "verdict": "partial", "note": "Vague."},
                "garbage-entry",
            ],
        ),
    ):
        response = analyze_interview_performance(
            job_description="Backend intern role",
            question_context="q",
            answer_text="Typed answer.",
            evaluation_criteria="- Rollout safety\n- Naming",
            provider="OpenAI",
            model_name="gpt-4o",
            api_key="key",
        )
    assert response.ok
    assert len(response.criteria_results) == 2
    assert response.criteria_results[0].verdict == "met"
    assert response.criteria_results[1].verdict == "partial"


def test_missing_score_defaults_to_zero_not_passed():
    with patch(
        "wecanfindintern.interview.service.complete_json",
        return_value=_fake_llm_result(score=None),
    ):
        response = analyze_interview_performance(
            job_description="Backend intern role",
            question_context="q",
            answer_text="Typed answer.",
            provider="OpenAI",
            model_name="gpt-4o",
            api_key="key",
        )
    assert response.ok
    assert response.score == 0


def test_timeline_timestamps_are_dropped_and_mapped_to_sections():
    with patch(
        "wecanfindintern.interview.service.complete_json",
        return_value=_fake_llm_result(
            timeline=[
                {"timestamp": "0:15", "type": "Opening", "observation": "clear"},
                {"section": "Evidence", "observation": "concrete metrics"},
            ],
        ),
    ):
        response = analyze_interview_performance(
            job_description="Backend intern role",
            question_context="q",
            answer_text="Typed answer.",
            provider="OpenAI",
            model_name="gpt-4o",
            api_key="key",
        )
    sections = [event.section for event in response.timeline]
    assert sections == ["0:15", "Evidence"]  # legacy timestamp mapped, never kept twice
    assert all(event.timestamp == "" for event in response.timeline)
    assert response.timeline[1].observation == "concrete metrics"
