PYTHON ?= .venv/bin/python
RUFF ?= .venv/bin/ruff
NODE ?= node
RENDERER_WEB := desktop/renderer/web

.PHONY: check lint test test-db contract frontend-check format-check

check: lint test contract frontend-check

lint:
	$(RUFF) check src cloud tests scripts desktop/build

test:
	PYTHONPATH=src $(PYTHON) -m pytest -m "not db"

test-db:
	@test -n "$$TEST_DATABASE_URL" || (echo "Set TEST_DATABASE_URL to a disposable PostgreSQL database" >&2; exit 1)
	PYTHONPATH=src $(PYTHON) -m pytest tests/integration -m db

contract:
	PYTHONPATH=src $(PYTHON) scripts/dev/verify_frontend_api_contract.py

frontend-check:
	@set -e; for f in $(RENDERER_WEB)/modules/*.js $(RENDERER_WEB)/ui/*.js; do $(NODE) --input-type=module --check < "$$f"; done
	@set -e; for f in desktop/src/*.js desktop/forge.config.js; do $(NODE) --check "$$f"; done
	$(NODE) --test $(RENDERER_WEB)/tests/*.test.mjs desktop/tests/*.test.mjs

format-check:
	$(RUFF) format --check src cloud tests scripts desktop/build
