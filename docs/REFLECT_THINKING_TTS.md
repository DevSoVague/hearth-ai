# Gemma Chat - Reflect Mode, Thinking, and TTS: how they actually work

A focused deep-dive into three independent subsystems inside `gemma-chat.html` that people
usually lump together as "the AI reasoning/voice stuff." They are **not** one mechanism - each
has its own state, its own trigger point in the turn lifecycle, and (importantly) they don't fully
compose with each other. This doc is precise about where they connect and where they silently
don't.

---

## 0. Where each one sits in one chat turn

```
 sendMessage()
      │
      ▼
 runChatTurn(iterCount=0)
      │
      │  sysContent = systemPrompt
      │             + (THINKING_INJECT   if thinkingEnabled)   ──► §2 THINKING
      │             + (AGENT_INJECT      if agentActive)
      │
      │  sendChatRequest(...) streams the reply
      │      onTextDelta: parseThinkingStream() splits <think>…</think> live  ──► §2
      │
      │  finalAnswer computed, message pushed to s.messages
      │
      ├── toolCallsAccum present? → handleToolCalls() → recurse (iterCount+1)
      │                              (Thinking's <think> parsing still applies each
      │                               recursion; Reflect Mode does NOT run until the
      │                               loop finally produces a tool-call-free answer)
      │
      └── no tool calls this turn - the REAL finalize:
              │
              │  if(ttsConfig.auto) ttsSpeak(finalAnswer)              ──► §3 TTS
              │  (fires immediately on the RAW model answer, BEFORE
              │   Reflect Mode gets a chance to revise it - see §4)
              │
              │  maybeSummarize(s, promptEvalCount)   [rolling context summary - unrelated]
              │
              └── if(reflectConfig.enabled) runReflectionLoop(originalUserText, ...)  ──► §4 REFLECT
                      → OVERWRITES the just-saved message's content/displayText with
                        the reflected answer once the loop finishes
```

**The one fact that trips people up**: TTS's auto-speak and Reflect Mode both react to "the
finalized answer," but they fire at different times relative to each other, and TTS never gets a
second chance to read the *reflected* answer - it already spoke (or started speaking) the
pre-reflection draft. See §4.4.

---

## 1. Prompt-injection mechanics common to Thinking and Agent Tools

Both `THINKING_INJECT` and `AGENT_INJECT` are plain string constants appended to `sysContent`
before it's sent as the `system` role message (or Anthropic's separate `system` field) for the
**main chat turn only** - this matters because Reflect Mode's Solver phase builds its own,
separate system prompt from scratch (§4.2) and does not reuse `sysContent`, so a flag like
`thinkingEnabled` has zero effect during reflection iterations unless you read §4.2 closely.

```js
const THINKING_INJECT=`
IMPORTANT: Before answering, reason through the problem step-by-step inside <think>...</think>
tags. Show your full reasoning process. Then provide your final answer OUTSIDE the tags.
Example:
<think>
Let me work through this...
Step 1: ...
</think>
Your clean final answer here.`;
```

This is a **soft prompt convention**, not a model feature - Gemma has no native "reasoning mode"
API the way some hosted models do. The app is entirely responsible for (a) asking the model to
wrap reasoning in `<think>` tags and (b) parsing that convention back out of the raw text stream.
If the model ignores the instruction, nothing breaks - `parseThinkingStream()` (§2.2) just finds no
`<think>` tag and treats the whole response as `answerText`.

---

## 2. Thinking (chain-of-thought)

### 2.1 The toggle

```js
let thinkingEnabled = false;   // persisted via saveSettings()/loadSettings()
function toggleThinking(){ thinkingEnabled=!thinkingEnabled; ...; saveSettings(); }
```
One global boolean, not per-session - it applies to every session equally, for as long as it's on.

### 2.2 Live parsing - `parseThinkingStream(raw)`

Called on **every incoming text chunk** during streaming (`onTextDelta`, inside `runChatTurn`),
not once at the end - this is what lets the UI show reasoning *while it's still being generated*,
not just after the fact.

