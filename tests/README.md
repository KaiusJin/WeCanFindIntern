# Test organization

Keep module-owned tests in a `tests/` directory next to the implementation.
Keep tests spanning multiple modules in the repository-level `tests/` directory.

| Location | Ownership |
|---|---|
| `src/wecanfindintern/<module>/tests/` | Python module behavior, including route and repository tests using fakes |
| `src/wecanfindintern/agent/memory/tests/` | Agent memory behavior |
| `src/wecanfindintern/agent/recommend/tests/` | Recommendation behavior |
| `scripts/collection/tests/` | Collection campaign scripts |
| `web/tests/` | Web UI and ES modules |
| `desktop/tests/` | Electron runtime and packaging |
| `tests/integration/` | Cross-module flows and live PostgreSQL integration |

Use `test_*.py` for Python tests and `*.test.mjs` for Node tests. Keep fixtures
under the owning test directory's `fixtures/` subdirectory. Place shared pytest
fixtures in the nearest common ancestor's `conftest.py`; use a repository-root
`conftest.py` if fixtures must span `src/`, `scripts/`, and `tests/`.

Pytest recursively discovers tests under `src/wecanfindintern`, `scripts`, and
`tests`. Importlib mode supports repeated test filenames across modules, and
the configured Python paths make application and script imports work without
editing `sys.path`. Module test directories do not need `__init__.py` files.
The root integration suite uses explicit packages for its shared helper imports.
Module tests and fixtures are excluded from the installed Python wheel.

Run from the repository root:

```sh
# All Python tests, or only tests without a live database dependency
.venv/bin/python -m pytest
.venv/bin/python -m pytest -m "not db"

# One module, including tests in its nested modules
.venv/bin/python -m pytest src/wecanfindintern/agent

# Run web and desktop JavaScript test files (Node does not accept directory entries)
node --test web/tests/*.test.mjs desktop/tests/*.test.mjs
```

Live database tests use the `db` marker and `TEST_DATABASE_URL`. Use only a
disposable test database: the fixtures initialize the schema and truncate tables.
Without an explicit URL these tests skip when the default database is unreachable.
An explicitly configured but unreachable database fails the suite. CI provides
a PostgreSQL 16/pgvector service and runs `make test-db` separately.

`make check` runs lint, Python unit tests, the frontend/API contract check, and
JavaScript checks. `make test-db` requires `TEST_DATABASE_URL` and runs the database
suite. Override `PYTHON`, `RUFF`, or `NODE` to select other installed tools.

CI is split into `static-checks.yml` (independent lint and API contract jobs),
`backend-tests.yml`, `database-tests.yml`, and `frontend-tests.yml`. Only database
tests start PostgreSQL. The workflows run on PRs and pushes to `main` to avoid
duplicate branch-push/PR runs, and support manual runs on other branches.
The Node runtime is explicitly set to 24. All workflows use the Makefile entry
points; Python discovery roots remain configured in `pyproject.toml`.
