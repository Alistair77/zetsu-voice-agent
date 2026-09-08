"""Every bug this project has had, as a test it can never have again.

Twenty failures were found the hard way — by running Zetsu, listening to it, and
attacking it. Fixing them is worth less than pinning them: a fix lasts until
someone refactors past it, a test lasts.

Two kinds of check. Most are behavioural: they run the real code and assert on
what it does. A few are structural, asserting a particular decision is still
present in the source — used only where the failure was architectural and
reproducing it would need a microphone, a wedged process or a live terminal.
Those are labelled, because a structural check is weaker evidence and pretending
otherwise would be the same mistake as the sandbox that was not a sandbox.

    ./.venv/bin/python test_regressions.py
"""

import inspect
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import corrections
import ears
import mouth
import privacy
import tools
import zetsu
from config import CONFIG

PASSED, FAILED = [], []


def check(number, what, structural=False):
    def register(test):
        label = f"REG-{number:02d}  {what}" + ("  [structural]" if structural else "")
        try:
            test()
            PASSED.append(label)
        except Exception as exc:
            FAILED.append((label, f"{type(exc).__name__}: {exc}"))
        return test
    return register


def source_of(function):
    return inspect.getsource(function)


@check(1, "a blocked action can never read as success")
def _():
    denied = tools.run("add_todo", {"text": "REG-01 must not exist"}, lambda s: False)
    assert "said NO" in denied and "NOT run" in denied, denied
    assert "REG-01" not in tools.list_todos("all")


@check(2, "the confirmation gate never blocks on the worker thread", structural=True)
def _():
    body = source_of(zetsu.wake_main)
    assert "gate_asked" in body and "gate_answered" in body, "gate handoff missing"
    assert "def settle_gate" in body, "the gate must be settled on the main thread"
    assert threading.Event().wait(timeout=0.2) is False, "unanswered must mean no"


@check(3, "a wedged recorder is killed, never raised out of")
def _():
    stubborn = subprocess.Popen(["sleep", "60"], stdin=subprocess.PIPE)
    ears.stop_recording(stubborn)
    assert stubborn.poll() is not None, "stubborn recorder survived"


@check(4, "a dropped microphone is closed, not leaked", structural=True)
def _():
    assert "mic.close()" in source_of(zetsu.wake_main)
    assert hasattr(ears.StreamMic, "close") and hasattr(ears.OpenMic, "close")


@check(5, "notes search actually runs")
def _():
    with tempfile.TemporaryDirectory() as notes:
        (Path(notes) / "n.md").write_text("Budget review is on the 14th.\n")
        real = CONFIG["notes"]["roots"]
        CONFIG["notes"]["roots"] = [notes]
        try:
            found = tools.search_notes("Budget review")
            assert "14th" in found and "did not work" not in found, found
        finally:
            CONFIG["notes"]["roots"] = real


@check(6, "notes search ignores case")
def _():
    with tempfile.TemporaryDirectory() as notes:
        (Path(notes) / "n.md").write_text("Budget review is on the 14th.\n")
        real = CONFIG["notes"]["roots"]
        CONFIG["notes"]["roots"] = [notes]
        try:
            assert "14th" in tools.search_notes("budget review")
        finally:
            CONFIG["notes"]["roots"] = real


@check(7, "it knows its own voice from anyone else's")
def _():
    import numpy as np

    mic = ears.StreamMic.__new__(ears.StreamMic)
    mic.cfg, mic.frame_ms, mic.frame_bytes = CONFIG, 20, 640
    rng = np.random.default_rng(11)
    played = rng.standard_normal(16000).astype(np.float32) * 4000
    mic.reference = lambda start, end: played
    echo = played[8000:8320] * 0.4 + rng.standard_normal(320).astype(np.float32) * 60
    assert mic.is_echo(echo.astype(np.int16).tobytes(), 0.0), "missed its own echo"
    other = rng.standard_normal(320).astype(np.float32) * 4000
    assert not mic.is_echo(other.astype(np.int16).tobytes(), 0.0), "false echo alarm"


@check(8, "tests never write to the real store or audit log", structural=True)
def _():
    body = source_of(zetsu.selftest)
    for pointed in ("_rails.LOG", "_rails.PAUSED", "_live.FILE"):
        assert pointed in body, f"{pointed} not sandboxed"
    assert "TemporaryDirectory" in body


@check(9, "latency marks can never read negative", structural=True)
def _():
    body = source_of(zetsu.wake_main)
    assert body.count("timed=False") >= 2, "non-turn listens must not stamp the marks"


