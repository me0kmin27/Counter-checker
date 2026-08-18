.PHONY: bootstrap setup dev test start stop restart status logs compose-up compose-down

bootstrap:
	python3 scripts/bootstrap.py

setup:
	python3 scripts/manage.py setup

dev:
	.venv/bin/python scripts/dev.py

test:
	.venv/bin/pytest

start:
	python3 scripts/manage.py start

stop:
	python3 scripts/manage.py stop

restart:
	python3 scripts/manage.py restart

status:
	python3 scripts/manage.py status

logs:
	python3 scripts/manage.py logs --follow

compose-up:
	python3 scripts/manage.py start

compose-down:
	python3 scripts/manage.py stop
