from sqlalchemy import ForeignKeyConstraint, Index, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from backend.app.models import Base


def test_core_tables_are_registered() -> None:
    assert set(Base.metadata.tables) == {
        "users",
        "functions",
        "function_versions",
        "invocations",
        "invocation_attempts",
        "invocation_dispatches",
        "workers",
    }


def test_registry_uniqueness_constraints_match_design() -> None:
    functions = Base.metadata.tables["functions"]
    function_versions = Base.metadata.tables["function_versions"]

    function_unique_constraints = {
        constraint.name
        for constraint in functions.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    version_unique_constraints = {
        constraint.name
        for constraint in function_versions.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert "uq_functions_owner_name" in function_unique_constraints
    assert "uq_function_versions_function_version" in version_unique_constraints

    invocations = Base.metadata.tables["invocations"]
    idempotency_indexes = {
        index.name: index
        for index in invocations.indexes
        if isinstance(index, Index)
    }
    assert idempotency_indexes["uq_invocations_owner_idempotency_key"].unique is True

    attempts = Base.metadata.tables["invocation_attempts"]
    attempt_unique_constraints = {
        constraint.name
        for constraint in attempts.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uq_invocation_attempts_invocation_attempt" in attempt_unique_constraints


def test_invocation_tables_keep_expected_foreign_keys() -> None:
    invocations = Base.metadata.tables["invocations"]
    attempts = Base.metadata.tables["invocation_attempts"]
    dispatches = Base.metadata.tables["invocation_dispatches"]

    invocation_targets = {
        next(iter(constraint.elements)).target_fullname
        for constraint in invocations.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    attempt_targets = {
        next(iter(constraint.elements)).target_fullname
        for constraint in attempts.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    dispatch_targets = {
        next(iter(constraint.elements)).target_fullname
        for constraint in dispatches.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }

    assert invocation_targets == {"users.id", "function_versions.id"}
    assert attempt_targets == {"invocations.id", "workers.id"}
    assert dispatch_targets == {"invocations.id"}


def test_models_compile_for_postgresql() -> None:
    dialect = postgresql.dialect()

    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=dialect))

        assert f"CREATE TABLE {table.name}" in ddl
