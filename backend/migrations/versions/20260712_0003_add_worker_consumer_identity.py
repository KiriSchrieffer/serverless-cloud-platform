"""add worker consumer identity

Revision ID: 20260712_0003
Revises: 20260710_0002
Create Date: 2026-07-12 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260712_0003"
down_revision: str | None = "20260710_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("workers", sa.Column("consumer_name", sa.String(length=255), nullable=True))
    op.create_index(
        op.f("ix_workers_consumer_name"),
        "workers",
        ["consumer_name"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_workers_consumer_name"), table_name="workers")
    op.drop_column("workers", "consumer_name")
