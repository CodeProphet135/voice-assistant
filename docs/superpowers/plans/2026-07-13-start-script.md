# start.sh Dev Launcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the multi-terminal, multi-`make`-command dev setup with a single `./start.sh` that bootstraps `.env`, brings up infra, migrates, and runs backend+frontend concurrently with clean teardown; document the existing containerized one-shot path in the README.

**Architecture:** One bash script (`start.sh`, repo root) run top-to-bottom: prereq check → `.env` bootstrap with interactive key prompts → `docker compose up -d` + Postgres healthcheck poll → `alembic upgrade head` → backend/frontend as background jobs with prefixed log output → `trap`-based teardown on exit that kills only the dev processes (infra stays up, matching the existing `make up`/`make down` split). No new dependencies — bash, `docker compose`, `uv`, `npm`, all already required by the project.

**Tech Stack:** bash (`set -euo pipefail`), `docker compose`, `uv`, `npm`, `sed`.

## Global Constraints

- No new dependencies (no `overmind`, `foreman`, `just`) — bash only.
- Don't replace or modify the `Makefile` — `start.sh` orchestrates the same underlying commands for one-shot use; granular `make` targets stay as-is.
- Never overwrite an existing `.env` — only bootstrap when it's absent.
- Empty API keys must not block startup — auth failures surface lazily as `ErrorEvent`s at call time (confirmed in `backend/src/voice_assistant/config.py` and `session.py`), so the script must say so, not treat blank keys as fatal.
- Teardown on exit kills only the backend/frontend dev processes, never `docker compose down` — Postgres data persists via the `pgdata` volume, Jaeger is stateless, and the port-5432 conflict noted in project memory makes it worth reminding the user infra is still running rather than silently stopping it.

---

### Task 1: Prereq check + `.env` bootstrap with interactive key prompts

**Files:**
- Create: `start.sh`

**Interfaces:**
- Produces: shell functions `info()`, `warn()`, `ok()`, `fail()` (colored log helpers) and color variables `RED`, `GREEN`, `YELLOW`, `CYAN`, `MAGENTA`, `NC` — reused by Tasks 2 and 3.

- [ ] **Step 1: Create `start.sh` with color helpers, prereq check, and `.env` bootstrap**

```bash
#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m'

info()  { echo -e "${CYAN}==>${NC} $1"; }
warn()  { echo -e "${YELLOW}==>${NC} $1"; }
ok()    { echo -e "${GREEN}==>${NC} $1"; }
fail()  { echo -e "${RED}==>${NC} $1" >&2; exit 1; }

# --- Prereq check ---
for cmd in docker uv npm; do
  command -v "$cmd" >/dev/null 2>&1 || fail "Missing required tool: $cmd. Install it and re-run ./start.sh."
done

# --- .env bootstrap ---
if [ ! -f .env ]; then
  cp .env.example .env
  info "Created .env from .env.example."

  read -r -s -p "OpenAI API key (leave blank to skip): " openai_key
  echo
  if [ -n "$openai_key" ]; then
    sed -i.bak "s|^OPENAI_API_KEY=.*|OPENAI_API_KEY=${openai_key}|" .env && rm -f .env.bak
  else
    warn "Left blank — the app will start, but every conversation turn will fail with an auth error until you add OPENAI_API_KEY to .env and restart."
  fi

  read -r -s -p "Deepgram API key (leave blank to skip): " deepgram_key
  echo
  if [ -n "$deepgram_key" ]; then
    sed -i.bak "s|^DEEPGRAM_API_KEY=.*|DEEPGRAM_API_KEY=${deepgram_key}|" .env && rm -f .env.bak
  else
    warn "Left blank — the app will start, but every conversation turn will fail with an auth error until you add DEEPGRAM_API_KEY to .env and restart."
  fi
fi
```

- [ ] **Step 2: Make it executable and syntax-check**

Run: `chmod +x start.sh && bash -n start.sh`
Expected: no output, exit code 0 (bash `-n` parses without executing).

