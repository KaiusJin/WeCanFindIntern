"""Text-to-speech for interview questions, with pluggable backends.

``gtts`` (default) synthesizes through Google's public TTS endpoint and needs
internet access. ``local`` (``INTERVIEW_TTS_BACKEND=local``) shells out to the
macOS ``say`` synthesizer and returns WAV bytes, so question playback works
fully offline.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache

TTS_BACKEND_ENV = "INTERVIEW_TTS_BACKEND"
DEFAULT_BACKEND = "gtts"


class TTSError(RuntimeError):
    """Raised when question audio cannot be synthesized."""


@dataclass(frozen=True, slots=True)
class TtsAudio:
    data: bytes
    media_type: str


def selected_backend() -> str:
    backend = (os.getenv(TTS_BACKEND_ENV) or DEFAULT_BACKEND).strip().lower()
    return backend if backend in {"gtts", "local"} else DEFAULT_BACKEND


@lru_cache(maxsize=128)
def _synthesize_gtts(text: str, lang: str) -> bytes:
    try:
        from gtts import gTTS

        tts = gTTS(text=text, lang=lang, slow=False)
        fp = io.BytesIO()
        tts.write_to_fp(fp)
        fp.seek(0)
        return fp.read()
    except Exception as exc:
        raise TTSError(f"TTS generation failed: {exc}") from exc


@lru_cache(maxsize=128)
def _synthesize_local(text: str) -> bytes:
    if sys.platform != "darwin":
        raise TTSError(
            "The local TTS backend currently requires macOS. "
            f"Unset {TTS_BACKEND_ENV} to use the online backend."
        )
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
        path = handle.name
    try:
        subprocess.run(
            ["say", "-o", path, "--data-format=LEI16@22050", text],
            check=True,
            capture_output=True,
            timeout=60,
        )
        with open(path, "rb") as audio:
            data = audio.read()
    except FileNotFoundError as exc:
        raise TTSError("The macOS 'say' command was not found.") from exc
    except subprocess.CalledProcessError as exc:
        raise TTSError(
            f"Local TTS failed: {exc.stderr.decode(errors='replace')[:200]}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise TTSError("Local TTS timed out.") from exc
    finally:
        with suppress(OSError):
            os.unlink(path)
    if not data:
        raise TTSError("Local TTS produced no audio.")
    return data


def generate_tts_audio(text: str, lang: str = "en") -> TtsAudio:
    """Synthesize question audio with the configured backend."""

    if not text or not text.strip():
        return TtsAudio(data=b"", media_type="audio/mpeg")
    if selected_backend() == "local":
        return TtsAudio(data=_synthesize_local(text.strip()), media_type="audio/wav")
    return TtsAudio(data=_synthesize_gtts(text.strip(), lang), media_type="audio/mpeg")
