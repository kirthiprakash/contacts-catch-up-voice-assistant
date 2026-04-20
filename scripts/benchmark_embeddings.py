#!/usr/bin/env python3
"""
benchmark_embeddings.py — Compare embedding models for memory retrieval quality.

Models tested:
  1. gemini-embedding-001   (Gemini API, 3072 dims)
  2. intfloat/multilingual-e5-small  (Qdrant cloud inference, 384 dims, 512-token ctx)
  3. sentence-transformers/all-minilm-l6-v2  (Qdrant cloud inference, 384 dims, 256-token ctx)
  4. qdrant/bm25  (Qdrant cloud inference, sparse keyword)

Usage:
    source .env
    python scripts/benchmark_embeddings.py

Requires EMBEDDING_API_KEY (Gemini), QDRANT_ENDPOINT, QDRANT_API_KEY in env.
Uses an isolated 'benchmark_test_*' collection — never touches production 'memories'.
Cleans up all test collections on exit.
"""

import asyncio
import math
import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Load .env
# ---------------------------------------------------------------------------

def _load_env() -> None:
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            if "=" in line:
                key, _, val = line.partition("=")
                val = val.strip().strip('"').strip("'")
                os.environ.setdefault(key.strip(), val)

_load_env()

GEMINI_KEY     = os.environ.get("EMBEDDING_API_KEY", "")
QDRANT_URL     = os.environ.get("QDRANT_ENDPOINT", "")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")

if not GEMINI_KEY:
    sys.exit("ERROR: EMBEDDING_API_KEY not set")
if not QDRANT_URL or not QDRANT_API_KEY:
    sys.exit("ERROR: QDRANT_ENDPOINT or QDRANT_API_KEY not set")

# ---------------------------------------------------------------------------
# Corpus — mix of short and long entries
# Long entries have the key fact near the END to test truncation impact
# ---------------------------------------------------------------------------

# (id, label, text, token_estimate)
CORPUS = [
    # Short entries (~10-30 tokens)
    ("m01", "promotion",       "Just got promoted to VP of Engineering at Stripe.", 10),
    ("m02", "relocation",      "Moving to Bangalore next month for a new opportunity.", 10),
    ("m03", "fundraising",     "Working on a Series B fundraise, targeting $10M.", 10),
    ("m04", "family_news",     "Daughter just started college at IIT Delhi, very proud parent.", 12),
    ("m05", "follow_up",       "Mentioned they want to reconnect next quarter to discuss partnership.", 13),
    ("m06", "health",          "Recovering from knee surgery, should be back in the office in 3 weeks.", 14),
    ("m07", "product_launch",  "Launching a new SaaS product for logistics companies in Q3.", 13),
    ("m08", "travel",          "Planning a trip to Japan in December for a tech conference.", 12),

    # Long entries — key fact placed at the END (truncation test)
    ("m09", "promotion_long",
     "Had a great catch-up call today. We discussed the general state of the tech industry, "
     "the challenges of remote work, and how teams are adapting post-pandemic. He shared some "
     "thoughts on AI tooling and how his company is experimenting with various LLM integrations. "
     "We talked about the upcoming conference season and which events are worth attending this year. "
     "He mentioned his family is doing well and they recently renovated their home. Towards the end "
     "of the call, he shared some exciting personal news: he was just promoted to Chief Technology "
     "Officer at Razorpay, effective next month.", 130),

    ("m10", "fundraising_long",
     "Long conversation covering a lot of ground — market conditions, hiring challenges in the "
     "current environment, the difficulty of retaining senior engineers, and how the startup "
     "ecosystem has changed since 2021. We discussed their product roadmap and some of the "
     "technical debt they are working through. She talked about her co-founder dynamics and "
     "how they divide responsibilities. We also spoke about their go-to-market strategy for "
     "enterprise clients. Near the end she mentioned they just closed a $15M Series A round "
     "led by Sequoia, which is a significant milestone for the company.", 120),

    ("m11", "relocation_long",
     "Caught up after a long gap. He is doing well overall — the new apartment is working out, "
     "the team at work has been supportive, and he has settled into a good routine. We talked "
     "about the local food scene, some restaurants he has discovered, and his weekend hiking "
     "habit that he picked up recently. He mentioned his parents visited last month and they "
     "had a great time. He is considering adopting a dog. The main update from his side is that "
     "he has accepted a new role in Singapore and will be relocating there permanently in two months.", 120),

    ("m12", "follow_up_long",
     "Productive call discussing a potential collaboration. We went through the details of the "
     "project scope, timelines, budget expectations, and who the key stakeholders would be on "
     "each side. There was a lot of alignment on the vision but some open questions on the "
     "commercial structure. We talked through a few different models — revenue share, retainer, "
     "project-based — and agreed that more thinking is needed. The conversation was warm and "
     "there is clearly a lot of goodwill on both sides. At the very end of the call she said "
     "she would like to schedule a proper follow-up meeting next week to finalise the terms.", 120),

    # Extra short entries for coverage
    ("m13", "deal",    "Closed a major enterprise deal with HDFC bank worth ₹2 crore.", 12),
    ("m14", "skill",   "Recently completed a machine learning certification from Coursera.", 10),
    ("m15", "event",   "Hosting a startup networking event in Mumbai next Friday.", 10),
]

