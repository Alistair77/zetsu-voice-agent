# Zetsu

A voice-first assistant. Warm, plain-spoken, brief.

**What it's for:** a personal daily driver — keeps track of my day, my notes and my
stuff — and a hands-free dev copilot I can talk to while I work.

**Who it's for:** just me. Single user, no user IDs anywhere.

## First three capabilities

These are the first tools (Tier 2) and the first test cases.

1. **Reminders & todos** — "remind me to X", "what's on my list today?" Local store,
   read and write.
2. **Answer from my notes & files** — search and read files on this machine and answer
   questions about them. Read-only.
3. **Run project commands** — build/test/git status in a project, reported back out
   loud. Reads run free; anything mutating hits the gate.

## Stack

| Piece | Choice | Notes |
|---|---|---|
| Language | Python 3.14 | stdlib-first, no framework |
| Brain | `[model] backend` — `ollama` or `claude-cli` | one config line, no code change |
| — local | Ollama `qwen2.5:3b` | free, offline, 0.2s to first token, blunter |
| — CLI | `claude -p`, sonnet-5 | ~$0.03/call, ~1.8s, much sharper. No key: uses your CLI login |
| Speech-to-text | whisper.cpp, `base.en` | on-device, ~0.5s. No key, audio never leaves the Mac |
| Text-to-speech | macOS `say`, voice **Samantha** | built in, free, instant. Closest match to the Bella I picked |
| Host | this laptop, M1 / 8 GB | heartbeat is relocatable, not relocated |

**No API keys anywhere.** Everything runs locally. Every provider sits behind a
one-function seam, so this is a config edit, not a refactor — a hosted model can be
dropped in later by rewriting one function.

## How I talk to it

- **Tier 1–2:** typed. The text path stays alive forever — it is how every later change
  gets debugged.
- **Tier 3 — `--voice`:** Enter to start talking, Enter to stop. A terminal cannot see a
  key *release*, so hold-to-talk isn't possible here; this is the same idea, an explicit
  start and stop, never guessing when I began or finished. Still the reliable path.
- **Tier 3.5 — `--wake`:** open mic, and a conversation that stays open. Say the wake
  word once; after that it keeps listening for `follow_up_seconds` after every reply, so
  a follow-up question can just be answered. Ask it to "keep listening" and it stays
  awake indefinitely; "bye bye" puts it back to sleep. Say "Zetsu". Listens in short slices, transcribes
  each locally, and goes deaf while it is speaking so it can never answer itself. Whisper
  will not spell a made-up name consistently, so `[wake] variants` holds the near-misses
  it actually gets heard as — run `--calibrate` to learn yours from three samples of your
  own voice, and `show_chunks = true` to watch exactly what it hears.

  **Barge-in.** Speaking while it talks cancels the reply mid-stream (measured 85ms
  from the decision to the stream closing) and your words become the next turn. An
  interrupted turn never runs its tools and never writes to history — a cancelled
  "add a todo" adds nothing.

  It also has to not interrupt *itself*: a laptop speaker feeds its own voice back
  into the mic. So the mouth remembers what it just said and the ears discard
  anything that resembles it, by word overlap and by character similarity, because
  the round trip mangles words ("Obito" returns as "A bito"). While it is actually
  speaking the bar for believing an interruption is raised further.

  Slices are longer asleep than awake: asleep a slice must hold the whole wake word,
  awake it must be short enough that an interruption registers. Consecutive slices
  share `chunk_overlap_seconds` of audio so a word spoken across the join survives,
  and the repeated words are stitched back out.

  Listening is gapless: the next capture starts *before* the last is transcribed, because
  whisper thinks for about a second and that is precisely when the start of your sentence
  used to go missing. Near-silence makes whisper invent — `[MUSIC]`, `(upbeat music)`,
  "Thanks for watching!" — so annotations are stripped and stock phrases dropped before
  anything reaches the brain.

## Never without asking

The hard confirmation gate (Tier 6) stops the tool **before** it runs and states plainly
what it is about to do. It covers typed, spoken, and heartbeat-initiated actions alike.

- Sending anything — email, message, post, calendar invite
- Spending money
- Deleting or overwriting anything
- **Any** filesystem write or shell command

