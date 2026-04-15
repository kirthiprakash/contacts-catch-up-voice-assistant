from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
)
from app.models.memory import MemoryEntry
from app.config import get_settings

COLLECTION_NAME = "memories"
VECTOR_SIZE = 768


def _get_client() -> AsyncQdrantClient:
    settings = get_settings()
    return AsyncQdrantClient(
        url=settings.QDRANT_ENDPOINT,
        api_key=settings.QDRANT_API_KEY,
    )


async def ensure_collection_exists() -> None:
    """
    Called at startup. Creates the 'memories' collection with vector_size=768
    and distance=Cosine if it does not already exist. Safe to call repeatedly.
    """
    client = _get_client()
    existing = await client.get_collections()
    names = [c.name for c in existing.collections]
    if COLLECTION_NAME not in names:
        await client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )


async def store_memory(entry: MemoryEntry) -> str:
    """Embeds entry.text and upserts into Qdrant. Returns point ID."""
    from app.services.embedding import embed

    vector = await embed(entry.text)
    client = _get_client()
    payload = {
        "contact_id": entry.contact_id,
        "type": entry.type,
        "text": entry.text,
        "timestamp": entry.timestamp.isoformat(),
    }
    await client.upsert(
        collection_name=COLLECTION_NAME,
        points=[
            PointStruct(
                id=entry.entry_id,
                vector=vector,
                payload=payload,
            )
        ],
    )
    return entry.entry_id


async def search_memory(contact_id: str, query: str, top_k: int = 5) -> list[MemoryEntry]:
    """
    Embeds query, performs cosine similarity search scoped to contact_id.
    Returns top_k results.
    """
    from app.services.embedding import embed
    from datetime import datetime

    vector = await embed(query)
    client = _get_client()
    contact_filter = Filter(
        must=[
            FieldCondition(
                key="contact_id",
                match=MatchValue(value=contact_id),
            )
        ]
    )
    results = await client.search(
        collection_name=COLLECTION_NAME,
        query_vector=vector,
        query_filter=contact_filter,
        limit=top_k,
    )
    entries = []
    for hit in results:
        p = hit.payload
        entries.append(
            MemoryEntry(
                entry_id=str(hit.id),
                contact_id=p["contact_id"],
                type=p["type"],
                text=p["text"],
                timestamp=datetime.fromisoformat(p["timestamp"]),
            )
        )
    return entries


async def delete_contact_memories(contact_id: str) -> None:
    """Deletes all memory entries for a given contact_id."""
    client = _get_client()
    contact_filter = Filter(
        must=[
            FieldCondition(
                key="contact_id",
                match=MatchValue(value=contact_id),
            )
        ]
    )
    await client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=contact_filter,
    )
