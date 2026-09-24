.PHONY: install test lint format typecheck check serve provision selfcheck compose-up compose-down

install:
	uv sync --extra dev

test:
	uv run pytest

lint:
	uv run ruff check src tests

format:
	uv run ruff format src tests
	uv run ruff check --fix src tests

typecheck:
	uv run mypy

check: lint typecheck test

serve:
	uv run jevops serve

provision:
	uv run jevops provision

selfcheck:
	uv run jevops selfcheck

compose-up:
	docker compose up -d elasticsearch logstash grafana

compose-down:
	docker compose down
