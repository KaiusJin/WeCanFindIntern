"""Unified LLM gateway: provider routing, bounded latency, retries, and usage.

All AI features should go through :func:`complete_json` so that provider
handling, API key sanitization, JSON parsing, timeouts, retries and token usage
are implemented exactly once. Business modules keep only their prompts and the
interpretation of the parsed result.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any


class LLMError(RuntimeError):
    """Raised when a provider call or its JSON response cannot be completed."""

    def __init__(self, provider: str, message: str, *, model: str | None = None) -> None:
        self.provider = provider
        self.model = model
        super().__init__(f"{provider} error: {message}")


@dataclass(frozen=True, slots=True)
class LLMResult:
    data: Any
    usage: dict[str, Any]
    provider: str
    model: str


def clean_api_key(api_key: str) -> str:
    """Strip whitespace/quotes and a leading ``Bearer`` prefix from a key."""

    cleaned = re.sub(r"[\r\n\t\s'\"]+", "", api_key.strip())
    if cleaned.startswith("Bearer"):
        cleaned = cleaned[6:].strip()
    return cleaned


def resolve_api_key(provider: str, api_key: str | None = None) -> str:
    """Validate and sanitize an API key supplied by the caller."""

    if api_key and api_key.strip():
        cleaned = clean_api_key(api_key)
        if cleaned:
            return cleaned
    raise LLMError(provider, f"Missing {provider} API key. Please enter your API key in Settings.")


def clean_json_text(text: str) -> str:
    """Extract the JSON block from an LLM response and strip fences."""

    if not text:
        return ""
    cleaned = re.sub(r"^```(?:json)?", "", text.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"```$", "", cleaned.strip(), flags=re.MULTILINE).strip()
    match = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)
    if match:
        return match.group(1).strip()
    return cleaned


def parse_json(text: str) -> Any:
    """Parse an LLM response as JSON, tolerating markdown fences and prose."""

    try:
        parsed = json.loads(clean_json_text(text))
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON response: {error}") from error
    if not isinstance(parsed, (dict, list)):
        raise ValueError("LLM response was not a JSON object or array.")
    return parsed


def complete_json(
    *,
    provider: str,
    model_name: str,
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    response_format: dict[str, str] | None = None,
    timeout_seconds: float = 60.0,
    max_retries: int = 1,
) -> LLMResult:
    """Return parsed JSON from the selected provider with bounded latency.

    Transient transport failures are retried with exponential backoff up to
    ``max_retries`` extra attempts. Validation and JSON parsing failures raise
    :class:`LLMError` without retrying, so malformed model output is surfaced
    to the caller instead of silently multiplying cost.
    """

    if not model_name or not model_name.strip():
        raise LLMError(provider, "No AI model selected. Please select a model in Settings.")
    key = clean_api_key(api_key)
    if not key:
        raise LLMError(
            provider,
            f"Missing {provider} API key. Please enter your API key in Settings.",
        )
    model = model_name.strip().replace("models/", "")
    if provider not in ("Gemini", "OpenAI", "DeepSeek"):
        raise LLMError(provider, f"Unsupported provider: {provider}", model=model)

    delay = 1.0
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            if provider in ("OpenAI", "DeepSeek"):
                return _openai_compatible(
                    provider=provider,
                    api_key=key,
                    model=model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_format=response_format,
                    timeout_seconds=timeout_seconds,
                )
            return _gemini(
                api_key=key,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                timeout_seconds=timeout_seconds,
            )
        except LLMError:
            raise
        except Exception as error:  # Transport/rate-limit failures are retryable.
            last_error = error
            if attempt >= max_retries:
                break
            time.sleep(delay)
            delay *= 2
    raise LLMError(provider, str(last_error), model=model) from last_error


def _openai_compatible(
    *,
    provider: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    response_format: dict[str, str] | None,
    timeout_seconds: float,
) -> LLMResult:
    from openai import OpenAI

    base_url = (
        os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com")
        if provider == "DeepSeek"
        else None
    )
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_seconds, max_retries=0)
    kwargs: dict[str, Any] = {}
    if response_format:
        kwargs["response_format"] = response_format
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        **kwargs,
    )
    content = response.choices[0].message.content or "{}"
    usage: dict[str, Any] = {}
    if response.usage is not None:
        usage["total_tokens"] = response.usage.total_tokens
        if getattr(response.usage, "prompt_tokens", None) is not None:
            usage["prompt_tokens"] = response.usage.prompt_tokens
        if getattr(response.usage, "completion_tokens", None) is not None:
            usage["completion_tokens"] = response.usage.completion_tokens
    try:
        data = parse_json(content)
    except ValueError as error:
        raise LLMError(provider, f"invalid JSON response: {error}", model=model) from error
    return LLMResult(
        data=data,
        usage=usage,
        provider=provider,
        model=model,
    )


def _gemini(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    timeout_seconds: float,
) -> LLMResult:
    import google.generativeai as genai

    genai.configure(api_key=api_key, transport="rest")
    genai_model = genai.GenerativeModel(model)
    response = genai_model.generate_content(
        f"{system_prompt}\n\n{user_prompt}",
        request_options={"timeout": timeout_seconds},
    )
    if response is None or not response.text:
        raise LLMError("Gemini", f"Gemini model {model} returned empty response.", model=model)
    try:
        data = parse_json(response.text)
    except ValueError as error:
        raise LLMError("Gemini", f"invalid JSON response: {error}", model=model) from error
    return LLMResult(
        data=data,
        usage={},
        provider="Gemini",
        model=model,
    )
