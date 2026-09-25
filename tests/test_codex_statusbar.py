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


def test_tmux_line_uses_active_pane_mapping(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(codex_statusbar.codex, "SESSIONS_DIR", str(tmp_path))
    mapping = tmp_path / "tt-terminal-map.json"
    mapping.write_text(json.dumps({"_terminal_map": {
        "other": {"tmux": "%1"}, "thread-1": {"tmux": "%2"}, "kimi-session": {"tmux": "%2"},
    }}), encoding="utf-8")
    monkeypatch.setattr(codex_statusbar, "TERMINAL_MAP_FILE", str(mapping))
    (tmp_path / "rollout-thread-1.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr(codex_statusbar, "_render", lambda script, session_id, path: "\x1b[38;2;255;0;16mA#B\x1b[0m\nsecond")

    assert codex_statusbar.tmux_line(0, "%2") == 0
    assert capsys.readouterr().out == "#[fg=#ff0010]A##B#[default]\n"
    assert codex_statusbar.tmux_line(1, "%2") == 0
    assert capsys.readouterr().out == "second\n"
    assert codex_statusbar.tmux_line(0, "%9") == 0
    assert "等待当前 Codex" in capsys.readouterr().out


def test_tmux_configures_padded_rows_for_current_pane(monkeypatch, capsys):
    monkeypatch.setenv("TMUX_PANE", "%3")
    run = MagicMock(return_value=subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(codex_statusbar.subprocess, "run", run)

    assert codex_statusbar.tmux() == 0
    commands = [call.args[0] for call in run.call_args_list]
    assert len(commands) == 6
    assert commands[0][0:4] == ["tmux", "set-option", "-t", "%3"]
    assert commands[0][4] == "status-format[0]" and commands[0][5].startswith("  #(")
    assert "tmux-line 0 #{pane_id}" in commands[0][5]
    assert commands[1][4] == "status-format[1]" and commands[1][5].startswith("  #(")
    assert "tmux-line 1 #{pane_id}" in commands[1][5]
    assert commands[2][-2:] == ["status-format[2]", ""]
    assert commands[3][-2:] == ["status", "3"]
    assert commands[4][-2:] == ["status-style", "bg=default,fg=default"]
    assert commands[5][-2:] == ["status-interval", "10"]
    assert "两行状态栏" in capsys.readouterr().out
