#!/bin/bash

# ── CONFIG ──
# Resolve the app folder from this script's own location — the previous
# hardcoded path ("AI WORKS") no longer matched the real folder ("AiWorks"),
# so this broke whenever the script was run from anywhere else.
HTML_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HTML_FILE="gemma-chat.html"
PORT=9092
# How long a model stays resident after its last use. 24h pinned ~18GB in RAM
# indefinitely; 30m keeps it warm for a working session then releases it.
OLLAMA_KEEP_ALIVE="30m"
# Ctrl+C always unloads the model. Set these to 1 to also stop the servers.
STOP_OLLAMA_ON_EXIT=0
STOP_LMSTUDIO_ON_EXIT=0
# Opening gemma-chat.html straight off disk makes the browser send "Origin: null",
# which Ollama rejects with 403. Ollama's CORS library will NOT accept the bare
# token "null" (it panics: origins must contain '*' or a scheme prefix), and its
# default allowance of file://* does not match the null origin the browser
# actually sends. So "*" is the only value that makes file:// work.
# SECURITY: "*" lets ANY web page you visit reach this Ollama. If you only ever
# open the app via http://localhost:9092/ you do NOT need this — comment it out.
OLLAMA_ALLOWED_ORIGINS="*"

# ── COLORS ──
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

echo ""
echo -e "${CYAN}  ◈ Gemma Chat Launcher${NC}"
echo -e "${CYAN}  ─────────────────────${NC}"
echo ""
echo -e "  Which model do you want to run?"
echo ""
echo -e "  ${BOLD}Ollama${NC} (GGUF — vision + tools + thinking)"
echo -e "    ${BOLD}1${NC}) qwen3.8-27b              — General (17.5GB)"
echo -e "    ${BOLD}2${NC}) qwen3.8-27b-abliterated  — Uncensored (17.4GB)"
echo -e "  ${BOLD}LM Studio${NC} (MLX — Apple-silicon optimised, usually faster)"
echo -e "    ${BOLD}3${NC}) qwen3.8-27b-mlx          — MLX 4-bit (16GB)"
echo ""
echo -e "  ${YELLOW}Note:${NC} 24GB unified memory fits only ONE of these at a time."
echo -e "  Whichever you pick, the others are unloaded automatically."
echo ""
read -p "  Enter choice [1/2/3]: " CHOICE
CHOICE="$(echo "$CHOICE" | tr -d '[:space:]')"

RUNTIME="ollama"
case $CHOICE in
  1) MODEL="qwen3.8-27b"              ;;
  2) MODEL="qwen3.8-27b-abliterated"  ;;
  3) MODEL="qwen3.8-27b-mlx"; RUNTIME="lmstudio" ;;
  *) MODEL="qwen3.8-27b"              ;;
esac

LMS="$HOME/.lmstudio/bin/lms"

echo ""
echo -e "${CYAN}  ─────────────────────${NC}"

