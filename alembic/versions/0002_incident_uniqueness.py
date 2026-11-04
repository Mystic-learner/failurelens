"""one non-resolved incident per signature

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX uq_incidents_one_active ON incidents (signature_id) "
        "WHERE status <> 'resolved'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_incidents_one_active")