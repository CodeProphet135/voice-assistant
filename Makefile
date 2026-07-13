.PHONY: dev-backend dev-frontend up down migrate test lint fmt

dev-backend:
	cd backend && uv run uvicorn voice_assistant.main:app --reload --port 8000

dev-frontend:
	cd frontend && npm run dev

up:
	docker compose up -d

down:
	docker compose down

migrate:
	cd backend && uv run alembic upgrade head

test:
	cd backend && uv run pytest
	cd frontend && npm run test && npm run typecheck

lint:
	cd backend && uv run ruff check .
	cd frontend && npm run typecheck

fmt:
	cd backend && uv run ruff format .
