# Git Commit Guidelines

This project uses small, meaningful commits that mirror real engineering work:
design, scaffolding, implementation, tests, fixes, and documentation should be
committed separately when they represent separate decisions.

## Commit Message Format

```text
type(scope): concise summary
```

Examples:

```text
docs: refine platform design document
chore: add local development tooling
feat(api): scaffold FastAPI gateway routes
feat(worker): add Redis Streams consumer loop
test(worker): cover retry policy decisions
fix(runtime): reject invalid handler output
```

## Types

- `feat`: Adds user-facing or system behavior.
- `fix`: Fixes a bug or incorrect behavior.
- `test`: Adds or improves tests.
- `docs`: Changes documentation only.
- `chore`: Changes tooling, configuration, dependencies, or repository setup.
- `refactor`: Restructures code without changing behavior.
- `perf`: Improves performance or benchmarked behavior.
- `ci`: Changes GitHub Actions or other CI configuration.

## Scopes

Use a short scope when it makes the commit easier to understand.

Common scopes:

- `api`
- `auth`
- `registry`
- `queue`
- `worker`
- `runtime`
- `dashboard`
- `metrics`
- `benchmarks`
- `infra`
- `ci`

## Rules

- Keep each commit focused on one coherent change.
- Prefer several honest commits over one large polished dump.
- Include tests in the same commit when they directly validate the change.
- Use `docs:` commits for project notes, reports, and design updates.
- Use `chore:` for repository setup, dependency files, and developer tooling.
- Do not commit generated caches, local virtualenvs, secrets, benchmark noise, or
  temporary debug output.
- Commit messages should describe what changed, not how hard the work was.

## Recommended Development Flow

1. Implement a small slice of behavior.
2. Run the most relevant tests or smoke checks.
3. Review the staged diff with `git diff --staged`.
4. Commit with a message that names the engineering outcome.
5. Continue with the next slice.

For larger milestones, commit in this order when applicable:

1. Data model or migration.
2. Service logic.
3. API or worker integration.
4. Tests.
5. Documentation or benchmark notes.
