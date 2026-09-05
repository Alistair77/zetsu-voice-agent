# Zetsu

A voice-first assistant that runs entirely on your own machine. No API keys, no
accounts, no audio leaving the laptop.

Say the wake word, ask for something, and keep talking — it stays listening for a
follow-up rather than making you summon it before every sentence.

```
you  › Friday, what is on my todo list?
zetsu› Your open todos are to buy oat milk and to renew your passport.
you  › How do I take my coffee?          ← no wake word needed
zetsu› With oat milk.
you  › Bye bye.
zetsu› Alright. Say my name when you need me.
```

## What it is

Six layers, each one runnable and testable on its own:

| | |
|---|---|
| **Brain** | a conversation loop with memory of the session |
| **Hands** | a tool registry the model calls — todos, notes search, memory |
| **Ears & mouth** | whisper.cpp in, macOS `say` out, wrapped around the same brain |
| **Memory** | plain-text facts that survive a restart |
| **Heartbeat** | acts without being spoken to, quietly |
| **Rails** | a confirmation gate, an audit trail, and a kill switch |

Nothing here is a framework. It is Python standard library plus local binaries —
**zero third-party Python dependencies**.

## Requirements

- macOS (uses `say`, `afplay` and AVFoundation for audio)
- Python 3.11+ (for `tomllib`)
- [Ollama](https://ollama.com) for the local brain
- `ffmpeg` and `whisper-cpp` via Homebrew

## Setup

```bash
brew install ffmpeg whisper-cpp
ollama pull qwen2.5:3b

# the Whisper model is not in this repo — it is 141MB, over GitHub's limit
mkdir -p models
curl -L -o models/ggml-base.en.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin

python3 -m venv .venv          # only needed if you add dependencies later
```

## Running it

```bash
./.venv/bin/python zetsu.py             # type at it — always the best way to debug
./.venv/bin/python zetsu.py --voice     # push to talk: Enter, speak, Enter
./.venv/bin/python zetsu.py --wake      # open mic, say the wake word
./.venv/bin/python zetsu.py --dash      # http://localhost:8765
./.venv/bin/python zetsu.py --heartbeat # proactive checks
./.venv/bin/python zetsu.py --calibrate # teach it how *you* say the wake word
./.venv/bin/python zetsu.py --selftest  # no network, no mic, no model needed
```

## Two brains

`[model] backend` in `config.toml` picks who thinks:

| | first token | cost | notes |
|---|---|---|---|
| `ollama` | ~0.2s | free | offline, blunter, streams |
| `claude-cli` | ~1.8s | ~$0.03/call | sharper, needs the Claude CLI, does not stream |
| `auto` | — | — | local by default, escalates on the `[router]` rules |

## Safety

Anything that sends, spends, deletes, writes a file or runs a shell command stops
and asks first, stating plainly what it intends to do. That is decided in code, so
a new consequential tool is gated whether or not anyone updates the config;
`[gate] unattended` is the only way to loosen it, one tool at a time.

Anything read from the outside world is data, never instructions. Text that looks
like it is giving orders gets flagged to you and to the model, and logged.

`state/audit.log` records what ran and why. `/pause` — or the Mute button on the
dashboard — stops all proactive behaviour without tearing anything down.

## Configuration

Everything tunable lives in `config.toml`: the model, the voice, the wake word and
its near-misses, quiet hours, router rules, and which tools need confirmation.
Read `AGENT.md` for what was built and why.
