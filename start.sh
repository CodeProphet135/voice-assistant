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
