"""create core schema

Revision ID: 20260611_0001
Revises:
Create Date: 2026-06-11 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260611_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    invocation_status = sa.Enum(
        "QUEUED",
        "RUNNING",
        "RETRYING",
        "SUCCEEDED",
        "FAILED",
        "TIMEOUT",
        "CANCELED",
        name="invocation_status",
    )
    invocation_attempt_status = sa.Enum(
        "RUNNING",
        "SUCCEEDED",
        "FAILED",
        "TIMEOUT",
        name="invocation_attempt_status",
    )
    worker_status = sa.Enum("IDLE", "RUNNING", "DRAINING", "OFFLINE", name="worker_status")

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    op.create_table(
        "workers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=False),
        sa.Column("status", worker_status, nullable=False),
        sa.Column("last_heartbeat", sa.DateTime(), nullable=False),
        sa.Column("active_invocations", sa.Integer(), nullable=False),
        sa.Column("max_concurrency", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workers")),
    )
    op.create_index(op.f("ix_workers_last_heartbeat"), "workers", ["last_heartbeat"], unique=False)
    op.create_index(op.f("ix_workers_status"), "workers", ["status"], unique=False)

    op.create_table(
        "functions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name=op.f("fk_functions_owner_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_functions")),
        sa.UniqueConstraint("owner_id", "name", name="uq_functions_owner_name"),
    )
    op.create_index(op.f("ix_functions_owner_id"), "functions", ["owner_id"], unique=False)

    op.create_table(
        "function_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("function_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("runtime", sa.String(length=64), nullable=False),
        sa.Column("handler", sa.String(length=255), nullable=False),
        sa.Column("package_uri", sa.String(length=1024), nullable=False),
        sa.Column("package_hash", sa.String(length=128), nullable=False),
        sa.Column("memory_limit_mb", sa.Integer(), nullable=False),
        sa.Column("cpu_limit", sa.Numeric(precision=4, scale=2), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["function_id"],
            ["functions.id"],
            name=op.f("fk_function_versions_function_id_functions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_function_versions")),
        sa.UniqueConstraint("function_id", "version_number", name="uq_function_versions_function_version"),
    )
    op.create_index(
        op.f("ix_function_versions_function_id"),
        "function_versions",
        ["function_id"],
        unique=False,
    )

    op.create_table(
        "invocations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("function_version_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("status", invocation_status, nullable=False),
        sa.Column("payload_ref", sa.String(length=1024), nullable=True),
        sa.Column("payload_inline", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result_ref", sa.String(length=1024), nullable=True),
        sa.Column("result_inline", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_type", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("queued_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("deadline_at", sa.DateTime(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["function_version_id"], ["function_versions.id"], name=op.f("fk_invocations_function_version_id_function_versions"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name=op.f("fk_invocations_owner_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invocations")),
    )
    op.create_index(
        "ix_invocations_idempotency_key",
        "invocations",
        ["owner_id", "idempotency_key"],
        unique=False,
    )
    op.create_index(op.f("ix_invocations_function_version_id"), "invocations", ["function_version_id"], unique=False)
    op.create_index(op.f("ix_invocations_owner_id"), "invocations", ["owner_id"], unique=False)
    op.create_index("ix_invocations_owner_status_created", "invocations", ["owner_id", "status", "created_at"], unique=False)
    op.create_index(op.f("ix_invocations_status"), "invocations", ["status"], unique=False)

    op.create_table(
        "invocation_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("invocation_id", sa.Uuid(), nullable=False),
        sa.Column("worker_id", sa.Uuid(), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", invocation_attempt_status, nullable=False),
        sa.Column("container_id", sa.String(length=255), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("error_type", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("logs_ref", sa.String(length=1024), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["invocation_id"], ["invocations.id"], name=op.f("fk_invocation_attempts_invocation_id_invocations"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["worker_id"], ["workers.id"], name=op.f("fk_invocation_attempts_worker_id_workers"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invocation_attempts")),
    )
    op.create_index(
        "ix_invocation_attempts_invocation_attempt",
        "invocation_attempts",
        ["invocation_id", "attempt_number"],
        unique=False,
    )
    op.create_index(op.f("ix_invocation_attempts_invocation_id"), "invocation_attempts", ["invocation_id"], unique=False)
    op.create_index(op.f("ix_invocation_attempts_worker_id"), "invocation_attempts", ["worker_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_invocation_attempts_worker_id"), table_name="invocation_attempts")
    op.drop_index(op.f("ix_invocation_attempts_invocation_id"), table_name="invocation_attempts")
    op.drop_index("ix_invocation_attempts_invocation_attempt", table_name="invocation_attempts")
    op.drop_table("invocation_attempts")

    op.drop_index(op.f("ix_invocations_status"), table_name="invocations")
    op.drop_index("ix_invocations_owner_status_created", table_name="invocations")
    op.drop_index(op.f("ix_invocations_owner_id"), table_name="invocations")
    op.drop_index(op.f("ix_invocations_function_version_id"), table_name="invocations")
    op.drop_index("ix_invocations_idempotency_key", table_name="invocations")
    op.drop_table("invocations")

    op.drop_index(op.f("ix_function_versions_function_id"), table_name="function_versions")
    op.drop_table("function_versions")

    op.drop_index(op.f("ix_functions_owner_id"), table_name="functions")
    op.drop_table("functions")

    op.drop_index(op.f("ix_workers_status"), table_name="workers")
    op.drop_index(op.f("ix_workers_last_heartbeat"), table_name="workers")
    op.drop_table("workers")

    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")

    op.execute("DROP TYPE IF EXISTS invocation_attempt_status")
    op.execute("DROP TYPE IF EXISTS invocation_status")
    op.execute("DROP TYPE IF EXISTS worker_status")
