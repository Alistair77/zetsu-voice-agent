"""The heartbeat. Tier 5: Zetsu acts without being spoken to.

Runs on its own clock, checking for things worth mentioning — for now, just
todos that came due — and holding them until they can actually be said.
AGENT.md calls four things non-negotiable here regardless of how chatty the
proactivity threshold is: quiet hours, notices held for you if you were away,
being able to walk away from one, and a kill switch. This module is all four:

- quiet hours:   `in_quiet_hours` — nothing is said while you'd rather not hear it
- held notices:  `state/notices.json` — a notice waits here until delivered
- dismissible:   delivery clears the store; nothing repeats once you've heard it
- kill switch:   `rails.is_paused()` — `/pause`, or `[heartbeat] enabled = false`
                 in config.toml. Both are re-read every tick, so neither needs a
                 restart, and there is only ever one answer to "am I stopped?"

Standalone:  ./.venv/bin/python zetsu.py --heartbeat
Every session (text or voice) also flushes whatever piled up while you were
gone, right at the start.
"""

import json
import time
import tomllib
from datetime import date, datetime

import rails
import tools
from config import CONFIG, ROOT, STATE

NOTICES = STATE / "notices.json"


def load_config():
    """Re-read config.toml fresh, so the kill switch works without a restart."""
    return tomllib.loads((ROOT / "config.toml").read_text())


def _minutes(hhmm):
    hour, minute = hhmm.split(":")
    return int(hour) * 60 + int(minute)


def in_quiet_hours(now, cfg=CONFIG):
    hb = cfg["heartbeat"]
    start, end = _minutes(hb["quiet_start"]), _minutes(hb["quiet_end"])
    minute = now.hour * 60 + now.minute
    if start <= end:
        return start <= minute < end
    return minute >= start or minute < end  # window wraps past midnight


def load_notices():
    if not NOTICES.exists():
        return []
    return json.loads(NOTICES.read_text())


def save_notices(items):
    STATE.mkdir(exist_ok=True)
    NOTICES.write_text(json.dumps(items, indent=2))


def check_due(today=None):
    """Queue a notice for every todo newly due, then mark it notified.

    The `notified` flag on the todo itself is what makes this idempotent — a
    todo is queued once, ever, no matter how many ticks pass before it's done.
    """
    today = today or date.today().isoformat()
    items = tools.load_todos()
    due = [
        item for item in items
        if not item["done"] and item.get("due") and item["due"] <= today
        and not item.get("notified")
    ]
    if not due:
        return []

    due_ids = {item["id"] for item in due}
    tools.save_todos(
        [{**item, "notified": True} if item["id"] in due_ids else item for item in items]
    )
    fresh = [
        {"text": f"Reminder: {item['text']}", "created": datetime.now().isoformat()}
        for item in due
    ]
    save_notices(load_notices() + fresh)
    return fresh


def deliver_pending(speak=None):
    """Say (and print) everything waiting, then clear it.

    ponytail: "dismissed" == "delivered". No snooze, no re-surfacing a notice
    you missed mid-scroll. Add an explicit dismiss/snooze if that turns out to
    lose things that mattered.
    """
    queued = load_notices()
    if not queued:
        return []
    for notice in queued:
        print(f"  \U0001f514 {notice['text']}")
        rails.log("SURFACED", notice["text"])
        if speak:
            speak(notice["text"])
    save_notices([])
    return queued


def tick(now=None, speak=None, cfg=None):
    """One heartbeat: look for new things to say, then say them — unless it's
    quiet hours, in which case they sit in the pending store untouched."""
    cfg = cfg or load_config()
    if rails.is_paused(cfg):
        return
    now = now or datetime.now()
    check_due(now.date().isoformat())
    if not in_quiet_hours(now, cfg):
        deliver_pending(speak)


def run_loop(speak=None):
    print("heartbeat running. Ctrl-C to stop.")
    try:
        while True:
            cfg = load_config()
            tick(speak=speak, cfg=cfg)
            time.sleep(cfg["heartbeat"]["interval_seconds"])
    except KeyboardInterrupt:
        print("\nheartbeat stopped.")
