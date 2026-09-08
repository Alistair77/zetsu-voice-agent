"""Deterministic commands, answered without the model.

Not for speed — a 3B answers in 64ms, so there is nothing to win there. For
*reliability*. A timer should not depend on a small model choosing the right
tool and parsing "ten" correctly; that is what code is for. The model handles
ambiguity and conversation, code handles operations with exactly one right
answer.

Anything not matched here falls through to the brain, unchanged.
"""

import re
import threading
from datetime import datetime, timedelta

import rails
from config import CONFIG

NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30,
    "forty": 40, "forty five": 45, "fifty": 50, "sixty": 60, "ninety": 90,
    "a": 1, "an": 1, "half": 0, "a half": 0,
}

TIMER = re.compile(
    r"\b(?:set|start)?\s*(?:a\s+)?(?:timer|alarm)\s*(?:for\s+)?"
    r"([a-z0-9 ]+?)\s*(second|seconds|minute|minutes|hour|hours)\b",
    re.I,
)
TIME_ASK = re.compile(r"\b(what(?:'s| is)? the time|what time is it|got the time)\b", re.I)
DATE_ASK = re.compile(r"\b(what(?:'s| is)? (?:the )?(?:date|day)(?: today)?|what day is it)\b", re.I)


def _count(words):
    words = words.strip().lower()
    if words.isdigit():
        return int(words)
    if words in NUMBERS:
        return NUMBERS[words]
    total = 0
    for part in words.split():
        if part.isdigit():
            total += int(part)
        elif part in NUMBERS:
            total += NUMBERS[part]
    return total or None


def handle(text, on_fire=None):
    """Return a spoken reply if this is a deterministic command, else None."""
    if not CONFIG["reflex"]["enabled"]:
        return None
    stripped = text.strip()

    if TIME_ASK.search(stripped):
        return f"It's {datetime.now():%H:%M}."

    if DATE_ASK.search(stripped):
        now = datetime.now()
        suffix = "th" if 11 <= now.day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(now.day % 10, "th")
        return f"It's {now:%A} the {now.day}{suffix} of {now:%B}."

    if found := TIMER.search(stripped):
        amount, unit = _count(found.group(1)), found.group(2).lower().rstrip("s")
        if not amount:
            return None  # could not read the number; let the model try
        seconds = amount * {"second": 1, "minute": 60, "hour": 3600}[unit]
        if seconds > CONFIG["reflex"]["max_timer_hours"] * 3600:
            return f"{amount} {unit}s is longer than I'll hold a timer for."
        _start_timer(seconds, f"{amount} {unit}" + ("s" if amount != 1 else ""), on_fire)
        due = datetime.now() + timedelta(seconds=seconds)
        rails.log("REFLEX", f"timer {amount} {unit} -> {due:%H:%M:%S}")
        return f"Timer set for {amount} {unit}" + ("s." if amount != 1 else ".")

    return None


def _start_timer(seconds, label, on_fire):
    def fire():
        message = f"That's your {label}."
        rails.log("TIMER", f"{label} elapsed")
        if on_fire:
            on_fire(message)
        else:
            import heartbeat

            heartbeat.save_notices(heartbeat.load_notices() + [
                {"text": message, "created": datetime.now().isoformat()}
            ])

    timer = threading.Timer(seconds, fire)
    timer.daemon = True
    timer.start()
