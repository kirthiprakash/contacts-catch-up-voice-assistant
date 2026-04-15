"""
Property-based tests for the memory store (qdrant.py) and embedding service.

Property 16: Memory store round-trip with embedding
Property 17: Memory search is scoped to contact_id — no cross-contact leakage
Property 18: Search result count bounded by top_k

Feature: contacts-catch-up-voice-assistant
"""

import os
import pytest
import unittest.mock as mock_lib
from datetime import datetime, UTC
from uuid import uuid4

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from app.models.memory import MemoryEntry

# Set required env vars before importing modules that call get_settings()
os.environ.setdefault("VAPI_API_KEY", "test-key")
os.environ.setdefault("VAPI_ASSISTANT_ID", "asst-123")
os.environ.setdefault("VAPI_PHONE_NUMBER_ID", "pn-456")
os.environ.setdefault("QDRANT_API_KEY", "qd-key")
os.environ.setdefault("QDRANT_ENDPOINT", "https://qdrant.example.com")
os.environ.setdefault("OPENAI_API_KEY", "oai-key")
os.environ.setdefault("OPENAI_BASE_URL", "https://api.openai.com/v1")
os.environ.setdefault("OPENAI_MODEL", "gpt-4o")

import app.services.qdrant as qdrant_module
import app.services.embedding as embedding_module

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VECTOR_SIZE = 768


def make_fake_vector(seed: int = 0) -> list[float]:
    """Returns a deterministic unit-ish vector of size 768."""
    import math
    v = [(math.sin(seed + i) + 1.0) / 2.0 for i in range(VECTOR_SIZE)]
    return v


