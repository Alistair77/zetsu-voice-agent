"""Work that takes long enough to talk about.

Looking at the screen takes ten seconds. A web search takes fifteen. Blocking
the conversation for that is the difference between an assistant and a progress
bar you cannot see — you say something, then nothing happens, and you are left
wondering whether it heard you.

So slow work runs in the background. Zetsu says roughly how long it will be,
carries on talking to you, and comes back with the answer when it has it. You
can ask something else in the meantime; that is the whole point.
"""

import threading
import time
import uuid
from datetime import datetime

import rails

_jobs = {}
_lock = threading.Lock()
_finished = []


def start(label, function, estimate_seconds):
    """Run something slow out of the way. Returns (id, estimate)."""
    job_id = uuid.uuid4().hex[:6]
    with _lock:
        _jobs[job_id] = {
            "id": job_id, "label": label, "started": time.time(),
            "estimate": estimate_seconds, "done": False, "result": None,
        }

    def run():
        try:
            result = function()
            ok = True
        except Exception as exc:
            result, ok = f"it didn't work: {exc}", False
        took = time.time() - _jobs[job_id]["started"]
        with _lock:
            _jobs[job_id].update(done=True, result=result, ok=ok, took=took)
            _finished.append(job_id)
        rails.log("JOB", f"{label} finished in {took:.1f}s ({'ok' if ok else 'failed'})")

    rails.log("JOB", f"{label} started, ~{estimate_seconds}s")
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return job_id, estimate_seconds


def running():
    with _lock:
        return [j for j in _jobs.values() if not j["done"]]


def collect():
    """Jobs that have finished and not yet been mentioned. Clears them."""
    with _lock:
        ready = [_jobs[i] for i in _finished]
        _finished.clear()
    return ready