# ── MLX PATH (LM Studio) ─────────────────────────────────────────────────
if [ "$RUNTIME" = "lmstudio" ]; then
  if [ ! -x "$LMS" ]; then
    echo -e "${RED}  ✗ lms CLI not found at $LMS — open LM Studio once to install it.${NC}"
    exit 1
  fi
  # A 27B MLX model needs ~16GB. Anything Ollama is holding must go first, or
  # LM Studio's guardrail refuses the load.
  if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    for L in $(curl -s http://localhost:11434/api/ps | python3 -c 'import sys,json;print(" ".join(m.get("name","") for m in json.load(sys.stdin).get("models",[])))' 2>/dev/null); do
      echo -e "${YELLOW}  ↻ Unloading Ollama model ${L} to free memory...${NC}"
      curl -s -X POST http://localhost:11434/api/generate -H "Content-Type: application/json" \
        -d "{\"model\":\"${L}\",\"keep_alive\":0}" > /dev/null 2>&1
    done
  fi
  # --cors is REQUIRED for the file:// version of the page to reach :1234.
  echo -e "${YELLOW}  ↻ Starting LM Studio server (CORS enabled)...${NC}"
  "$LMS" server start --cors > /dev/null 2>&1
  sleep 2
  echo -e "${YELLOW}  ↻ Loading ${MODEL}...${NC}"
  # Pipe the output to a file rather than through `tail` — piping makes `if`
  # test the LAST command in the pipeline (always 0), which reported a failed
  # load as a success. Then confirm against the server, not the exit status.
  "$LMS" load "$MODEL" --identifier "$MODEL" > /tmp/lms-load.log 2>&1
  LOAD_RC=$?
  sed 's/\x1b\[[0-9;]*[a-zA-Z]//g' /tmp/lms-load.log | grep -v "^\s*$" | tail -2 | sed "s/^/    /"
  LOADED_OK=$(curl -s --max-time 5 http://localhost:1234/v1/models \
    | python3 -c "import sys,json;print('yes' if any(m['id']=='$MODEL' for m in json.load(sys.stdin).get('data',[])) else 'no')" 2>/dev/null)
  if [ "$LOAD_RC" -eq 0 ] && [ "$LOADED_OK" = "yes" ]; then
    echo -e "${GREEN}  ✓ ${MODEL} loaded and serving${NC}"
  else
    echo -e "${RED}  ✗ ${MODEL} did NOT load.${NC}"
    echo -e "${YELLOW}    Almost always free memory. Check what is holding it:${NC}"
    echo -e "      ollama ps        # unload:  curl -s localhost:11434/api/generate -d '{\"model\":\"NAME\",\"keep_alive\":0}'"
    echo -e "      ${LMS} ps"
    echo -e "${YELLOW}    Close heavy apps (browsers, editors) — a 27B needs ~16GB free — then rerun.${NC}"
    echo -e "${YELLOW}    Or relax LM Studio → Settings → Hardware → Model loading guardrails (risks a freeze).${NC}"
  fi
  "$LMS" ps 2>&1 | head -6 | sed "s/^/    /"

  lsof -ti tcp:$PORT | xargs kill -9 2>/dev/null
  sleep 0.5
  cd "$HTML_DIR"
  PY_BIN="${PYTHON:-python3}"   # set PYTHON=/path/to/python to override; default uses the active venv
  "$PY_BIN" gemma-server.py &> /tmp/gemma-http.log &
  HTTP_PID=$!
  sleep 1
  open "http://localhost:${PORT}/"
  echo ""
  echo -e "${CYAN}  ─────────────────────${NC}"
  echo -e "${GREEN}  ✓ MLX ready via LM Studio${NC}"
  echo -e "  Chat:   ${CYAN}http://localhost:${PORT}/${NC}"
  echo -e "  In the app: choose the ${BOLD}OPENAI-COMPATIBLE${NC} tab, then click"
  echo -e "  ${BOLD}\"Use LM Studio (local MLX)\"${NC} — no API key needed."
  echo -e "    Base URL: ${CYAN}http://localhost:1234/v1${NC}"
  echo -e "    Model:    ${CYAN}${MODEL}${NC}"
  echo ""
  echo -e "  Press ${RED}Ctrl+C${NC} to stop the page server (LM Studio keeps running)."
  cleanup(){
    echo ""
    kill $HTTP_PID 2>/dev/null
    "$LMS" unload --all > /dev/null 2>&1 && echo -e "${GREEN}  ✓ MLX model unloaded${NC}"
    if [ "$STOP_LMSTUDIO_ON_EXIT" = "1" ]; then
      "$LMS" server stop > /dev/null 2>&1 && echo -e "${GREEN}  ✓ LM Studio server stopped${NC}"
    else
      echo -e "${CYAN}  · LM Studio server left running (no model resident).${NC}"
      echo -e "${CYAN}    Fully stop it with: bash stop-gemma.sh${NC}"
    fi
    echo ""; exit 0; }
  trap cleanup SIGINT SIGTERM
  wait $HTTP_PID
  exit 0
fi

# ── STEP 1: Check Ollama is installed ──
if ! command -v ollama &> /dev/null; then
  echo -e "${RED}  ✗ Ollama not found. Install from https://ollama.ai${NC}"
  exit 1
fi

# ── STEP 2: Start Ollama if not running ──
if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
  echo -e "${GREEN}  ✓ Ollama already running${NC}"
  # Already-running instance may predate the origin allowance. Probe it the way
  # a file:// page would, and warn with the exact fix rather than failing later.
  ORIGIN_CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "Origin: null" http://localhost:11434/api/tags)
  if [ "$ORIGIN_CODE" != "200" ]; then
    echo -e "${YELLOW}  ! This Ollama rejects file:// pages (HTTP $ORIGIN_CODE for Origin: null).${NC}"
    echo -e "${YELLOW}    Opening gemma-chat.html directly from disk will not connect.${NC}"
    echo -e "${YELLOW}    Fix once, then restart Ollama:${NC}"
    echo -e "      launchctl setenv OLLAMA_ORIGINS '*' && pkill -f 'ollama serve'"
    echo -e "${YELLOW}    (Using http://localhost:${PORT}/ instead needs no such change.)${NC}"
  fi
else
  echo -e "${YELLOW}  ↻ Starting Ollama server...${NC}"
  export OLLAMA_KEEP_ALIVE=$OLLAMA_KEEP_ALIVE
  export OLLAMA_HOST=127.0.0.1:11434
  export OLLAMA_ORIGINS=$OLLAMA_ALLOWED_ORIGINS
  ollama serve &> /tmp/ollama-gemma.log &
  for i in {1..15}; do
    sleep 1
    if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
      echo -e "${GREEN}  ✓ Ollama started${NC}"
      break
    fi
    if [ $i -eq 15 ]; then
      echo -e "${RED}  ✗ Ollama failed to start. Check /tmp/ollama-gemma.log${NC}"
      exit 1
    fi
  done
fi

# ── STEP 3: Unload anything that is loaded but NOT selected ──
# This is why picking "1" still showed both models: OLLAMA_KEEP_ALIVE=24h keeps
# whatever a previous run loaded resident in VRAM, and `ollama serve` is left
# running between sessions. Selecting a model only ever ADDED to what was
# already loaded — it never took the other one down. Ask Ollama what is
# currently resident and evict everything we didn't ask for (keep_alive:0).
LOADED="$(curl -s http://localhost:11434/api/ps \
  | python3 -c 'import sys,json;print(" ".join(m.get("name","") for m in json.load(sys.stdin).get("models",[])))' 2>/dev/null)"

# /api/ps reports fully-qualified names ("qwen3.8-27b:latest") while the menu
# uses the bare name ("qwen3.8-27b"). Comparing them raw never matched, so the
# script unloaded the very model it was about to warm up — a pointless 17.5GB
# reload on every launch. Normalise the implicit ":latest" on both sides.
norm(){ case "$1" in *:*) echo "$1";; *) echo "$1:latest";; esac; }