def make_entry(contact_id: str = None, text: str = "hello world") -> MemoryEntry:
    return MemoryEntry(
        entry_id=str(uuid4()),
        contact_id=contact_id or str(uuid4()),
        type="fact",
        text=text,
        timestamp=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

contact_id_strategy = st.uuids().map(str)
text_strategy = st.text(min_size=1, max_size=200, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd", "Zs")))
type_strategy = st.sampled_from(["summary", "highlight", "fact", "social"])
top_k_strategy = st.integers(min_value=1, max_value=20)

entry_strategy = st.builds(
    MemoryEntry,
    entry_id=st.uuids().map(str),
    contact_id=contact_id_strategy,
    type=type_strategy,
    text=text_strategy,
    timestamp=st.just(datetime.now(UTC)),
)


# ---------------------------------------------------------------------------
# Shared mock infrastructure
# ---------------------------------------------------------------------------

class FakeQdrantStore:
    """In-memory Qdrant-like store for testing without a live Qdrant instance."""

    def __init__(self):
        self.points: dict[str, dict] = {}  # id -> {vector, payload}

    def upsert(self, points):
        for p in points:
            self.points[str(p.id)] = {"vector": p.vector, "payload": p.payload}

    def search(self, query_vector, contact_filter, limit):
        """Returns hits filtered by contact_id, sorted by cosine similarity."""
        import math

        def cosine_sim(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            norm_a = math.sqrt(sum(x * x for x in a))
            norm_b = math.sqrt(sum(x * x for x in b))
            if norm_a == 0 or norm_b == 0:
                return 0.0
            return dot / (norm_a * norm_b)

        # Extract contact_id filter value
        filter_contact_id = None
        if contact_filter and contact_filter.must:
            for cond in contact_filter.must:
                if cond.key == "contact_id":
                    filter_contact_id = cond.match.value

        results = []
        for point_id, data in self.points.items():
            if filter_contact_id and data["payload"].get("contact_id") != filter_contact_id:
                continue
            score = cosine_sim(query_vector, data["vector"])
            results.append((score, point_id, data["payload"]))

        results.sort(key=lambda x: x[0], reverse=True)
        results = results[:limit]

        # Return ScoredPoint-like objects
        hits = []
        for score, point_id, payload in results:
            hit = mock_lib.MagicMock()
            hit.id = point_id
            hit.score = score
            hit.payload = payload
            hits.append(hit)
        return hits

    def delete(self, points_selector):
        """Delete points matching the filter."""
        filter_contact_id = None
        if points_selector and points_selector.must:
            for cond in points_selector.must:
                if cond.key == "contact_id":
                    filter_contact_id = cond.match.value

        if filter_contact_id:
            to_delete = [
                pid for pid, data in self.points.items()
                if data["payload"].get("contact_id") == filter_contact_id
            ]
            for pid in to_delete:
                del self.points[pid]


def make_mock_client(store: FakeQdrantStore):
    """Creates an AsyncMock Qdrant client backed by FakeQdrantStore."""
    client = mock_lib.AsyncMock()

    async def fake_upsert(collection_name, points):
        store.upsert(points)

    async def fake_search(collection_name, query_vector, query_filter, limit):
        return store.search(query_vector, query_filter, limit)

    async def fake_delete(collection_name, points_selector):
        store.delete(points_selector)

    client.upsert = fake_upsert
    client.search = fake_search
    client.delete = fake_delete
    return client


# ---------------------------------------------------------------------------
# Property 16: Memory store round-trip with embedding
# Feature: contacts-catch-up-voice-assistant, Property 16: Memory store round-trip with embedding
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@given(entry=entry_strategy)
@settings(max_examples=100)
async def test_property_16_memory_store_round_trip(entry: MemoryEntry):
    """
    **Validates: Requirements 6.1, 6.2**
    For any MemoryEntry, after store_memory(entry), search_memory(entry.contact_id, entry.text, top_k=1)
    returns a result whose first element has the same text and contact_id as the original entry,
    and the stored vector is a non-empty float list.
    """
    store = FakeQdrantStore()
    mock_client = make_mock_client(store)

    # Use a deterministic vector based on entry text hash
    fake_vector = make_fake_vector(seed=hash(entry.text) % 1000)

    with mock_lib.patch.object(qdrant_module, "_get_client", return_value=mock_client):
        with mock_lib.patch.object(embedding_module, "embed", return_value=fake_vector) as mock_embed:
            # Store the entry
            returned_id = await qdrant_module.store_memory(entry)

            # Verify the returned ID matches entry_id
            assert returned_id == entry.entry_id

            # Verify the vector was stored (non-empty)
            assert entry.entry_id in store.points
            stored_vector = store.points[entry.entry_id]["vector"]
            assert isinstance(stored_vector, list)
            assert len(stored_vector) > 0

            # Search for the entry
            results = await qdrant_module.search_memory(entry.contact_id, entry.text, top_k=1)

    # The round-trip should return the entry
    assert len(results) == 1
    assert results[0].text == entry.text
    assert results[0].contact_id == entry.contact_id


# ---------------------------------------------------------------------------
# Property 17: Memory search is scoped to contact_id — no cross-contact leakage
# Feature: contacts-catch-up-voice-assistant, Property 17: Memory search scoped to contact_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@given(
    contact_id_a=contact_id_strategy,
    contact_id_b=contact_id_strategy,
    texts_a=st.lists(text_strategy, min_size=1, max_size=3),
    texts_b=st.lists(text_strategy, min_size=1, max_size=3),
    query=text_strategy,
    top_k=top_k_strategy,
)
@settings(max_examples=100)
async def test_property_17_cross_contact_isolation(
    contact_id_a: str,
    contact_id_b: str,
    texts_a: list[str],
    texts_b: list[str],
    query: str,
    top_k: int,
):
    """
    **Validates: Requirements 6.4, 4.5**
    Every entry returned by search_memory(contact_id, query) has entry.contact_id == contact_id.
    No entries belonging to other contacts appear in the results.
    """
    assume(contact_id_a != contact_id_b)

    store = FakeQdrantStore()
    mock_client = make_mock_client(store)

    # Use different vectors for different contacts to ensure they're distinguishable
    vector_a = make_fake_vector(seed=1)
    vector_b = make_fake_vector(seed=2)
    query_vector = make_fake_vector(seed=3)

    entries_a = [
        MemoryEntry(
            entry_id=str(uuid4()),
            contact_id=contact_id_a,
            type="fact",
            text=t,
            timestamp=datetime.now(UTC),
        )
        for t in texts_a
    ]
    entries_b = [
        MemoryEntry(
            entry_id=str(uuid4()),
            contact_id=contact_id_b,
            type="fact",
            text=t,
            timestamp=datetime.now(UTC),
        )
        for t in texts_b
    ]

    def fake_embed_side_effect(text):
        # Return vector_a for contact_a texts, vector_b for contact_b texts, query_vector for query
        if text in texts_a:
            return vector_a
        elif text in texts_b:
            return vector_b
        else:
            return query_vector

    with mock_lib.patch.object(qdrant_module, "_get_client", return_value=mock_client):
        with mock_lib.patch.object(embedding_module, "embed", side_effect=fake_embed_side_effect):
            # Store all entries for both contacts
            for entry in entries_a + entries_b:
                await qdrant_module.store_memory(entry)

            # Search for contact_a only
            results = await qdrant_module.search_memory(contact_id_a, query, top_k=top_k)

    # All results must belong to contact_a
    for result in results:
        assert result.contact_id == contact_id_a, (
            f"Expected contact_id={contact_id_a}, got {result.contact_id}"
        )


# ---------------------------------------------------------------------------
# Property 18: Search result count bounded by top_k
# Feature: contacts-catch-up-voice-assistant, Property 18: Search result count bounded by top_k
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@given(
    contact_id=contact_id_strategy,
    num_entries=st.integers(min_value=0, max_value=15),
    top_k=top_k_strategy,
    query=text_strategy,
)
@settings(max_examples=100)
async def test_property_18_search_result_count_bounded(
    contact_id: str,
    num_entries: int,
    top_k: int,
    query: str,
):
    """
    **Validates: Requirements 6.3**
    The length of the list returned by search_memory shall be <= top_k.
    """
    store = FakeQdrantStore()
    mock_client = make_mock_client(store)

    fake_vector = make_fake_vector(seed=42)

    entries = [
        MemoryEntry(
            entry_id=str(uuid4()),
            contact_id=contact_id,
            type="fact",
            text=f"memory entry {i}",
            timestamp=datetime.now(UTC),
        )
        for i in range(num_entries)
    ]

    with mock_lib.patch.object(qdrant_module, "_get_client", return_value=mock_client):
        with mock_lib.patch.object(embedding_module, "embed", return_value=fake_vector):
            for entry in entries:
                await qdrant_module.store_memory(entry)

            results = await qdrant_module.search_memory(contact_id, query, top_k=top_k)

    assert len(results) <= top_k


# ---------------------------------------------------------------------------
# Unit tests for ensure_collection_exists and delete_contact_memories
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ensure_collection_creates_if_missing():
    """ensure_collection_exists creates the collection when it doesn't exist."""
    mock_client = mock_lib.AsyncMock()
    mock_client.get_collections.return_value = mock_lib.MagicMock(collections=[])
    mock_client.create_collection = mock_lib.AsyncMock()

    with mock_lib.patch.object(qdrant_module, "_get_client", return_value=mock_client):
        await qdrant_module.ensure_collection_exists()

    mock_client.create_collection.assert_called_once()
    call_kwargs = mock_client.create_collection.call_args
    assert call_kwargs.kwargs["collection_name"] == "memories"
    vectors_config = call_kwargs.kwargs["vectors_config"]
    assert vectors_config.size == 768


@pytest.mark.asyncio
async def test_ensure_collection_skips_if_exists():
    """ensure_collection_exists does not create the collection if it already exists."""
    existing = mock_lib.MagicMock()
    existing.name = "memories"
    mock_client = mock_lib.AsyncMock()
    mock_client.get_collections.return_value = mock_lib.MagicMock(collections=[existing])
    mock_client.create_collection = mock_lib.AsyncMock()

    with mock_lib.patch.object(qdrant_module, "_get_client", return_value=mock_client):
        await qdrant_module.ensure_collection_exists()

    mock_client.create_collection.assert_not_called()


@pytest.mark.asyncio
async def test_delete_contact_memories_calls_delete_with_filter():
    """delete_contact_memories calls Qdrant delete with the correct contact_id filter."""
    store = FakeQdrantStore()
    mock_client = make_mock_client(store)

    contact_id = str(uuid4())
    fake_vector = make_fake_vector(seed=0)

    # Store two entries for the contact and one for another contact
    entry1 = make_entry(contact_id=contact_id, text="entry one")
    entry2 = make_entry(contact_id=contact_id, text="entry two")
    other_entry = make_entry(contact_id=str(uuid4()), text="other contact entry")

    with mock_lib.patch.object(qdrant_module, "_get_client", return_value=mock_client):
        with mock_lib.patch.object(embedding_module, "embed", return_value=fake_vector):
            await qdrant_module.store_memory(entry1)
            await qdrant_module.store_memory(entry2)
            await qdrant_module.store_memory(other_entry)

            await qdrant_module.delete_contact_memories(contact_id)

    # The two entries for contact_id should be gone
    assert entry1.entry_id not in store.points
    assert entry2.entry_id not in store.points
    # The other contact's entry should remain
    assert other_entry.entry_id in store.points
