"""What Zetsu is doing *right now*, so the dashboard can show it.

Written by whichever front end is running, read by the dashboard. Deliberately
one small file rather than a socket: the page can be opened at any time, the
loop doesn't care whether anyone is watching, and nothing breaks if both are
running or neither is.
"""

import json
import os
import time

from config import STATE

FILE = STATE / "live.json"
PID = STATE / "wake.pid"
IDLE = {"phase": "idle", "detail": "", "backend": "", "since": 0.0, "typical": {}}


def read():
    try:
        return json.loads(FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(IDLE)


def write(data):
    STATE.mkdir(exist_ok=True)
    scratch = FILE.with_suffix(".tmp")
    scratch.write_text(json.dumps(data))
    scratch.replace(FILE)  # atomic — the page never reads half a file


def set_phase(phase, detail="", backend=None):
    """idle | listening | transcribing | thinking | working | speaking"""
    data = read()
    data.update(phase=phase, detail=detail, since=time.time())
    if backend is not None:
        data["backend"] = backend
    write(data)


def remember_duration(backend, seconds):
    """Roll an average of how long this brain takes, so the loader can predict.

    ponytail: exponential average, no history table. Two turns in and it's
    already useful; it drifts to the truth as the model or machine changes.
    """
    data = read()
    typical = data.setdefault("typical", {})
    previous = typical.get(backend)
    typical[backend] = seconds if previous is None else previous * 0.7 + seconds * 0.3
    write(data)


# --- who is holding the microphone ------------------------------------------
# A pidfile rather than a handle, so the dashboard can see a mic loop it did not
# start itself — you might have run it from a terminal.

def claim_mic():
    STATE.mkdir(exist_ok=True)
    PID.write_text(str(os.getpid()))


def release_mic():
    PID.unlink(missing_ok=True)


def mic_pid():
    """The live wake-loop pid, or None. Stale pidfiles are ignored, not trusted."""
    try:
        pid = int(PID.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None
    try:
        os.kill(pid, 0)  # signal 0 asks "are you there?" without touching it
    except OSError:
        return None
    return pid
