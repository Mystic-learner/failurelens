import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.dbtools import APP_TABLES, vector_literal

pytestmark = pytest.mark.integration

EXPECTED_INDEXES = {
    "ix_chunks_error_codes": "gin",
    "ix_chunks_tsv": "gin",
    "ix_chunks_text_trgm": "gin_trgm_ops",
    "ix_chunk_embeddings_openai_hnsw": "hnsw",
    "ix_chunk_embeddings_local_hnsw": "hnsw",
}

INSERT_CHUNK = (
    "INSERT INTO chunks (text, chunk_hash, error_codes) "
    "VALUES (:text, :hash, CAST(:codes AS text[])) RETURNING id"
)


async def new_chunk(engine: AsyncEngine, body: str, h: str, codes: list[str] | None = None) -> int:
    async with engine.begin() as conn:
        result = await conn.execute(
            text(INSERT_CHUNK), {"text": body, "hash": h, "codes": codes or []}
        )
        return int(result.scalar_one())


async def test_extensions_installed(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        rows = await conn.execute(text("SELECT extname FROM pg_extension"))
    names = {r[0] for r in rows}
    assert {"vector", "pg_trgm"} <= names


async def test_all_tables_exist(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
    names = {r[0] for r in rows}
    assert set(APP_TABLES) <= names


async def test_indexes_use_the_required_methods(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        rows = await conn.execute(text("SELECT indexname, indexdef FROM pg_indexes"))
    defs = {r[0]: r[1] for r in rows}
    for name, marker in EXPECTED_INDEXES.items():
        assert name in defs, f"missing index {name}"
        assert marker in defs[name], f"{name} should mention {marker}: {defs[name]}"


async def test_each_provider_has_its_own_vector_dimension(engine: AsyncEngine) -> None:
    chunk_id = await new_chunk(engine, "e", "h-dim")
    other_chunk_id = await new_chunk(engine, "e2", "h-dim-2")
    insert = "INSERT INTO {table} (chunk_id, embedding) VALUES (:id, CAST(CAST(:v AS text) AS vector))"
    async with engine.begin() as conn:
        await conn.execute(
            text(insert.format(table="chunk_embeddings_openai")),
            {"id": chunk_id, "v": vector_literal(1536)},
        )
        await conn.execute(
            text(insert.format(table="chunk_embeddings_local")),
            {"id": chunk_id, "v": vector_literal(384)},
        )
    # Mixing dimensions is rejected by the column type itself.
    with pytest.raises(DBAPIError):
        async with engine.begin() as conn:
            await conn.execute(
                text(insert.format(table="chunk_embeddings_openai")),
                {"id": other_chunk_id, "v": vector_literal(384)},
            )


async def test_tsv_uses_the_simple_config_without_stemming(engine: AsyncEngine) -> None:
    await new_chunk(engine, "Connection RESET while Running migrations", "h-tsv")
    async with engine.connect() as conn:
        exact = await conn.execute(
            text("SELECT count(*) FROM chunks WHERE tsv @@ to_tsquery('simple', 'running')")
        )
        stemmed = await conn.execute(
            text("SELECT count(*) FROM chunks WHERE tsv @@ to_tsquery('simple', 'run')")
        )
        stopword = await conn.execute(
            text("SELECT count(*) FROM chunks WHERE tsv @@ to_tsquery('simple', 'while')")
        )
    assert exact.scalar_one() == 1
    assert stemmed.scalar_one() == 0  # the english config would have matched this
    assert stopword.scalar_one() == 1  # the english config would have dropped this stop word


async def test_error_code_array_lookup(engine: AsyncEngine) -> None:
    await new_chunk(engine, "a", "h-a", ["ECONNRESET", "HTTP_502"])
    await new_chunk(engine, "b", "h-b", ["ENOENT"])
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT text FROM chunks WHERE error_codes @> CAST(:c AS text[])"),
            {"c": ["ECONNRESET"]},
        )
    assert [r[0] for r in rows] == ["a"]


async def test_trigram_similarity(engine: AsyncEngine) -> None:
    await new_chunk(engine, "connection reset by peer", "h-t1")
    await new_chunk(engine, "disk quota exceeded", "h-t2")
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT text FROM chunks WHERE text % :q"), {"q": "connection resett by peer"}
        )
    assert [r[0] for r in rows] == ["connection reset by peer"]


async def test_deleting_a_document_cascades_to_chunks_and_embeddings(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        doc_id = (
            await conn.execute(
                text("INSERT INTO documents (source_id, filename) VALUES ('s', 'f.log') RETURNING id")
            )
        ).scalar_one()
        chunk_id = (
            await conn.execute(
                text("INSERT INTO chunks (document_id, text, chunk_hash) VALUES (:d, 't', 'h-doc') RETURNING id"),
                {"d": doc_id},
            )
        ).scalar_one()
        await conn.execute(
            text("INSERT INTO chunk_embeddings_local (chunk_id, embedding) VALUES (:c, CAST(CAST(:v AS text) AS vector))"),
            {"c": chunk_id, "v": vector_literal(384)},
        )
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM documents WHERE id = :d"), {"d": doc_id})
        chunks = (await conn.execute(text("SELECT count(*) FROM chunks"))).scalar_one()
        embeddings = (
            await conn.execute(text("SELECT count(*) FROM chunk_embeddings_local"))
        ).scalar_one()
    assert (chunks, embeddings) == (0, 0)


async def test_chunk_hash_is_unique(engine: AsyncEngine) -> None:
    await new_chunk(engine, "x", "dup")
    with pytest.raises(IntegrityError):
        await new_chunk(engine, "y", "dup")


@pytest.mark.parametrize(
    ("column", "value"),
    [("trigger", "cosmic_rays"), ("status", "exploded")],
)
async def test_incident_check_constraints(engine: AsyncEngine, column: str, value: str) -> None:
    async with engine.begin() as conn:
        sig_id = (
            await conn.execute(
                text("INSERT INTO signatures (service, fingerprint) VALUES ('svc', 'fp') RETURNING id")
            )
        ).scalar_one()
    values = {"trigger": "new_signature", "status": "open", column: value}
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO incidents (signature_id, trigger, status) VALUES (:s, :t, :st)"),
                {"s": sig_id, "t": values["trigger"], "st": values["status"]},
            )


async def test_incident_defaults(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        sig_id = (
            await conn.execute(
                text("INSERT INTO signatures (service, fingerprint) VALUES ('svc', 'fp') RETURNING id")
            )
        ).scalar_one()
        row = (
            await conn.execute(
                text(
                    "INSERT INTO incidents (signature_id, trigger) VALUES (:s, 'rate_spike') "
                    "RETURNING status, degraded, diagnosis_deferred"
                ),
                {"s": sig_id},
            )
        ).one()
    assert tuple(row) == ("open", False, False)


async def test_ingest_batches_idempotency_key_is_unique_per_stream(engine: AsyncEngine) -> None:
    insert = (
        "INSERT INTO ingest_batches (stream_id, idempotency_key, seq, line_count) "
        "VALUES (:s, :k, 1, 10)"
    )
    async with engine.begin() as conn:
        s1, s2 = [
            (
                await conn.execute(
                    text("INSERT INTO streams (service, source_id) VALUES ('svc', :src) RETURNING id"),
                    {"src": src},
                )
            ).scalar_one()
            for src in ("a", "b")
        ]
        await conn.execute(text(insert), {"s": s1, "k": "key-1"})
        again = await conn.execute(text(insert + " ON CONFLICT DO NOTHING"), {"s": s1, "k": "key-1"})
        other_stream = await conn.execute(text(insert), {"s": s2, "k": "key-1"})
    assert again.rowcount == 0  # duplicate batch is ignored
    assert other_stream.rowcount == 1  # same key on another stream is a different batch
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(text(insert), {"s": s1, "k": "key-1"})


async def test_ingest_batches_status_can_only_be_committed(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        s = (
            await conn.execute(text("INSERT INTO streams (service, source_id) VALUES ('s', 'x') RETURNING id"))
        ).scalar_one()
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO ingest_batches (stream_id, idempotency_key, seq, line_count, status) "
                    "VALUES (:s, 'k', 1, 1, 'pending')"
                ),
                {"s": s},
            )


async def test_event_ids_increase_and_support_replay(engine: AsyncEngine) -> None:
    ids = []
    for n in range(4):
        async with engine.begin() as conn:
            result = await conn.execute(
                text("INSERT INTO events (type, payload) VALUES ('t', CAST(:p AS jsonb)) RETURNING id"),
                {"p": f'{{"n": {n}}}'},
            )
            ids.append(result.scalar_one())
    assert ids == sorted(ids) and len(set(ids)) == 4
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT id FROM events WHERE id > :last ORDER BY id"), {"last": ids[1]}
        )
    assert [r[0] for r in rows] == ids[2:]


async def test_llm_usage_upsert_increments(engine: AsyncEngine) -> None:
    upsert = (
        "INSERT INTO llm_usage (hour_bucket, count) VALUES (date_trunc('hour', now()), 1) "
        "ON CONFLICT (hour_bucket) DO UPDATE SET count = llm_usage.count + 1"
    )
    for _ in range(3):
        async with engine.begin() as conn:
            await conn.execute(text(upsert))
    async with engine.connect() as conn:
        count = (await conn.execute(text("SELECT count FROM llm_usage"))).scalar_one()
    assert count == 3
