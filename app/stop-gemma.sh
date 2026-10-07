#!/bin/bash
# Stop everything this project starts. Nothing here survives: no resident model,
# no inference server, no page server. Safe to run at any time — each step is a
# no-op if that thing is already down.
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
LMS="$HOME/.lmstudio/bin/lms"
PORT=9092

echo ""
echo -e "${CYAN}  ◈ Stopping everything${NC}"

# 1. Unload any resident Ollama model (frees the ~18GB immediately)
if curl -s --max-time 3 http://localhost:11434/api/ps > /dev/null 2>&1; then
  for M in $(curl -s http://localhost:11434/api/ps | python3 -c 'import sys,json;print(" ".join(m.get("name","") for m in json.load(sys.stdin).get("models",[])))' 2>/dev/null); do
    curl -s -X POST http://localhost:11434/api/generate -H "Content-Type: application/json" \
      -d "{\"model\":\"${M}\",\"keep_alive\":0}" > /dev/null 2>&1
    echo -e "${GREEN}  ✓ unloaded ${M}${NC}"
  done
fi

# 2. Stop the Ollama server itself, so the app shows no models at all
if pgrep -f "ollama serve" > /dev/null 2>&1; then
  pkill -f "ollama serve" 2>/dev/null
  echo -e "${GREEN}  ✓ ollama serve stopped${NC}"
fi
pkill -x "Ollama" 2>/dev/null && echo -e "${GREEN}  ✓ Ollama.app quit${NC}"

# 3. Unload MLX + stop the LM Studio server (the app itself stays open)
# Only talk to lms if its server is actually up — the CLI blocks for seconds
# otherwise, which made this script feel hung.
if [ -x "$LMS" ] && curl -s --max-time 2 http://localhost:1234/v1/models > /dev/null 2>&1; then
  "$LMS" unload --all > /dev/null 2>&1 && echo -e "${GREEN}  ✓ LM Studio models unloaded${NC}"
  "$LMS" server stop > /dev/null 2>&1 && echo -e "${GREEN}  ✓ LM Studio server stopped${NC}"
fi

# 4. Stop the page/agent server
if lsof -ti tcp:$PORT > /dev/null 2>&1; then
  lsof -ti tcp:$PORT | xargs kill -9 2>/dev/null
  echo -e "${GREEN}  ✓ page server on :${PORT} stopped${NC}"
fi
pkill -f "launch-gemma.sh" 2>/dev/null

echo ""
echo -e "${CYAN}  Now idle. Verify:${NC}"
echo -e "    ollama ps            $(pgrep -f 'ollama serve' >/dev/null && echo '' || echo '# server is down, so this will start it again — use pgrep instead')"
echo -e "    pgrep -f 'ollama serve'   # nothing = stopped"
echo -e "    $LMS ps"
echo ""
