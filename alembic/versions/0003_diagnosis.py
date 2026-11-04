"""diagnosis columns + llm_usage budget table"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_usage (
            hour timestamptz PRIMARY KEY,
            tokens bigint NOT NULL DEFAULT 0,
            calls integer NOT NULL DEFAULT 0
        )
        """
    )
    op.execute("ALTER TABLE incidents ADD COLUMN IF NOT EXISTS diagnosis_deferred boolean NOT NULL DEFAULT false")
    op.execute("ALTER TABLE incidents ADD COLUMN IF NOT EXISTS diagnosis jsonb")
    op.execute("ALTER TABLE incidents ADD COLUMN IF NOT EXISTS diagnosed_at timestamptz")
    op.execute("ALTER TABLE incidents ADD COLUMN IF NOT EXISTS last_diagnosis_attempt_at timestamptz")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_incidents_diagnosis_deferred "
        "ON incidents (id) WHERE diagnosis_deferred"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_incidents_diagnosis_deferred")
    op.execute("ALTER TABLE incidents DROP COLUMN IF EXISTS last_diagnosis_attempt_at")
    op.execute("ALTER TABLE incidents DROP COLUMN IF EXISTS diagnosed_at")
    op.execute("ALTER TABLE incidents DROP COLUMN IF EXISTS diagnosis")
    op.execute("ALTER TABLE incidents DROP COLUMN IF EXISTS diagnosis_deferred")