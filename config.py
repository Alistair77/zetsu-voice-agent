"""Settings and paths, in one place. Edit config.toml, not the code."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).parent
STATE = ROOT / "state"
CONFIG = tomllib.loads((ROOT / "config.toml").read_text())
NAME = CONFIG["agent"]["name"]
