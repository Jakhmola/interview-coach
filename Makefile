.PHONY: help up down logs build rebuild ps sh-api sh-ui sh-db db-ui test fmt lint lock sync

help:
	@echo "make up        - docker compose up -d (build if needed)"
	@echo "make down      - docker compose down"
	@echo "make logs      - tail logs from all services"
	@echo "make build     - docker compose build"
	@echo "make rebuild   - docker compose build --no-cache"
	@echo "make ps        - docker compose ps"
	@echo "make sh-api    - shell into api container"
	@echo "make sh-ui     - shell into React ui container"
	@echo "make sh-db     - psql into db container"
	@echo "make db-ui     - print Adminer URL + creds for the local DB"
	@echo "make test      - run pytest on host via uv"
	@echo "make fmt       - ruff format"
	@echo "make lint      - ruff check"
	@echo "make lock      - uv lock"
	@echo "make sync      - uv sync"

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f

build:
	docker compose build

rebuild:
	docker compose build --no-cache

ps:
	docker compose ps

sh-api:
	docker compose exec api bash

sh-ui:
	docker compose exec ui bash

sh-db:
	docker compose exec db psql -U interview_coach -d interview_coach

db-ui:
	@echo "Adminer:  http://localhost:8090"
	@echo "  System:   PostgreSQL"
	@echo "  Server:   db"
	@echo "  User:     interview_coach"
	@echo "  Password: $${POSTGRES_PASSWORD:-interview_coach}"
	@echo "  Database: interview_coach"

test:
	uv run pytest -q

fmt:
	uv run ruff format .

lint:
	uv run ruff check .

lock:
	uv lock

sync:
	uv sync
