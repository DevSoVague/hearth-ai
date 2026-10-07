# Gemma4 - Technical Architecture

Deep technical reference for the `gemma4` project: every running service, every file's internal
structure (with the frontend files broken into their logical submodules, since each is a single
large HTML/JS file rather than several small ones), and the exact dependency graph - both the
Python **import** graph and the **runtime** graph (HTTP calls, process spawns) that connects
files with no import relationship between them at all.

---

## 0. Repository layout

```
gemma4/
├── Gemma Chat.command          double-click launcher → bash launch-gemma.sh
├── launch-gemma.sh             orchestration: starts Ollama, gemma-server.py, opens browser
├── gemma-server.py             Flask backend for Chat: serves the page + /api/agent/*
├── agent_tools.py              sandboxed file/shell primitives used by gemma-server.py
├── gemma-chat.html             the entire Chat app - UI + logic, one file, ~3,700 lines
├── launch-coach.sh             orchestration: starts Ollama, conda env, coach-server.py
├── coach-server.py             Flask + Whisper backend for Coach
├── gemma-coach.html            the entire Coach app - one file, ~500 lines
├── agent_config.json           auto-created; stores the configured Agent Tools project root
├── .gemma_agent/                auto-created per project root; backups/, snapshots/, run-sandboxed.sh
├── gemma-chat-package/          standalone export copy of Chat (for sharing elsewhere)
└── old version/                 earlier iterations of gemma-chat.html, kept for reference
```

Unlike a typical multi-file web app, **there is no bundler, no ES module graph, and no shared
frontend code between `gemma-chat.html` and `gemma-coach.html`** - each is one self-contained
`<script>` block. The "modules" in this document are therefore logical (regions of one file that
have a single responsibility and a stable internal contract with the rest of the file), not
separate files.

---

## 1. Services - every process that actually runs

| # | Service | How it starts | Port | Implemented by |
|---|---|---|---|---|
| 1 | **Ollama daemon** (external) | `ollama serve`, or auto-started by the launch scripts | `127.0.0.1:11434` | Not part of this repo - both apps talk to it directly via `fetch()` from the browser |
| 2 | **Gemma Chat backend** | `Gemma Chat.command` → `launch-gemma.sh` → `python3 gemma-server.py` | `127.0.0.1:9092` | `gemma-server.py` (Flask, `threaded=True`) |
| 3 | **Gemma Chat frontend** | Opened automatically by `launch-gemma.sh` in your default browser | n/a (runs in-browser) | `gemma-chat.html` - served as a static file by service #2, but all its logic runs client-side |
| 4 | **Gemma Coach backend** | `bash launch-coach.sh` → `python coach-server.py` | `127.0.0.1:9091` | `coach-server.py` (Flask + Whisper, `threaded=True`) |
| 5 | **Gemma Coach frontend** | Opened automatically by `launch-coach.sh` | n/a (runs in-browser) | `gemma-coach.html` |

