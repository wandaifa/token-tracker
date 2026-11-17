#!/usr/bin/env python3
"""token-tracker Antigravity CLI (agy) statusLine — 三行主题彩色状态栏 + 实时 /usage 配额。
L1: [项目](分支* +A -D ?U) | Model: <模型> | Total: <总量> (<缓存率>% cache) | <轮数>t · <时长>
L2: 5h <进度条> <已用%> (<重置>) | 7d <进度条> <已用%> (<重置>) | Ctx <进度条> <%> (<已用>/<窗口>)
L3: <会话标题…> | SID: <完整 Session ID>
由 `tt setup` 生成，勿手改。"""
from __future__ import annotations

__version__ = "__AGY_STATUSLINE_HOOK_VERSION__"

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

THEME_COLORS = __STATUSLINE_TRUECOLOR__
THEME_COLORS_256 = __STATUSLINE_COLOR256__


def _supports_truecolor() -> bool:
    return os.environ.get("COLORTERM", "") in ("truecolor", "24bit")


C = THEME_COLORS if _supports_truecolor() else THEME_COLORS_256
RST = C["reset"]
BOLD = "\033[1m"
FAINT = "\033[2m"

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

QUOTA_REFRESH_INTERVAL = 120   # 后台每 2 分钟最多刷新一次 /usage
QUOTA_LOCK_COOLDOWN = 25       # 防止短时间内重复拉起后台刷新进程
QUOTA_DISPLAY_MAX_AGE = 900    # 缓存超过 15 分钟视为失效，不展示旧配额


def _agy_home() -> Path:
    env = os.environ.get("AGY_HOME", "").strip()
    return Path(env) if env else Path.home() / ".gemini" / "antigravity-cli"


def _strip_ansi(s: str) -> str:
    out = []
    i = 0
    n = len(s)
    while i < n:
        if s[i] == "\033" and i + 1 < n and s[i + 1] == "[":
            j = i + 2
            while j < n and s[j] != "m":
                j += 1
            i = j + 1
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def _vlen(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in _strip_ansi(s))


def _truncate_display(text: str, max_cols: int) -> str:
    text = " ".join(text.split())
    if not text or max_cols <= 0:
        return ""
    if _vlen(text) <= max_cols:
        return text
    if max_cols <= 1:
        return "…"
    acc = []
    cols = 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if cols + w > max_cols - 1:
            break
        acc.append(ch)
        cols += w
    return "".join(acc).rstrip() + "…"


def _fmt_tokens(n: float) -> str:
    val = max(0, int(round(n)))
    if val >= 1_000_000:
        return f"{val / 1_000_000:.1f}M"
    if val >= 1_000:
        return f"{val / 1_000:.0f}k"
    return str(val)


def _fmt_duration(seconds: float) -> str:
    s = max(0, int(seconds))
    if s >= 86400:
        return f"{s // 86400}d{(s % 86400) // 3600}h"
    if s >= 3600:
        return f"{s // 3600}h{(s % 3600) // 60}m"
    return f"{s // 60}m"


def _pct_color(pct: float) -> str:
    return C["bar_ok"] if pct < 50 else C["bar_warn"] if pct < 80 else C["bar_danger"]


def _bar(pct: float | None, width: int = 8) -> str:
    if pct is None:
        return f"{FAINT}{C['label']}{'░' * width}{RST} {C['label']}--{RST}"
    pct = max(0.0, min(100.0, float(pct)))
    filled = round(pct / 100.0 * width)
    empty = width - filled
    color = _pct_color(pct)
    empty_s = f"{color}{'░' * empty}{RST}" if pct > 0 and empty else f"{FAINT}{C['label']}{'░' * empty}{RST}"
    return f"{color}{'█' * filled}{RST}{empty_s} {color}{pct:.0f}%{RST}"


def _clean_dir(path_str: str) -> str:
    if not path_str:
        return ""
    if path_str.startswith("file://"):
        path_str = path_str[7:]
    return os.path.normpath(path_str)


