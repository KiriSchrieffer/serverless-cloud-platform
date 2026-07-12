"""add invocation outbox and idempotency uniqueness

Revision ID: 20260710_0002
Revises: 20260611_0001
Create Date: 2026-07-10 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260710_0002"
down_revision: str | None = "20260611_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_invocations_idempotency_key", table_name="invocations")
    op.create_index(
        "uq_invocations_owner_idempotency_key",
        "invocations",
        ["owner_id", "idempotency_key"],
        unique=True,
    )
    op.drop_index(
        "ix_invocation_attempts_invocation_attempt",
        table_name="invocation_attempts",
    )
    op.create_unique_constraint(
        "uq_invocation_attempts_invocation_attempt",
        "invocation_attempts",
        ["invocation_id", "attempt_number"],
    )
    op.create_table(
        "invocation_dispatches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("invocation_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("publish_attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("published_message_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["invocation_id"],
            ["invocations.id"],
            name=op.f("fk_invocation_dispatches_invocation_id_invocations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invocation_dispatches")),
        sa.UniqueConstraint(
            "invocation_id",
            "attempt_number",
            name="uq_invocation_dispatches_invocation_attempt",
        ),
    )
    op.create_index(
        op.f("ix_invocation_dispatches_invocation_id"),
        "invocation_dispatches",
        ["invocation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_invocation_dispatches_available_at"),
        "invocation_dispatches",
        ["available_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_invocation_dispatches_published_at"),
        "invocation_dispatches",
        ["published_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_invocation_dispatches_published_at"),
        table_name="invocation_dispatches",
    )
    op.drop_index(
        op.f("ix_invocation_dispatches_available_at"),
        table_name="invocation_dispatches",
    )
    op.drop_index(
        op.f("ix_invocation_dispatches_invocation_id"),
        table_name="invocation_dispatches",
    )
    op.drop_table("invocation_dispatches")
    op.drop_constraint(
        "uq_invocation_attempts_invocation_attempt",
        "invocation_attempts",
        type_="unique",
    )
    op.create_index(
        "ix_invocation_attempts_invocation_attempt",
        "invocation_attempts",
        ["invocation_id", "attempt_number"],
        unique=False,
    )
    op.drop_index("uq_invocations_owner_idempotency_key", table_name="invocations")
    op.create_index(
        "ix_invocations_idempotency_key",
        "invocations",
        ["owner_id", "idempotency_key"],
        unique=False,
    )
