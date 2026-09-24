"""在 iTerm2 底部窗格显示当前 Codex 会话的两行彩色状态。"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from .adapters import codex
from .hooks import _render_codex_statusline_hook

_SPLIT_OK = "tt_statusbar_split_ok"
_POLL_SECONDS = 5
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
    viewer = subparsers.add_parser("watch")
    viewer.add_argument("session_id")
    viewer.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    return split() if args.action == "split" else watch(args.session_id, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
