from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import subprocess

from hermes_cli.dev_run_status import WorkerSession
from hermes_cli.dev_supervision_closeout import (
    CloseoutResult,
    RepoSnapshot,
    SteerHistoryEntry,
    _looks_like_tool_call_blob,
    collect_closeout,
    find_worker,
    main,
    telegram_summary,
)


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "r@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "R"], cwd=path, check=True)
    (path / "README.md").write_text("demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def _worker(repo: Path) -> WorkerSession:
    return WorkerSession(
        session_name="ludeme-codex",
        window_index="0",
        pane_index="0",
        pane_pid=123,
        current_command="node",
        current_path=repo,
        active=True,
        dead=False,
        title="worker",
    )


def test_find_worker_matches_session_name_and_target(tmp_path):
    repo = tmp_path / "ludeme"
    worker = _worker(repo)

    assert find_worker("ludeme-codex", [worker]) == worker
    assert find_worker("ludeme-codex:0.0", [worker]) == worker
    assert find_worker("missing", [worker]) is None


def test_collect_closeout_writes_report_without_real_tmux_or_gbrain(tmp_path, monkeypatch):
    repo = tmp_path / "ludeme"
    _init_repo(repo)
    auto = repo / ".auto" / "orchestrator" / "run1"
    auto.mkdir(parents=True)
    (auto / "receipt.md").write_text("ok\n", encoding="utf-8")
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")

    monkeypatch.setattr(
        "hermes_cli.dev_supervision_closeout.capture_pane",
        lambda target, lines: ["Codex working", "tests passed"],
    )
    monkeypatch.setattr(
        "hermes_cli.dev_supervision_closeout.default_dashboard_url",
        lambda: "http://100.64.0.1:8765/dev-run-status-latest.html",
    )
    monkeypatch.setattr(
        "hermes_cli.dev_supervision_closeout.collect_steer_history",
        lambda **kwargs: (
            [
                SteerHistoryEntry(
                    db_path=tmp_path / "state.db",
                    session_id="20260601_demo",
                    role="user",
                    timestamp=1780370000.0,
                    source="telegram",
                    snippet="Supervise repo ludeme session ludeme-codex",
                )
            ],
            None,
        ),
    )

    result = collect_closeout(
        repo=repo,
        session="ludeme-codex",
        report_dir=tmp_path / "reports",
        generated_at=datetime(2026, 6, 2, 3, 30, tzinfo=timezone.utc),
        write_gbrain=False,
        workers=[_worker(repo)],
    )

    assert result.markdown_path.exists()
    text = result.markdown_path.read_text(encoding="utf-8")
    assert "Dev Supervision Closeout" in text
    assert "ludeme-codex:0.0" in text
    assert "Codex working" in text
    assert "Steer History" in text
    assert "Supervise repo ludeme session ludeme-codex" in text
    assert "M README.md" in text
    assert "receipt.md" in text
    summary = telegram_summary(result)
    assert "Dev supervision closeout ready." in summary
    assert "worker: ludeme-codex:0.0" in summary
    assert "steer snippets: 1" in summary
    assert "gbrain:" not in summary
    assert "attention: repo has uncommitted changes" in summary


def test_render_markdown_handles_missing_worker(tmp_path):
    repo = tmp_path / "demo"
    _init_repo(repo)
    result = collect_closeout(
        repo=repo,
        session="missing-worker",
        report_dir=tmp_path / "reports",
        generated_at=datetime(2026, 6, 2, 3, 30, tzinfo=timezone.utc),
        write_gbrain=False,
        workers=[],
        dashboard_url=None,
        include_steer_history=False,
    )

    text = result.markdown_path.read_text(encoding="utf-8")
    assert "Worker state: `missing`" in text
    assert "Worker kind: `unknown`" in text


def test_main_uses_default_steer_db_paths_when_flag_omitted(tmp_path, monkeypatch, capsys):
    captured = {}

    def fake_collect_closeout(**kwargs):
        captured.update(kwargs)
        return CloseoutResult(
            repo=tmp_path / "demo",
            session="demo-codex",
            generated_at=datetime(2026, 6, 2, 3, 30, tzinfo=timezone.utc),
            markdown_path=tmp_path / "report.md",
            dashboard_url=None,
            worker=None,
            repo_snapshot=RepoSnapshot(
                path=tmp_path / "demo",
                branch="main",
                status_short=[],
                recent_commits=[],
                diff_stat="",
                recent_artifacts=[],
            ),
            pane_capture_lines=0,
            steer_history=[],
            steer_history_error=None,
            gbrain_slug=None,
            gbrain_error=None,
        )

    monkeypatch.setattr("hermes_cli.dev_supervision_closeout.collect_closeout", fake_collect_closeout)

    assert main(["--repo", str(tmp_path / "demo"), "--session", "demo-codex", "--no-gbrain"]) == 0

    assert captured["steer_db_paths"] is None
    assert captured["steer_search_terms"] is None
    assert "steer snippets: 0" in capsys.readouterr().out


def test_tool_call_blob_snippets_are_filtered():
    assert _looks_like_tool_call_blob(
        '[{"id": "call-1", "call_id": "call-1", "function": {"name": "terminal"}}]'
    )
    assert not _looks_like_tool_call_blob("Supervise repo ludeme session ludeme-codex")
