"""diagnosis bookkeeping + token budget column"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE llm_usage ADD COLUMN IF NOT EXISTS tokens bigint NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE incidents ADD COLUMN IF NOT EXISTS diagnosed_at timestamptz")
    op.execute(
        "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS last_diagnosis_attempt_at timestamptz"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_incidents_deferred_pending "
        "ON incidents (id) WHERE diagnosis_deferred"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_incidents_deferred_pending")
    op.execute("ALTER TABLE incidents DROP COLUMN IF EXISTS last_diagnosis_attempt_at")
    op.execute("ALTER TABLE incidents DROP COLUMN IF EXISTS diagnosed_at")
    op.execute("ALTER TABLE llm_usage DROP COLUMN IF EXISTS tokens")