for L in $LOADED; do
  KEEP=0
  LN="$(norm "$L")"
  for M in $MODEL; do
    [ "$LN" = "$(norm "$M")" ] && KEEP=1
  done
  if [ $KEEP -eq 0 ]; then
    echo -e "${YELLOW}  ↻ Unloading ${L} (not selected)...${NC}"
    curl -s -X POST http://localhost:11434/api/generate \
      -H "Content-Type: application/json" \
      -d "{\"model\":\"${L}\",\"keep_alive\":0}" > /dev/null 2>&1
    echo -e "${GREEN}  ✓ ${L} unloaded${NC}"
  fi
done

# Free any MLX model LM Studio is holding — 24GB fits only one 27B.
# Gate on a fast HTTP probe first: `lms ps` blocks for many seconds when the
# LM Studio server is down, which stalled the whole launcher.
if [ -x "$LMS" ] && curl -s --max-time 2 http://localhost:1234/v1/models > /dev/null 2>&1; then
  if "$LMS" ps 2>/dev/null | grep -qiE "qwen|llama|mistral|gemma"; then
    echo -e "${YELLOW}  ↻ Unloading LM Studio model(s) to free memory...${NC}"
    "$LMS" unload --all > /dev/null 2>&1
    echo -e "${GREEN}  ✓ LM Studio unloaded${NC}"
  fi
