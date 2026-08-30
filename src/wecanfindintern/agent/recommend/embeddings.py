"""Provider-neutral embedding gateway for recommendation retrieval."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

import httpx
from openai import OpenAI

from wecanfindintern.llm.gateway import clean_api_key

SUPPORTED_EMBEDDING_PROVIDERS = {"OpenAI", "Gemini", "Ollama"}
DEFAULT_MODELS = {
    "OpenAI": "text-embedding-3-small",
    "Gemini": "gemini-embedding-001",
    "Ollama": "qwen3-embedding:0.6b",
}
DEFAULT_DIMENSIONS = 768


def _provider_name(value: str | None) -> str:
    normalized = (value or "OpenAI").strip().lower()
    names = {provider.lower(): provider for provider in SUPPORTED_EMBEDDING_PROVIDERS}
    if normalized not in names:
        raise ValueError(f"Unsupported embedding provider: {value}")
    return names[normalized]


def _normalize_base_url(provider: str, value: str | None) -> str | None:
    if not value:
        return "http://127.0.0.1:11434" if provider == "Ollama" else None
    base = value.rstrip("/")
    if provider == "Ollama" and base.endswith("/v1"):
        base = base[:-3]
    return base


@dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    provider: str
    model: str
    dimensions: int = DEFAULT_DIMENSIONS
    api_key: str = ""
    api_base: str | None = None

    def __post_init__(self) -> None:
        provider = _provider_name(self.provider)
        if not self.model.strip():
            raise ValueError("Embedding model is required")
        if not 1 <= self.dimensions <= 4096:
            raise ValueError("Embedding dimensions must be between 1 and 4096")
        if provider != "Ollama" and not clean_api_key(self.api_key):
            raise ValueError(f"{provider} embedding API key is required")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "model", self.model.strip())
        object.__setattr__(self, "api_key", clean_api_key(self.api_key))
        object.__setattr__(self, "api_base", _normalize_base_url(provider, self.api_base))

    @property
    def version(self) -> str:
        return f"{self.provider}:{self.model}:{self.dimensions}:v1"

    @classmethod
    def from_env(cls) -> EmbeddingConfig | None:
        provider = _provider_name(os.getenv("RECOMMEND_EMBEDDING_PROVIDER"))
        key = clean_api_key(
            os.getenv("RECOMMEND_EMBEDDING_API_KEY")
            or (os.getenv("OPENAI_API_KEY") if provider == "OpenAI" else "")
            or (
                os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
                if provider == "Gemini"
                else ""
            )
            or ""
        )
        if provider != "Ollama" and not key:
            return None
        default_base = os.getenv("OLLAMA_API_BASE") if provider == "Ollama" else None
        return cls(
            provider=provider,
            model=os.getenv("RECOMMEND_EMBEDDING_MODEL", DEFAULT_MODELS[provider]),
            dimensions=int(
                os.getenv("RECOMMEND_EMBEDDING_DIMENSIONS", str(DEFAULT_DIMENSIONS))
            ),
            api_key=key,
            api_base=os.getenv("RECOMMEND_EMBEDDING_API_BASE") or default_base,
        )

    @classmethod
    def from_values(
        cls,
        *,
        provider: str | None,
        model: str | None,
        dimensions: int | None,
        api_key: str | None,
        api_base: str | None,
    ) -> EmbeddingConfig | None:
        if not provider:
            return cls.from_env()
        canonical = _provider_name(provider)
        return cls(
            provider=canonical,
            model=model or DEFAULT_MODELS[canonical],
            dimensions=dimensions or DEFAULT_DIMENSIONS,
            api_key=api_key or "",
            api_base=api_base,
        )


class EmbeddingGateway:
    def __init__(self, config: EmbeddingConfig) -> None:
        self.config = config

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, task_type="RETRIEVAL_DOCUMENT")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], task_type="RETRIEVAL_QUERY")[0]

    def _embed(self, texts: list[str], *, task_type: str) -> list[list[float]]:
        if not texts:
            return []
        if self.config.provider == "OpenAI":
            vectors = self._openai(texts)
        elif self.config.provider == "Gemini":
            vectors = self._gemini(texts, task_type=task_type)
        else:
            vectors = self._ollama(texts)
        if len(vectors) != len(texts):
            raise RuntimeError("Embedding provider returned an incomplete batch")
        return [self._validate_and_normalize(vector) for vector in vectors]

    def _openai(self, texts: list[str]) -> list[list[float]]:
        client = OpenAI(
            api_key=self.config.api_key,
            base_url=self.config.api_base,
            timeout=30.0,
            max_retries=1,
        )
        response = client.embeddings.create(
            model=self.config.model,
            input=texts,
            dimensions=self.config.dimensions,
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        return [item.embedding for item in ordered]

    def _gemini(self, texts: list[str], *, task_type: str) -> list[list[float]]:
        model = self.config.model.removeprefix("models/")
        base = self.config.api_base or "https://generativelanguage.googleapis.com/v1beta"
        requests = [
            {
                "model": f"models/{model}",
                "content": {"parts": [{"text": text}]},
                "taskType": task_type,
                "outputDimensionality": self.config.dimensions,
            }
            for text in texts
        ]
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                f"{base.rstrip('/')}/models/{model}:batchEmbedContents",
                headers={"x-goog-api-key": self.config.api_key},
                json={"requests": requests},
            )
            response.raise_for_status()
        return [item["values"] for item in response.json().get("embeddings", [])]

    def _ollama(self, texts: list[str]) -> list[list[float]]:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                f"{self.config.api_base}/api/embed",
                json={
                    "model": self.config.model,
                    "input": texts,
                    "dimensions": self.config.dimensions,
                    "truncate": True,
                },
            )
            response.raise_for_status()
        return response.json().get("embeddings", [])

    def _validate_and_normalize(self, vector: list[float]) -> list[float]:
        if len(vector) != self.config.dimensions:
            raise RuntimeError(
                f"{self.config.provider} returned {len(vector)} dimensions; "
                f"expected {self.config.dimensions}"
            )
        magnitude = math.sqrt(sum(value * value for value in vector))
        if not magnitude or not math.isfinite(magnitude):
            raise RuntimeError("Embedding provider returned an invalid vector")
        return [value / magnitude for value in vector]