@check(10, "audio capture runs with low-latency flags", structural=True)
def _():
    body = source_of(ears.StreamMic.__init__)
    for flag in ("nobuffer", "low_delay", "flush_packets"):
        assert flag in body, f"missing {flag}: ~1.5s of buffering returns without it"


@check(11, "the wake word survives landing on a boundary")
def _():
    assert CONFIG["wake"]["chunk_overlap_seconds"] > 0
    assert CONFIG["wake"]["chunk_seconds"] >= 2.0
    assert ears.find_wake("Friday what is on my list") == "what is on my list"
    assert ears.find_wake("fridey add milk") == "add milk"
    assert ears.find_wake("on tuesday i went out") is None


@check(12, "an apostrophe never splits a word in two")
def _():
    assert ears.normalise("I'm done") == "im done"
    assert ears.matches_any("I'm done for the day.", CONFIG["wake"]["goodbyes"])
    assert ears.matches_any("Let's talk for a while", CONFIG["wake"]["stay_awake"])


@check(13, "the voice shuts down cleanly instead of aborting on exit")
def _():
    body = source_of(mouth.Speaker.close)
    assert "join" in body and "self.piper = None" in body
    assert "speaker.close()" in source_of(zetsu.wake_main)


@check(14, "waking on the bare name never mutes away the question", structural=True)
def _():
    body = source_of(zetsu.wake_main)
    branch = body.find("len(spoken.split()) < 2")
    prompt_at = body.find('say_now("Yes?")', branch)
    listen_at = body.find("hear_a_sentence(", branch)
    if listen_at == -1:
        listen_at = body.find("listen(", branch)
    assert 0 < listen_at < prompt_at, "it must listen before prompting"


@check(15, "it does not cut you off mid-sentence")
def _():
    assert CONFIG["stream"]["hangover_ms"] >= 700, "a thinking pause is 500-1000ms"
    assert ears.sounds_unfinished("remind me to make an advertisement for my")
    assert ears.sounds_unfinished("my mothers name change document in the")
    assert not ears.sounds_unfinished("remind me to buy milk")
    assert CONFIG["stream"]["max_continuations"] >= 1


@check(16, "the model is shown no marker worth copying")
def _():
    worked = tools.run("list_todos", {}, lambda s: True)
    assert not worked.lstrip().startswith("["), f"copyable marker: {worked[:40]}"
    assert "TOOL RESULT" not in worked, "a 3B will imitate this"
    assert "did not work" in tools.run("nope", {}, lambda s: True)


@check(17, "private material is blocked by the kernel, not by good manners")
def _():
    assert privacy.is_off_limits("~/Pictures/a.jpg")
    assert privacy.is_off_limits("~/Movies/b.mov")
    assert privacy.is_off_limits("~/.ssh/id_rsa")
    assert not privacy.is_off_limits("~/Desktop/raw2/README.md")
    assert privacy.confine(["x"])[0] == "sandbox-exec", "subprocess not confined"
    assert privacy.sandbox_works(), "the sandbox does not actually block anything"


@check(18, "the prompt is never passed where a variadic flag can eat it", structural=True)
def _():
    body = source_of(tools._ask_claude)
    assert "input=prompt" in body, "the prompt must go in on stdin"
    assert "allowed, prompt]" not in body, "--allowedTools would swallow it"


@check(19, "the same complaint said differently still counts as repeated")
def _():
    real = corrections.FILE
    with tempfile.TemporaryDirectory() as sandbox:
        corrections.FILE = Path(sandbox) / "c.json"
        try:
            assert corrections.note("no dont do that") is None
            promoted = corrections.note("No, don't do that again")
            assert promoted and promoted["times"] == 2, "don't / dont must match"
        finally:
            corrections.FILE = real


@check(20, "finished background work reaches the terminal unprompted", structural=True)
def _():
    body = source_of(zetsu.read_or_report)
    assert "select.select" in body, "input() alone hides a finished job"
    assert "announce_finished" in body
    assert "read_or_report" in source_of(zetsu.main)


if __name__ == "__main__":
    for line in PASSED:
        print(f"  pass  {line}")
    for line, why in FAILED:
        print(f"  FAIL  {line}\n          {why}")
    total = len(PASSED) + len(FAILED)
    print(f"\n  {len(PASSED)}/{total} regressions pinned")
    sys.exit(1 if FAILED else 0)