fi

# ── STEP 4: Warm up selected model(s) ──
for M in $MODEL; do
  echo -e "${YELLOW}  ↻ Warming up ${M}...${NC}"
  curl -s -X POST http://localhost:11434/api/generate \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${M}\",\"prompt\":\"\",\"keep_alive\":\"${OLLAMA_KEEP_ALIVE}\"}" \
    > /dev/null 2>&1 &
  echo -e "${GREEN}  ✓ ${M} warming up in background${NC}"
done

# ── STEP 5: Kill any existing server on this port ──
lsof -ti tcp:$PORT | xargs kill -9 2>/dev/null
sleep 0.5

# ── STEP 6: Start agent server (serves the page + /api/agent/* file+run tools) ──
# Needs Flask: activate the repo's venv first (see README), or set PYTHON=/path/to/python.
echo -e "${YELLOW}  ↻ Starting agent server on port ${PORT}...${NC}"
cd "$HTML_DIR"
PY_BIN="${PYTHON:-python3}"
"$PY_BIN" gemma-server.py &> /tmp/gemma-http.log &
HTTP_PID=$!
sleep 1

if ! curl -s http://localhost:$PORT/health > /dev/null 2>&1; then
  echo -e "${RED}  ✗ Agent server failed to start. Check /tmp/gemma-http.log${NC}"
  exit 1
fi
echo -e "${GREEN}  ✓ Agent server running${NC}"

# ── STEP 7: Open browser ──
sleep 0.5
open "http://localhost:${PORT}/"
echo -e "${GREEN}  ✓ Browser opened${NC}"

echo ""
echo -e "${CYAN}  ─────────────────────${NC}"
echo -e "${GREEN}  ✓ Everything is running!${NC}"
echo -e "  Chat:   ${CYAN}http://localhost:${PORT}/${NC}"
echo -e "  Model:  ${CYAN}${MODEL}${NC}"
RESIDENT="$(curl -s http://localhost:11434/api/ps | python3 -c 'import sys,json;print(", ".join(m.get("name","") for m in json.load(sys.stdin).get("models",[])) or "(none yet — still warming up)")' 2>/dev/null)"
echo -e "  Loaded: ${CYAN}${RESIDENT}${NC}"
echo ""
echo -e "  Press ${RED}Ctrl+C${NC} to stop everything"
echo ""

# ── CLEANUP on exit ──
cleanup() {
  echo ""
  echo -e "${YELLOW}  Shutting down...${NC}"
  kill $HTTP_PID 2>/dev/null
  # Ctrl+C means "turn it off" — so actually free the ~18GB and stop the
  # server, rather than leaving the model resident for OLLAMA_KEEP_ALIVE.
  for M in $MODEL; do
    curl -s -X POST http://localhost:11434/api/generate -H "Content-Type: application/json" \
      -d "{\"model\":\"${M}\",\"keep_alive\":0}" > /dev/null 2>&1
    echo -e "${GREEN}  ✓ ${M} unloaded${NC}"
  done
  if [ "$STOP_OLLAMA_ON_EXIT" = "1" ]; then
    pkill -f "ollama serve" 2>/dev/null
    echo -e "${GREEN}  ✓ ollama serve stopped${NC}"
  else
    echo -e "${CYAN}  · ollama serve left running (no model resident).${NC}"
    echo -e "${CYAN}    Fully stop it with: bash stop-gemma.sh${NC}"
  fi
  echo ""
  exit 0
}
trap cleanup SIGINT SIGTERM

wait $HTTP_PID
