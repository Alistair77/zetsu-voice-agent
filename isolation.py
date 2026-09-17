"""Point every piece of persistent state at a throwaway directory.

Tests must never read or write the real store. Twice they did anyway: first the
selftest wrote into the live audit log, and later — with that fixed and pinned —
the regression suite did the same thing, down to leaving a false "privacy
sandbox unverified" alert in the owner's real audit trail and passing only
because the owner's real todo list happened to contain "oat milk".

Both failures came from redirecting the state files one at a time and missing
some. So there is one list, here, of every file anything persists to. Add a new
store and it goes in this list, or tests will find it.
"""

import contextlib
import tempfile
from pathlib import Path


def _stores():
    import corrections
    import heartbeat
    import live
    import memory
    import privacy
    import rails
    import tools
    import working

    return [
        (rails, "LOG", "audit.log"),
        (rails, "PAUSED", "PAUSED"),
        (live, "FILE", "live.json"),
        (live, "PID", "wake.pid"),
        (tools, "TODOS", "todos.json"),
        (memory, "FILE", "memory.md"),
        (corrections, "FILE", "corrections.json"),
        (working, "FILE", "working.json"),
        (heartbeat, "NOTICES", "notices.json"),
        (privacy, "PROFILE", "sandbox.sb"),
    ]


@contextlib.contextmanager
def isolated_state():
    """Everything persisted inside this block goes to a temporary directory."""
    stores = _stores()
    saved = [(module, name, getattr(module, name)) for module, name, _ in stores]
    with tempfile.TemporaryDirectory() as scratch:
        for module, name, filename in stores:
            setattr(module, name, Path(scratch) / filename)
        try:
            yield Path(scratch)
        finally:
            for module, name, original in saved:
                setattr(module, name, original)


def unisolated_paths():
    """Store paths still pointing at real state. Empty inside isolated_state()."""
    from config import STATE

    real = STATE.resolve()
    return [
        f"{module.__name__}.{name}"
        for module, name, _ in _stores()
        if Path(getattr(module, name)).resolve().parent == real
    ]
