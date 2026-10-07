#!/bin/bash

# ── CONFIG ──
# Resolve the app folder from this script's own location — the previous
# hardcoded path ("AI WORKS") no longer matched the real folder ("AiWorks"),
# so this broke whenever the script was run from anywhere else.
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HTML_FILE="gemma-coach.html"
PORT=9091
MODEL="gemma4:e4b"
CONDA_ENV="${CONDA_ENV:-}"   # optional: name of a conda env that has Whisper; empty = use the active venv
OLLAMA_KEEP_ALIVE="24h"

# ── COLORS ──
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'

echo ""
echo -e "${CYAN}  ◈ Gemma Coach — Angel on Your Shoulder${NC}"
echo -e "${CYAN}  ──────────────────────────────────────${NC}"
echo ""

# ── STEP 1: Ollama installed? ──
if ! command -v ollama &> /dev/null; then
  echo -e "${RED}  ✗ Ollama not found. Install from https://ollama.ai${NC}"; exit 1
fi

# ── STEP 2: Start Ollama if not running ──
if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
  echo -e "${GREEN}  ✓ Ollama already running${NC}"
else
  echo -e "${YELLOW}  ↻ Starting Ollama server...${NC}"
  export OLLAMA_KEEP_ALIVE=$OLLAMA_KEEP_ALIVE
  export OLLAMA_HOST=127.0.0.1:11434
  ollama serve &> /tmp/ollama-coach.log &
  for i in {1..15}; do
    sleep 1
    if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
      echo -e "${GREEN}  ✓ Ollama started${NC}"; break
    fi
    if [ $i -eq 15 ]; then
      echo -e "${RED}  ✗ Ollama failed to start. See /tmp/ollama-coach.log${NC}"; exit 1
    fi
  done
fi

# ── STEP 3: Warm the coach model ──
echo -e "${YELLOW}  ↻ Warming up ${MODEL}...${NC}"
curl -s -X POST http://localhost:11434/api/generate \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"${MODEL}\",\"prompt\":\"\",\"keep_alive\":\"${OLLAMA_KEEP_ALIVE}\"}" \
  > /dev/null 2>&1 &
echo -e "${GREEN}  ✓ ${MODEL} warming in background${NC}"

# ── STEP 4: Free the port ──
lsof -ti tcp:$PORT | xargs kill -9 2>/dev/null
sleep 0.5

# ── STEP 5: Python env with Whisper (active venv by default, conda optional) ──
if [ -n "$CONDA_ENV" ]; then
  CONDA_SH="$(conda info --base 2>/dev/null)/etc/profile.d/conda.sh"
  if [ -f "$CONDA_SH" ]; then
    source "$CONDA_SH"
  else
    echo -e "${RED}  ✗ conda not found; unset CONDA_ENV to use the active venv${NC}"; exit 1
  fi
  conda activate "$CONDA_ENV" || { echo -e "${RED}  ✗ could not activate conda env '${CONDA_ENV}'${NC}"; exit 1; }
  echo -e "${GREEN}  ✓ conda env '${CONDA_ENV}' active${NC}"
fi
PY_BIN="${PYTHON:-python3}"

# ── STEP 6: Open browser once the server is up ──
(
  for i in {1..90}; do
    sleep 1
    if curl -s http://localhost:$PORT/health > /dev/null 2>&1; then
      open "http://localhost:${PORT}/${HTML_FILE}"
      break
    fi
  done
) &

echo ""
echo -e "${CYAN}  ──────────────────────────────────────${NC}"
echo -e "${YELLOW}  ↻ Loading Whisper + starting web server (first run downloads weights)...${NC}"
echo -e "  Chat opens automatically at ${CYAN}http://localhost:${PORT}/${HTML_FILE}${NC}"
echo -e "  Press ${RED}Ctrl+C${NC} to stop."
echo ""

# ── STEP 7: Run the Whisper backend (serves the page + /transcribe) ──
cd "$APP_DIR"
exec "$PY_BIN" coach-server.py
