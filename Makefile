.PHONY: bootstrap dev test compose-up compose-down

bootstrap:
	python3 scripts/bootstrap.py

dev:
	.venv/bin/python scripts/dev.py

test:
	.venv/bin/pytest

compose-up:
	python3 scripts/ensure_compose_env.py
	docker compose up --build

compose-down:
	docker compose down
