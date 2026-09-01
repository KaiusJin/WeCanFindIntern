"""Canonical provider names and capabilities shared by all AI sections."""

from __future__ import annotations

from typing import Literal

ProviderName = Literal["Gemini", "OpenAI", "DeepSeek", "GLM", "Qwen", "Ollama"]
SUPPORTED_LLM_PROVIDERS = frozenset(
    {"Gemini", "OpenAI", "DeepSeek", "GLM", "Qwen", "Ollama"}
)
JSON_RESPONSE_PROVIDERS = frozenset({"OpenAI", "DeepSeek", "GLM", "Qwen", "Ollama"})
