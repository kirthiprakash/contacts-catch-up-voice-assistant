from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.embedding import embed


@pytest.mark.asyncio
async def test_embed_falls_back_to_deterministic_vector_on_api_error():
    with (
        patch("app.services.embedding.get_settings") as mock_settings,
        patch("app.services.embedding.AsyncOpenAI") as mock_openai_cls,
    ):
        mock_settings.return_value = MagicMock(
            OPENAI_API_KEY="test",
            OPENAI_BASE_URL="http://localhost:11434/v1",
        )
        mock_client = AsyncMock()
        mock_client.embeddings.create = AsyncMock(side_effect=RuntimeError("connection failed"))
        mock_openai_cls.return_value = mock_client

        v1 = await embed("alice memory")
        v2 = await embed("alice memory")
        v3 = await embed("different memory")

    assert len(v1) == 768
    assert len(v2) == 768
    assert len(v3) == 768
    assert v1 == v2
    assert v1 != v3
