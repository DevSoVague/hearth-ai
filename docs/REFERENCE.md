# Gemma4

Two local-first web apps that talk to a Gemma model running in **Ollama** on
your Mac (or any Anthropic-/OpenAI-compatible API you point them at). Each
app is a single self-contained HTML file plus a small local Python (Flask)
server that serves the page and, where needed, backs a feature the browser
can't do alone (sandboxed file/shell access, Whisper transcription).

- **Gemma Chat** (`gemma-chat.html` / `gemma-server.py`) - a full-featured
  chat client: streaming, multi-session memory with rolling summarization,
  math/diagram/plot rendering, file attachments, a live artifact preview
  panel, a sandboxed coding-agent mode, and a Judge/Solver self-reflection
  loop.
- **Gemma Coach** (`gemma-coach.html` / `coach-server.py`) - "angel on your
  shoulder": listens to a live conversation through your mic, transcribes it
  locally with Whisper, and periodically suggests what to say next against
  talking points you set up in advance.

---

## Architecture

```
┌─────────────────────┐   POST /api/generate, /api/chat (streaming)
│  Browser              │ ──────────────────────────────────► Ollama (11434)
│  gemma-chat.html      │ ◄────────────────────────────────── token stream
│  (all UI + app logic) │
│                        │   POST /api/agent/*  (agent mode only)
│                        │ ──────────────────────────────────► gemma-server.py (9092)
└────────────────────────┘        file read/write/edit/list, sandboxed run
```

```
┌─────────────────────┐   POST /api/generate
│  Browser              │ ──────────────────────────────────► Ollama (11434)
│  gemma-coach.html      │
│                        │   POST /transcribe  (mic audio, ~7s chunks)
│                        │ ──────────────────────────────────► coach-server.py (9091)
└────────────────────────┘        Whisper transcription, fully local
```

Everything above runs on `localhost`. The one way data leaves your machine
is if you switch Chat's boot screen (or Reflect Mode, which needs its own
provider config) to **Custom API** or **OpenAI-Compatible** - those send
your messages to whatever base URL + key you supply.

| Port  | Process | Serves |
|-------|---------|--------|
| 11434 | Ollama | model inference (`/api/generate`, `/api/chat`, `/api/tags`, `/api/ps`) |
| 9092  | `gemma-server.py` | `gemma-chat.html` + `/api/agent/*` |
| 9091  | `coach-server.py` | `gemma-coach.html` + `/transcribe` |

---

## Quick start

### Gemma Chat
```
Double-click "Gemma Chat.command"        (or: bash launch-gemma.sh)
```
This picks a model (E4B / 26B / both when prompted), starts Ollama if it's
not already running, warms the model into VRAM, starts `gemma-server.py`,
and opens `http://localhost:9092/`. On the boot screen pick a provider tab
(**Ollama** auto-detects `http://localhost:11434` and its installed
models), set generation parameters if you want, and **Connect**. If Ollama
won't connect, click the **?** next to "Ollama Endpoint" for the exact
terminal commands.

### Gemma Coach
```
bash launch-coach.sh
```
Starts Ollama, warms `gemma4:e4b`, uses the active venv (or `$CONDA_ENV` if set) for
Whisper/PyTorch, starts `coach-server.py`, and opens
`http://localhost:9091/gemma-coach.html` once the Whisper model finishes
loading (can take a while on first run - it downloads weights).

---

## Gemma Chat - full capability list

### Models & providers
- **Ollama (local)** - auto-detects every model you have pulled; switch
  models mid-conversation from the header dropdown. Also polls Ollama's
  `/api/ps` every second to show a color-coded **VRAM bar** (green → yellow
  → red as you approach your limit).
- **Custom API (Anthropic-compatible)** - any base URL + API key, e.g.
  Anthropic directly (default `https://api.anthropic.com`) or OpenRouter. Add/remove named
  models as chips.
- **OpenAI-Compatible API** - any base URL + API key, e.g. OpenAI, Groq,
  Together, Fireworks. Same model-chip UI.
- All three providers share one code path (tool calls, streaming, message
  history) - switching provider mid-session doesn't lose context.

### Conversation & memory
- **Sessions** - unlimited chat sessions in the sidebar, auto-named from
  the first message; create/switch/delete/rename via the session list.
- **Export/Import** - download a session as JSON, re-import it later.
- **Full-context memory** by default: the whole conversation is resent
  every turn.