# ---------------------------------------------------------------------------
# Queries — each with labelled correct answer(s)
# ---------------------------------------------------------------------------

QUERIES = [
    ("q1", "career change or promotion",          ["m01", "m09"]),
    ("q2", "relocation or moving to a new city",  ["m02", "m11"]),
    ("q3", "fundraising or investment round",     ["m03", "m10"]),
    ("q4", "family news or personal milestone",   ["m04"]),
    ("q5", "scheduled follow-up or reconnect",    ["m05", "m12"]),
]

# Token limits per model (approximate; used only for display)
MODEL_TOKEN_LIMITS = {
    "gemini-embedding-001": 2048,
    "intfloat/multilingual-e5-small": 512,
    "sentence-transformers/all-minilm-l6-v2": 256,
    "qdrant/bm25": 9999,  # sparse, no meaningful limit
}

# ---------------------------------------------------------------------------
# Gemini embeddings (dense, via native HTTP API)
# ---------------------------------------------------------------------------

import httpx

GEMINI_DELAY_S = 0.5  # 15 req/min limit → 4s needed; 0.5s is safe for 20 sequential requests

async def embed_gemini(text: str) -> list[float]:
    model_id = "models/gemini-embedding-001"
    url = f"https://generativelanguage.googleapis.com/v1beta/{model_id}:embedContent"
    await asyncio.sleep(GEMINI_DELAY_S)
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            headers={"X-goog-api-key": GEMINI_KEY, "Content-Type": "application/json"},
            json={"model": model_id, "content": {"parts": [{"text": text}]}},
            timeout=20.0,
        )
        resp.raise_for_status()
        return resp.json()["embedding"]["values"]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


async def run_gemini_benchmark() -> dict:
    """Embed corpus and queries with Gemini, rank results, return metrics."""
    print("\n── Gemini gemini-embedding-001 ──")
    corpus_vectors: dict[str, list[float]] = {}
    latencies = []

    for entry_id, label, text, _ in CORPUS:
        t0 = time.monotonic()
        vec = await embed_gemini(text)
        latencies.append((time.monotonic() - t0) * 1000)
        corpus_vectors[entry_id] = vec
        print(f"  embedded {entry_id} ({label[:20]:<20}) {len(vec)} dims  {latencies[-1]:.0f}ms")

    results = {}
    for qid, query_text, correct_ids in QUERIES:
        t0 = time.monotonic()
        qvec = await embed_gemini(query_text)
        q_latency = (time.monotonic() - t0) * 1000
        scored = sorted(
            [(eid, cosine_similarity(qvec, cvec)) for eid, cvec in corpus_vectors.items()],
            key=lambda x: x[1], reverse=True
        )
        results[qid] = {
            "top3": scored[:3],
            "correct_rank": next((i + 1 for i, (eid, _) in enumerate(scored) if eid in correct_ids), None),
            "q_latency_ms": q_latency,
        }

    return {"model": "gemini-embedding-001", "corpus_latencies": latencies, "results": results}


# ---------------------------------------------------------------------------
# Qdrant cloud inference (dense models)
# ---------------------------------------------------------------------------

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Document, PointStruct, VectorParams, Distance,
    SparseVectorParams, SparseIndexParams,
    NamedSparseVector,
)

