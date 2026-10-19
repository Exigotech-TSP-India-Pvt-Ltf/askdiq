## Summary

<!-- What changes and why, in 2-5 bullets. -->

## Plan task ids

<!-- e.g. B0.2, B0.4 (docs/archive/planchanges4.md). PR-xx. -->

## Type

- [ ] feat
- [ ] fix
- [ ] chore / ci
- [ ] refactor
- [ ] docs
- [ ] test

## Screenshots (light and dark)

<!-- UI work only: attach light AND dark theme screenshots. Otherwise "n/a". -->

| Light | Dark |
|---|---|
| | |

## Migration

<!-- Alembic revision id, down_revision, data impact. Otherwise "n/a". -->

- [ ] `alembic heads` prints exactly one head
- [ ] `alembic upgrade head` -> `alembic downgrade -1` -> `alembic upgrade head` tested

## Security (OWASP rows touched)

<!-- List OWASP Top 10 rows affected (e.g. A01 Broken Access Control) and how they are handled. -->

## Performance (budgets measured)

<!-- Budgets from newplan section 13.4 and measured numbers, or "n/a". -->

## Assumptions

<!-- Anything decided without explicit owner confirmation. -->

## Test evidence

<!-- Commands run and results (pass / fail / skip counts). State any DB-skipped tests. -->

## Rollback

<!-- How to revert safely (revert commit, downgrade migration, feature flag). -->

## Checklist

- [ ] **[Dev]** Code, migration (with working downgrade), config, `.env.example` entry and docs are in; no TODOs or dead code
- [ ] **[Lint]** `ruff check .`, `ruff format --check .`, `pylint app`, `npm run lint`, `npm run typecheck` pass with zero new findings
- [ ] **[Test]** Backend `pytest` and frontend `npm test` pass with zero regressions; new routes have 401/403/422/injection tests
- [ ] CI green: `backend-lint`, `backend-tests`, `frontend-checks`, `fullstack-smoke`
