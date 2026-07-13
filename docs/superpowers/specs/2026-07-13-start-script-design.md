# `start.sh` dev launcher + documented one-shot try-it path

## Problem

Running the app currently takes five separate `make` invocations across
multiple terminals (`make up`, `make migrate`, `make dev-backend` in one
terminal, `make dev-frontend` in another), plus a manual `cp .env.example
.env` and hand-editing in API keys before any of it works. This is friction
for anyone cloning the repo to contribute or just try it out.

## Goals

- One command to go from a fresh clone to a running app in dev mode.
- One command (already existing, just undocumented) to try the app with
  zero local Python/Node toolchain — Docker only.
- No new dependencies (no `overmind`, `foreman`, `just`, etc.) — bash only,
  consistent with the project's "no over-engineering" posture.
- Don't replace or duplicate the Makefile; `start.sh` orchestrates the same
  underlying commands `make` targets already run, for people who want
  everything in one shot instead of granular control.

## Non-goals

- Replacing the Makefile or CI's use of individual `make` targets.
- Building new containerization — the `app` compose profile already exists
  and already does the "no toolchain needed" job; this just documents it.
- Automatically stopping Postgres/Jaeger on exit (see Teardown below).

## Design

### `start.sh` (repo root, dev mode)

1. **Prereq check** — verify `docker`, `uv`, `npm` are on `PATH`. On any
   missing tool, print which one and exit non-zero (no auto-install).
2. **`.env` bootstrap** — only if `.env` doesn't already exist:
   - Copy `.env.example` → `.env`.
   - Interactively prompt (`read -s`, not echoed) for `OPENAI_API_KEY` and
     `DEEPGRAM_API_KEY`.
   - If either is left blank, print: *"Left blank — the app will start, but
     every conversation turn will fail with an auth error until you add
     `<VAR>` to `.env` and restart."* (Accurate per current code: both
     `AsyncOpenAI` construction and the Deepgram provider seams are
     keyless-safe at startup — failures surface lazily as `ErrorEvent`s at
     call time, not at boot.)
   - Write entered values into `.env` in place (`sed`), never overwrite an
     `.env` that already exists.
3. **Infra** — `docker compose up -d` (Postgres + Jaeger; idempotent if
   already running), then poll the Postgres healthcheck (reuse
   `docker compose ps --format` or `pg_isready` via `docker compose exec`)
   up to a timeout instead of a blind `sleep`.
4. **Migrate** — `cd backend && uv run alembic upgrade head`.
5. **Run** — start `uv run uvicorn ... --reload --port 8000` and
   `npm run dev` (frontend, `cd frontend`) as background jobs in the same
   terminal, each piped through `sed` to prefix every line with
   `[backend]`/`[frontend]` in distinct colors.
6. **Teardown** — `trap 'kill $(jobs -p) 2>/dev/null' EXIT` on script exit
   (Ctrl+C or normal exit) kills only the backend/frontend jobs. Postgres
   and Jaeger are deliberately left running — see rationale below — and the
   script's final line reminds the user: `make down` to stop them.
7. **Ready banner** — once both processes are up, print the two URLs
   (`http://localhost:5173`, `http://localhost:8000`).

#### Why infra isn't torn down on exit

Matches the existing `make up` / `make down` split — they're already
separate commands, so not stopping infra on script exit is consistent with
current behavior, not a deviation. Postgres data persists via the `pgdata`
volume; Jaeger is in-memory/stateless, so nothing is lost either way by
leaving it running or restarting it later. The only cost is background
RAM/CPU and the fixed host ports (5432, 4318, 16686) staying occupied,
which the final banner flags explicitly.

### README: one-shot try-it path (no script needed)

Add a short "Try it without a dev setup" section documenting the existing
containerized path:

```bash
cp .env.example .env   # fill in OPENAI_API_KEY and DEEPGRAM_API_KEY
docker compose --profile app up --build
```

This builds frontend+backend into one image (per the existing multi-stage
`Dockerfile`) and serves the whole app on `:8000` — Docker is the only
requirement. No migration step is currently wired into the `app` service's
startup; note in the README that a fresh DB needs `docker compose run --rm
app alembic upgrade head` (or equivalent) before first use — this is a
one-line documentation fix, not new code.

### Quickstart section rewrite

Replace the current multi-step `make`-based Quickstart in `README.md` with:

```bash
./start.sh
```

...as the primary path, keeping the existing granular `make` commands
below it under a "Manual / step-by-step" subsection for people who want
control over individual steps (or are debugging one stage).

## Testing

- Manual: run `start.sh` on a clean checkout (`.env` absent) and verify the
  prompt, infra start, migration, and both dev servers come up and are
  reachable.
- Manual: run `start.sh` again with `.env` already present and confirm the
  prompt is skipped.
- Manual: Ctrl+C during `start.sh` and confirm both dev processes die but
  `docker compose ps` still shows Postgres/Jaeger running.
- No automated test — this is a shell entrypoint script, consistent with
  `scripts/mic_sim.py` and `scripts/ws_client.py` having no test coverage
  either.
