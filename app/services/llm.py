"""
LLM Extractor service.

Calls an OpenAI-compatible API to extract structured data from call transcripts.
Retries up to 2 times on any failure; returns empty ExtractionResult on exhaustion.
"""

import json
import logging
from openai import AsyncOpenAI

from app.config import get_settings
from app.models.memory import CallbackIntent, ExtractionResult

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a call-notes extractor. Given a phone call transcript, extract structured information and return ONLY valid JSON with these fields:

{
  "summary": "<one-paragraph summary of the call>",
  "highlights": ["<notable moment 1>", "<notable moment 2>"],
  "facts": [{"key": "<fact label>", "value": "<fact value>"}],
  "followups": ["<action item 1>", "<action item 2>"],
  "callback": {
    "type": "relative" | "absolute" | "none",
    "value": "<e.g. '1 hour' or '2024-12-01T10:00:00' or ''>"
  },
  "call_time_preference": "morning" | "evening" | "specific_time" | "none"
}

Rules:
- Return ONLY the JSON object, no markdown fences, no extra text.
- If a field has no relevant content, use its empty default (empty string, empty list, or "none").
- callback.type is "relative" when the contact said something like "call me in X", "absolute" for a specific date/time, "none" otherwise.
- call_time_preference reflects when the contact prefers to be called.
"""


async def extract_from_transcript(transcript: str) -> ExtractionResult:
    """
    Calls OpenAI-compatible API with structured output schema.
    Retries up to 2 times. Returns empty ExtractionResult on failure.
    """
    settings = get_settings()
    client = AsyncOpenAI(
        base_url=settings.OPENAI_BASE_URL,
        api_key=settings.OPENAI_API_KEY,
    )

    last_error: Exception | None = None
    for attempt in range(3):  # initial attempt + 2 retries
        try:
            response = await client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": f"Transcript:\n{transcript}"},
                ],
                temperature=0.0,
            )
            raw = response.choices[0].message.content or ""
            data = json.loads(raw)
            return ExtractionResult.model_validate(data)
        except Exception as exc:
            last_error = exc
            logger.warning(
                "LLM extraction attempt %d/%d failed: %s",
                attempt + 1,
                3,
                exc,
            )

    logger.warning("LLM extraction exhausted all retries. Returning empty result. Last error: %s", last_error)
    return ExtractionResult()
