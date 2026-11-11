"""one vector per window: (chunk_id, window_idx) is the key of both embedding tables"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TABLES = ("chunk_embeddings_openai", "chunk_embeddings_local")


def upgrade() -> None:
    # Local vectors were embedded truncated and the worker only checks "has any vector",
    # so they would never be redone. Drop them; the worker regenerates them.
    op.execute("DELETE FROM chunk_embeddings_local")
    for t in TABLES:
        op.execute(
            f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS window_idx integer NOT NULL DEFAULT 0"
        )
        op.execute(f"ALTER TABLE {t} DROP CONSTRAINT {t}_pkey")
        op.execute(f"ALTER TABLE {t} ADD PRIMARY KEY (chunk_id, window_idx)")


def downgrade() -> None:
    for t in TABLES:
        op.execute(f"ALTER TABLE {t} DROP CONSTRAINT {t}_pkey")
        op.execute(f"DELETE FROM {t} WHERE window_idx > 0")
        op.execute(f"ALTER TABLE {t} ADD PRIMARY KEY (chunk_id)")
        op.execute(f"ALTER TABLE {t} DROP COLUMN window_idx")
