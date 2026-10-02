"""创建并同步 Token Tracker 专用 iTerm2 动态 Profile，不接管其它 Profile。"""

import json
import os
import uuid
from pathlib import Path

from .ui.themes import get_theme

PROFILE_PATH = Path.home() / "Library/Application Support/iTerm2/DynamicProfiles/token-tracker-colors-trial.json"
PROFILE_NAME = "Token Tracker Colors Trial"
PROFILE_GUID = "CFB7E6B3-3AC7-49B0-87A8-6A3F5836B3C1"
MANAGED_PROFILE_PATH = PROFILE_PATH.with_name("token-tracker-colors.json")
MANAGED_PROFILE_NAME = "Token Tracker Colors"
TRIGGER_SLOTS = (
    "green", "red", "green", "mauve", "peach", "red", "red",
    "pink", "green", "yellow", "red",
)
TRIGGER_REGEXES = (
    r"\[[^\]\r\n]+\](?=(?:\([^\r\n]*\))? \| (?:Total|Cost|Model): )",
    r"\([^\r\n]*?\)(?= \| (?:Total|Cost|Model): )",
    r"\+[0-9]+(?=(?: -[0-9]+)?(?: \?[0-9]+)?\) \| )",
    r"\?[0-9]+(?=\) \| )",
    r"(?<= \| )Total: [0-9.]+[kM]?(?= \| )",
    r"(?<= \| )Cost: \$[0-9.]+(?= \| )",
    r"(?<= \| )Model: [^\r\n]+$",
    r"(?<=^    )Limit:(?= 5h )|(?:5h|7d|[0-9.]+[kM]? Ctx)(?= [█░]{8} )|\(reset [0-9dhm]+\)",
    r"(?:(?<=5h )|(?<=7d )|(?<=Ctx ))[█░]{8} [0-4]?[0-9]%(?= |$)",
    r"(?:(?<=5h )|(?<=7d )|(?<=Ctx ))[█░]{8} [5-7][0-9]%(?= |$)",
    r"(?:(?<=5h )|(?<=7d )|(?<=Ctx ))[█░]{8} (?:[89][0-9]|100)%(?= |$)",
)


def _write_profile(path: Path, data: dict) -> bool:
    """在 DynamicProfiles 外写完整 JSON，再原子替换，避免 iTerm2 读取半成品。"""
    temporary = path.parent.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
        return True
    except OSError:
        return False
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _sync_existing(path: Path, name: str, theme: str, *, legacy: bool = False) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        profiles = data.get("Profiles", [])
        profile = next(p for p in profiles if p.get("Name") == name)
        if legacy and profile.get("Guid") != PROFILE_GUID:
            return False
        triggers = profile.get("Triggers", [])
        if len(triggers) != len(TRIGGER_SLOTS) or any(t.get("action") != "HighlightTrigger" for t in triggers):
            return False
        if not legacy and any(t.get("regex") != regex for t, regex in zip(triggers, TRIGGER_REGEXES, strict=True)):
            return False
        base = get_theme(theme)["base"]
        changed = False
        for trigger, slot in zip(triggers, TRIGGER_SLOTS, strict=True):
            color = "{" + base[slot] + ",}"
            if trigger.get("parameter") != color:
                trigger["parameter"] = color
                changed = True
        return _write_profile(path, data) if changed else True
    except (OSError, ValueError, TypeError, StopIteration, AttributeError):
        return False


def sync_trigger_colors(theme: str) -> str | None:
    """优先兼容旧试验 Profile；仅在 iTerm2 配置根目录已存在时建立专用 Profile。"""
    if PROFILE_PATH.is_file() and _sync_existing(PROFILE_PATH, PROFILE_NAME, theme, legacy=True):
        return PROFILE_NAME
    if MANAGED_PROFILE_PATH.is_file():
        return MANAGED_PROFILE_NAME if _sync_existing(MANAGED_PROFILE_PATH, MANAGED_PROFILE_NAME, theme) else None
    if not MANAGED_PROFILE_PATH.parent.is_dir():
        if not MANAGED_PROFILE_PATH.parent.parent.is_dir():
            return None
        try:
            MANAGED_PROFILE_PATH.parent.mkdir(exist_ok=True)
        except OSError:
            return None
    base = get_theme(theme)["base"]
    profile = {
        "Name": MANAGED_PROFILE_NAME,
        "Guid": str(uuid.uuid4()),
        "Triggers": [
            {"regex": regex, "action": "HighlightTrigger", "parameter": "{" + base[slot] + ",}", "partial": False}
            for regex, slot in zip(TRIGGER_REGEXES, TRIGGER_SLOTS, strict=True)
        ],
    }
    return MANAGED_PROFILE_NAME if _write_profile(MANAGED_PROFILE_PATH, {"Profiles": [profile]}) else None