- [ ] **Step 3: Manually verify the bootstrap prompt flow**

Run:
```bash
mv .env /tmp/env.bak
./start.sh
```
Expected: prints "Missing required tool" and exits if `docker`/`uv`/`npm` aren't installed (skip verifying this path if all three are present), otherwise prompts for both keys, hidden input (nothing echoed as you type), and prints the "Left blank" warning if you just press enter. Then interrupt with Ctrl+C (later tasks add the rest of the script; there's nothing to run yet after the bootstrap).

Restore your real `.env`:
```bash
mv /tmp/env.bak .env
```
Expected: `.env` is back to its original contents (confirm with `git status` showing no diff on `.env` — it's gitignored either way, but this makes sure your API keys are still there for later manual tests).

- [ ] **Step 4: Commit**

```bash
git add start.sh
git commit -m "feat: add start.sh with prereq check and .env bootstrap"
```

---

### Task 2: Infra bring-up, Postgres healthcheck poll, migration

**Files:**
- Modify: `start.sh` (append after the `.env` bootstrap block from Task 1)

**Interfaces:**
- Consumes: `info()`, `ok()`, `fail()` from Task 1.
- Produces: nothing new consumed by later tasks (Task 3 doesn't reference any variable defined here).

- [ ] **Step 1: Append infra bring-up, healthcheck poll, and migration to `start.sh`**

Add this immediately after the `.env` bootstrap `fi` (end of Task 1's block), before end of file:

```bash

# --- Infra ---
info "Starting Postgres + Jaeger (docker compose up -d)..."
docker compose up -d

info "Waiting for Postgres to be healthy..."
tries=0
until docker compose exec -T postgres pg_isready -U va -d voice_assistant >/dev/null 2>&1; do
  tries=$((tries + 1))
  if [ "$tries" -ge 30 ]; then
    fail "Postgres did not become ready after 30s. Check 'docker compose logs postgres'."
  fi
  sleep 1
done
ok "Postgres is ready."

# --- Migrate ---
info "Running database migrations..."
(cd backend && uv run alembic upgrade head)
```

- [ ] **Step 2: Syntax-check**

Run: `bash -n start.sh`
Expected: no output, exit code 0.

- [ ] **Step 3: Manually verify infra + migration**

Run:
```bash
docker compose down
./start.sh
```
Expected: "Starting Postgres + Jaeger..." → "Waiting for Postgres to be healthy..." → "Postgres is ready." → "Running database migrations..." → alembic output ending in the current head revision, then the script exits (nothing after migration yet). Confirm with:
```bash
docker compose ps
```
Expected: `postgres` and `jaeger` both show as `running`/`healthy`.

- [ ] **Step 4: Commit**

```bash
git add start.sh
git commit -m "feat: bring up infra and run migrations in start.sh"
```

---

### Task 3: Concurrent backend/frontend run with prefixed logs and teardown

**Files:**
- Modify: `start.sh` (append after the migration block from Task 2)

**Interfaces:**
- Consumes: `info()`, `warn()` from Task 1; runs after the migration step from Task 2.

- [ ] **Step 1: Append the run + teardown section to `start.sh`**

Add this after the migration line at the end of the file:

```bash

# --- Run backend + frontend ---
cleanup() {
  info "Stopping dev servers..."
  jobs -p | xargs -r kill 2>/dev/null
  warn "Postgres and Jaeger are still running in the background. Run 'make down' to stop them."
}
trap cleanup EXIT INT TERM

(cd backend && uv run uvicorn voice_assistant.main:app --reload --port 8000 2>&1 | sed -e "s/^/${MAGENTA}[backend]${NC} /") &
(cd frontend && npm run dev 2>&1 | sed -e "s/^/${CYAN}[frontend]${NC} /") &

sleep 2
ok "Backend:  http://localhost:8000"
ok "Frontend: http://localhost:5173"

wait
```

- [ ] **Step 2: Syntax-check**

Run: `bash -n start.sh`
Expected: no output, exit code 0.

- [ ] **Step 3: Manually verify full run and teardown**

Run: `./start.sh`
Expected: after migration output, `[backend]`/`[frontend]` prefixed lines appear (uvicorn startup log, Vite dev server banner), followed by the "Backend:"/"Frontend:" ready lines.

While it's running, in another terminal:
```bash
curl -sf http://localhost:8000/ -o /dev/null && echo backend-ok
curl -sf http://localhost:5173/ -o /dev/null && echo frontend-ok
```
Expected: both print their `-ok` line.

Back in the `start.sh` terminal, press Ctrl+C.
Expected: "Stopping dev servers..." then the "Postgres and Jaeger are still running..." reminder, then the script exits. Confirm infra is still up:
```bash
docker compose ps
```
Expected: `postgres` and `jaeger` still show `running`. Confirm the dev processes are gone:
```bash
lsof -i :8000 -i :5173
```
Expected: no output (both ports free).

- [ ] **Step 4: Commit**

```bash
git add start.sh
git commit -m "feat: run backend and frontend concurrently in start.sh with teardown"
```

---

### Task 4: README updates — one-shot try-it path and Quickstart rewrite

**Files:**
- Modify: `README.md:138-146` (the existing `## Quickstart` section, running up to the line before `## Testing` at 183)

**Interfaces:**
- Consumes: `start.sh` from Tasks 1–3; the pre-existing `app` compose profile in `docker-compose.yml` (unmodified).

- [ ] **Step 1: Replace the Quickstart section**

Find the current Quickstart section:
```markdown
## Quickstart

```bash
cp .env.example .env   # fill in OPENAI_API_KEY and DEEPGRAM_API_KEY; this is the
                        # only .env the app reads (backend resolves it by
                        # absolute path regardless of cwd) — don't add another
make up                  # start Postgres + Jaeger via docker compose
make migrate
make dev-backend         # FastAPI on :8000
make dev-frontend        # Vite on :5173
```

Then open http://localhost:5173 and talk (or type) to the assistant.
```

Replace it with:
```markdown
## Quickstart

```bash
./start.sh
```

This bootstraps `.env` (prompting for your `OPENAI_API_KEY` and
`DEEPGRAM_API_KEY` the first time — leave either blank and the app still
starts, but conversations will fail with an auth error until you add it),
starts Postgres + Jaeger, runs migrations, and launches the backend
(`:8000`) and frontend (`:5173`) together with interleaved, prefixed logs.
Ctrl+C stops the dev servers; Postgres/Jaeger keep running in the
background (`make down` to stop them too).

Then open http://localhost:5173 and talk (or type) to the assistant.

### Try it without a dev setup

No Python/Node toolchain, just Docker:

```bash
cp .env.example .env   # fill in OPENAI_API_KEY and DEEPGRAM_API_KEY
docker compose --profile app up --build
docker compose run --rm app alembic upgrade head   # first run only
```

This builds frontend+backend into one image and serves the whole app on
`:8000`.

### Manual / step-by-step

Prefer to run each step yourself (or are debugging one stage):

```bash
cp .env.example .env   # fill in OPENAI_API_KEY and DEEPGRAM_API_KEY; this is the
                        # only .env the app reads (backend resolves it by
                        # absolute path regardless of cwd) — don't add another
make up                  # start Postgres + Jaeger via docker compose
make migrate
make dev-backend         # FastAPI on :8000
make dev-frontend        # Vite on :5173
```
```

- [ ] **Step 2: Verify the README renders sensibly**

Run: `grep -n "^##" README.md`
Expected: output includes `## Quickstart` followed later by `## Architecture` (or whatever the next top-level section is), with `### Try it without a dev setup` and `### Manual / step-by-step` as sub-sections nested under Quickstart — confirms heading levels weren't accidentally broken.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document start.sh and the containerized try-it path in Quickstart"
```