def _qdrant_client() -> AsyncQdrantClient:
    return AsyncQdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, cloud_inference=True)


async def run_qdrant_dense_benchmark(model_name: str) -> dict:
    """Upsert corpus and query via Qdrant cloud inference (dense model)."""
    collection = f"benchmark_test_{model_name.replace('/', '_').replace('-', '_')}"
    client = _qdrant_client()
    print(f"\n── Qdrant inference: {model_name} ──")

    # Create collection with correct dims for the model (384 for e5-small and all-minilm)
    DENSE_MODEL_DIMS = {
        "intfloat/multilingual-e5-small": 384,
        "sentence-transformers/all-minilm-l6-v2": 384,
    }
    dims = DENSE_MODEL_DIMS.get(model_name, 384)
    try:
        await client.delete_collection(collection)
    except Exception:
        pass
    await client.create_collection(
        collection_name=collection,
        vectors_config=VectorParams(size=dims, distance=Distance.COSINE),
    )

    # Upsert corpus
    latencies = []
    points = []
    for entry_id, label, text, _ in CORPUS:
        t0 = time.monotonic()
        points.append(PointStruct(
            id=int(entry_id[1:]),
            payload={"entry_id": entry_id, "label": label},
            vector=Document(text=text, model=model_name),
        ))
        latencies.append((time.monotonic() - t0) * 1000)

    t0 = time.monotonic()
    await client.upsert(collection_name=collection, points=points)
    upsert_ms = (time.monotonic() - t0) * 1000
    print(f"  upserted {len(points)} points  total={upsert_ms:.0f}ms")

    # Query
    results = {}
    id_to_entry = {int(eid[1:]): eid for eid, _, _, _ in CORPUS}

    for qid, query_text, correct_ids in QUERIES:
        t0 = time.monotonic()
        hits = await client.query_points(
            collection_name=collection,
            query=Document(text=query_text, model=model_name),
            limit=3,
        )
        q_latency = (time.monotonic() - t0) * 1000

        top3 = [(id_to_entry.get(int(h.id), str(h.id)), h.score) for h in hits.points]
        top3_ids = [eid for eid, _ in top3]
        correct_rank = next((i + 1 for i, eid in enumerate(top3_ids) if eid in correct_ids), None)
        if correct_rank is None:
            # Search beyond top-3 for rank
            all_hits = await client.query_points(
                collection_name=collection,
                query=Document(text=query_text, model=model_name),
                limit=len(CORPUS),
            )
            all_ids = [id_to_entry.get(int(h.id), str(h.id)) for h in all_hits.points]
            correct_rank = next((i + 1 for i, eid in enumerate(all_ids) if eid in correct_ids), None)

        results[qid] = {"top3": top3, "correct_rank": correct_rank, "q_latency_ms": q_latency}
        print(f"  {qid}: top={top3[0][0] if top3 else '?'}  rank={correct_rank}  {q_latency:.0f}ms")

    await client.delete_collection(collection)
    return {"model": model_name, "corpus_latencies": latencies, "results": results}


async def run_qdrant_bm25_benchmark() -> dict:
    """Upsert corpus and query via Qdrant BM25 (sparse keyword model)."""
    model_name = "qdrant/bm25"
    collection = "benchmark_test_qdrant_bm25"
    client = _qdrant_client()
    print(f"\n── Qdrant inference: {model_name} ──")

    try:
        await client.delete_collection(collection)
    except Exception:
        pass

    await client.create_collection(
        collection_name=collection,
        vectors_config={},
        sparse_vectors_config={"text": SparseVectorParams(index=SparseIndexParams())},
    )

    latencies = []
    points = []
    for entry_id, label, text, _ in CORPUS:
        t0 = time.monotonic()
        points.append(PointStruct(
            id=int(entry_id[1:]),
            payload={"entry_id": entry_id, "label": label},
            vector={"text": Document(text=text, model=model_name)},
        ))
        latencies.append((time.monotonic() - t0) * 1000)

    t0 = time.monotonic()
    await client.upsert(collection_name=collection, points=points)
    print(f"  upserted {len(points)} points  total={(time.monotonic()-t0)*1000:.0f}ms")

    results = {}
    id_to_entry = {int(eid[1:]): eid for eid, _, _, _ in CORPUS}

    for qid, query_text, correct_ids in QUERIES:
        t0 = time.monotonic()
        hits = await client.query_points(
            collection_name=collection,
            query=Document(text=query_text, model=model_name),
            using="text",
            limit=3,
        )
        q_latency = (time.monotonic() - t0) * 1000
        top3 = [(id_to_entry.get(int(h.id), str(h.id)), h.score) for h in hits.points]
        top3_ids = [eid for eid, _ in top3]
        correct_rank = next((i + 1 for i, eid in enumerate(top3_ids) if eid in correct_ids), None)
        results[qid] = {"top3": top3, "correct_rank": correct_rank, "q_latency_ms": q_latency}
        print(f"  {qid}: top={top3[0][0] if top3 else '?'}  rank={correct_rank}  {q_latency:.0f}ms")

    await client.delete_collection(collection)
    return {"model": model_name, "corpus_latencies": latencies, "results": results}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _truncated(text: str, token_limit: int) -> bool:
    return len(text) // 4 > token_limit  # ~4 chars/token estimate


