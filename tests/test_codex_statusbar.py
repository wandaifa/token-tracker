import json
import subprocess
from unittest.mock import MagicMock

from token_tracker import codex_statusbar


def test_session_path_matches_only_requested_codex_thread(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_statusbar.codex, "SESSIONS_DIR", str(tmp_path))
    wrong = tmp_path / "rollout-other.jsonl"
    wrong.write_text("", encoding="utf-8")
    target = tmp_path / "2026" / "rollout-thread-1.jsonl"
    target.parent.mkdir()
    target.write_text("", encoding="utf-8")

    assert codex_statusbar._session_path("thread-1") == target
    assert codex_statusbar._session_path("missing") is None


def test_once_renders_exact_session_in_truecolor(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(codex_statusbar.codex, "SESSIONS_DIR", str(tmp_path))
    target = tmp_path / "rollout-thread-1.jsonl"
    target.write_text("\n".join(json.dumps(row) for row in [
        {"type": "session_meta", "payload": {"id": "thread-1", "cwd": str(tmp_path)}},
        {"type": "turn_context", "payload": {"model": "gpt-6-sol", "effort": "high"}},
        {"type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": {"total_tokens": 1000, "input_tokens": 800, "output_tokens": 200},
            "last_token_usage": {"input_tokens": 100}, "model_context_window": 258000,
        }}},
    ]) + "\n", encoding="utf-8")

    assert codex_statusbar.watch("thread-1", once=True) == 0
    output = capsys.readouterr().out
    assert "\x1b[" in output
    assert "Total: 1k" in output and "Model: gpt-6-sol high" in output
    assert len(output.splitlines()) == 2


def test_bottom_split_targets_source_and_restores_focus(monkeypatch, capsys):
    script = codex_statusbar._APPLESCRIPT
    assert "split horizontally with same profile command" in script
    assert "set rows of statusSession to 3" in script
    assert "select sourceSession" in script
    assert "close statusSession" in script
    monkeypatch.setenv("CODEX_THREAD_ID", "thread-1")
    monkeypatch.setenv("ITERM_SESSION_ID", "w0t0:source-uuid")
    run = MagicMock(return_value=subprocess.CompletedProcess([], 0, "tt_statusbar_split_ok\n", ""))
    monkeypatch.setattr(codex_statusbar.subprocess, "run", run)

    assert codex_statusbar.split() == 0
    argv = run.call_args.args[0]
    assert argv[:3] == ["/usr/bin/osascript", "-e", script]
    assert argv[-3] == "--" and argv[-2] == "source-uuid"
    assert "token_tracker.codex_statusbar watch thread-1" in argv[-1]
    assert "已在" in capsys.readouterr().out


def test_once_missing_session_does_not_fall_back_to_other_thread(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(codex_statusbar.codex, "SESSIONS_DIR", str(tmp_path))
    (tmp_path / "rollout-other.jsonl").write_text("", encoding="utf-8")

    assert codex_statusbar.watch("missing", once=True) == 2
    assert "找不到当前 Codex 会话文件" in capsys.readouterr().err
