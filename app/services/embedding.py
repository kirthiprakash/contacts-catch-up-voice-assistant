import hashlib
import logging

import httpx
from openai import AsyncOpenAI
from app.config import get_settings

logger = logging.getLogger(__name__)


def _get_vector_size() -> int:
    try:
        return get_settings().EMBEDDING_VECTOR_SIZE
    except Exception:
        return 3072


def _deterministic_fallback_embedding(text: str) -> list[float]:
    """Stable deterministic fallback when the remote embedding call fails."""
    size = _get_vector_size()
    values: list[float] = []
    seed = text.encode("utf-8")
    counter = 0
    while len(values) < size:
        digest = hashlib.sha256(seed + counter.to_bytes(8, "big")).digest()
        for i in range(0, len(digest), 4):
            chunk = digest[i:i + 4]
            if len(chunk) < 4:
                continue
            num = int.from_bytes(chunk, "big", signed=False)
            values.append((num / 2**31) - 1.0)
            if len(values) == size:
                break
        counter += 1
    return values


def _is_gemini_url(base_url: str) -> bool:
    return "generativelanguage.googleapis.com" in base_url


async def _embed_gemini(text: str, api_key: str, model: str) -> list[float]:
    """Native Gemini embedContent API — uses X-goog-api-key auth."""
    model_id = model if model.startswith("models/") else f"models/{model}"
    url = f"https://generativelanguage.googleapis.com/v1beta/{model_id}:embedContent"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            headers={"X-goog-api-key": api_key, "Content-Type": "application/json"},
            json={"model": model_id, "content": {"parts": [{"text": text}]}},
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()["embedding"]["values"]


async def _embed_openai_compat(text: str, api_key: str, base_url: str, model: str) -> list[float]:
    """OpenAI-compatible embeddings endpoint (nomic, OpenAI, etc.)."""
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    response = await client.embeddings.create(model=model, input=text)
    return response.data[0].embedding


async def embed(text: str) -> list[float]:
    """
    Returns an embedding vector for the given text.
    - Gemini URLs (generativelanguage.googleapis.com) → native embedContent API
    - All other URLs → OpenAI-compatible /embeddings endpoint
    Falls back to a deterministic local embedding on any failure.
    """
    settings = get_settings()
    try:
        if _is_gemini_url(settings.EMBEDDING_BASE_URL):
            return await _embed_gemini(text, settings.EMBEDDING_API_KEY, settings.EMBEDDING_MODEL)
        return await _embed_openai_compat(
            text, settings.EMBEDDING_API_KEY, settings.EMBEDDING_BASE_URL, settings.EMBEDDING_MODEL
        )
    except Exception as exc:
        logger.warning("Embedding API failed; using deterministic fallback: %s", exc)
        return _deterministic_fallback_embedding(text)
