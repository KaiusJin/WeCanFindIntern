"""Local speech-to-text for recorded interview answers.

Uses faster-whisper (CTranslate2) running fully on-device so transcription
works with any configured AI provider. The model is downloaded on first use
and cached; callers without the package get a clear, actionable error instead
of a hard dependency failure.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache

MODEL_ENV = "INTERVIEW_STT_MODEL"
DEFAULT_MODEL = "base"

_MIME_SUFFIX = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}


class STTError(RuntimeError):
    """Raised when audio cannot be transcribed."""


@dataclass(frozen=True, slots=True)
class Transcript:
    text: str
    language: str
    duration_seconds: float


@lru_cache(maxsize=2)
def _load_model(model_size: str):
    from faster_whisper import WhisperModel

    return WhisperModel(model_size, device="cpu", compute_type="int8")


def transcribe_audio(data: bytes, *, mime: str = "audio/webm") -> Transcript:
    """Transcribe recorded answer audio locally; raises STTError on failure."""

    if not data:
        raise STTError("Empty audio upload.")
    try:
        from faster_whisper import WhisperModel  # noqa: F401
    except ImportError as error:
        raise STTError(
            "Local transcription requires the faster-whisper package. "
            "Install it with: pip install faster-whisper"
        ) from error

    model_size = os.getenv(MODEL_ENV, DEFAULT_MODEL)
    return _transcribe_with_model(model_size, data, mime)


# Real implementation kept separate so tests can patch model loading without
# touching the temp-file plumbing.
def _transcribe_with_model(model_size: str, data: bytes, mime: str) -> Transcript:
    model = _load_model(model_size)
    suffix = _MIME_SUFFIX.get((mime or "").split(";")[0].strip().lower(), ".webm")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(data)
        path = handle.name
    try:
        segments_iter, info = model.transcribe(path, vad_filter=True)
        segments = [segment.text.strip() for segment in segments_iter]
    except Exception as error:
        raise STTError(f"Audio transcription failed: {error}") from error
    finally:
        with suppress(OSError):  # temp cleanup is best effort
            os.unlink(path)
    text = " ".join(segment for segment in segments if segment)
    if not text:
        raise STTError("No speech was detected in the recording.")
    return Transcript(
        text=text,
        language=getattr(info, "language", "") or "",
        duration_seconds=float(getattr(info, "duration", 0.0) or 0.0),
    )
