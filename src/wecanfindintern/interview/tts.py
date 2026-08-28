"""Text-to-speech audio generation for interview questions."""

from __future__ import annotations

import io
from functools import lru_cache

from gtts import gTTS


@lru_cache(maxsize=128)
def _synthesize(text: str, lang: str) -> bytes:
    """Convert normalized text to MP3 bytes (cached per exact question)."""
    try:
        tts = gTTS(text=text, lang=lang, slow=False)
        fp = io.BytesIO()
        tts.write_to_fp(fp)
        fp.seek(0)
        return fp.read()
    except Exception as exc:
        raise RuntimeError(f"TTS generation failed: {exc}") from exc


def generate_tts_audio(text: str, lang: str = "en") -> bytes:
    """Convert text to MP3 audio stream using gTTS."""
    if not text or not text.strip():
        return b""
    return _synthesize(text.strip(), lang)
