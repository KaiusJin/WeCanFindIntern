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

# Recursively run web and desktop JavaScript tests
node --test web/tests desktop/tests
```

Live database tests use the `db` marker and `TEST_DATABASE_URL`. Use only a
disposable test database: the fixtures initialize the schema and truncate tables.
These tests currently skip when the database is unreachable. CI provides a
PostgreSQL 16/pgvector service and runs the `db` suite separately.

`make check` and `.github/workflows/ci.yml` use these discovery roots; adding
tests inside them does not require listing each module in CI.
