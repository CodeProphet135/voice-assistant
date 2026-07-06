# --- Stage 1: build the frontend ---
FROM node:22-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Stage 2: python runtime ---
FROM python:3.12-slim AS runtime
WORKDIR /app

RUN pip install --no-cache-dir uv

COPY backend/pyproject.toml backend/uv.lock* ./backend/
WORKDIR /app/backend
RUN uv sync --no-dev --frozen || uv sync --no-dev

COPY backend/ ./
COPY --from=frontend-build /app/frontend/dist ./static

ENV PORT=8000
EXPOSE 8000

CMD ["uv", "run", "uvicorn", "voice_assistant.main:app", "--host", "0.0.0.0", "--port", "8000"]
