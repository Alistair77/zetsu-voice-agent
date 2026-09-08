"""Working memory — what we are doing right now.

Different from long-term memory, which holds durable facts about you. This holds
the shape of the current piece of work: what we are on, what is next, what got
pushed aside. It is what stops the classic failure where you say "carry on with
that" and get "could you clarify what you mean?".

Ephemeral by design. It expires, because a stale objective is worse than none.
"""

import json
from datetime import datetime, timedelta

from config import CONFIG, STATE

FILE = STATE / "working.json"
EMPTY = {"focus": "", "next": [], "deferred": [], "updated": ""}


def read():
    try:
        data = json.loads(FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(EMPTY)
    stamp = data.get("updated")
    if stamp:
        age = datetime.now() - datetime.fromisoformat(stamp)
        if age > timedelta(hours=CONFIG["working"]["expires_hours"]):
            return dict(EMPTY)   # yesterday's plan is not today's
    return {**EMPTY, **data}


def write(data):
    STATE.mkdir(exist_ok=True)
    data["updated"] = datetime.now().isoformat()
    FILE.write_text(json.dumps(data, indent=2))


def set_focus(what):
    data = read()
    if data["focus"] and data["focus"].lower() != what.lower():
        data["deferred"] = ([data["focus"]] + data["deferred"])[:CONFIG["working"]["keep_deferred"]]
    data["focus"] = what
    data["next"] = [item for item in data["next"] if item.lower() != what.lower()]
    write(data)
    return data


def queue_next(what):
    data = read()
    if what.lower() not in [item.lower() for item in data["next"]]:
        data["next"].append(what)
    write(data)
    return data


def clear():
    write(dict(EMPTY))


def describe(data=None):
    data = data or read()
    if not data["focus"] and not data["next"]:
        return ""
    lines = []
    if data["focus"]:
        lines.append(f"Right now we are working on: {data['focus']}")
    if data["next"]:
        lines.append("Next: " + "; ".join(data["next"]))
    if data["deferred"]:
        lines.append("Set aside earlier: " + "; ".join(data["deferred"]))
    return "\n".join(lines)


def for_prompt():
    described = describe()
    if not described:
        return ""
    return (
        "The thread of work you are both in the middle of. Use it to resolve "
        '"that", "it" and "carry on" without asking what they mean.\n'
        f"{described}\n\n"
    )
