from openai import AsyncOpenAI
from app.config import get_settings


async def embed(text: str) -> list[float]:
    """
    Calls nomic-embed-text via OpenAI-compatible embeddings endpoint.
    Returns a float vector of dimension 768.
    """
    settings = get_settings()
    client = AsyncOpenAI(
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL,
    )
    response = await client.embeddings.create(
        model="nomic-embed-text",
        input=text,
    )
    return response.data[0].embedding
