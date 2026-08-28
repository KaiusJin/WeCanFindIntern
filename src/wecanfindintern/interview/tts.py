"""Text-to-speech audio generation for interview questions."""

from __future__ import annotations

import io
from gtts import gTTS


def generate_tts_audio(text: str, lang: str = "en") -> bytes:
    """Convert text to MP3 audio stream using gTTS."""
    if not text or not text.strip():
        return b""
    try:
        clean_text = text.strip()
        tts = gTTS(text=clean_text, lang=lang, slow=False)
        fp = io.BytesIO()
        tts.write_to_fp(fp)
        fp.seek(0)
        return fp.read()
    except Exception as exc:
        raise RuntimeError(f"TTS generation failed: {exc}") from exc
