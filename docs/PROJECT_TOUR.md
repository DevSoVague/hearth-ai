# Gemma4 - Project Tour

A first-look explainer for someone meeting this repo cold. Reads outward from the surface
(what you'd see using it) inward to how it's built and why. If you want the step-by-step
runtime mechanism instead, start with [`WORKFLOW.md`](WORKFLOW.md) and
[`CHAT_AGENT_WORKFLOW.md`](CHAT_AGENT_WORKFLOW.md).

---

## 1. TL;DR

Gemma4 is two local-first web apps that talk to a Gemma model in Ollama on your Mac:
**Gemma Chat**, a full-featured chat client with a sandboxed coding-agent mode and a
Judge/Solver reflection loop, and **Gemma Coach**, a live "angel on your shoulder" that
transcribes your mic with Whisper and nudges you against pre-set talking points every 15
seconds. Everything runs on `localhost` - the only bytes that leave your machine are Ollama
model pulls and, if you switch providers, whatever calls you make to Anthropic-/OpenAI-
compatible APIs.

---

## 2. The problem

The polished cloud chat clients (Claude.ai, ChatGPT) are excellent, but they can't talk to a
model you've spent a weekend pulling into your own VRAM, they can't run code inside *your*
project folder, and they can't listen to *you* while you're on a call. Ollama solves
inference-on-your-machine, but its stock UI is a bare terminal. Everything else that stacks
on top of Ollama is either a chat wrapper (no tools) or a code agent (no chat polish),
almost never both.

The obvious alternative - install a general-purpose local chat like Open WebUI - gets you
streaming and history but leaves the harder pieces missing: no unified diff-and-approve gate
before a model edits your files, no macOS `sandbox-exec` confinement for a model that wants
to run `python3 script.py`, no in-the-loop Judge model that re-scores an answer before you
see it, and nothing at all for the "coach me during a live conversation" use case. Gemma4
is what those extras look like when they're written together rather than glued on.

---

## 3. What it does

**Gemma Chat.** You double-click `Gemma Chat.command`, a Terminal window warms a Gemma model
into VRAM, and a page opens at `http://localhost:9092/`. The boot screen lets you pick a
provider (Ollama by default, or any Anthropic-/OpenAI-compatible endpoint), pick a model
from what you already have installed, tweak sampling knobs, and Connect. From there it feels
like any modern chat client: streaming replies, a sidebar of sessions, drag-and-drop
attachments (PDFs, notebooks, images, code files), math and mermaid and Plotly rendering,
copy-and-download on every code block, a live token/tok-per-sec meter, and a VRAM bar in the
header that turns yellow then red as you fill the card.

Turning on **Agent Mode** for a chosen project folder is where it stops feeling like a
wrapper. The model gets five tools (`list_dir`, `read_file`, `write_file`, `edit_file`,
`run_command`); read-only calls run silently, but every proposed write or command surfaces
as an in-chat approval card with a unified diff (or the exact command). Nothing touches disk
until you click Approve. Every write takes a timestamped backup first, every approved
`run_command` may first zip the whole project into `.gemma_agent/snapshots/`, and every
command runs under `sandbox-exec` with writes confined to the project root and network
denied outright.

**Reflect Mode** stacks on top: after the answer comes back, a Judge model (any provider,
independent from the Solver) scores the reply against a 0-100 threshold (default 80); if it
fails, the Solver retries with the Judge's feedback, up to a configured cap. You see the
final version, not the iterations.

**Gemma Coach.** A separate launcher opens `http://localhost:9091/`. It captures your mic in
rolling ~7 s WebM chunks, ships each one to a local Whisper `large-v3` model for
transcription, and streams the running transcript into the left panel. You add talking
points on the right - the ones you want to make sure you hit. Every 15 seconds (or when you
click **✧ Go**), the recent transcript plus your remaining talking points are sent to Gemma,
which returns structured JSON coaching advice rendered in the "angel on your shoulder"
panel. The mic capture never leaves the machine.

---

## 4. Who uses it

The intended users are hypothetical - this is a personal project, not a deployed product.
The design targets:

- **The developer at their own machine**, who wants a Claude/ChatGPT-shaped UI over their
  local model *and* wants the model to reach into a specific project without giving it
  unfettered shell access. Chat + Agent Mode is aimed at this person.
- **Someone in a high-stakes live conversation** - a job interview, a difficult phone call,
  a sales pitch, a rehearsal - who's prepared talking points and wants a second pair of eyes
  watching whether they're hitting them. Coach is aimed at this person.
- **The tinkerer** who wants to swap providers mid-session (fast local Solver, expensive
  cloud Judge in Reflect Mode) without losing conversation state.

The moment you'd reach for either app: when the cloud tools' privacy story doesn't fit
(sensitive files, live audio), or when the exact model or endpoint matters more than the
polish of the wrapper.

---

## 5. How it's built

```mermaid
flowchart LR
    subgraph Browser
        CH[gemma-chat.html]
        CO[gemma-coach.html]
    end
    subgraph "gemma-server.py (:9092)"
        GS["/api/sessions<br/>/api/agent/*"]
        GS --- AT[agent_tools.py]
    end
    subgraph "coach-server.py (:9091)"
        CS[/transcribe]
        CS --- W[Whisper large-v3]
    end
    O[Ollama · :11434]
    CH -- "/api/chat streaming" --> O
    CH -- sessions · agent ops --> GS
    CO -- "/api/generate" --> O
    CO -- audio blobs --> CS
    AT -- "sandbox-exec" --> SH[bash]
    AT -- "backups + snapshots" --> FS[".gemma_agent/"]
```

- **`gemma-chat.html`** - the entire Chat app, ~4.9 kLOC of vanilla JS in a single file. UI,
  markdown / math / mermaid / plotly rendering, three provider adapters (Ollama, OpenAI-
  compatible, Anthropic-compatible), the agent tool loop, and the Reflect loop all live
  here. Uses [highlight.js](../app/gemma-chat.html#L1287), [pdf.js](../app/gemma-chat.html#L2100),
  [MathJax](../app/gemma-chat.html#L1365), [Mermaid](../app/gemma-chat.html#L1366), [Plotly](../app/gemma-chat.html#L1373),
  [JSZip](../app/gemma-chat.html#L2103) - all loaded from CDNs.
- **`gemma-server.py`** - [Flask](../app/gemma-server.py#L19) on `:9092`. Serves the HTML, persists
  sessions to [`chat-history/sessions.json`](../app/gemma-server.py#L86-L108) with atomic writes and
  20 rolling snapshots, exposes `/api/agent/*` for the tool endpoints and
  [`/api/agent/config`](../app/gemma-server.py#L157-L177) for the project root. Doesn't proxy
  Ollama - the browser talks to Ollama directly.
- **`agent_tools.py`** - the pure-Python module the server imports. Owns
  [path safety](../app/agent_tools.py#L28-L56), [backups](../app/agent_tools.py#L70-L78), [pre-run zip
  snapshots](../app/agent_tools.py#L265-L290), and the
  [`sandbox-exec` seatbelt profile](../app/agent_tools.py#L213-L234). No Flask coupling - trivially
  testable outside the server.
- **`coach-server.py`** - a smaller [Flask](../app/coach-server.py#L19) app on `:9091` that loads
  [Whisper `large-v3`](../app/coach-server.py#L34) on Apple Silicon MPS (falling back to CPU where
  MPS lacks ops), and gates each chunk with an [RMS energy floor](../app/coach-server.py#L74-L76)
  plus [three post-transcription confidence gates](../app/coach-server.py#L91-L98) to strip the
  hallucinated text Whisper produces on silence.
- **`gemma-coach.html`** - Coach's frontend. [Records the mic in 7 s WebM
  chunks](../app/gemma-coach.html#L263), uploads them serially to `/transcribe`, and runs the
  coaching prompt against Ollama every [15 s](../app/gemma-coach.html#L179) or on `✧ Go`.
- **Ollama at `:11434`** - the model runtime. Not part of this repo; both apps assume it's
  running and probe `/api/tags` on load.

---

## 6. The key decisions

- **Decision:** Every write/edit/run is gated behind an in-chat approval card that shows the
  literal diff (or command), and only that click triggers the actual disk write.
  **Why:** giving a local model tool-use is only useful if it can actually change files, but
  a model that quietly rewrites your code across a five-turn loop is worse than no model.
  **Trade-off:** you can't leave the agent to churn autonomously - every non-read tool call
  blocks until you look. The `AGENT_INJECT` system-prompt line is a soft nudge; the approval
  card at [gemma-chat.html:3413-3447](../app/gemma-chat.html#L3413-L3447) is the hard gate.

- **Decision:** `run_command`'s sandbox is `(allow default)` + narrow `deny` rules, not
  `(deny default)` + an allowlist.
  **Why:** the allowlist approach was tried first ([agent_tools.py:200-209](../app/agent_tools.py#L200-L209))
  and rejected - `dyld` couldn't read its own shared cache without an exhaustive, fragile
  profile, so ordinary commands SIGABRT'd. `(allow default)` + `(deny file-write*)` outside
  the project root + `(deny network*)` empirically confines the actually-dangerous
  capabilities without breaking `python3` or `bash`.
  **Trade-off:** reads and process exec inside the sandbox behave normally, so a curious
  command can enumerate the machine (it just can't exfiltrate or corrupt anything).

- **Decision:** `edit_file` requires an `old_string` that appears exactly once, and
  re-validates that uniqueness *at apply time* - not just at preview.
  **Why:** the preview and the click are separated by an unknown human latency; another tool
  call in the same loop, or an external editor, can change the file between them. Blindly
  applying the previewed diff to the now-different file was silently corrupting the wrong
  region.
  **Trade-off:** an external edit between preview and click produces a rejected apply and a
  wasted iteration in the tool loop.

- **Decision:** All three provider backends (Ollama NDJSON, OpenAI SSE, Anthropic named-SSE)
  are normalized to one internal `{onTextDelta, onThinkingDelta, onDone({toolCalls,...})}`
  shape ([gemma-chat.html:2514-2528](../app/gemma-chat.html#L2514-L2528)).
  **Why:** the tool loop, the reflection loop, and the streaming renderer all had to work
  identically across providers, and letting each of them branch on provider was going to
  metastasize.
  **Trade-off:** every new provider needs a translation layer; per-provider quirks (Anthropic
  tool_result blocks needing a preceding tool_use, OpenAI fragmenting tool_calls across
  chunks by `index`) live inside those adapters, not at the call site.

- **Decision:** Chat history is written server-side to
  [`chat-history/sessions.json`](../app/gemma-server.py#L86-L108) with atomic replace and a rolling
  20-snapshot backup vault. `localStorage` is a cache, not the source of truth.
  **Why:** losing chat history to a browser clearing site data or a corrupt tab was a
  regular pre-existing failure mode of local-first chat wrappers.
  **Trade-off:** the app can't run without its Flask backend up - a pure-static deploy isn't
  possible any more (which is fine given `/api/agent/*` requires the backend anyway).

- **Decision:** Coach uses Whisper `large-v3` with a hard RMS gate *before* transcription
  and three confidence gates *after*, rather than trusting Whisper's own thresholds.
  **Why:** Whisper hallucinates fluent, plausible-sounding text on silence and noise -
  exactly the input it gets when nobody's speaking during a 7 s window. Left unguarded,
  every quiet gap became fake dialogue in the transcript.
  **Trade-off:** an unusually quiet speaker can trip the RMS floor
  ([coach-server.py:9](../app/coach-server.py#L9), `SILENCE_RMS = 0.008`) and have their words
  dropped; the value is exposed at the top of the file exactly because it's the knob most
  likely to need per-mic tuning.

- **Decision (contradiction with the README):** The README claims Reflect Mode's rolling
  summarization kicks in at "60% of the model's context window"; the code uses
  `windowThreshold: 0.75` at [gemma-chat.html:1400](../app/gemma-chat.html#L1400) for eviction and a
  separate `summaryThreshold: 0.6` at [gemma-chat.html:1406](../app/gemma-chat.html#L1406) for
  triggering background summarization. Both are true - different fractions do different
  things - but the README collapses them into one number.

---

## 7. What it's good at, what it isn't

**Good at**

- Being a fast, private chat over a local Gemma / Qwen / any Ollama model, with the
  quality-of-life features (streaming, sessions, markdown, mermaid, math, artifacts,
  attachments) that a stock terminal Ollama session doesn't have.
- Letting a model act on *one specific project folder* with a review-every-write gate that
  is actually enforced (the seatbelt profile + the approval card are the real controls; the
  system prompt is not).
- Composing providers - a cheap local Solver with a stronger cloud Judge, or the reverse -
  without losing session state or context.
- Coach's use case: hitting your prepared talking points during a live call without staring
  at a wall of notes.

**Not designed for**

- **Multi-user / multi-machine deployment.** `agent_config.json`, `chat-history/`,
  `.gemma_agent/`, and `localStorage` all assume one operator on one box; there's no auth,
  no per-user isolation, and CORS deliberately allows `null` and `file://` origins.
- **Linux/Windows agent command execution.** `sandbox-exec` is macOS-only
  ([agent_tools.py:237-262](../app/agent_tools.py#L237-L262)); file read/write/edit still work, but
  `run_command` errors out.
- **Truly long conversations without care.** Rolling summarization prevents context blowup,
  but a summary is still lossy. For a long-lived research thread, exporting the session as
  JSON and starting fresh beats hoping the summary preserved the important bits.
- **A polished consumer app.** No installer, no auto-update, no crash reporting; you launch
  it from a `.command` file that opens a Terminal window.

---

## 8. What's next

1. **Ship a proper capability probe for OpenAI-/Anthropic-compatible providers.** Right now
   image attachments are hard-gated to Ollama only ([gemma-chat.html:2911-2918](../app/gemma-chat.html#L2911-L2918))
   because the other providers use different content-block shapes for vision. Wire them up
   per provider so image attachments work everywhere the model itself supports vision.
2. **Persist agent tool history to session state.** Approved diffs currently live in the
   transcript, but the *backup paths* they refer to under `.gemma_agent/backups/` never get
   surfaced back once the chip scrolls away. A small "undo this edit" affordance that
   restores from `backup_path` would be a five-line addition and a large usability win.
3. **Reconcile the two `num_ctx` defaults.** The state initializer says `8192`
   ([gemma-chat.html:1396](../app/gemma-chat.html#L1396)), the boot input and the runtime fallback
   say `32768`. Pick one, delete the other, and delete the comment that describes a value
   the code doesn't set.
4. **Give Coach a per-conversation "goal" system prompt.** The nudge prompt at
   [gemma-coach.html:404](../app/gemma-coach.html#L404) is generic; letting the user pin *the*
   objective of the call (interview / negotiation / rehearsal) would sharpen every nudge.
5. **Replace the `.command` launch with a native menu-bar app.** Long-term - the current
   double-click flow works but leaves a Terminal window open for the life of the session.

---

## 9. Reading order for the codebase

1. [`REFERENCE.md`](REFERENCE.md) - the feature list and the launcher story. Fastest way to
   understand what surface exists.
2. [`gemma-server.py`](../app/gemma-server.py) - 312 lines, one file, all the HTTP endpoints. Get
   the shape of what the frontend can call before opening the frontend.
3. [`agent_tools.py`](../app/agent_tools.py) - 314 lines. Read
   [`resolve_safe_path`](../app/agent_tools.py#L28), [`atomic_write`](../app/agent_tools.py#L81),
   [`build_sandbox_profile`](../app/agent_tools.py#L213), then everything else falls out.
4. [`gemma-chat.html`](../app/gemma-chat.html) - jump to the section-banner comments (they're all
   `// ── SECTION ──`). The order that matters: **CONSTANTS** → **STATE** →
   **AGENT TOOLS** (schema + `AGENT_INJECT`) → **MULTI-PROVIDER STREAMING** →
   **STREAMING SEND** (`sendMessage` and `runChatTurn`) → **AGENT TOOL LOOP**
   (`handleToolCalls` and `executeOneToolCall`) → **APPROVAL CARDS** →
   **REFLECT MODE** (near the bottom).
5. [`coach-server.py`](../app/coach-server.py) - 115 lines. Read the anti-hallucination gates at
   the top of the file and the two-stage filter in `/transcribe`; that's 80 % of what
   makes Coach not-embarrassing.
6. [`gemma-coach.html`](../app/gemma-coach.html) - the [chunk recorder loop](../app/gemma-coach.html#L263),
   the [serial upload queue](../app/gemma-coach.html#L266), and the
   [coach prompt](../app/gemma-coach.html#L404). The rest is UI.
7. [`WORKFLOW.md`](WORKFLOW.md), [`CHAT_AGENT_WORKFLOW.md`](CHAT_AGENT_WORKFLOW.md),
   [`REFLECT_WORKFLOW.md`](REFLECT_WORKFLOW.md),
   [`THINKING_WORKFLOW.md`](THINKING_WORKFLOW.md), [`TTS_WORKFLOW.md`](TTS_WORKFLOW.md) -
   the per-subsystem execution-order docs; go here after you have the shape of the code.
