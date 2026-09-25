"""在 iTerm2 底部窗格显示当前 Codex 会话的两行彩色状态。"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

from .adapters import codex
from .config import TERMINAL_MAP_FILE
from .hooks import _render_codex_statusline_hook

_SPLIT_OK = "tt_statusbar_split_ok"
_POLL_SECONDS = 5
_ANSI = re.compile(r"\x1b\[([0-9;]*)m")
_APPLESCRIPT = r"""
on run argv
    set targetId to item 1 of argv
    set launchCommand to item 2 of argv
    tell application "iTerm2"
        set sourceSession to missing value
        set sourceWindow to missing value
        set sourceTab to missing value
        repeat with currentWindow in windows
            repeat with currentTab in tabs of currentWindow
                repeat with currentSession in sessions of currentTab
                    if id of currentSession is targetId then
                        set sourceSession to currentSession
                        set sourceWindow to currentWindow
                        set sourceTab to currentTab
                        exit repeat
                    end if
                end repeat
                if sourceSession is not missing value then exit repeat
            end repeat
            if sourceSession is not missing value then exit repeat
        end repeat
        if sourceSession is missing value then error "tt_statusbar_source_not_found"

        set originalBounds to bounds of sourceWindow
        set originalZoomed to zoomed of sourceWindow
        set originalSourceRows to rows of sourceSession
        set statusSession to missing value
        try
            set wrappedCommand to "/bin/zsh -lc " & quoted form of launchCommand
            tell sourceSession
                set statusSession to split horizontally with same profile command wrappedCommand
            end tell
            set bounds of sourceWindow to originalBounds
            if (zoomed of sourceWindow) is not originalZoomed then set zoomed of sourceWindow to originalZoomed
            -- 恢复窗口边界会把 3 行状态窗格重新放大；最后收窄状态窗格，再补回主窗格。
            set rows of statusSession to 3
            set rows of sourceSession to originalSourceRows
            if rows of statusSession > 4 then error "tt_statusbar_height_mismatch"
            tell sourceWindow to select sourceTab
            select sourceSession
            return "tt_statusbar_split_ok"
        on error errorMessage number errorNumber
            try
                if statusSession is not missing value then close statusSession
            end try
            try
                set bounds of sourceWindow to originalBounds
                if (zoomed of sourceWindow) is not originalZoomed then set zoomed of sourceWindow to originalZoomed
                tell sourceWindow to select sourceTab
                select sourceSession
            end try
            error errorMessage number errorNumber
        end try
    end tell