def _git_status(cwd: str, vcs_hint: dict | None = None) -> tuple[str, int, int, int]:
    if not cwd or not os.path.isdir(cwd):
        branch = str((vcs_hint or {}).get("branch") or "")
        if branch and (vcs_hint or {}).get("dirty"):
            branch += "*"
        return branch, 0, 0, 0

    def run(args: list[str]) -> str:
        return subprocess.check_output(
            ["git", *args],
            cwd=cwd,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=0.8,
        ).rstrip("\n")

    try:
        status_out = run(["status", "--porcelain=v1", "-b"])
    except Exception:
        branch = str((vcs_hint or {}).get("branch") or "")
        if branch and (vcs_hint or {}).get("dirty"):
            branch += "*"
        return branch, 0, 0, 0

    branch = ""
    dirty = False
    untracked = 0
    for idx, ln in enumerate(status_out.splitlines()):
        if idx == 0 and ln.startswith("## "):
            head = ln[3:].split("...", 1)[0]
            if head.startswith("No commits yet on "):
                branch = head[len("No commits yet on "):]
            elif head.startswith("HEAD (no branch)"):
                try:
                    branch = run(["rev-parse", "--short", "HEAD"]).strip()
                except Exception:
                    branch = "HEAD"
            else:
                branch = head
            continue
        if ln.startswith("?? "):
            untracked += 1
        elif ln:
            dirty = True

    if branch and dirty:
        branch += "*"

    added = deleted = 0
    if dirty:
        try:
            for ln in run(["diff", "HEAD", "--numstat"]).splitlines():
                parts = ln.split("\t")
                if len(parts) >= 2:
                    if parts[0].isdigit():
                        added += int(parts[0])
                    if parts[1].isdigit():
                        deleted += int(parts[1])
        except Exception:
            pass
    return branch, added, deleted, untracked


def _render_project(current_dir: str, project_dir: str, vcs_hint: dict | None) -> str:
    cur = _clean_dir(current_dir)
    proj = _clean_dir(project_dir) or cur
    if not proj:
        return ""
    root_name = os.path.basename(proj.rstrip("/")) or proj
    label = root_name
    if cur and proj and cur != proj:
        try:
            rel = os.path.relpath(cur, proj)
            if rel != "." and not rel.startswith(".."):
                sub = rel if len(rel) <= 16 else "…/" + os.path.basename(cur.rstrip("/"))
                label = f"{root_name}/{sub}"
        except Exception:
            pass
    git_dir = cur if cur and os.path.isdir(cur) else proj
    branch, added, deleted, untracked = _git_status(git_dir, vcs_hint)
    proj_part = f"{BOLD}{C['project']}[{label}]{RST}"
    if not branch:
        return proj_part
    inner = f"{C['branch']}{branch}{RST}"
    if added:
        inner += f" {C['added']}+{added}{RST}"
    if deleted:
        inner += f" {C['deleted']}-{deleted}{RST}"
    if untracked:
        inner += f" {C['untracked']}?{untracked}{RST}"
    return f"{proj_part}({inner})"


def _parse_iso_ts(ts: str) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _read_transcript_stats(transcript_path: str) -> tuple[int, float | None, int, int, int]:
    """Return (turns, first_ts, sum_input, sum_cache_read, sum_output)."""
    if not transcript_path or not os.path.isfile(transcript_path):
        return 0, None, 0, 0, 0
    turns = 0
    first_ts: float | None = None
    sum_in = sum_cr = sum_out = 0
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if first_ts is None or '"PLANNER_RESPONSE"' in line or '"USER_INPUT"' in line:
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if first_ts is None:
                        first_ts = _parse_iso_ts(str(obj.get("created_at") or ""))
                    t = obj.get("type")
                    if t == "USER_INPUT":
                        turns += 1
                    elif t == "PLANNER_RESPONSE":
                        sum_in += int(obj.get("input_tokens") or 0)
                        sum_cr += int(obj.get("cache_read_tokens") or 0)
                        sum_out += int(obj.get("output_tokens") or 0)
    except Exception:
        pass
    return turns, first_ts, sum_in, sum_cr, sum_out


def _model_label(model_val: object) -> tuple[str, str]:
    """Return (display_label, raw_model_id_or_name)."""
    if isinstance(model_val, str):
        s = model_val.strip()
        return s, s
    if isinstance(model_val, dict):
        mid = str(model_val.get("id") or "").strip()
        name = str(model_val.get("display_name") or mid).strip()
        effort = str(model_val.get("effort") or "").strip()
        if effort and name and effort.lower() not in name.lower():
            return f"{name} ({effort.capitalize()})", (mid or name)
        return name, (mid or name)
    return "", ""


def _default_settings_model() -> str:
    try:
        s = json.loads((_agy_home() / "settings.json").read_text(encoding="utf-8"))
        return str(s.get("model") or "").strip()
    except Exception:
        return ""


def _find_agy_bin() -> str | None:
    env_bin = os.environ.get("TT_AGY_BIN", "").strip()
    if env_bin:
        return env_bin if os.path.isfile(env_bin) and os.access(env_bin, os.X_OK) else None
    found = shutil.which("agy")
    if found:
        return found
    cand = Path.home() / ".local" / "bin" / "agy"
    return str(cand) if cand.is_file() and os.access(cand, os.X_OK) else None