- **Rolling summarization** - once context usage crosses a threshold
  (60% of the model's context window), older turns are compressed into a
  running summary (injected as a system message) and only the most recent
  turns are kept verbatim, so long conversations don't blow the context
  window. A **∑** flag in the header shows when a summary is active.
- **Clear Memory** vs **Clear All** - Clear Memory keeps the messages
  visible on screen but tells the model to forget everything before the
  next reply (a clean slate without losing your scrollback); Clear All
  permanently deletes every session.
- **Context usage bar** and a **live token/word estimate** for the current
  draft message.
- **System prompt** - customizable per session, persisted to
  `localStorage`.

### Generation
- **Streaming** responses token-by-token for all three providers.
- **Thinking / chain-of-thought mode** - prompts the model to reason inside
  a `<think>` block, rendered as a collapsible section before the final
  answer (best with larger models).
- **Sampling controls** - temperature, top-p, top-k, max tokens, repeat
  penalty, context size (`num_ctx`), all editable live from Settings.
- **Live response timer** with an ETA while generating, plus final
  tokens/sec and total time stats.
- **Stop** button to cancel an in-flight generation.

### Content & rendering
- **Markdown** rendering throughout, with copy-to-clipboard buttons on code
  blocks.
- **Syntax highlighting** (highlight.js) for code blocks.
- **Math** rendering via MathJax (`$...$` / `$$...$$` / LaTeX).
- **Diagrams** via Mermaid (flowcharts, sequence diagrams, etc. in fenced
  ```mermaid``` blocks).
- **Interactive plots** via Plotly (fenced plot blocks render as real,
  zoomable/pannable charts).
- **Artifacts** - HTML/SVG content in a special code-block syntax renders
  live in a side panel instead of as raw code, so the model can hand you a
  working mini-app or diagram you can view/interact with in place.
- **Export a reply** as a downloaded text file, or generate a standalone
  PDF from a response.

### Attachments (📎, or drag & drop)
- Text/code files of most common extensions (`.py .js .ts .html .css
  .json .yaml .sql .sh …`), read as plain text.
- **PDFs** - text extracted client-side via pdf.js.
- **Jupyter notebooks (.ipynb)** - cells and their outputs extracted and
  sent as readable text.
- **Images** - base64-encoded and sent to multimodal-capable models.

### Agent mode (sandboxed coding agent)
Opt-in per project folder from the **Agent Tools** panel. Once a project
root is set and file/run tools are enabled, the model can:
- **List directories** and **read files** inside that folder.
- **Propose writes/edits**, always shown to you first as a **unified diff**
  - nothing touches disk until you click Approve in the chat.
- **Run shell commands** in a sandbox (macOS `sandbox-exec`) that denies
  filesystem writes outside the project root and denies network access
  entirely, regardless of what the command tries - opt-in separately from
  file tools.
- Every real write/edit is preceded by a **backup** of the previous
  contents (`.gemma_agent/backups/`); every real command run is preceded by
  a **zip snapshot** of the whole project (`.gemma_agent/snapshots/`).
- A `.gemma_agent/run-sandboxed.sh` helper script is auto-generated so you
  can run the exact same sandboxed command by hand from a terminal.
- See `agent_tools.py` / `gemma-server.py` for the implementation; this
  mode requires macOS (`sandbox-exec`).

### Reflect Mode (Judge/Solver self-critique)
- A **Solver** model answers, a **Judge** model scores that answer against
  a threshold (default 80%); if it fails, the Solver retries with the
  Judge's feedback, up to a max iteration count (default 5).
- Solver and Judge can each independently be any configured provider/model
  - e.g. a fast local model as Solver, a stronger API model as Judge.
- Judge prompt is customizable; otherwise a sensible default is used.

### Misc
- Configurable **body/display/code fonts** from a font picker.
- **Enter** to send, **Shift+Enter** for a newline.
- All settings/sessions/providers/agent config persist in `localStorage`
  (except the agent project root, which is always re-read from
  `gemma-server.py` on load - never trusted from the browser alone).

---

## Gemma Coach - full capability list

- **Live mic capture** in rolling ~7-second WebM chunks (fresh recorder
  each cycle so every chunk is independently decodable), uploaded to
  `coach-server.py`'s `/transcribe` endpoint.
- **Local transcription** via OpenAI Whisper (`large-v3` by default;
  `MODEL` at the top of `coach-server.py` can be swapped for the faster
  `medium.en` / `small.en`). Runs on Apple Silicon MPS (falling back to CPU
  ops where MPS lacks support), or CUDA/CPU elsewhere.
- **Anti-hallucination gates**, since Whisper invents text on silence/noise:
  - an RMS energy gate skips near-silent chunks before they're even sent
    to the model,
  - segments are dropped if their no-speech probability, average
    log-probability, or compression ratio (a repetition signature) cross
    configured thresholds.
- **Live transcript** of the conversation, tagged by speaker.
- **Talking points list** - add/check off/remove points you want to hit
  during the conversation; the model sees which are still outstanding.
- **Periodic coaching nudges** - every 15 seconds (`COACH_MS`) or on demand
  via the **✧ Go** button, the recent transcript + your remaining talking
  points are sent to the model, which returns structured (JSON) advice on
  what to say next - rendered live in the "angel on your shoulder" panel.
- **Notes stash** and a **session reset** to start a fresh conversation.

---

## Requirements

- **Ollama** (https://ollama.ai) with a Gemma model pulled, e.g.
  `ollama pull gemma4:e4b`.
- **Python + Flask** for `gemma-server.py`. The launchers run `python3` from
  the active venv (override with `PYTHON=/path/to/python`).
- **`flask`, `torch`, `openai-whisper`, `numpy`** for `coach-server.py`
  (plus the `ffmpeg` binary, which Whisper uses to decode audio). Set
  `CONDA_ENV=<name>` to have `launch-coach.sh` activate a conda env instead.
  Whisper's `large-v3` weights download on first run (a few GB).
- **macOS with `sandbox-exec`** for Chat's agent-mode command execution -
  the rest of both apps is otherwise platform-agnostic.
- Internet access only for: pulling Ollama models, downloading Whisper
  weights on first run, and the CDN-hosted JS libraries `gemma-chat.html`
  loads (highlight.js, pdf.js, MathJax, Mermaid, Plotly) - everything else
  is local.

---

## Troubleshooting

- **"Could not reach Ollama"** - run `ollama serve` in Terminal, or just
  relaunch via `Gemma Chat.command` / `launch-gemma.sh`, which does this
  for you. See the **?** next to "Ollama Endpoint" on the boot screen for
  the exact commands.
- **Coach never opens a browser tab** - it polls `coach-server.py`'s
  `/health` for up to 90s before opening the browser; the Whisper model
  load (especially first run, downloading weights) is the slow part -
  watch the Terminal output.
- **Agent commands fail immediately** - `sandbox-exec` is macOS-only;
  agent mode's run-command tool won't work on Linux/Windows (file
  read/write tools are unaffected).
- **Port already in use** - both launch scripts kill whatever's already
  listening on their port before starting
  (`lsof -ti tcp:$PORT | xargs kill -9`), so a stale process from a
  previous run shouldn't block a fresh launch.
- **Context growing huge / replies slowing down** - use Clear Memory
  (keeps scrollback, resets what's sent to the model), or rely on the
  automatic rolling summary that kicks in once you cross ~60% of the
  model's context window (watch for the **∑** flag).

---

## File map

| File | Purpose |
|------|---------|
| `Gemma Chat.command` | Double-click launcher → runs `launch-gemma.sh` |
| `launch-gemma.sh` | Starts Ollama, warms the model, starts `gemma-server.py`, opens the browser |
| `gemma-server.py` | Flask server: serves `gemma-chat.html`, hosts `/api/agent/*`, stores agent config |
| `gemma-chat.html` | The entire Chat app - UI, rendering, providers, agent mode, reflect mode |
| `agent_tools.py` | Sandboxed file/shell tools used by agent mode (path safety, diffs, backups, snapshots, `sandbox-exec` profile) |
| `launch-coach.sh` | Starts Ollama, uses the active venv (or `$CONDA_ENV`), runs `coach-server.py` |
| `coach-server.py` | Flask + Whisper server: serves `gemma-coach.html`, hosts `/transcribe` |
| `gemma-coach.html` | The entire Coach app - mic capture, transcript, talking points, coaching loop |
| `agent_config.json` | Auto-created; stores the configured agent project root |
| `.gemma_agent/` | Auto-created per project root; `backups/`, `snapshots/`, `run-sandboxed.sh` |
| `somecommands.txt` | Scratch notes / manual Ollama commands |
| `gemma-chat-package/` | Standalone copy of Chat for sharing elsewhere |
| `old version/` | Earlier iterations of `gemma-chat.html`, kept for reference |
