from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.services.embedding_worker import embed_pending
from app.services.embeddings import EmbeddingError, FakeEmbeddingProvider
from app.services.retrieval import SearchFilters, query_tokens, rrf_merge, search
from tests.storagetools import commit, make_chunks, setup_stream

PROVIDER = FakeEmbeddingProvider()

DB_DOWN = "2024-01-01 10:00:00 ERROR connect ECONNRESET to postgres db-1 pool exhausted\n"
DISK = "2024-01-01 10:00:10 ERROR write failed ENOSPC no space left on device /var/data\n"
HTTP503 = "2024-01-01 10:00:20 ERROR upstream payments returned HTTP/1.1 503 service unavailable\n"
OK_LINE = "2024-01-01 10:00:30 INFO health check ok\n"


async def load(engine: AsyncEngine, raw: str, service: str = "svc", n: int = 1) -> None:
    sid = await setup_stream(engine, service, f"src-{service}")
    await commit(
        engine, sid, f"k{n}-{service}", n, make_chunks(raw, f"src-{service}"), service=service
    )
    await embed_pending(engine, PROVIDER)


class Broken(FakeEmbeddingProvider):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise EmbeddingError("down", transient=True)


# ---------------------------------------------------------------- pure functions
def test_rrf_scores_are_the_sum_of_reciprocal_ranks() -> None:
    scores = rrf_merge({"a": [10, 20], "b": [20, 30]})
    assert scores[10] == pytest.approx(1 / 61)
    assert scores[20] == pytest.approx(1 / 62 + 1 / 61)
    assert scores[30] == pytest.approx(1 / 62)
    assert max(scores, key=lambda k: scores[k]) == 20  # present in both lists


def test_rrf_empty_and_single_list() -> None:
    assert rrf_merge({}) == {}
    assert rrf_merge({"a": []}) == {}
    assert list(rrf_merge({"a": [5, 6, 7]})) == [5, 6, 7]


def test_query_tokens_are_deduplicated_lowercase_and_capped() -> None:
    assert query_tokens("ECONNRESET econnreset a db-1") == ["econnreset", "db"]
    assert len(query_tokens(" ".join(f"word{i}" for i in range(200)))) == 40
    assert query_tokens("\u30c7\u30fc\u30bf\u30d9\u30fc\u30b9 \u9519\u8bef") != []


# ---------------------------------------------------------------- real database
@pytest.mark.integration
async def test_cold_start_empty_database_is_inconclusive(engine: AsyncEngine) -> None:
    result = await search(engine, PROVIDER, "connection reset ECONNRESET")
    assert result.inconclusive is True
    assert result.hits == []
    assert result.degraded is False


@pytest.mark.integration
async def test_empty_and_whitespace_queries_are_inconclusive(engine: AsyncEngine) -> None:
    await load(engine, DB_DOWN)
    for q in ("", "   \n\t "):
        assert (await search(engine, PROVIDER, q)).inconclusive is True


@pytest.mark.integration
async def test_exact_error_code_finds_the_right_chunk(engine: AsyncEngine) -> None:
    await load(engine, DB_DOWN + DISK + HTTP503 + OK_LINE)
    result = await search(engine, PROVIDER, "Error: read ECONNRESET")
    assert not result.inconclusive
    assert "ECONNRESET" in result.hits[0].text
    assert "error_code" in result.hits[0].sources


@pytest.mark.integration
async def test_http_status_code_is_matched(engine: AsyncEngine) -> None:
    await load(engine, DB_DOWN + HTTP503)
    result = await search(engine, PROVIDER, "got HTTP/1.1 503 from upstream")
    assert "HTTP/1.1 503" in result.hits[0].text


@pytest.mark.integration
async def test_same_failure_with_different_ids_gets_the_fingerprint_boost(
    engine: AsyncEngine,
) -> None:
    await load(engine, "2024-01-01 10:00:00 ERROR payment 123 failed for user 77\n")
    result = await search(
        engine, PROVIDER, "2025-05-05 09:09:09 ERROR payment 98765 failed for user 4"
    )
    assert result.hits[0].fingerprint_match is True


@pytest.mark.integration
async def test_unrelated_query_is_inconclusive_not_a_bluff(engine: AsyncEngine) -> None:
    await load(engine, DB_DOWN + DISK)
    result = await search(engine, PROVIDER, "zzzqqq xylophone quantum banana", min_similarity=0.9)
    assert result.inconclusive is True
    assert result.hits == []


@pytest.mark.integration
async def test_one_word_query(engine: AsyncEngine) -> None:
    await load(engine, DB_DOWN + DISK)
    result = await search(engine, PROVIDER, "ENOSPC")
    assert "ENOSPC" in result.hits[0].text


