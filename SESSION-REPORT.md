# Zetsu — Session Report

What was built, what was measured, what broke, and what to do next.

---

## 1. The headline result

A complete voice turn went from **2865 ms to roughly 500 ms**.

| stage | before | after |
|---|---|---|
| you stop talking → endpoint detected | ~1670 ms | **0 ms** |
| endpoint → transcript ready | 0 ms | **214 ms** |
| transcript → first token | — | **64 ms** |
| first token → first sound | 869 ms | **~226 ms** |
| **heard-to-heard** | **2865 ms** | **~500 ms** |

Other measured wins:

| | before | after |
|---|---|---|
| Speech recognition per slice | 1260 ms | **150 ms** |
| Silence between spoken fragments | ~600 ms | **9 ms** |
| Self-interruptions per conversation | 5 | **0** |
| RAM held after quitting | 2.2 GB | **0** |
| Lines of code in the capture path | — | **124 fewer** |

---

## 2. The single most important lesson

**Nothing in the system was ever slow. The waiting was the entire cost.**

Benchmarked warm on an idle machine:

```
VAD level + tail decision        2 ms
STT, 2.5s slice -> text        118 ms
LLM, request -> first token     64 ms
TTS, 15-char fragment           51 ms
-------------------------------------
all computation                235 ms
```

235 ms of computation inside a 2865 ms turn. Everything else was the architecture
waiting for fixed-length audio windows to elapse — once for the window holding the
speech, again for a silent window to confirm the end.

The fix was therefore **architectural, not an optimisation**. Replacing fixed slices
with a continuous audio stream deleted 124 lines *and* the latency, and removed a
whole class of device-handover bug along with it.

**Generalisable rule: measure the chain before optimising a link.** Three components
were made faster this session. Two of them did not matter.

---

## 3. Every bug found, and what caused it

Ordered by how much damage each could do.

| # | Bug | Cause | How it was caught |
|---|---|---|---|
| 1 | Claimed success for a blocked action | A soft "user declined" message the 3B model glossed over | Ran the deny path and read the reply |
| 2 | Confirmation gate deadlocked the turn | `input()` ran on the worker thread; answering out loud was heard as a barge-in | Traced the code path, reproduced with a blocked thread |
| 3 | Wedged recorder killed the whole session | An unguarded second `wait()` in the shutdown escalation | It crashed during an acoustic test |
| 4 | One bad slice poisoned every later slice | Dropped the mic without closing it, leaving an ffmpeg holding the device | Every slice failed after the first |
| 5 | `search_notes` broken for two tiers | A refactor stripped `from pathlib import Path`; nothing exercised it | Prompt-injection test failed for the wrong reason |
| 6 | Notes search was case-sensitive | Plain `grep` | "budget review" missed "Budget review" |
| 7 | It interrupted itself constantly | Laptop speakers feed its own voice into its own mic | Heard `"You're open"` → `↳ interrupted` in a log |
| 8 | Selftest wrote into real state | Tests pointed at the live store and audit log | Real audit log filled with test noise |
| 9 | Latency report read negative | Barge-in polls overwrote the timestamps of the turn being measured | `-8690 ms` in a report |
| 10 | ~1.5 s hidden in the audio pipe | ffmpeg buffers output before releasing it | Endpoint measured 1563 ms when theory said 520 ms |
| 11 | Wake word chopped in half | 1.5 s slices split "Friday" into "Day" | A/B test at 1.0 s vs 2.5 s |
| 12 | "I'm done for the day" never matched | Normaliser turned the apostrophe into a space → `i m done` | Unit test on the phrase list |
| 13 | Crash on exit | Neural voice held an ONNX session Python tore down underneath it | `Abort trap: 6` on quit |
| 14 | Saying "Yes?" ate the question | It muted the mic to say it, and people say the name and question in one breath | Question vanished from the log |

