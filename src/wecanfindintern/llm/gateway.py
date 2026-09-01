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
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from wecanfindintern.llm import cache as llm_cache
from wecanfindintern.llm.providers import JSON_RESPONSE_PROVIDERS, SUPPORTED_LLM_PROVIDERS


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

    if provider == "Ollama":
        return "local"
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
        parsed = _find_last_json(clean_json_text(text))
        if parsed is None:
            raise ValueError(f"invalid JSON response: {error}") from error
    if not isinstance(parsed, (dict, list)):
        raise ValueError("LLM response was not a JSON object or array.")
    return parsed


def _find_last_json(text: str) -> Any:
    """Locate the last balanced JSON value when models emit extra blocks.

    Some providers return a reasoning/scratch block followed by the real JSON
    answer. ``json.loads`` rejects those concatenated documents, so fall back
    to scanning for the last balanced JSON value in the text.
    """

    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.rfind(opener)
        while start != -1:
            depth = 0
            in_string = False
            escaped = False
            for index in range(start, len(text)):
                char = text[index]
                if in_string:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == '"':
                        in_string = False
                    continue
                if char == '"':
                    in_string = True
                elif char == opener:
                    depth += 1
                elif char == closer:
                    depth -= 1
                    if depth == 0:
                        candidate = text[start : index + 1]
                        try:
                            parsed = json.loads(candidate)
                        except json.JSONDecodeError:
                            break
                        if isinstance(parsed, (dict, list)):
                            return parsed
            start = text.rfind(opener, 0, start)
    return None


def json_response_format(provider: str) -> dict[str, str] | None:
    """``json_object`` mode for providers that support it; ``None`` otherwise."""

    if provider in JSON_RESPONSE_PROVIDERS:
        return {"type": "json_object"}
    return None


def complete_json(
    *,
    provider: str,
    model_name: str,
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    response_format: dict[str, str] | None = None,
    api_base: str | None = None,
    timeout_seconds: float = 60.0,
    max_retries: int = 1,
    use_cache: bool = False,
) -> LLMResult:
    """Return parsed JSON from the selected provider with bounded latency.

    Transient transport failures are retried with exponential backoff up to
    ``max_retries`` extra attempts. Validation and JSON parsing failures raise
    :class:`LLMError` without retrying, so malformed model output is surfaced
    to the caller instead of silently multiplying cost.
    """

    if not model_name or not model_name.strip():
        raise LLMError(provider, "No AI model selected. Please select a model in Settings.")
    cache_entry: str | None = None
    cache_store_key: str | None = None
    if use_cache and llm_cache.cache_enabled():
        cache_store_key = llm_cache.cache_key(provider, model_name, system_prompt, user_prompt)
        cache_entry = llm_cache.lookup(cache_store_key)
        if cache_entry is not None:
            return LLMResult(
                data=parse_json(cache_entry),
                usage={"cached": True},
                provider=provider,
                model=model_name,
            )
    key = clean_api_key(api_key)
    if provider == "Ollama":
        key = "local"
    elif not key:
        raise LLMError(
            provider,
            f"Missing {provider} API key. Please enter your API key in Settings.",
        )
    model = model_name.strip().replace("models/", "")
    if provider not in SUPPORTED_LLM_PROVIDERS:
        raise LLMError(provider, f"Unsupported provider: {provider}", model=model)

    delay = 1.0
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            if provider in JSON_RESPONSE_PROVIDERS:
                result = _openai_compatible(
                    provider=provider,
                    api_key=key,
                    model=model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_format=response_format,
                    api_base=api_base,
                    timeout_seconds=timeout_seconds,
                )
            else:
                result = _gemini(
                    api_key=key,
                    model=model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    timeout_seconds=timeout_seconds,
                )
            if cache_store_key is not None:
                llm_cache.store(
                    cache_store_key,
                    provider,
                    model_name,
                    json.dumps(result.data, ensure_ascii=False, default=str),
                )
            return result
        except LLMError:
            raise
        except Exception as error:  # Transport/rate-limit failures are retryable.
            last_error = error
            if attempt >= max_retries:
                break
            time.sleep(delay)
            delay *= 2
    raise LLMError(provider, str(last_error), model=model) from last_error