def refresh_quota_sync() -> int:
    """Invoke `agy -p /usage --output-format json` and atomically write quota cache."""
    agy_bin = _find_agy_bin()
    if not agy_bin:
        return 1
    cache_dir = _agy_home() / "cache"
    quota_file = cache_dir / "statusline-quota.json"
    env = dict(os.environ, AGY_STATUSLINE_REFRESH="1")
    try:
        res = subprocess.run(
            [agy_bin, "-p", "/usage", "--output-format", "json",
             "--settings", '{"statusLine":{"enabled":false}}'],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=18,
            env=env,
        )
        if res.returncode != 0 or not res.stdout.strip():
            return 1
        data = json.loads(res.stdout)
        groups = ((data.get("command") or {}).get("data") or {}).get("groups")
        if not isinstance(groups, list) or not groups:
            return 1
        cache_dir.mkdir(parents=True, exist_ok=True)
        payload = {"fetched_at": time.time(), "groups": groups}
        fd, tmp = tempfile.mkstemp(dir=str(cache_dir), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp, quota_file)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return 0
    except Exception:
        return 1


def _spawn_quota_refresh() -> None:
    """Detached background refresh if lock cooldown has elapsed."""
    try:
        cache_dir = _agy_home() / "cache"
        lock_file = cache_dir / "statusline-quota.json.lock"
        cache_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        try:
            if now - lock_file.stat().st_mtime < QUOTA_LOCK_COOLDOWN:
                return
        except FileNotFoundError:
            pass
        lock_file.write_text(str(os.getpid()), encoding="utf-8")
        os.utime(lock_file, (now, now))
        cmd = [sys.executable, str(Path(__file__).resolve()), "--refresh-quota"]
        kw: dict = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":
            kw["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            kw["start_new_session"] = True
        subprocess.Popen(cmd, **kw)
    except Exception:
        pass


def _load_quota_groups() -> list[dict]:
    now = time.time()
    quota_file = _agy_home() / "cache" / "statusline-quota.json"
    data = None
    try:
        data = json.loads(quota_file.read_text(encoding="utf-8"))
    except Exception:
        data = None
    fetched_at = data.get("fetched_at") if isinstance(data, dict) else None
    groups = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(fetched_at, (int, float)) or now - fetched_at > QUOTA_REFRESH_INTERVAL:
        _spawn_quota_refresh()
    if isinstance(fetched_at, (int, float)) and now - fetched_at <= QUOTA_DISPLAY_MAX_AGE and isinstance(groups, list):
        return groups
    return []


def _match_quota_group(groups: list[dict], model_hint: str) -> dict | None:
    if not groups:
        return None
    m = (model_hint or "").lower()
    if "argon" in m or "orchestrator" in m:
        for g in groups:
            if "argon" in str(g.get("name", "")).lower():
                return g
    if any(k in m for k in ("claude", "opus", "sonnet", "gpt")):
        for g in groups:
            gname = str(g.get("name", "")).lower()
            if "claude" in gname or "gpt" in gname:
                return g
    clean = m.split("(")[0].strip().replace("-", " ")
    if clean:
        for g in groups:
            desc = str(g.get("description", "")).lower().replace("-", " ")
            if clean in desc:
                return g
    if "gemini" in m or "hydrogen" in m or "flash" in m or not m:
        for g in groups:
            gname = str(g.get("name", "")).lower()
            if "gemini" in gname and "argon" not in gname:
                return g
    return groups[0]


def _extract_buckets(group: dict | None) -> list[tuple[str, float, float | None]]:
    if not group:
        return []
    out: list[tuple[str, float, float | None]] = []
    buckets = group.get("buckets") or []
    by_window: dict[str, dict] = {}
    for b in buckets:
        if isinstance(b, dict) and b.get("window"):
            by_window[str(b["window"])] = b
    for win, label in (("5h", "5h"), ("weekly", "7d")):
        b = by_window.get(win)
        if not b:
            continue
        rem = b.get("remaining_fraction")
        if not isinstance(rem, (int, float)):
            continue
        used_pct = max(0.0, min(100.0, (1.0 - float(rem)) * 100.0))
        reset_ts = _parse_iso_ts(str(b.get("reset_time") or "")) if rem < 0.9999 else None
        out.append((label, used_pct, reset_ts))
    return out


def _lookup_title(sid: str, payload: dict, transcript_path: str) -> str:
    for key in ("conversation_title", "title"):
        val = str(payload.get(key) or "").strip()
        if val:
            return val
    agy_dir = _agy_home()
    if sid and sid != "pending":
        meta_file = agy_dir / "cache" / "conversation_metadata.json"
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            title = str((((meta.get("conversations") or {}).get(sid) or {}).get("summary") or {}).get("Title") or "").strip()
            if title:
                return title
        except Exception:
            pass
        db_file = agy_dir / "conversation_summaries.db"
        if db_file.is_file():
            try:
                conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True, timeout=0.2)
                try:
                    row = conn.execute(
                        "SELECT title FROM conversation_summaries WHERE conversation_id = ? LIMIT 1",
                        (sid,),
                    ).fetchone()
                    if row and row[0]:
                        return str(row[0]).strip()
                finally:
                    conn.close()
            except Exception:
                pass
    if transcript_path and os.path.isfile(transcript_path):
        try:
            with open(transcript_path, encoding="utf-8", errors="replace") as f:
                for idx, line in enumerate(f):
                    if idx > 20:
                        break
                    if '"USER_INPUT"' in line:
                        obj = json.loads(line)
                        if obj.get("type") == "USER_INPUT":
                            txt = str(obj.get("content") or "")
                            txt = txt.replace("<USER_REQUEST>", "").split("</USER_REQUEST>", 1)[0].strip()
                            if txt:
                                return txt.splitlines()[0].strip()
        except Exception:
            pass
    return ""


