"""Learning from being told off.

A correction said once is a moment; said three times it is a preference. Writing
every "no, don't do that" straight into permanent memory would fill it with
noise and contradictions, so corrections climb a ladder instead:

    heard once      -> noted, nothing changes
    heard again     -> a candidate
    heard enough    -> Zetsu offers to remember it, and you decide

Nothing is promoted without being asked. Memory is yours, and something that
quietly rewrites how the assistant behaves is exactly the thing that should not
happen behind your back.
"""

import json
import re
from datetime import datetime

from config import CONFIG, STATE

FILE = STATE / "corrections.json"

# What being corrected actually sounds like out loud.
MARKERS = re.compile(
    r"\b(no,? ?(don'?t|do not|stop|not)|don'?t do that|stop doing that"
    r"|i (already )?told you|i said|that'?s not what i (meant|said|asked)"
    r"|not like that|wrong|actually,? ?(no|don'?t)|never mind that"
    r"|quit (doing )?that|please don'?t|stop it)\b",
    re.I,
)


def looks_like_correction(text):
    return bool(MARKERS.search(text or ""))


def load():
    try:
        return json.loads(FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save(items):
    STATE.mkdir(exist_ok=True)
    FILE.write_text(json.dumps(items, indent=2))


def _key(text):
    """A loose fingerprint. Apostrophes dropped so "don't" and "dont" match, and
    only the meaningful words kept, so "again" or "please" do not make the same
    complaint look like a new one."""
    cleaned = re.sub(r"[''`]", "", text.lower())
    words = {w for w in re.findall(r"[a-z]+", cleaned) if len(w) > 2 and w not in FILLER}
    return " ".join(sorted(words))


FILLER = {"the", "and", "for", "that", "this", "you", "your", "please", "again",
          "just", "not", "did", "was", "are", "but", "its", "it"}


def _similar(one, two):
    """Overlap between two fingerprints. Said differently is still said twice."""
    first, second = set(one.split()), set(two.split())
    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


def note(text, context=""):
    """Record a correction. Returns it if it has now been said often enough."""
    items = load()
    key = _key(text)
    for item in items:
        if _similar(item["key"], key) >= CONFIG["corrections"]["same_complaint"]:
            item["times"] += 1
            item["last"] = datetime.now().isoformat()
            item["said"] = text
            save(items)
            return item if item["times"] >= CONFIG["corrections"]["promote_after"] \
                and not item.get("offered") else None
    items.append({
        "key": key, "said": text, "context": context[:120], "times": 1,
        "first": datetime.now().isoformat(), "last": datetime.now().isoformat(),
        "offered": False,
    })
    save(items)
    return None


def mark_offered(key):
    items = load()
    for item in items:
        if item["key"] == key:
            item["offered"] = True
    save(items)


def pending():
    """Corrections said often enough to be worth asking about."""
    threshold = CONFIG["corrections"]["promote_after"]
    return [i for i in load() if i["times"] >= threshold and not i.get("offered")]
