.PHONY: bootstrap dev test compose-up compose-down

bootstrap:
	./scripts/bootstrap.sh

dev:
	./scripts/dev.sh

test:
	.venv/bin/pytest

compose-up:
	./scripts/ensure-compose-env.sh
	docker compose up --build

compose-down:
	docker compose down