```
 raw = everything streamed so far (may or may not contain <think>...</think> yet)

 openIdx = raw.indexOf('<think>')
 closeIdx = raw.indexOf('</think>')

 no <think> found at all?
     → answerText = raw; thinkingDone = true            (model didn't use the convention
                                                            this turn, or hasn't started yet
                                                            - indistinguishable at this point,
                                                            resolved once the stream ends)
 <think> found, no </think> yet?
     → thinkingText = everything after <think>
       answerText   = everything before <think> (usually empty)
       inThinkBlock = true, thinkingDone = false          (still reasoning - live "reasoning…"
                                                              placeholder shown, §2.3)
 both found?
     → thinkingText = between the tags (trimmed)
       answerText   = everything after </think>, with anything BEFORE <think>
                       prepended (handles a model that emits a little text, then thinks,
                       then answers - rare but not disallowed)
       thinkingDone = true, inThinkBlock = false
```
This is a **pure re-derivation from the full accumulated string every call**, not an incremental
parser carrying state between calls - simpler and correct at the cost of re-scanning the whole
buffer each chunk (fine at chat-message scale).

### 2.3 Rendering - `renderLiveState()`

- First time `thinkingText` becomes non-empty: injects a collapsible `.thinking-block` into the
  placeholder div (`#thinking-ph-<id>`), with a pulsing dot (`.thinking-pulse.active`) and a
  `▾` chevron - auto-expanded (`open` class) while reasoning is in progress.
- Every subsequent call while still thinking: `tbody.textContent = thinkingText` - plain
  `textContent`, not `renderMarkdown()`, so the reasoning trace is shown as raw text, not
  formatted (a deliberate simplicity/perf tradeoff - reasoning traces can be long and don't need
  syntax highlighting).
- Once `thinkingDone`: pulse stops, label changes from "Thinking…" to "Thinking", and the body
  **auto-collapses** (`open` class removed) - the user has to click the header
  (`toggleThinkingBlock(id)`) to re-expand a finished reasoning trace.
- Meanwhile `answerText` (or the raw stream if `!thinkingEnabled`) is rendered into the main
  message body via the normal `renderMarkdown()` pipeline the whole time - reasoning and answer
  are visually and semantically separate the entire way through.

### 2.4 What gets persisted

```js
s.messages.push({..., thinking: finalThinking || undefined, ...});
```
The final `thinkingText` is saved on the message (`msg.thinking`) alongside the answer - this is
what `exportFullSession()` picks up as `thinking` in its message export, and what
`toggleThinkingBlock()` re-renders from when you switch sessions and come back.

### 2.5 Interaction with Agent Tools / tool-call loops

`thinkingEnabled` stays in effect across every recursive `runChatTurn()` call inside the agent
tool-calling loop (`handleToolCalls` → `runChatTurn(iterCount+1, ...)`) - each iteration gets its
own fresh `<think>` parse. There is no cross-iteration reasoning continuity; each turn's `<think>`
block only covers that turn's own reasoning, not the whole multi-step agent task.

---

## 3. Text-to-Speech (TTS)

Built entirely on the browser's native **Web Speech API** (`speechSynthesis`,
`SpeechSynthesisUtterance`) - no server involvement, no external TTS service, and it stops working
entirely (with a toast: *"TTS not supported in this browser"*) if `'speechSynthesis' in window` is
false.

### 3.1 Config

```js
let ttsConfig = {auto:false, voiceIdx:0, rate:1, pitch:1, volume:1};   // persisted to localStorage (TTS_KEY)
let ttsVoices = [];        // populated from speechSynthesis.getVoices()
let ttsCurrentBtn = null;  // the DOM button currently showing "■ Stop" (only one utterance at a time)
```
`ttsLoadVoices()` populates `ttsVoices` and the voice `<select>` - voices load **asynchronously**
on some browsers (notably Chrome), which is why the app also wires
`window.speechSynthesis.onvoiceschanged = ttsLoadVoices` rather than assuming a synchronous
`getVoices()` call on page load will return anything.

### 3.2 Text cleaning - `ttsCleanText(text)`

Markdown reads terribly out loud verbatim (`**bold**`, `` `code` ``, `# Heading`, `| table | pipes
|`), so every string is scrubbed before being spoken:

```
```code blocks```        → " code block. "
`inline code`             → removed
# headings                → stripped (just the marker, text kept)
**bold** / *italic*       → unwrapped to plain text
[link text](url)          → "link text" only
- bullets / 1. numbers    → markers stripped
| table pipes |           → replaced with spaces
$$block math$$            → " math equation. "
$inline math$              → " equation "
blank lines / extra spaces → collapsed
```
This is a regex pipeline, not a real markdown parser - good enough for speech, not attempting
full CommonMark fidelity (unlike `renderMarkdown()`, §2.3's sibling in the visual pipeline).

### 3.3 Speaking - `ttsSpeak(text, btn)`

```js
ttsStop();                                    // only one utterance ever active - cancel any prior
const clean = ttsCleanText(text); if(!clean) return;
const utt = new SpeechSynthesisUtterance(clean);
if(ttsVoices.length) utt.voice = ttsVoices[ttsConfig.voiceIdx];
utt.rate = ttsConfig.rate; utt.pitch = ttsConfig.pitch; utt.volume = ttsConfig.volume;
if(btn){ btn becomes "■ Stop", clicking it calls ttsStop() instead of ttsSpeak() again }
utt.onend = utt.onerror = ttsClearBtn;        // button reverts to "▶ Speak" either way
speechSynthesis.speak(utt);
```
Two call sites reach this function:
- **Manual**: the "▶ Speak" button on any assistant message → `ttsSpeakBtn(btn)` → reads the
  message's text out of the button's own `data-text` attribute (URI-encoded at render time to
  survive HTML attribute escaping) → `ttsSpeak(text, btn)`.
- **Automatic**: `if(ttsConfig.auto) ttsSpeak(finalAnswer)` - no `btn` argument, so there's no
  button to toggle to "■ Stop"; the only way to interrupt an auto-spoken utterance is the header's
  "TTS: ON/OFF" toggle turning auto-speak off for *future* turns, or clicking any other message's
  Speak button (which calls `ttsStop()` on whatever was playing before starting the new one).

### 3.4 Where auto-speak actually fires

```js
// inside runChatTurn()'s normal-finalize path, right after the message is pushed:
if(ttsConfig.auto) ttsSpeak(finalAnswer);
```
This line runs **before** `maybeSummarize()` and **before** the `reflectConfig.enabled` block -
see §4.4 for why that ordering matters.

---

## 4. Reflect Mode - the Judge/Solver loop

### 4.1 Trigger condition

```js
if(reflectConfig.enabled && finalAnswer && !finalAnswer.startsWith('Error:')){
  const reflectResult = await runReflectionLoop(originalUserText, msgDiv, liveTextEl);
  ...
}
```
Only runs on the **normal finalize path** (no pending tool calls) - a turn that ends in a tool
call never reaches this check until the agent loop eventually produces a tool-call-free answer,
at which point *that* answer is what gets reflected on, not any of the intermediate tool-calling
turns.

### 4.2 Solver phase - a deliberately separate system prompt

```js
let solverMessages = [{role:'system', content:
  (systemPrompt || 'You are a helpful assistant.') +
  '\n\nIMPORTANT: When given feedback from a judge about your previous answer, carefully address
   ALL points raised. Do not ask for clarification - you have everything you need...'}];
```
This does **not** include `THINKING_INJECT` or `AGENT_INJECT` - Reflect Mode's Solver calls
`callLLM()` directly (a plain, non-streaming, no-`tools` request), completely bypassing
`sendChatRequest()`/`buildAgentTools()`/the thinking-parse pipeline. Two concrete consequences:
- **Thinking mode has no effect inside reflection iterations** - even with the toggle on, the
  Solver's answer during reflection is never asked to emit `<think>` tags, and even if it did,
  nothing would parse them out (no `parseThinkingStream()` call anywhere in `runReflectionLoop`).
- **Agent Tools are unavailable during reflection** - the Solver cannot call `list_dir`/
  `read_file`/etc. while iterating; it can only re-answer from the original prompt + prior
  attempt + judge feedback, all as plain text in the message array.

First iteration sends just the original prompt; every iteration after a failed judgment resends
the **full context every time** (original prompt + previous answer + judge feedback), not a
running conversation - the comment in the source is explicit about this being intentional so the
Solver always has complete grounding, not a partial/ambiguous "continue from where you left off."

### 4.3 Judge phase - parsing an unreliable model into a structured verdict

```js
const judgeMessages = [
  {role:'system', content: (reflectConfig.judgePrompt || DEFAULT_JUDGE_PROMPT).replace('THRESHOLD', threshold)},
  {role:'user', content: `ORIGINAL PROMPT:\n${originalPrompt}\n\nCANDIDATE SOLUTION:\n${answer}`}
];
const judgeRaw = await callLLM(reflectConfig.judgeProvider, reflectConfig.judgeModel, judgeMessages, {max_tokens:1024});
```
`DEFAULT_JUDGE_PROMPT` asks for **strict JSON only**: `{"pass": bool, "score": 0-100, "feedback":
"..."}`, scored against four criteria (correctness, completeness, quality, edge cases). Because
models don't reliably emit clean JSON on demand, parsing falls through three strategies in order:

```
1. Regex-extract a { ... } object containing "pass"/"score"/"feedback" anywhere in the raw text,
   JSON.parse it directly.
2. No JSON found? Regex for "score: 65" / "65/100" patterns, and a bare /\bpass\b/i vs /\bfail\b/i
   text search, to reconstruct a judgment from prose.
3. Neither worked? Treat the whole raw response as the feedback text, score=0, pass=false -
   fails safe (never crashes, never silently treats an unparseable judge response as a pass).
```
`passed = judgment.pass || (finalScore >= threshold)` - belt-and-suspenders: even if the judge's
own `pass` boolean disagrees with its `score` (a model inconsistency that does happen), crossing
the numeric threshold is enough to pass.

### 4.4 The default provider split - and why TTS speaks the wrong draft

```js
let reflectConfig = {
  solverProvider: 'ollama',  solverModel: '',                                  // local, free
  judgeProvider: 'custom',   judgeModel: 'claude-sonnet-4-20250514',           // cloud, stronger
  maxIter: 5, threshold: 80, judgePrompt: ''
};
```
The **defaults** deliberately split roles across providers - a local Gemma model solves (fast,
free, private), a stronger cloud model judges (higher-quality critique) - the same "local does the
volume work, cloud does the judgment call" pattern documented elsewhere in this project's Workbench
side-project (`workbench_complete/ARCHITECTURE.md` §... - Gemma-reads/Claude-reasons). Both are
independently reconfigurable to any provider/model via the Reflect Mode settings panel.

This is also the concrete reason TTS's auto-speak (§3.4) can end up reading a **draft that gets
silently replaced**: `ttsSpeak(finalAnswer)` fires immediately after the first (un-reflected)
answer is finalized, while `runReflectionLoop()` - which can take several solver+judge round trips
 -  runs afterward and **overwrites** `s.messages[last].content` with the reflected answer once
done. There is no code path that re-triggers `ttsSpeak()` on the reflected result; if you're
listening with auto-speak on and Reflect Mode enabled, you hear the pre-reflection draft, not the
version that ends up saved and displayed.

### 4.5 Per-iteration history - and what actually gets persisted

Each iteration through the loop pushes one record:
```js
iterations.push({iter, solverModel, judgeModel, answer, score, passed, feedback, error: null});
```
(or `{..., score:null, passed:false, feedback:null, error: err.message}` if that iteration threw).
This `iterations` array is the full audit trail - every attempt's answer and every judge verdict,
not just the final one - returned from `runReflectionLoop()` as `reflectResult.iterations` and
saved onto the message as `msg.reflectIterations`, alongside the older `msg.reflectScore`/
`msg.reflectPassed` (final-attempt-only summary fields that predate the full history). This is
exactly what `exportFullSession()` (see the export feature added this session) surfaces under each
message's `reflect.iterations` - the only place in the app where you can see *every* solver
attempt and judge critique, not just the winner.

### 4.6 Loop termination

```
for iter in 1..maxIter:
    solve → judge
    passed?        → finalAnswer = this answer; BREAK (success)
    iter==maxIter? → finalAnswer = this answer anyway (best-effort - the LAST attempt is used
                                                          even though it never passed)
    error thrown?  → finalAnswer = lastAnswer or a generic error string; BREAK
    (AbortError specifically re-thrown, not swallowed - lets Stop Generation actually stop
     a reflection loop mid-flight, same as it stops a normal streaming turn)
```
There is no "give up and show the original answer" path - even a `maxIter`-exhausted, never-passed
reflection loop replaces the displayed/saved answer with its last (failing) attempt, never falls
back to the pre-reflection draft.

---

## 5. Quick reference - what's independent vs. what composes

| | Thinking | Reflect Mode | TTS |
|---|---|---|---|
| Scope | Global toggle, all sessions | Global toggle, all sessions | Global toggle + per-message manual |
| Persisted where | `localStorage` (SETTINGS_KEY) via `saveSettings()` | `localStorage` (REFLECT_KEY) | `localStorage` (TTS_KEY) |
| Runs during agent tool-call loop? | Yes, every recursive turn | No - only the final tool-call-free turn | N/A (fires once, on final text) |
| Applies inside Reflect Mode's own Solver calls? | **No** (separate system prompt, §4.2) | - | **No** auto-fire on the reflected result (§4.4) |
| What's saved on the message | `thinking` (final reasoning text) | `reflectScore`, `reflectPassed`, `reflectIterations` (full history) | nothing - TTS is fire-and-forget, not persisted |
| Exported by `exportFullSession()` | ✅ `thinking` | ✅ `reflect.iterations` | n/a - not a message-level artifact |