@pytest.mark.integration
async def test_query_in_another_language_does_not_crash(engine: AsyncEngine) -> None:
    await load(engine, DB_DOWN)
    result = await search(
        engine,
        PROVIDER,
        "\u30c7\u30fc\u30bf\u30d9\u30fc\u30b9\u63a5\u7d9a\u30a8\u30e9\u30fc \u9519\u8bef",
    )
    assert isinstance(result.inconclusive, bool)
    mixed = await search(engine, PROVIDER, "\u9519\u8bef ECONNRESET")
    assert not mixed.inconclusive  # the exact code still matches


@pytest.mark.integration
async def test_same_error_in_two_services_and_the_service_filter(engine: AsyncEngine) -> None:
    await load(engine, DB_DOWN, service="alpha", n=1)
    await load(engine, DB_DOWN, service="beta", n=2)
    both = await search(engine, PROVIDER, "ECONNRESET")
    assert {h.service for h in both.hits} == {"alpha", "beta"}
    only = await search(engine, PROVIDER, "ECONNRESET", filters=SearchFilters(service="beta"))
    assert {h.service for h in only.hits} == {"beta"}


@pytest.mark.integration
async def test_time_range_filter(engine: AsyncEngine) -> None:
    raw = (
        "2024-01-01 10:00:00 ERROR connect ECONNRESET early\n"
        "2024-06-01 10:00:00 ERROR connect ECONNRESET late\n"
    )
    await load(engine, raw)
    since = datetime(2024, 3, 1, tzinfo=UTC)
    result = await search(engine, PROVIDER, "ECONNRESET", filters=SearchFilters(since=since))
    assert [("late" in h.text) for h in result.hits] == [True]


@pytest.mark.integration
async def test_two_unrelated_errors_in_one_chunk_are_both_findable(engine: AsyncEngine) -> None:
    await load(engine, "just text ECONNRESET and then ENOSPC on the same record\n")
    for code in ("ECONNRESET", "ENOSPC"):
        result = await search(engine, PROVIDER, code)
        assert len(result.hits) == 1


@pytest.mark.integration
async def test_embedding_failure_falls_back_to_keywords_and_marks_degraded(
    engine: AsyncEngine,
) -> None:
    await load(engine, DB_DOWN + DISK)
    result = await search(engine, Broken(), "ECONNRESET")
    assert result.degraded is True
    assert not result.inconclusive
    assert "ECONNRESET" in result.hits[0].text
    assert "vector" not in result.hits[0].sources


@pytest.mark.integration
async def test_no_provider_is_degraded_keyword_only(engine: AsyncEngine) -> None:
    await load(engine, DB_DOWN)
    result = await search(engine, None, "ECONNRESET")
    assert result.degraded is True
    assert result.hits


@pytest.mark.integration
async def test_hostile_query_text_is_safe(engine: AsyncEngine) -> None:
    await load(engine, DB_DOWN)
    nasty = "'; DROP TABLE chunks; -- \\ ' | & ! ( ) : * <-> \x00 \u0000 ECONNRESET"
    result = await search(engine, PROVIDER, nasty)
    assert not result.inconclusive
    again = await search(engine, PROVIDER, "ECONNRESET")
    assert again.hits


@pytest.mark.integration
async def test_secrets_in_the_query_are_redacted_before_use(engine: AsyncEngine) -> None:
    await load(engine, DB_DOWN)
    result = await search(engine, PROVIDER, "login failed password=hunter2 ECONNRESET")
    assert "hunter2" not in result.query
    assert "<SECRET>" in result.query


@pytest.mark.integration
async def test_top_k_limits_results(engine: AsyncEngine) -> None:
    raw = "".join(f"2024-01-01 10:00:{i:02d} ERROR connect ECONNRESET host{i}\n" for i in range(10))
    await load(engine, raw)
    assert len((await search(engine, PROVIDER, "ECONNRESET", top_k=3)).hits) == 3


# ---------------------------------------------------------------- HTTP endpoint
@pytest.mark.integration
async def test_search_endpoint(client: httpx.AsyncClient, engine: AsyncEngine) -> None:
    await load(engine, DB_DOWN)
    client.app.state.embedding_provider = PROVIDER  # type: ignore[attr-defined]
    resp = await client.post("/search", json={"query": "ECONNRESET"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["inconclusive"] is False
    assert "ECONNRESET" in body["hits"][0]["text"]


@pytest.mark.integration
async def test_search_endpoint_validation_and_cold_start(client: httpx.AsyncClient) -> None:
    assert (await client.post("/search", json={"query": ""})).status_code == 422
    assert (await client.post("/search", json={"query": "x", "top_k": 0})).status_code == 422
    resp = await client.post("/search", json={"query": "anything at all"})
    assert resp.status_code == 200
    assert resp.json()["inconclusive"] is True