def _terminal_width(payload: dict) -> int:
    tw = payload.get("terminal_width")
    if isinstance(tw, (int, float)) and tw > 0:
        return max(40, int(tw) - 2)
    env_cols = os.environ.get("COLUMNS", "")
    if env_cols.isdigit() and int(env_cols) > 0:
        return max(40, int(env_cols) - 2)
    try:
        return max(40, os.get_terminal_size(2).columns - 2)
    except Exception:
        return 120


def _forward_cli_agents_hook(raw: str) -> None:
    surface_id = os.environ.get("CLI_AGENTS_SURFACE_ID")
    hook_cli = os.environ.get("CLI_AGENTS_HOOK_CLI")
    if not surface_id or not hook_cli or not os.access(hook_cli, os.X_OK):
        return
    try:
        subprocess.run(
            [hook_cli, "agent", "hook", "agy-statusline"],
            input=raw,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=0.5,
        )
    except Exception:
        pass


def main() -> None:
    if "--refresh-quota" in sys.argv:
        sys.exit(refresh_quota_sync())
    if os.environ.get("AGY_STATUSLINE_REFRESH") == "1":
        return

    raw = sys.stdin.read()
    _forward_cli_agents_hook(raw)
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    width = _terminal_width(payload)
    ws = payload.get("workspace") if isinstance(payload.get("workspace"), dict) else {}
    current_dir = str(ws.get("current_dir") or payload.get("cwd") or "")
    project_dir = str(ws.get("project_dir") or current_dir)
    vcs_hint = payload.get("vcs") if isinstance(payload.get("vcs"), dict) else None

    transcript_path = str(payload.get("transcript_path") or "")
    turns, first_ts, tr_in, tr_cr, tr_out = _read_transcript_stats(transcript_path)

    ctx = payload.get("context_window") if isinstance(payload.get("context_window"), dict) else {}
    cur_usage = ctx.get("current_usage") if isinstance(ctx.get("current_usage"), dict) else {}

    if tr_in or tr_cr or tr_out:
        total_tok = tr_in + tr_cr + tr_out
        in_den = tr_in + tr_cr
        cache_pct = round(tr_cr / in_den * 100) if in_den > 0 else 0
    else:
        t_in = int(ctx.get("total_input_tokens") or 0)
        t_out = int(ctx.get("total_output_tokens") or 0)
        total_tok = t_in + t_out
        c_in = int(cur_usage.get("input_tokens") or 0)
        c_cr = int(cur_usage.get("cache_read_input_tokens") or 0)
        c_cc = int(cur_usage.get("cache_creation_input_tokens") or 0)
        c_den = c_in + c_cr + c_cc
        cache_pct = round(c_cr / c_den * 100) if c_den > 0 else 0

    model_str, model_hint = _model_label(payload.get("model"))
    if not model_hint:
        fallback_model = _default_settings_model()
        model_hint = fallback_model
        if not model_str:
            model_str = fallback_model
    proj_seg = _render_project(current_dir, project_dir, vcs_hint)

    dur_str = ""
    if first_ts is not None:
        dur_str = _fmt_duration(datetime.now(timezone.utc).timestamp() - first_ts)
    turn_dur = ""
    if turns and dur_str:
        turn_dur = f"{turns}t · {dur_str}"
    elif turns:
        turn_dur = f"{turns}t"
    elif dur_str:
        turn_dur = dur_str

    sid = str(payload.get("conversation_id") or payload.get("session_id") or "").strip() or "pending"
    raw_title = _lookup_title(sid, payload, transcript_path)

    # --- Line 1 (项目 | 模型 | Total | 轮次时长) ---
    def build_line1(include_cache: bool, include_model_prefix: bool, include_turn_dur: bool) -> str:
        segs = []
        if proj_seg:
            segs.append(proj_seg)
        if model_str:
            prefix = "Model: " if include_model_prefix else ""
            segs.append(f"{C['model']}{prefix}{model_str}{RST}")
        if total_tok > 0:
            cache_s = f" ({cache_pct}% cache)" if include_cache and cache_pct > 0 else ""
            segs.append(f"{C['tokens']}Total: {_fmt_tokens(total_tok)}{cache_s}{RST}")
        if include_turn_dur and turn_dur:
            segs.append(f"{C['label']}{turn_dur}{RST}")
        return " | ".join(segs)

    line1 = build_line1(True, True, True)
    for args in (
        (False, True, True),
        (False, False, True),
        (False, False, False),
    ):
        if _vlen(line1) <= width:
            break
        line1 = build_line1(*args)

    # --- Line 2 (5h | 7d | Ctx) ---
    now_ts = int(time.time())
    groups = _load_quota_groups()
    quota_buckets = _extract_buckets(_match_quota_group(groups, model_hint))

    ctx_pct_val = ctx.get("used_percentage")
    ctx_pct = float(ctx_pct_val) if isinstance(ctx_pct_val, (int, float)) else None
    ctx_size = int(ctx.get("context_window_size") or 0)
    if ctx_pct is None and ctx_size > 0 and cur_usage:
        cur_used = (
            int(cur_usage.get("input_tokens") or 0)
            + int(cur_usage.get("cache_read_input_tokens") or 0)
            + int(cur_usage.get("cache_creation_input_tokens") or 0)
        )
        if cur_used > 0:
            ctx_pct = min(100.0, cur_used / ctx_size * 100.0)
    ctx_used_est = (ctx_size * ctx_pct / 100.0) if (ctx_pct is not None and ctx_size > 0) else None

    def build_line2(bar_w: int, include_reset: bool, include_cap: bool) -> str:
        segs = []
        for label, used_pct, reset_ts in quota_buckets:
            if bar_w > 0:
                seg = f"{C['label']}{label}{RST} {_bar(used_pct, bar_w)}"
            else:
                seg = f"{C['label']}{label}:{RST}{_pct_color(used_pct)}{used_pct:.0f}%{RST}"
            if include_reset and reset_ts and reset_ts > now_ts:
                seg += f" {FAINT}{C['label']}({_fmt_duration(reset_ts - now_ts)}){RST}"
            segs.append(seg)

        cap = ""
        if include_cap and ctx_used_est is not None and ctx_size > 0:
            cap = f" {FAINT}{C['label']}({_fmt_tokens(ctx_used_est)}/{_fmt_tokens(ctx_size)}){RST}"
        if bar_w > 0:
            segs.append(f"{C['label']}Ctx{RST} {_bar(ctx_pct, bar_w)}{cap}")
        else:
            ctx_txt = f"{_pct_color(ctx_pct)}{ctx_pct:.0f}%{RST}" if ctx_pct is not None else f"{C['label']}--{RST}"
            segs.append(f"{C['label']}Ctx:{RST}{ctx_txt}")
        return " | ".join(segs)

    line2 = build_line2(8, True, True)
    for args in (
        (8, True, False),
        (6, True, False),
        (6, False, False),
        (4, False, False),
        (0, False, False),
    ):
        if _vlen(line2) <= width:
            break
        line2 = build_line2(*args)

    # --- Line 3 (Title | SID) ---
    sid_full = f"{C['label']}SID:{RST} {sid}"
    sid_bare = sid
    line3_segs = []
    if raw_title:
        avail = width - len(" | ") - _vlen(sid_full)
        if avail >= 8:
            t_disp = _truncate_display(raw_title, min(52, avail))
            if t_disp:
                line3_segs.append(f"{C['tokens']}{t_disp}{RST}")
    line3_segs.append(sid_full if _vlen(sid_full) <= width else sid_bare)
    line3 = " | ".join(line3_segs)

    out_lines = [ln for ln in (line1, line2, line3) if ln]
    if out_lines:
        print("\n".join(out_lines))


if __name__ == "__main__":
    main()