**Pattern worth noting:** almost none of these were found by reading code. They were
found by running the thing and listening to it. The ones found by inspection (#2)
were the exception.

---

## 4. What the results actually taught

### Measure the whole chain, not the parts
Three optimisations were made before any end-to-end measurement. Two were irrelevant.
The benchmark reordered the entire priority list in one command.

### Test conditions can lie louder than bugs
One full-turn measurement read **31.6 seconds**. The cause was not the code: the
machine was at load 52 with an unrelated `npm` process pegging a core. Another read
7.6 s because the test harness itself cold-loaded the model on every run — a cost
that does not exist in real use.

**Rule: check machine load before trusting any timing number.**

### Some problems are physical, not logical
Self-interruption could not be solved by comparing text. Whisper *hallucinates* on
its own echo — it produced `"How do you just die in goofas?"` from Zetsu reading a
battery report. No similarity test against what was said will ever match that.

The fix had to be acoustic: its voice crosses the room, yours arrives a foot from the
microphone, so the threshold is raised while it speaks. Textual echo detection still
runs, but as the second line of defence.

### A small model is not the bottleneck
First token from a 3B: **64 ms**. Tool definitions cost only ~21 ms of that. Every
argument for a bigger model was about quality, never speed — and every proposal to
"bypass the LLM for speed" was solving a 64 ms problem.

### Safety held under conditions it was not designed for
During an unrelated test, a garbled transcript triggered `remember`. The gate asked
out loud, received a non-answer, denied, and Zetsu correctly said *"I did not run the
remember function."* That chain was never tested deliberately; it worked anyway.

---

## 5. Known open problems

### 5.1 It cuts you off mid-sentence — CRITICAL
**Reported in real use.** Dictating *"remind me to make an advertisement for my
mother's name change document in the newspaper"* ended the turn before "in the
newspaper" and jumped straight to asking whether to remember it.

**Cause:** `hangover_ms = 400`. Any thinking pause longer than 0.4 s is read as the
end of the sentence. That value was chosen to minimise latency and is simply wrong
for natural speech, where mid-sentence pauses of 0.5–1.0 s are normal — especially in
longer, composed sentences.

**This is the most important open issue.** Being cut off is worse than waiting.

### 5.2 Barge-in on laptop speakers is still imperfect
Ducking the microphone while speaking took self-interruptions from 5 to 0 in one
test, but a loud garbled hallucination still broke through in another. Headphones
remove the problem entirely; without acoustic echo cancellation this stays a
probabilistic defence.

### 5.3 Wake-word reliability is roughly 2 in 3
Measured with a synthesised voice through laptop speakers — a deliberately hostile
test. A real voice near the microphone should do considerably better, and
`--calibrate` has not yet been run on a real human voice.

### 5.4 The end-to-end 500 ms is assembled, not observed
Each stage was measured cleanly, but a single uninterrupted real-world turn was never
captured, because the test harness kept cold-loading the model. The number is
credible but not yet confirmed as one continuous observation.

---

## 6. Current capability

**Runs entirely locally.** No API keys, no accounts, no audio leaving the machine.
One optional Python dependency; everything else is the standard library plus local
binaries.

- Wake word with fuzzy matching and voice calibration
- Continuous listening; conversation stays open, "keep listening" holds it open
  indefinitely, "bye bye" ends it
- Barge-in: talk over it and it stops in 85 ms
- Neural voice, local
- Tools: todos, notes search, memory, **real calendar**, **Apple Reminders**, system status
- Durable memory in plain text you can edit by hand
- Heartbeat that can speak up on its own, with quiet hours and held notices
- Confirmation gate on everything consequential, per-action, defaulting to no
- Prompt-injection screening
- Full audit trail, kill switch, live dashboard
- Two brains: local by default, Claude for hard requests
- RAM released on exit

---

## 7. Where to go next

### Immediate — fix what is broken
1. **Adaptive endpointing.** Do not end a turn on a fixed silence. Extend the window
   when the transcript ends on a dangling word ("for my", "in the", "to make an") —
   grammatical incompleteness is a strong signal that the speaker is still going.
2. **Raise the base hangover** to ~800 ms, with the dangling-word check reclaiming the
   latency where the sentence clearly ended.
3. **Confirm 500 ms in one real turn** on an idle machine with a warm model.

### High value — reliability over speed
4. **Confidence thresholds.** High confidence executes, medium asks for clarification,
   low routes to Claude. This compensates for a small brain without slowing anything.
5. **Deterministic intent parsing.** Timers, alarms and "stop" should not depend on a
   3B model's judgement. Code for deterministic operations, the model for ambiguity.
6. **Repeat-back for long dictation.** When capturing something detailed, read it back
   before acting.

### Capability — make it genuinely useful
7. **Email** (read and draft, never send unprompted)
8. **Vision** — screenshot understanding, "what's this error?"
9. **Music and media control**
10. **Web search** for facts the local model does not have
11. **Files** — find, move, summarise
12. **Home automation** if there is anything to control

### Polish
13. **Heartbeat priority** — background work must never delay the voice loop
14. **Anticipation** — "meeting in 10 minutes, traffic is bad"
15. **Personal voice** — Piper can be fine-tuned

---

## 8. The honest assessment

**Against ChatGPT Voice: ~85%.** Latency is now comparable, interruption works, the
voice is natural. The gap is model intelligence, not speed.

**Against Jarvis: ~40%.** It has the hard parts — always listening, memory,
proactivity, tools, safety. What it lacks is reach: it cannot see, cannot control much,
and does not anticipate.

**What is genuinely good here:** every layer is independently runnable and testable,
the safety rails are real and have been tested under stress, and every performance
claim in this document is a measurement rather than an estimate.

**What to watch:** the temptation to add capability before fixing the endpointing.
Being cut off mid-sentence will annoy a user far more than a missing integration.
