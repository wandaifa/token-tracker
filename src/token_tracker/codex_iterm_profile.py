"""同步已有 Token Tracker iTerm2 试验 profile 的触发器颜色，不接管其它 profile。"""

import json
from pathlib import Path

from .ui.themes import get_theme

PROFILE_PATH = Path.home() / "Library/Application Support/iTerm2/DynamicProfiles/token-tracker-colors-trial.json"
PROFILE_NAME = "Token Tracker Colors Trial"
PROFILE_GUID = "CFB7E6B3-3AC7-49B0-87A8-6A3F5836B3C1"
TRIGGER_SLOTS = (
    "green", "red", "green", "mauve", "peach", "red", "red",
    "pink", "green", "yellow", "red",
)


def sync_trigger_colors(theme: str) -> bool:
    """仅更新已存在的 11 条托管触发器；其它内容和 profile 不动。"""
    if not PROFILE_PATH.is_file():
        return False
    try:
        data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        profiles = data.get("Profiles", [])
        profile = next(p for p in profiles if p.get("Name") == PROFILE_NAME and p.get("Guid") == PROFILE_GUID)
        triggers = profile.get("Triggers", [])
        if len(triggers) != len(TRIGGER_SLOTS) or any(t.get("action") != "HighlightTrigger" for t in triggers):
            return False
        base = get_theme(theme)["base"]
        changed = False
        for trigger, slot in zip(triggers, TRIGGER_SLOTS, strict=True):
            color = "{" + base[slot] + ",}"
            if trigger.get("parameter") != color:
                trigger["parameter"] = color
                changed = True
        if changed:
            PROFILE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True
    except (OSError, ValueError, TypeError, StopIteration, AttributeError):
        return False