**Critical architectural fact**: services #2 and #4 are each two things wearing one hat - a
**static file server** (serving the single HTML file at `/`) and an **API server** (`/api/agent/*`
for Chat, `/transcribe` for Coach). The browser tab (#3/#5) is not a passive client of #2/#4 the
way a typical SPA is of its backend - it talks to **three** different HTTP endpoints depending on
what it's doing:

```
                         ┌─────────────────────────────┐
                         │   Browser tab (gemma-chat     │
                         │   .html - all app logic runs   │
                         │   here, nothing server-side)   │
                         └───────────┬────────┬──────────┘
                                     │        │
                normal chat, model   │        │  ONLY for Agent Tools:
                list, VRAM polling   │        │  list_dir/read_file/
                (direct, always)     │        │  preview_write/write_file/
                                     │        │  preview_edit/edit_file/
                                     │        │  run_command
                                     ▼        ▼
                    ┌────────────────┐  ┌──────────────────────┐
                    │ Service #1:     │  │ Service #2:            │
                    │ Ollama :11434   │  │ gemma-server.py :9092  │
                    │ /api/tags       │  │ /api/agent/*            │
                    │ /api/ps          │  │  (imports agent_tools.py)│
                    │ /api/chat        │  └──────────────────────┘
                    └────────────────┘
```
If "Custom API"/"OpenAI-Compatible" is selected instead of Ollama, the top-left arrow points at
that external base URL instead of `:11434` - `gemma-server.py` is never involved in model
inference either way, only in the optional Agent Tools file/shell operations.

---

## 2. Module deep-dive: `gemma-server.py` (Flask backend, Chat)

```python
PORT = 9092; HTML_FILE = "gemma-chat.html"; CONFIG_FILE = "agent_config.json"
app = Flask(__name__, static_folder=str(HERE), static_url_path="")

GET  /                          → send_from_directory(HERE, HTML_FILE)
GET  /health                    → {"ok": true}
GET  /api/agent/config          → {"configured": bool, "project_root": str|None}
POST /api/agent/config          → validates + persists a new project_root to agent_config.json,
                                    calls agent_tools.write_run_script(resolved)
POST /api/agent/list_dir        → agent_tools.list_dir(root, path)
POST /api/agent/read_file       → agent_tools.read_file(root, path)
POST /api/agent/preview_write   → agent_tools.preview_write(root, path, content)     [dry run]
POST /api/agent/preview_edit    → agent_tools.preview_edit(root, path, old, new)     [dry run]
POST /api/agent/write_file      → agent_tools.write_file(root, path, content)        [real]
POST /api/agent/edit_file       → agent_tools.edit_file(root, path, old, new)        [real]
POST /api/agent/run_command     → agent_tools.snapshot_project(root) then
                                    agent_tools.run_in_sandbox(root, command, timeout_s)
```

**Design pattern - preview/apply split, enforced at the route layer**: every mutating operation
has two routes, a `preview_*` (pure, no disk writes, returns a unified diff) and the real one. The
Flask layer itself does nothing to *require* the frontend call `preview_*` before the real one -
that ordering guarantee is enforced entirely in `gemma-chat.html`'s `executeOneToolCall()` (§4.6),
which always awaits `presentApprovalCard()` between the two. `gemma-server.py` trusts whatever the
frontend sends it; the safety boundary that matters for "was this approved by a human" is
**client-side**, while the safety boundary that matters for "can this touch anything outside the
folder" is **server-side**, inside `agent_tools.py`.

`_get_project_root()` is called by every `/api/agent/*` route except `config` (GET/POST) -
re-reads `agent_config.json` from disk on every single call rather than caching it in memory, so
an external edit to that file takes effect on the very next request with no server restart needed.

`_require_root()` is the one shared guard: every route function starts with
`root, err = _require_root(); if err: return err` - returns a `403`-shaped-in-body (not an actual
HTTP 403, just `{"ok": false, "error": "no project root configured"}` with a `200`) if no root is
configured yet.

---

## 3. Module deep-dive: `agent_tools.py` (sandboxed primitives)

This is the only place in the whole Chat codebase that actually touches the filesystem or spawns
a shell - everything above it (`gemma-server.py`) and beside it (`gemma-chat.html`) is a thin
client of these functions.

### 3.1 Path safety - `resolve_safe_path(project_root, rel_path)`

```
   rel_path starts with "/", "~", "\\"?  ──yes──► PathViolation
   contains "\x00"?                       ──yes──► PathViolation
   root = project_root.resolve(strict=True)
   candidate = (root / rel_path).resolve(strict=False)   ← resolves symlinks on
                                                              EXISTING path components
   candidate.relative_to(root) raises ValueError?  ──yes──► PathViolation ("escapes root")
   first path component == ".gemma_agent"?          ──yes──► PathViolation (reserved)
   else: return candidate
```
The comment in the source is explicit about *why* `.resolve()` happens before the containment
check rather than a string-prefix check on the raw path: a plain `str.startswith(root_str)` check
on the *unresolved* path would not catch a symlink inside the project root pointing somewhere
outside it - `Path.resolve()` dereferences symlinks first, so the containment check that follows
is against the real, final filesystem location.

### 3.2 Backups & atomic writes

```python
def make_backup(root, target) -> Path:
    # copies target's CURRENT content to root/.gemma_agent/backups/<rel-path-dirs>/<name>.<timestamp>.bak
    # BEFORE it gets overwritten

def atomic_write(target, content):
    # write to <target>.tmp-<pid>, then os.replace() onto the real path
    # - a crash mid-write can never leave a half-written file at the real path
```
`write_file()`/`edit_file()` both call `make_backup()` (only if the target already existed) inside
the same `with _write_lock:` block as the actual `atomic_write()` call - `_write_lock` is a
module-level `threading.Lock()` (Flask's dev server runs threaded, so two concurrent requests
touching the same file need this).

### 3.3 Diff generation - `preview_write` / `preview_edit`

Both build a `difflib.unified_diff` between old and proposed content and return it as a string -
`preview_edit` additionally requires the `old_string` to appear **exactly once** in the file
(`n = content.count(old_string); if n > 1: return {"ok": False, "error": "...must be unique..."}`)
 -  the same "old_string must be unique" discipline as a text-based patch tool, chosen so an
ambiguous edit fails loudly instead of guessing which occurrence was meant.

`edit_file()` (the real, mutating version) **re-reads the file and re-validates the uniqueness
check at apply time**, not just at preview time - the comment explains why: the file may have
changed (another tool call, or an external edit) in the gap between the approval card being shown
and the human clicking Approve.

### 3.4 The sandbox - `build_sandbox_profile` / `run_in_sandbox`

```python
def build_sandbox_profile(root) -> str:      # a macOS Seatbelt (sandbox-exec) profile, as text
    return (
      "(version 1)\n"
      "(allow default)\n"                     # ← everything allowed by default...
      "(deny file-write*\n"
      "  (require-not (require-any\n"
      f'    (subpath "{root}")\n'              # ...EXCEPT writes are denied unless inside root...
      f'    (subpath "{tempdir}")\n'            # ...or the system temp dir (interpreters cache here)...
      f'    {literal_exceptions}\n'             # ...or /dev/null, /dev/tty, /dev/dtracehelper
      "  )))\n"
      "(deny network*)\n"                      # network denied entirely, no exceptions
    )

def run_in_sandbox(root, command, timeout_s):
    subprocess.run(["sandbox-exec", "-p", profile, "/bin/bash", "-lc", command],
                    cwd=str(root), timeout=timeout_s, capture_output=True, text=True)
```
The module's own comment explains a real design iteration: a `(deny default)`-based allowlist
profile was tried first and rejected because it `SIGABRT`s on ordinary commands (dyld can't read
its own shared-cache/frameworks without an exhaustive, fragile allowlist, verified empirically on
macOS 15.7.4). The `(allow default)` + narrow targeted `(deny ...)` shape is what actually works
while still delivering the real safety property: nothing the sandboxed process runs can write
outside the project root or reach the network, regardless of what it tries.

`run_in_sandbox` is a **blocking** `subprocess.run()` call - this is fine because `gemma-server.py`
is `threaded=True`, so one long-running command doesn't block other Flask requests, but it does
mean this one request thread is parked for the command's full duration (up to `MAX_TIMEOUT_S =
300`).

### 3.5 Snapshots

```python
def snapshot_project(root) -> dict:
    # zips the whole project root (excluding .gemma_agent, .git, node_modules, .venv)
    # into root/.gemma_agent/snapshots/pre-run-<timestamp>.zip, BEFORE a run_command call
    # skipped entirely if the root exceeds SNAPSHOT_SIZE_CAP (100MB)
```
Called from `gemma-server.py`'s `run_command` route, unconditionally, before `run_in_sandbox` -
a second, coarser safety net beyond the per-file backups in §3.2, since a shell command could touch
many files at once in ways `write_file`/`edit_file`'s per-call backup can't anticipate.

### 3.6 The "toggle between" helper script - `write_run_script`

```python
RUN_SCRIPT_TEMPLATE = '''#!/bin/bash
set -e
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"
exec sandbox-exec -p '{profile}' /bin/bash -lc "$*"
'''
```
Regenerated every time the project root is (re)set (called from `gemma-server.py`'s
`agent_config_post`) - writes `root/.gemma_agent/run-sandboxed.sh`, letting a human run the exact
same sandbox profile by hand from a real terminal, outside the chat UI, for debugging or manual
verification of what the AI would/wouldn't be able to do.

---

## 4. Module deep-dive: `gemma-chat.html` - logical submodules within one file

Since this is one file, "module boundaries" here means: a named region of the `<script>` block
with its own state and a stable calling contract with the rest of the file. Line numbers refer to
the file as of this writing and will drift as it's edited further - treat them as approximate
anchors, not permanent addresses.

### 4.1 Boot & connection (`~1240–1440`)

```
loadSettings() ──► autoConnect() ──► probeOllama() [only if provider==ollama]
                       │                    │
                       │             fetchModels(endpoint) → GET {endpoint}/api/tags
                       │                    │
                       ▼                    ▼
                setBootTabUI(provider)  populate #boot-model-select,
                                        set #status-dot/#status-text
                       (user clicks Connect)
                       ▼
                  startChat() → reads all boot-screen fields into genParams/providers/
                                 currentProvider/modelName, calls saveSettings()+enterApp()
                       ▼
                   enterApp() → hides #boot-screen, shows the chat UI, starts VRAM polling
```
`switchBootTab(provider)` re-probes Ollama on **every click**, not just once at page load - the
comment is explicit that this is a deliberate requirement: the connection status must always
reflect whether Ollama is reachable *right now*, not a stale check from when the page first
loaded.

### 4.2 Sessions & memory (`~1177–1240`, `~1763–1857`)

```
sessions: {id: {id, name, messages[], provider, model, summary?, ...}}   ← the whole app's state
STORAGE_KEY → localStorage, via saveSessions()/loadSessions()
```
`buildNeutralApiMessages(session, newUserContent)` (§4.3 depends on this) is the pivot point
between "what's stored" and "what's sent to a provider": it takes the session's message history,
prunes/injects a rolling summary if one exists (`session.summary` gets injected as a synthetic
leading system message: `"Conversation summary so far (older messages compressed): ..."`), and
returns a **provider-neutral** message array that every one of the three `stream*` functions
(§4.3) then separately reshapes into that provider's wire format.

`maybeSummarize(session, promptEvalCount)` - fire-and-forget, called at the end of every
successful turn in `runChatTurn()` (§4.4). Once token usage crosses `summaryThreshold` (60% of the
model's context window), it asks the model itself to compress older turns into `session.summary`
and marks messages older than `summaryKeepRecent` (6) turns as summarized - this is why the header
shows a **∑** flag once a session has an active summary.

### 4.3 Multi-provider streaming abstraction (`~1921–2115`)

This is the cleanest "submodule" boundary in the file - a single dispatch function plus three
interchangeable implementations, all normalized to the same callback contract:

```
sendChatRequest(provider, opts, {onTextDelta, onDone})
        │
   ┌────┼────────────────┬──────────────────┐
   ▼                      ▼                  ▼
streamOllama()      streamOpenAI()      streamAnthropic()
 NDJSON lines        SSE, "data: " lines  SSE, NAMED events
 (/api/chat)         (/chat/completions)   (Anthropic Messages API)
```
Each implementation is responsible for:
1. Converting the neutral message array into that provider's exact wire shape
   (`toOpenAIMessage()`, `toAnthropicMessages()`/`toAnthropicTools()` - Ollama needs no conversion
   since the neutral format already matches Ollama's own shape).
2. Parsing that provider's specific stream framing.
3. Calling `onTextDelta(chunk)` for every text token and, at the end,
   `onDone({toolCalls, promptEvalCount, evalCount, evalDuration})` - **identical shape regardless
   of provider** - this is what lets `runChatTurn()` (§4.4) stay completely provider-agnostic.

Provider-specific quirks each implementation has to paper over, called out in the file's own
comments:
- **Ollama**: tool_calls arrive as one complete array per line, not fragmented (verified live,
  flagged as "the one spot to revisit if that changes").
- **OpenAI-compatible**: tool_calls are fragmented across chunks *by index*, and `arguments`
  streams in token-by-token as a raw string that has to be accumulated and JSON-parsed only once
  complete (`toolBuf[idx] = {id, name, argsStr}`).
- **Anthropic-compatible**: tool results have to be grouped into one following `user` message
  containing multiple `tool_result` content blocks, unlike Ollama/OpenAI's one-message-per-result.

### 4.4 The chat turn engine (`~2206–2408`)

`runChatTurn(session, msgDiv, liveTextEl, thinkingBlockId, iterCount, originalUserText)` is the
central orchestrator - everything else in the file either feeds into or reacts to this one
function:

```
buildNeutralApiMessages(s)
     │
sysContent = systemPrompt + (THINKING_INJECT if thinkingEnabled) + (AGENT_INJECT if agentActive)
tools = agentActive ? buildAgentTools() : null
     │
     ▼
sendChatRequest(provider, {...}, {onTextDelta, onDone})
     │  onTextDelta: accumulate fullRaw, parseThinkingStream() splits <think>...</think> from
     │               the answer if thinkingEnabled, renderLiveState() repaints both incrementally
     │  onDone: capture toolCallsAccum + token stats
     ▼
finalAnswer computed, rendered, timer/token-stat UI updated
     │
     ├── toolCallsAccum present AND agentActive? ──► handleToolCalls(...) [§4.6] - DOES NOT
     │                                                finalize this turn as the real answer
     │
     └── else: normal finalize - push to s.messages, saveSessions(), maybeSummarize(),
               and IF reflectConfig.enabled: runReflectionLoop() [§4.7] replaces the saved
               answer with the reflected one before the turn is considered done
```
`parseThinkingStream(raw)` is a small state machine run on every incoming chunk (not just once at
the end): it looks for `<think>`/`</think>` in the accumulated raw text and splits it into
`thinkingText`/`answerText`, tracking whether the model is still mid-think-block so the UI can
show a live "reasoning…" placeholder before any answer text exists yet.

### 4.5 Rendering pipeline (`renderMarkdown`, `postProcessRendered`, `extractAndRegisterArtifacts`)

```
raw markdown text
     │
renderMarkdown(text, isAssistant)   → HTML string (custom lightweight markdown parser,
     │                                  not a full CommonMark implementation - handles the
     │                                  subset this app's own prompting relies on: headers,
     │                                  code fences, lists, bold/italic, inline code, links)
     ▼
liveTextEl.innerHTML = <that HTML>
     │
applyHljs(container)                → hljs.highlightElement() on every <pre><code> not
     │                                  already highlighted
     ▼
postProcessRendered(container)      → finds MathJax delimiters and calls
     │                                  MathJax.typesetPromise(); finds ```mermaid fences and
     │                                  calls mermaid.render(); finds ```plot fences and calls
     │                                  Plotly.newPlot()
     ▼
extractAndRegisterArtifacts(text)   → scans for the artifact code-fence syntax, stores each
                                        one in `artifacts[id]`, and if the side panel is open,
                                        renderArtifactPanel() shows the live HTML/SVG preview
```

### 4.6 Agent tool loop + approval (`~2408–2575`)

Already documented mechanically in `REFERENCE.md`'s Agent Tools section; the internal call graph:

```
runChatTurn() detects toolCallsAccum
     │
     ▼
handleToolCalls(s, assistantContent, toolCalls, iterCount, originalUserText)
     │  iterCount >= agentConfig.maxIter? → stop with a warning message, don't loop further
     │  push the assistant's tool_calls turn to s.messages
     ▼
for each call: executeOneToolCall(call)
     │  name in READ_ONLY (list_dir, read_file)?
     │      → agentFetch('/api/agent/list_dir'|'read_file', ...) directly, no approval
     │  name in WRITE (write_file, edit_file, run_command)?
     │      → agentFetch('/api/agent/preview_*', ...) first
     │      → presentApprovalCard(kind, preview) - returns a Promise that only resolves
     │        when resolveApproval(id, decision) is called by a real button click
     │      → decision === 'approved'? → agentFetch the REAL (mutating) route
     │      → decision === 'rejected'/'aborted'? → return without ever calling the real route
     ▼
push a {role:'tool', tool_call_id, content: JSON.stringify(result)} message per call,
renderToolResultChip(name, result) for each
     │
     ▼
if any call was aborted (stopGeneration() clicked mid-approval): stop, don't continue the loop
if backend unreachable: stop with an error message
else: createAssistantBubble() + runChatTurn(..., iterCount+1, ...) - RECURSES so the model
      sees the tool results and can respond or make another tool call
```
`presentApprovalCard()`'s `Promise` never resolving on its own is the actual mechanism that makes
approval a **hard** gate rather than a prompt-level suggestion: `executeOneToolCall()` is `await`ing
that promise, so no code path can reach the real mutating `agentFetch()` call without
`resolveApproval()` having been invoked by an actual DOM click handler
(`onclick="resolveApproval('${id}','approved')"`).

### 4.7 Reflect Mode (`runReflectionLoop`, referenced `~3218+`)

Triggered from `runChatTurn()`'s normal-finalize path, only if `reflectConfig.enabled`. A **Judge**
model scores the **Solver**'s answer; if below `reflectConfig.threshold` (default 80), the Solver
retries with the Judge's feedback appended, up to `reflectConfig.maxIter` (default 5) attempts.
Solver and Judge each independently resolve to any configured provider/model via
`getAllModelsForProvider()`/`callLLM()` - a small provider-neutral wrapper around the same
`sendChatRequest()` used by the main chat turn, but used here for single non-streaming
judge/solver calls rather than the live chat UI.

### 4.8 Live instrumentation (`pollVRAM`, `startTimer`/`updateETA`, `updateContextMeter`)

- `pollVRAM()` - every second (while `vramInterval` is active), `GET {endpoint}/api/ps`, sums
  loaded models' VRAM, color-codes the bar (green/yellow/red) against a rough capacity heuristic.
  **Ollama-only** - hidden entirely for the other two providers, since there's no equivalent
  metric to poll from a remote API.
- `startTimer()`/`updateETA(tokensGenerated, elapsedMs)` - a live elapsed-time + naive
  linear-extrapolation ETA shown during generation, replaced with real `tok/s` stats once
  `onDone()` delivers `evalCount`/`evalDuration` (Ollama-only fields; other providers show a
  simpler `N tokens · Ns` since they don't report timing at that granularity).
- `updateContextMeter(promptEvalCount)` - fills the small bar next to the input box from the most
  recent turn's actual prompt token count against the configured `num_ctx`.

---

## 5. Module deep-dive: `coach-server.py`

```python
MODEL = "large-v3"; LANGUAGE = "en"; PORT = 9091
DEVICE = "mps" if torch.backends.mps.is_available() else ("cuda" if ... else "cpu")
model = whisper.load_model(MODEL, device=DEVICE)     # loaded ONCE at process start, blocks startup

GET  /                → send_from_directory(HERE, "gemma-coach.html")
GET  /health           → {"ok": true, "device": DEVICE, "model": MODEL}
POST /transcribe        → the only real endpoint (see below)
```

**`/transcribe` - the anti-hallucination pipeline**, in exact order:
```
1. Save uploaded blob to a NamedTemporaryFile (keeps original extension - Whisper reads via
   ffmpeg from a real file path, not a Python buffer)
2. whisper.load_audio(path) → float32 mono @ 16kHz
3. GATE 1 (energy): rms = sqrt(mean(audio**2)); rms < SILENCE_RMS (0.008)?
       → return {"text": "", "skipped": "silence"} WITHOUT ever calling the model
4. model.transcribe(audio, temperature=0.0, condition_on_previous_text=False,
                     no_speech_threshold=0.6, logprob_threshold=-1.0,
                     compression_ratio_threshold=2.4)
       temperature=0.0            → greedy decoding, no random sampling/"improvisation"
       condition_on_previous_text → False: each ~7s chunk is transcribed independently,
                                     preventing hallucinated context from one chunk bleeding
                                     into the next
5. GATE 2 (per-segment confidence): for each segment in result["segments"]:
       drop if no_speech_prob > 0.6, OR avg_logprob < -1.0, OR compression_ratio > 2.4
       (compression_ratio is Whisper's own repetition signature - a classic hallucination
        pattern is looping the same phrase, which compresses unusually well)
6. join surviving segments' text, return {"text": ..., "rms": ...}
```
`_lock = threading.Lock()` serializes every call to `model.transcribe()` - the module comment
notes the MPS-backed model is not safe for concurrent `transcribe()` calls, and the frontend
already uploads chunks serially anyway, so this is "belt-and-suspenders," not the only thing
preventing concurrent access.

---

## 6. Module deep-dive: `gemma-coach.html`

```
toggleMic() ──► getUserMedia() ──► cycle()
                                       │
                          MediaRecorder records for ~7s, a FRESH recorder
                          each cycle (not one long-running recorder) -
                          comment: "self-contained decodable webm" - a
                          single long recording can't be decoded in
                          isolated chunks by Whisper/ffmpeg, but N
                          independent short recordings can
                                       │
                          onstop → enqueue(blob, speaker, ext) → drainQueue()
                                       │
                          drainQueue(): POST the blob to coach-server.py's
                          /transcribe, append result to the transcript,
                          renderTranscript()
                                       │
                          if micOn: cycle() again immediately - the loop
                          is self-perpetuating while the mic is on
```

Separately, `startCoachTimer()` runs `setInterval(() => runCoach(false), COACH_MS)` (`COACH_MS =
15000`) - **not** tied to the mic-chunk cycle above; every 15 seconds (or immediately on manual
**✧ Go**, `runCoach(true)`), `recentTranscript()` + the still-unchecked entries in `points[]` are
sent to the configured model, which is prompted to return **structured JSON** (parsed by
`safeJson()`, tolerant of a model wrapping its JSON in prose or code fences), applied to the UI by
`applyCoach(parsed)`.

`points[]` (talking points, each `{id, text, done}`) and the transcript are both plain in-memory
arrays - **there is no `localStorage` persistence in Coach**, unlike Chat's sessions; a page
refresh loses the current conversation's transcript and points (this is a real difference from
Chat, not an oversight to assume away).

---

## 7. Launch scripts - orchestration, not application logic

### 7.1 `Gemma Chat.command` → `launch-gemma.sh`
```bash
cd "$(dirname "$0")"; bash launch-gemma.sh
```
```
1. Prompt for model choice (E4B / 26B / both)
2. curl -s :11434/api/tags reachable? → skip; else `ollama serve &`, poll up to 15s
3. Warm the chosen model(s): POST :11434/api/generate with empty prompt + keep_alive=24h (background)
4. lsof -ti tcp:9092 | xargs kill -9   (free the port from any stale prior run)
5. Run gemma-server.py with $PYTHON (default: python3 from the active venv)
6. curl -s :9092/health - confirm it actually started before declaring success
7. open http://localhost:9092/
8. trap SIGINT/SIGTERM → kill the server process on Ctrl+C; Ollama is deliberately left running
```

### 7.2 `launch-coach.sh`
Same shape as §7.1, with two differences: it needs a Python env with
`torch`/`whisper` installed (the active venv, or a conda env named by `$CONDA_ENV`)
before running `coach-server.py`, and it polls `:9091/health` in a background subshell *while*
`coach-server.py` is still loading (since Whisper model load can take a while on first run) rather
than requiring it to already be healthy before opening the browser.

---

## 8. File-level dependency graph

Two independent axes: the **Python import graph** (small - only two files import anything from
each other in this repo) and the **runtime graph** (large - HTTP calls and process spawns connect
almost everything, with zero corresponding import statement).

### 8.1 Python import graph

```
Level 0 (stdlib + third-party only)
├── agent_tools.py     (os, shutil, subprocess, threading, time, zipfile, datetime, pathlib)

Level 1
└── gemma-server.py    imports agent_tools  (as `at`)            (+ flask, json, pathlib, threading)
                        ← the ONLY file in this repo that imports another local file

coach-server.py         (no local imports - stdlib + torch, whisper, numpy, flask)   Level 0
```
That's the entire Python import graph: **one edge**, `gemma-server.py → agent_tools.py`.
`coach-server.py` and both `.html` files have zero import/require relationships with anything else
in the repo.

### 8.2 Runtime dependency graph (HTTP calls, process spawns - no import involved)

```
Gemma Chat.command
     │ spawns (bash)
     ▼
launch-gemma.sh
     │ spawns (subprocess exec)          │ spawns (subprocess exec)
     ▼                                    ▼
ollama serve (external binary)      python3 gemma-server.py
                                           │ imports (Python-level, §8.1)
                                           ▼
                                     agent_tools.py
                                           │ serves as a static file (no import)
                                           ▼
                                     gemma-chat.html
                                           │ opened in browser; ALL of the following are
                                           │ runtime fetch() calls, none are imports:
                              ┌────────────┼─────────────────────────┐
                              ▼            ▼                          ▼
                    GET/POST :11434   GET/POST :9092/api/agent/*   (if configured)
                    (Ollama, direct)  (gemma-server.py routes,     Custom/OpenAI-
                                       which then call into         compatible base URL,
                                       agent_tools.py functions)     external, direct
```
```
launch-coach.sh
     │ spawns (subprocess exec, inside the Whisper env)
     ▼
python coach-server.py
     │ serves as a static file (no import)
     ▼
gemma-coach.html
     │ opened in browser; runtime fetch()/getUserMedia() calls:
     ┌───────────────┴────────────────┐
     ▼                                 ▼
GET/POST :11434 (Ollama, direct,   POST :9091/transcribe
for the periodic coaching          (coach-server.py → Whisper,
suggestions, via callLLM()-        which coach-server.py imports
equivalent logic in the file)      at Python level - see §8.1)
```

### 8.3 Full "who depends on whom, at what level" summary table

| File | Depends on (import) | Depends on (runtime - HTTP/process) | Level |
|---|---|---|---|
| `agent_tools.py` | - | - | 0 |
| `gemma-server.py` | `agent_tools.py` | Ollama not called by this file at all - model inference bypasses it entirely | 1 (import) |
| `coach-server.py` | - | - | 0 (import); depends on nothing local at runtime either - it's a pure backend the frontend calls into |
| `gemma-chat.html` | - (no imports; not even ES modules) | Ollama `:11434` **directly**, `gemma-server.py` `:9092/api/agent/*` **only when Agent Tools is enabled**, optionally an external Custom/OpenAI-compatible API | runtime-only, 2 |
| `gemma-coach.html` | - | Ollama `:11434` directly (coaching suggestions), `coach-server.py` `:9091/transcribe` (mic chunks) | runtime-only, 2 |
| `launch-gemma.sh` | - | spawns `ollama serve`, spawns `python3 gemma-server.py`, `open`s a browser URL | orchestration (process level) |
| `launch-coach.sh` | - | spawns `ollama serve`, spawns `python coach-server.py` (inside a conda env), `open`s a browser URL | orchestration (process level) |
| `Gemma Chat.command` | - | spawns `launch-gemma.sh` | orchestration (process level) |

**The one fact worth internalizing from this table**: `gemma-server.py` is *not* on the critical
path for actually talking to the AI model at all - that's a direct browser→Ollama (or
browser→external API) connection with zero server-side involvement. `gemma-server.py`'s entire
reason to exist is serving the static HTML file and hosting the four Agent Tools mutation-safety
routes (`preview_write`, `write_file`, `preview_edit`, `edit_file`) plus the sandboxed
`run_command` route - turn Agent Tools off, and `gemma-server.py` becomes, functionally, just a
static file server. The same is true of `coach-server.py` relative to Coach's periodic coaching
suggestions (those go straight to Ollama) - it exists purely to host the Whisper transcription
pipeline that a browser cannot run on its own.