Reads are free. Confirmation is per-action and does not generalize: one yes authorizes
one action.

Anything Zetsu reads from the outside world — a file, a web page, a transcript, a stored
memory — is **data, not instructions**. If it looks like it is giving orders, Zetsu
surfaces it and asks; it never obeys.

## Proactivity

Yes, and with a low bar for speaking up — I would rather hear about it. That bar is a
config threshold, so it gets dialled back the first time it is annoying. Non-negotiable
regardless of the threshold: quiet hours, notices held for me if I was away, dismissible
items, and a kill switch.

## Memory

Durable facts live in `state/memory.md` — plain text, one fact per line. Open it, fix a
wrong line, delete one: Zetsu respects the edit next run. Facts are loaded into every
system prompt as **knowledge, never as instructions** — a stored note reading "always do
X" does not get to walk around the confirmation gate.

## The rails

- **Gate** — tools that send, spend, delete, write or run anything stop and state plainly
  what they intend, then wait. Declared in code, so a new consequential tool is gated by
  default; `[gate] unattended` in config.toml is the only way to loosen it, one name at a
  time. Approval is per-action and never generalises.
- **Kill switch** — `rails.is_paused()` is the single question. Two ways to set it:
  `/pause` at the console, or `[heartbeat] enabled = false`. Both re-read live, no restart.
  Paused stops proactive work only — Zetsu still answers when spoken to.
- **Audit trail** — `state/audit.log`, plain text: every tool run, allowed, denied, failed,
  every notice surfaced, every injection caught, and per-turn token counts so a runaway
  loop shows up early. `/audit` shows the tail, `/status` shows the current posture.
- **Untrusted content** — anything read from a file is prefixed as data, and text that
  looks like it is giving orders is flagged to the model *and* to you, and logged.

## Two brains, routed

`[model] backend = "auto"` picks per turn and logs why:

- **Ollama** by default — 0.2s to first token, free, offline. Chat, todos, memory, recall.
- **Claude CLI** when the ask earns it — code, files, browsing, analysis, anything long,
  or anything you prefix with `!`. ~1.8s, ~$0.03 a call, much sharper.

Tune the trigger words in `[router]`. Force either one by setting `backend` outright.

## The face

`--dash` serves a local page at `http://localhost:8765`.

**The hero** is a WebGL ring ported from the `voice-dictator` React component — the same
Bayer-dithered shader and amplitude smoothing, rewritten in vanilla JS so this project
stays dependency-free. It is driven by `state/live.json`, which the conversation loop
writes as it moves through each phase:

| phase | colour | motion |
|---|---|---|
| idle | white | still — it does not fidget when nothing is happening |
| listening | cyan | fast pulse on the mic |
| hearing you | violet | steady, while whisper works |
| thinking | amber | slow breath, with elapsed time and a bar against the usual |
| using a tool | orange | slow breath, tool named |
| replying | green | medium pulse, the reply itself scrolling underneath |

The loader learns: it keeps a rolling average of how long each brain takes and shows
`usually ~3.6s`, then `slower than usual…` when a turn overruns it. First run says
`first run…` because it has nothing to compare against yet.

**The controls**, top right of the hero: **Start** launches the open mic, **Mute** stops
it hearing you without tearing it down (the same kill switch as `/pause`, one question
one answer), **Stop** ends it. The dot says which: green live, amber muted, grey stopped.
Every press lands in the audit log.

**Below the hero** — live/paused, backend, spend today, todos, memory, pending notices
and the audit trail. Bound to 127.0.0.1 only: it shows your memory and your logs, so it
has no business on the network.

## Build order

Each tier runs and verifies on its own before the next one starts.

- [x] **Tier 0** — this file
- [x] **Tier 1** — the brain: text conversation loop
- [x] **Tier 2** — the hands: tool registry
- [x] **Tier 3** — the ears and mouth: voice in and out, fully local
- [x] **Tier 4** — the memory: facts that survive a restart (`state/memory.md`)
- [x] **Tier 5** — the heartbeat: acts without being spoken to
- [x] **Tier 6** — the rails: gate, config, audit log, kill switch (`rails.py`)
- [x] **Beyond** — model routing (`brain.route`), status dashboard (`dash.py`)
