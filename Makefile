.PHONY: check lint test contract frontend-check format-check

check: lint test contract frontend-check

lint:
	.venv/bin/ruff check src tests scripts

test:
	PYTHONPATH=src .venv/bin/python -m pytest

contract:
	PYTHONPATH=src .venv/bin/python scripts/dev/verify_frontend_api_contract.py

frontend-check:
	for f in web/modules/*.js; do node --check "$$f"; done

format-check:
	.venv/bin/ruff format --check src tests scripts
