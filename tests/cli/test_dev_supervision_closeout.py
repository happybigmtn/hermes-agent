from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess

from hermes_cli.dev_run_status import WorkerSession
from hermes_cli.dev_supervision_closeout import (
    CloseoutResult,
    EvidenceGrade,
    RepoSnapshot,
    SteerHistoryEntry,
    _looks_like_tool_call_blob,
    collect_closeout,
    find_worker,
    main,
    telegram_summary,
)
from hermes_cli.dev_manager_events import ManagerEvent


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
    monkeypatch.setattr(
        "hermes_cli.dev_supervision_closeout.matching_events",
        lambda **kwargs: [
            ManagerEvent(
                id="evt1",
                created_at=datetime(2026, 6, 2, 3, 0, tzinfo=timezone.utc),
                event_type="worker-start",
                repo=str(repo),
                worker_session="ludeme-codex",
                intent="Run a supervised Ludeme implementation slice.",
                requested_by="hermes",
                delivery_channel="telegram",
                resulting_artifacts=[".auto/orchestrator/ludeme-codex/prompt.md"],
            )
        ],
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
    assert "Manager Events" in text
    assert "Run a supervised Ludeme implementation slice." in text
    assert ".auto/orchestrator/ludeme-codex/prompt.md" in text
    assert "Steer History" in text
    assert "Supervise repo ludeme session ludeme-codex" in text
    assert "M README.md" in text
    assert "receipt.md" in text
    assert "Grade: `weak`" in text
    assert "pane prose is not proof" in text
    assert "not a code-quality verdict" in text
    assert "Codex must produce the merge/fix/blocked verdict" in text
    summary = telegram_summary(result)
    assert "Dev supervision closeout ready." in summary
    assert "worker: ludeme-codex:0.0" in summary
    assert "evidence: weak" in summary
    assert "manager events: 1" in summary
    assert "steer snippets: 1" in summary
    assert "gbrain:" not in summary
    assert "attention: do not claim worker success from pane prose" in summary
    assert "attention: repo has uncommitted changes" in summary


def test_collect_closeout_grades_fresh_machine_receipt_verified(tmp_path, monkeypatch):
    repo = tmp_path / "ludeme"
    _init_repo(repo)
    receipts = repo / "verification-receipts"
    receipts.mkdir()
    (receipts / "t_123.json").write_text('{"status": "passed", "tests": ["cargo test"]}\n', encoding="utf-8")

    monkeypatch.setattr("hermes_cli.dev_supervision_closeout.capture_pane", lambda target, lines: [])
    monkeypatch.setattr("hermes_cli.dev_supervision_closeout.matching_events", lambda **kwargs: [])

    result = collect_closeout(
        repo=repo,
        session="ludeme-codex",
        report_dir=tmp_path / "reports",
        generated_at=datetime(2026, 6, 2, 3, 30, tzinfo=timezone.utc),
        write_gbrain=False,
        workers=[_worker(repo)],
        include_steer_history=False,
    )

    assert result.evidence_grade.grade == "verified"
    text = result.markdown_path.read_text(encoding="utf-8")
    assert "Grade: `verified`" in text
    assert "verification-receipts/t_123.json" in text
    assert "status `passed`" in text
    assert "evidence: verified" in telegram_summary(result)


def test_collect_closeout_marks_machine_receipt_stale_after_new_manager_event(tmp_path, monkeypatch):
    repo = tmp_path / "ludeme"
    _init_repo(repo)
    receipts = repo / "verification-receipts"
    receipts.mkdir()
    receipt = receipts / "t_123.json"
    receipt.write_text('{"status": "passed"}\n', encoding="utf-8")
    old_ts = datetime(2026, 6, 2, 3, 0, tzinfo=timezone.utc).timestamp()
    os.utime(receipt, (old_ts, old_ts))
    event_time = datetime(2026, 6, 2, 3, 30, tzinfo=timezone.utc)

    monkeypatch.setattr("hermes_cli.dev_supervision_closeout.capture_pane", lambda target, lines: [])
    monkeypatch.setattr(
        "hermes_cli.dev_supervision_closeout.matching_events",
        lambda **kwargs: [
            ManagerEvent(
                id="evt1",
                created_at=event_time,
                event_type="worker-start",
                repo=str(repo),
                worker_session="ludeme-codex",
                intent="Run newer work.",
                requested_by="hermes",
                delivery_channel="telegram",
                resulting_artifacts=[],
            )
        ],
    )

    result = collect_closeout(
        repo=repo,
        session="ludeme-codex",
        report_dir=tmp_path / "reports",
        generated_at=datetime(2026, 6, 2, 3, 45, tzinfo=timezone.utc),
        write_gbrain=False,
        workers=[_worker(repo)],
        include_steer_history=False,
    )

    assert result.evidence_grade.grade == "stale"
    text = result.markdown_path.read_text(encoding="utf-8")
    assert "none are newer than the latest manager event" in text
    assert "Freshness threshold: `2026-06-02T03:30:00+00:00`" in text
    summary = telegram_summary(result)
    assert "evidence: stale" in summary
    assert "attention: do not claim worker success from pane prose" in summary


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
            evidence_grade=EvidenceGrade(
                grade="weak",
                reason="no machine-readable receipt found; pane prose is not proof",
                receipts=[],
            ),
            pane_capture_lines=0,
            manager_events=[],
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