def extract_json_array_objects(buffer: str) -> tuple[list[Any], str]:
    """Pop complete top-level objects from a partially streamed JSON array.

    Returns the finished objects and the remainder to keep buffering. Used
    by streaming endpoints so each array element can be emitted the moment
    its closing brace arrives.
    """

    start = buffer.find("[")
    if start == -1:
        return [], buffer
    body = buffer[start + 1:]
    objects: list[Any] = []
    consumed = 0
    index = 0
    depth = 0
    in_string = False
    escaped = False
    object_start: int | None = None
    while index < len(body):
        char = body[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                object_start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and object_start is not None:
                try:
                    objects.append(json.loads(body[object_start : index + 1]))
                    consumed = index + 1
                except json.JSONDecodeError:
                    pass
                object_start = None
        index += 1
    return objects, "[" + body[consumed:]


def _openai_base_url(provider: str, api_base: str | None) -> str | None:
    if api_base:
        return api_base
    if provider == "DeepSeek":
        return os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com")
    if provider == "GLM":
        return os.environ.get("GLM_API_BASE", "https://open.bigmodel.cn/api/paas/v4")
    if provider == "Qwen":
        return os.environ.get(
            "QWEN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
    if provider == "Ollama":
        return os.environ.get("OLLAMA_API_BASE", "http://localhost:11434/v1")
    return None


def stream_text(
    *,
    provider: str,
    model_name: str,
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    api_base: str | None = None,
    timeout_seconds: float = 120.0,
) -> Iterator[str]:
    """Yield raw text deltas from the selected provider (no JSON handling).

    Consumed for streaming UI responses; errors raise :class:`LLMError`
    exactly like :func:`complete_json`. No retry: a stream that already
    produced output cannot be safely replayed.
    """

    if not model_name or not model_name.strip():
        raise LLMError(provider, "No AI model selected. Please select a model in Settings.")
    key = clean_api_key(api_key)
    if provider == "Ollama":
        key = "local"
    elif not key:
        raise LLMError(
            provider, f"Missing {provider} API key. Please enter your API key in Settings."
        )
    model = model_name.strip().replace("models/", "")
    if provider in JSON_RESPONSE_PROVIDERS:
        yield from _openai_stream(
            provider=provider,
            api_key=key,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            api_base=api_base,
            timeout_seconds=timeout_seconds,
        )
    elif provider == "Gemini":
        yield from _gemini_stream(
            api_key=key,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            timeout_seconds=timeout_seconds,
        )
    else:
        raise LLMError(provider, f"Unsupported provider: {provider}", model=model)


def _openai_stream(
    *,
    provider: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    api_base: str | None = None,
    timeout_seconds: float,
) -> Iterator[str]:
    from openai import OpenAI

    client = OpenAI(
        api_key=api_key,
        base_url=_openai_base_url(provider, api_base),
        timeout=timeout_seconds,
        max_retries=0,
    )
    stream = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        stream=True,
    )
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


def _gemini_stream(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    timeout_seconds: float,
) -> Iterator[str]:
    import google.generativeai as genai

    genai.configure(api_key=api_key, transport="rest")
    genai_model = genai.GenerativeModel(model)
    stream = genai_model.generate_content(
        f"{system_prompt}\n\n{user_prompt}",
        stream=True,
        request_options={"timeout": timeout_seconds},
    )
    for chunk in stream:
        if chunk.text:
            yield chunk.text


def _openai_compatible(
    *,
    provider: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    response_format: dict[str, str] | None,
    api_base: str | None = None,
    timeout_seconds: float,
) -> LLMResult:
    from openai import OpenAI

    base_url = _openai_base_url(provider, api_base)
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
