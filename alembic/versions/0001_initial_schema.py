"""initial schema: streams, batches, documents, chunks, embeddings, signatures, incidents, events

Revision ID: 0001
Revises:
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# One statement per op.execute: asyncpg cannot run several commands in one prepared statement.
UPGRADE_STATEMENTS = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    """
    CREATE TABLE streams (
        id BIGSERIAL PRIMARY KEY,
        service TEXT NOT NULL,
        source_id TEXT NOT NULL,
        committed_seq BIGINT NOT NULL DEFAULT 0,
        parser_state JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_streams_service_source UNIQUE (service, source_id)
    )
    """,
    # Batch bookkeeping only. Raw lines are never stored here.
    """
    CREATE TABLE ingest_batches (
        id BIGSERIAL PRIMARY KEY,
        stream_id BIGINT NOT NULL REFERENCES streams(id) ON DELETE CASCADE,
        idempotency_key TEXT NOT NULL,
        seq BIGINT NOT NULL,
        line_count INTEGER NOT NULL CHECK (line_count >= 0),
        status TEXT NOT NULL DEFAULT 'committed' CHECK (status = 'committed'),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_ingest_batches_stream_key UNIQUE (stream_id, idempotency_key)
    )
    """,
    "CREATE INDEX ix_ingest_batches_stream_seq ON ingest_batches (stream_id, seq)",
    """
    CREATE TABLE documents (
        id BIGSERIAL PRIMARY KEY,
        source_id TEXT NOT NULL,
        filename TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # tsv uses the 'simple' config on purpose: log tokens must not be stemmed or dropped.
    """
    CREATE TABLE chunks (
        id BIGSERIAL PRIMARY KEY,
        stream_id BIGINT REFERENCES streams(id) ON DELETE SET NULL,
        document_id BIGINT REFERENCES documents(id) ON DELETE CASCADE,
        text TEXT NOT NULL,
        chunk_hash TEXT NOT NULL,
        fingerprint TEXT,
        level TEXT,
        ts TIMESTAMPTZ,
        service TEXT,
        error_codes TEXT[] NOT NULL DEFAULT '{}',
        tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_chunks_chunk_hash UNIQUE (chunk_hash)
    )
    """,
    "CREATE INDEX ix_chunks_error_codes ON chunks USING gin (error_codes)",
    "CREATE INDEX ix_chunks_tsv ON chunks USING gin (tsv)",
    "CREATE INDEX ix_chunks_text_trgm ON chunks USING gin (text gin_trgm_ops)",
    "CREATE INDEX ix_chunks_fingerprint ON chunks (fingerprint)",
    "CREATE INDEX ix_chunks_service_ts ON chunks (service, ts)",
    "CREATE INDEX ix_chunks_document_id ON chunks (document_id)",
    # One table and one HNSW index per embedding provider. Providers are never mixed.
    """
    CREATE TABLE chunk_embeddings_openai (
        chunk_id BIGINT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
        embedding vector(1536) NOT NULL
    )
    """,
    """
    CREATE INDEX ix_chunk_embeddings_openai_hnsw
        ON chunk_embeddings_openai USING hnsw (embedding vector_cosine_ops)
    """,
    """
    CREATE TABLE chunk_embeddings_local (
        chunk_id BIGINT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
        embedding vector(384) NOT NULL
    )
    """,
    """
    CREATE INDEX ix_chunk_embeddings_local_hnsw
        ON chunk_embeddings_local USING hnsw (embedding vector_cosine_ops)
    """,
    """
    CREATE TABLE signatures (
        id BIGSERIAL PRIMARY KEY,
        service TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        exemplar_chunk_id BIGINT REFERENCES chunks(id) ON DELETE SET NULL,
        level TEXT,
        count BIGINT NOT NULL DEFAULT 0,
        first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_signatures_service_fingerprint UNIQUE (service, fingerprint)
    )
    """,
    """
    CREATE TABLE signature_buckets (
        signature_id BIGINT NOT NULL REFERENCES signatures(id) ON DELETE CASCADE,
        minute TIMESTAMPTZ NOT NULL,
        count BIGINT NOT NULL DEFAULT 0,
        PRIMARY KEY (signature_id, minute)
    )
    """,
    "CREATE INDEX ix_signature_buckets_minute ON signature_buckets (minute)",
    """
    CREATE TABLE incidents (
        id BIGSERIAL PRIMARY KEY,
        signature_id BIGINT NOT NULL REFERENCES signatures(id) ON DELETE CASCADE,
        trigger TEXT NOT NULL CHECK (trigger IN ('new_signature', 'rate_spike')),
        status TEXT NOT NULL DEFAULT 'open'
            CHECK (status IN ('open', 'diagnosing', 'diagnosed', 'resolved')),
        cooldown_until TIMESTAMPTZ,
        diagnosis JSONB,
        degraded BOOLEAN NOT NULL DEFAULT false,
        diagnosis_deferred BOOLEAN NOT NULL DEFAULT false,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        resolved_at TIMESTAMPTZ
    )
    """,
    "CREATE INDEX ix_incidents_signature_id ON incidents (signature_id)",
    "CREATE INDEX ix_incidents_status ON incidents (status)",
    # The event id is the WebSocket event_id; replay is WHERE id > :last_event_id.
    """
    CREATE TABLE events (
        id BIGSERIAL PRIMARY KEY,
        type TEXT NOT NULL,
        payload JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # Hourly counter for the LLM cost guard; survives restarts.
    """
    CREATE TABLE llm_usage (
        hour_bucket TIMESTAMPTZ PRIMARY KEY,
        count INTEGER NOT NULL DEFAULT 0
    )
    """,
]

DOWNGRADE_TABLES = [
    "llm_usage",
    "events",
    "incidents",
    "signature_buckets",
    "signatures",
    "chunk_embeddings_local",
    "chunk_embeddings_openai",
    "chunks",
    "documents",
    "ingest_batches",
    "streams",
]


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    # The vector and pg_trgm extensions are left installed: other database objects
    # or a later upgrade may rely on them, and dropping them needs superuser rights.
    for table in DOWNGRADE_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