end run
"""


def _session_path(session_id: str) -> Path | None:
    """只接受文件名精确匹配的会话，避免多 Codex 窗格串数据。"""
    for path in Path(codex.SESSIONS_DIR).rglob("*.jsonl"):
        if path.stem.endswith(f"-{session_id}"):
            return path
    return None


def _render(script: str, session_id: str, path: Path) -> str:
    payload = {"session_id": session_id, "transcript_path": str(path)}
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, "--direct"],
        input=json.dumps(payload), capture_output=True, text=True, timeout=10,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "状态栏渲染失败")
    return result.stdout.rstrip("\n")


def _tmux_markup(value: str) -> str:
    """将现有真彩色状态文本转换为 tmux 状态栏样式。"""
    parts = []
    pos = 0
    for match in _ANSI.finditer(value):
        parts.append(value[pos:match.start()].replace("#", "##"))
        codes = match.group(1).split(";")
        if codes[:2] == ["38", "2"] and len(codes) == 5:
            parts.append("#[fg=#{:02x}{:02x}{:02x}]".format(*(int(c) for c in codes[2:])))
        elif "0" in codes or codes == [""]:
            parts.append("#[default]")
        elif "1" in codes:
            parts.append("#[bold]")
        elif "2" in codes:
            parts.append("#[dim]")
        pos = match.end()
    parts.append(value[pos:].replace("#", "##"))
    return "".join(parts)


def _session_for_pane(pane: str) -> str | None:
    try:
        mappings = json.loads(Path(TERMINAL_MAP_FILE).read_text(encoding="utf-8")).get("_terminal_map", {})
        for session_id, terminal in reversed(list(mappings.items())):
            if terminal.get("tmux") == pane and _session_path(session_id) is not None:
                return session_id
    except (OSError, ValueError, AttributeError):
        pass
    return None


def tmux_line(index: int, pane: str) -> int:
    session_id = _session_for_pane(pane)
    path = _session_path(session_id) if session_id else None
    if path is None or session_id is None:
        print("等待当前 Codex 会话首次回答…" if index == 0 else "")
        return 0
    try:
        lines = _render(_render_codex_statusline_hook(), session_id, path).splitlines()
        print(_tmux_markup(lines[index]) if index < len(lines) else "")
    except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError):
        print("状态数据暂不可用" if index == 0 else "")
    return 0


def tmux() -> int:
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        print("请先在 tmux 中运行此命令。", file=sys.stderr)
        return 1
    for index in range(3):
        if index == 2:
            value = ""
        else:
            command = shlex.join([sys.executable, "-B", "-m", "token_tracker.codex_statusbar", "tmux-line", str(index)])
            value = f"  #({command} #{{pane_id}})"
        result = subprocess.run(["tmux", "set-option", "-t", pane, f"status-format[{index}]", value],
                                capture_output=True, text=True)
        if result.returncode:
            print(result.stderr.strip(), file=sys.stderr)
            return 1
    for option, value in (("status", "3"), ("status-style", "bg=default,fg=default"),
                          ("status-interval", "10")):
        result = subprocess.run(["tmux", "set-option", "-t", pane, option, value], capture_output=True, text=True)
        if result.returncode:
            print(result.stderr.strip(), file=sys.stderr)
            return 1
    print("当前 tmux 会话底部已配置两行状态栏；Codex 首次回答后显示数据。")
    return 0


def watch(session_id: str, once: bool = False) -> int:
    script = _render_codex_statusline_hook()
    path = _session_path(session_id)
    if once:
        if path is None:
            print("找不到当前 Codex 会话文件。", file=sys.stderr)
            return 2
        print(_render(script, session_id, path))
        return 0

    sys.stdout.write("\x1b[?1049h\x1b[?25l")
    try:
        previous = None
        next_redraw = 0.0
        while True:
            if path is None:
                path = _session_path(session_id)
            signature = (path.stat().st_mtime_ns, path.stat().st_size) if path and path.exists() else None
            now = time.monotonic()
            if signature != previous or now >= next_redraw:
                message = _render(script, session_id, path) if path and signature else "等待当前 Codex 会话数据…"
                sys.stdout.write("\x1b[H\x1b[2J" + message + "\n")
                sys.stdout.flush()
                previous = signature
                next_redraw = now + 30
            time.sleep(_POLL_SECONDS)
    except KeyboardInterrupt:
        return 0
    finally:
        sys.stdout.write("\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()


def split() -> int:
    session_id = os.environ.get("CODEX_THREAD_ID", "").strip()
    term_id = os.environ.get("ITERM_SESSION_ID", "").rpartition(":")[2]
    if not session_id or not term_id:
        print("请在 iTerm2 的 Codex 会话中启动状态栏。", file=sys.stderr)
        return 1
    command = shlex.join([sys.executable, "-B", "-m", "token_tracker.codex_statusbar", "watch", session_id])
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", _APPLESCRIPT, "--", term_id, command],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"iTerm2 底部状态栏启动失败：{exc}", file=sys.stderr)
        return 1
    if result.returncode == 0 and _SPLIT_OK in result.stdout:
        print("已在当前 iTerm2 会话底部打开彩色状态栏。")
        return 0
    print(f"iTerm2 底部状态栏启动失败：{(result.stderr or result.stdout).strip()}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Codex 彩色状态栏")
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("split")
    subparsers.add_parser("tmux")
    tmux_row = subparsers.add_parser("tmux-line")
    tmux_row.add_argument("index", type=int, choices=(0, 1))
    tmux_row.add_argument("pane")
    viewer = subparsers.add_parser("watch")
    viewer.add_argument("session_id")
    viewer.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if args.action == "split":
        return split()
    if args.action == "tmux":
        return tmux()
    if args.action == "tmux-line":
        return tmux_line(args.index, args.pane)
    return watch(args.session_id, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
