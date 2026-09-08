"""A hard boundary around things Zetsu must never go looking through.

The rule, in the owner's words: anything private that already exists is off
limits, permanently. A picture it takes itself, or a file whose path it was
handed deliberately, is a different matter — that was an instruction. Wandering
into a photo library was never one.

This is enforced at the tool layer rather than asked of the model. A 3B model
that has been told not to look somewhere is a request; a path check is a rule.
"""

import shutil
import subprocess
from pathlib import Path

import rails
from config import CONFIG, STATE

HOME = Path.home()


def _forbidden_roots():
    return [Path(entry).expanduser().resolve() for entry in CONFIG["privacy"]["never_touch"]]


def _forbidden_suffixes():
    return {suffix.lower() for suffix in CONFIG["privacy"]["never_open"]}


def is_off_limits(path):
    """True if this is somewhere Zetsu has no business being."""
    try:
        target = Path(path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return True   # cannot resolve it, so cannot vouch for it

    if target.suffix.lower() in _forbidden_suffixes():
        return True

    for root in _forbidden_roots():
        try:
            target.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def allow(path, why=""):
    """Gate a path. Returns True if it may be touched, and logs it if not."""
    if is_off_limits(path):
        rails.log("PRIVACY", f"refused {Path(path).name} ({why or 'off limits'})")
        return False
    return True


def keep_allowed(paths, why=""):
    return [p for p in paths if allow(p, why)]


def safe_roots(roots):
    """Drop any configured search root that overlaps forbidden ground."""
    return [r for r in roots if not is_off_limits(r)]


# --- an actual fence, not a request ------------------------------------------
# Claude Code's Read tool is not bounded by its working directory: launched
# inside a temporary folder it will still happily read an absolute path
# elsewhere on the machine. That was tested, and it did.
#
# So the boundary is enforced by the kernel instead. macOS seatbelt denies reads
# on the private paths outright, and a model that tries anyway is simply told
# "operation not permitted" — which is the only kind of guarantee worth making
# about someone's photo library.

PROFILE = STATE / "sandbox.sb"


def _sandbox_roots():
    return [Path(entry).expanduser().resolve() for entry in CONFIG["privacy"]["sandbox_deny"]]


def _write_profile():
    STATE.mkdir(exist_ok=True)
    denied = "\n".join(
        f'  (subpath "{root}")' for root in _sandbox_roots() if root.exists()
    )
    PROFILE.write_text(
        "(version 1)\n(allow default)\n"
        f"(deny file-read*\n{denied})\n" if denied else "(version 1)\n(allow default)\n"
    )
    return PROFILE


def confine(command):
    """Wrap a command so it physically cannot read private paths.

    Falls back to the bare command if seatbelt is unavailable, and says so in
    the log rather than pretending the boundary is there.
    """
    if not CONFIG["privacy"]["sandbox_subprocesses"]:
        return command
    if not shutil.which("sandbox-exec"):
        rails.log("PRIVACY", "sandbox-exec missing — subprocess NOT confined")
        return command
    return ["sandbox-exec", "-f", str(_write_profile())] + list(command)


def sandbox_works():
    """Prove the fence is real, rather than assuming it."""
    if not shutil.which("sandbox-exec"):
        return False
    target = next((r for r in _sandbox_roots() if r.exists()), None)
    if target is None:
        return True
    probe = subprocess.run(
        confine(["/bin/ls", str(target)]), capture_output=True, text=True, timeout=15
    )
    return probe.returncode != 0