def print_report(all_results: list[dict]) -> None:
    models = [r["model"] for r in all_results]
    id_to_label = {eid: label for eid, label, _, _ in CORPUS}

    print("\n" + "═" * 90)
    print("BENCHMARK RESULTS")
    print("═" * 90)

    # Per-query table
    header = f"{'Query':<40}" + "".join(f"  {m.split('/')[-1][:18]:<20}" for m in models)
    print(f"\n{header}")
    print("-" * len(header))

    for qid, query_text, correct_ids in QUERIES:
        row = f"{query_text[:38]:<40}"
        for r in all_results:
            res = r["results"][qid]
            top1_id = res["top3"][0][0] if res["top3"] else "?"
            top1_score = res["top3"][0][1] if res["top3"] else 0
            rank = res["correct_rank"]
            hit = "✓" if rank == 1 else f"#{rank}" if rank else "✗"
            row += f"  {hit} {top1_id}({id_to_label.get(top1_id,'?')[:10]}) {top1_score:.3f}  "
        print(row)

    # Truncation impact
    print("\n── Truncation risk (long entries) ──")
    for entry_id, label, text, _ in CORPUS:
        if len(text) > 200:
            flags = []
            for model in models:
                limit = MODEL_TOKEN_LIMITS.get(model, 9999)
                if _truncated(text, limit):
                    flags.append(f"{model.split('/')[-1]}(⚠ truncated)")
            if flags:
                print(f"  {entry_id} ({label}): {', '.join(flags)}")

    # Summary
    print("\n── Summary ──")
    print(f"{'Model':<45}  {'Top-1 hits':>10}  {'Avg corpus embed ms':>20}  {'Avg query ms':>12}")
    print("-" * 95)
    for r in all_results:
        top1_hits = sum(1 for q in QUERIES if r["results"][q[0]]["correct_rank"] == 1)
        avg_embed = sum(r["corpus_latencies"]) / len(r["corpus_latencies"]) if r["corpus_latencies"] else 0
        avg_query = sum(r["results"][q[0]]["q_latency_ms"] for q in QUERIES) / len(QUERIES)
        print(f"  {r['model']:<43}  {top1_hits:>4}/{len(QUERIES)}       {avg_embed:>10.0f} ms          {avg_query:>8.0f} ms")

    print("\n" + "═" * 90)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    print("Embedding benchmark — 4 models, 15 corpus entries, 5 queries")
    print(f"Qdrant: {QDRANT_URL}")

    all_results = []

    # 1. Gemini
    try:
        all_results.append(await run_gemini_benchmark())
    except Exception as e:
        print(f"  Gemini failed: {e}")

    # 2. Qdrant dense models
    for model in ["intfloat/multilingual-e5-small", "sentence-transformers/all-minilm-l6-v2"]:
        try:
            all_results.append(await run_qdrant_dense_benchmark(model))
        except Exception as e:
            print(f"  {model} failed: {e}")

    # 3. Qdrant BM25
    try:
        all_results.append(await run_qdrant_bm25_benchmark())
    except Exception as e:
        print(f"  BM25 failed: {e}")

    if all_results:
        print_report(all_results)
    else:
        print("All models failed — check credentials and qdrant-client version.")


if __name__ == "__main__":
    asyncio.run(main())
