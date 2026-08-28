"""Common LLM provider client and sanitization utilities."""

from __future__ import annotations

import re
from typing import Any


def clean_json_text(text: str) -> str:
    """Extract and sanitize JSON block from LLM output."""
    if not text:
        return ""
    cleaned = re.sub(r"^```(?:json)?", "", text.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"```$", "", cleaned.strip(), flags=re.MULTILINE).strip()
    match = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)
    if match:
        return match.group(1).strip()
    return cleaned


def resolve_api_key(provider: str, api_key: str | None = None) -> str:
    """Validate and sanitize API key from request payload."""
    if api_key and api_key.strip():
        cleaned = re.sub(r"[\r\n\t\s'\"]+", "", api_key.strip())
        if cleaned.startswith("Bearer"):
            cleaned = cleaned[6:].strip()
        if cleaned:
            return cleaned
    raise ValueError(f"Missing {provider} API key. Please enter your API key in Settings.")


def call_openai_compatible(
    api_key: str,
    model_name: str | None,
    messages: list[dict[str, str]],
    base_url: str | None = None,
    response_format: dict[str, str] | None = None,
) -> tuple[str, int]:
    """Execute chat completion via OpenAI-compatible endpoint."""
    if not model_name or not model_name.strip():
        raise ValueError("No AI model selected. Please select a model in Settings.")
    clean_key = re.sub(r"[\r\n\t\s'\"]+", "", api_key.strip())
    from openai import OpenAI

    client = OpenAI(api_key=clean_key, base_url=base_url)
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


def call_gemini(
    api_key: str,
    prompt: Any,
    requested_model: str | None = None,
) -> str:
    """Execute Gemini generate_content using strictly the user-requested model."""
    if not requested_model or not requested_model.strip():
        raise ValueError("No AI model selected. Please select a model in Settings.")
    clean_key = re.sub(r"[\r\n\t\s'\"]+", "", api_key.strip())
    import google.generativeai as genai

    genai.configure(api_key=clean_key, transport="rest")

    target_model = requested_model.strip().replace("models/", "")
    model = genai.GenerativeModel(target_model)
    resp = model.generate_content(prompt)
    if resp and resp.text:
        return resp.text
    raise RuntimeError(f"Gemini model {target_model} returned empty response.")
