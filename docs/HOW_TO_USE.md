# Gemma Chat & Gemma Coach - a plain-English guide

This folder contains two apps that let you talk to an AI **on your own computer**, without sending
your conversations to any company's servers. Think of it as your own private ChatGPT.

- **Gemma Chat** - a chat window, like ChatGPT, but private and offline.
- **Gemma Coach** - listens in on a live conversation through your microphone and quietly suggests
  what to say next.

You don't need to know how to code to use either one.

---

## 1. Before you start - what you need

Both apps need a program called **Ollama** installed first - it's the "engine" that actually runs
the AI model on your Mac. Think of Ollama as the AI itself, and Gemma Chat/Coach as the steering
wheel and dashboard on top of it.

1. Download and install Ollama from **https://ollama.ai** (it's free).
2. Open the Terminal app (search for "Terminal" in Spotlight) and type:
   ```
   ollama pull gemma3:27b
   ```
   Press Enter and wait - this downloads the AI model itself (several GB, so it takes a while on
   the first run).

That's it - one-time setup. You won't need to touch the Terminal again after this for Gemma Chat.

---

## 2. Starting Gemma Chat

1. Find the file named **`Gemma Chat.command`** in this folder.
2. **Double-click it.** A black Terminal window will pop up and do some setup - that's normal,
   just let it run.
3. Your web browser will open automatically to the chat app. If it doesn't, it also tells you a
   web address (like `http://localhost:9092`) you can copy into your browser.
4. On the first screen, make sure **"Ollama"** is selected at the top, then click **Connect**.

If it says "Could not reach Ollama," click the small **`?`** next to "Ollama Endpoint" - it tells
you exactly what to type in Terminal to fix it (usually just `ollama serve`).

To stop the app, close that black Terminal window (or press Ctrl+C inside it).

---

## 3. Using Gemma Chat - what everything does

### The basics
- **Type your message** in the box at the bottom and press **Enter** to send. (Shift+Enter adds a
  new line without sending.)
- **New chat** - starts a fresh conversation, keeping your old ones saved in the sidebar.
- **Model dropdown** (top of the screen) - lets you switch which AI "brain" you're talking to,
  if you have more than one installed.

### Attaching things
Click the **📎 paperclip** icon (or just drag a file into the window) to hand the AI:
- A PDF, Word-style text file, or code file - it reads the whole thing.
- A photo - it can look at images if the model supports it.
- A Jupyter notebook - for coders, it reads the code and its results.

### Making it explain its reasoning
Toggle **Thinking** on and the AI will show its step-by-step reasoning in a collapsible box before
giving you its final answer - useful for math, logic, or "why did you say that?" moments.

### Customizing behavior
- **System Prompt** - a note you can write once (e.g. "Always answer like a pirate" or "You are
  my writing coach") that shapes how the AI behaves for that conversation.
- **Settings** - sliders for how creative vs. predictable the AI's answers are (you usually never
  need to touch these; the defaults are fine).

### Diagrams, charts, and math
If you ask for a chart, a diagram, or a math formula, Gemma Chat renders it properly instead of
showing raw code - flowcharts, interactive graphs, and neatly-typeset equations all just appear
inline.

### Artifacts panel
If the AI builds you something like a small interactive webpage or an SVG image, it opens in a
side panel so you can see/use it live, rather than as a wall of code.

### Letting the AI work on your files (Agent Tools) - use with care
This is the most "hands-on" feature. Click **Agent Tools** in the header, and you can point the AI
at **one folder** on your computer. Once turned on, the AI can:
- Look inside that folder and read files.
- Propose changes - but it always shows you the exact change first (like a "before/after"
  comparison) and **waits for you to click Approve** before anything actually happens.
- Every change it makes is automatically backed up first, so you can always undo it.
- It **cannot** see or touch anything outside that one folder, and it cannot access the internet
  through this feature.

Good for: "read my project and write me a README," "find the bug in this file," "clean up this
folder of notes." Leave it off if you just want to chat.

### Reflect Mode - a built-in second opinion
Turn this on and a second AI silently grades the first AI's answer and sends it back for a redo if
it's not good enough - up to a few tries. Good for tasks where accuracy really matters (this uses
more time/resources per answer, so it's off by default).

### Saving your work
- **Export** a conversation to a file you can keep or share.
- **Import** a previously exported conversation back in.
- **Clear Memory** - the AI "forgets" the conversation so far, but your messages stay visible on
  screen (good for starting fresh without losing your history).
- **Clear All** - permanently deletes every saved conversation.

---

## 4. Starting & using Gemma Coach

Gemma Coach listens to a live conversation (in person, or over a call) and gives you quiet
suggestions on what to say next - like a coach whispering in your ear.

**To start it**, you'll need a technical friend to run one command for you (`bash
launch-coach.sh` in Terminal), since it needs a bit more setup than Chat. Once it's running, it
opens automatically in your browser.

**How to use it:**
1. Type in who you're talking to.
2. Add a few **talking points** - things you want to make sure you mention - and check them off as
   you cover them.
3. Click **● Start Mic** and grant microphone access when asked.
4. Talk normally. Every ~15 seconds (or whenever you click **✧ Go**), it reads back the recent
   conversation and gives you a short suggestion for what to say next.
5. Click **■ Stop Mic** when you're done.

Everything is transcribed and processed on your own computer - nothing is sent anywhere.

---

## 5. Common use cases

- **Private brainstorming or writing help** - draft emails, essays, or code without your text
  leaving your machine.
- **"Read this and explain it to me"** - drop in a PDF, a contract, a research paper, or a
  spreadsheet-turned-text-file and ask questions about it.
- **Studying** - turn Thinking mode on and ask it to walk through a math or logic problem
  step-by-step.
- **Coding help with guardrails** - turn on Agent Tools, point it at a project folder, and ask it
  to find bugs or write documentation; you approve every change before it happens.
- **High-stakes conversations** - use Gemma Coach during a negotiation, interview prep, or
  difficult conversation to stay on track with your talking points.
- **Getting a second opinion on an answer** - turn on Reflect Mode for anything where "close
  enough" isn't good enough.

---

## 6. If something goes wrong

| Problem | What to do |
|---|---|
| "Could not reach Ollama" on the boot screen | Click the **`?`** next to "Ollama Endpoint" - it shows the exact fix. Usually: open Terminal, type `ollama serve`, leave that window open. |
| The browser never opens | Look at the black Terminal window - it prints a web address near the bottom; copy that into your browser manually. |
| Coach never opens | It's loading its speech-recognition model, which can take a couple of minutes the first time - just wait, the Terminal window shows progress. |
| Nothing happens when I ask the AI to change my files | Check that **Agent Tools** is turned on and a folder is set - otherwise the AI can only talk, not touch files. |
| I want to start completely fresh | Use **Clear All** in Gemma Chat's session menu (careful - this deletes every saved conversation). |

For a much more technical write-up (for developers), see `REFERENCE.md` in this same folder.
