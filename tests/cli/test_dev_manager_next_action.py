from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.dev_manager_next_action import (
    ManagerPacket,
    NextAction,
    SafeExecutionResult,
    _should_print_execution_result,
    collect_board_snapshots,
    collect_manager_packet,
    execute_safe_action,
    recommend_next_action,
    render_markdown,
    telegram_cadence,
    telegram_summary,
    write_gbrain_page,
)
from hermes_cli.dev_manager_events import ManagerEvent, load_events
from hermes_cli.dev_orchestrator_preflight import Check, PreflightResult, ProfilePreflight
from hermes_cli.dev_run_status import WorkerSession


@pytest.fixture
def isolated_kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


@pytest.fixture(autouse=True)
def clean_repo_status_by_default(monkeypatch):
    monkeypatch.setattr(
        "hermes_cli.dev_manager_next_action._repo_has_uncommitted_changes",
        lambda repo: False,
    )


def _preflight(now: datetime, *, failing: bool = False) -> PreflightResult:
    profile = ProfilePreflight(
        name="orchestrator",
        profile_root=Path("/tmp/hermes"),
        subprocess_home=Path("/tmp/home"),
        purpose="routes campaigns",
    )
    profile.checks.append(
        Check(
            "command:auto",
            not failing,
            "/bin/auto" if not failing else "missing",
        )
    )
    return PreflightResult(
        generated_at=now,
        hermes_root=Path("/tmp/hermes"),
        default_home=Path("/tmp/home"),
        profiles=[profile],
    )


def test_next_action_prioritizes_preflight_failure(isolated_kanban_home):
    now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
    kb.create_board("ludeme")
    conn = kb.connect(board="ludeme")
    try:
        kb.create_task(conn, title="ready work", assignee="codexworker", priority=50)
    finally:
        conn.close()

    boards = collect_board_snapshots(now=now, stale_after=timedelta(minutes=30))
    action = recommend_next_action(preflight=_preflight(now, failing=True), boards=boards, runs=[])

    assert action.kind == "fix-preflight"
    assert "command:auto" in action.reason


def test_next_action_flags_blocked_codexworker_for_repair(isolated_kanban_home):
    now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
    kb.create_board("ludeme")
    conn = kb.connect(board="ludeme")
    try:
        tid = kb.create_task(
            conn,
            title="implement motion vocabulary",
            assignee="codexworker",
            priority=100,
        )
        kb.block_task(conn, tid, reason="review-required; tests not run due to env")
    finally:
        conn.close()

    packet = collect_manager_packet(
        now=now,
        repo_roots=[],
        stale_after=timedelta(minutes=30),
        recent_after=now - timedelta(hours=12),
        preflight=_preflight(now),
        runs=[],
        workers=[],
    )

    assert packet.next_action.kind == "repair-blocked-task"
    assert packet.next_action.board == "ludeme"
    assert packet.next_action.task_id == tid
    assert "unverified" in packet.next_action.reason
    assert "Kanban comment/status/run" in packet.next_action.evidence_requirement()
    markdown = render_markdown(packet)
    assert "Dev Orchestrator Manager Tick" in markdown
    assert tid in markdown
    assert "review-required" in markdown
    assert "Evidence after:" in markdown
    assert now.isoformat() in markdown
    assert "Required evidence:" in markdown
    assert "Evidence must be fresh" in markdown


def test_next_action_supervises_active_worker_before_stale_board_packets(isolated_kanban_home, monkeypatch):
    now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
    kb.create_board("ludeme")
    conn = kb.connect(board="ludeme")
    try:
        kb.create_task(
            conn,
            title="blocked but not more important than the live worker",
            assignee="codexworker",
            priority=100,
        )
    finally:
        conn.close()

    worker = WorkerSession(
        session_name="ludeme-codex",
        window_index="0",
        pane_index="0",
        pane_pid=123,
        current_command="node",
        current_path=Path("/srv/dev/repos/ludeme"),
        active=True,
        dead=False,
        title="codex",
    )
    monkeypatch.setattr("hermes_cli.dev_manager_next_action._latest_commit", lambda repo: None)
    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: [])
    monkeypatch.setattr("hermes_cli.dev_manager_next_action.capture_worker_pane", lambda target: [])

    packet = collect_manager_packet(
        now=now,
        repo_roots=[],
        stale_after=timedelta(minutes=30),
        recent_after=now - timedelta(hours=12),
        preflight=_preflight(now),
        runs=[],
        workers=[worker],
    )

    assert packet.next_action.kind == "supervise-interactive-worker"
    assert packet.next_action.worker == "ludeme-codex:0.0"
    summary = telegram_summary(packet)
    assert "Dev manager status: supervise-interactive-worker" in summary
    assert "Human action: none" in summary
    assert "Command:" not in summary
    assert "Interactive workers: 1" not in summary
    assert "fresh worker closeout/receipt" in summary
    assert telegram_summary(packet, quiet_routine=True) == ""


def test_next_action_runs_codex_review_for_unreviewed_worker_commit(isolated_kanban_home, monkeypatch):
    now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
    repo = Path("/srv/dev/repos/ludeme")
    worker = WorkerSession(
        session_name="ludeme-codex",
        window_index="0",
        pane_index="0",
        pane_pid=123,
        current_command="node",
        current_path=repo,
        active=True,
        dead=False,
        title="codex",
    )
    commit = "abcdef1234567890"
    monkeypatch.setattr(
        "hermes_cli.dev_manager_next_action._latest_commit",
        lambda repo: (commit, datetime(2026, 6, 2, 1, 44, tzinfo=timezone.utc), "demo commit"),
    )
    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: [])
    monkeypatch.setattr("hermes_cli.dev_manager_next_action.capture_worker_pane", lambda target: [])

    packet = collect_manager_packet(
        now=now,
        repo_roots=[],
        stale_after=timedelta(minutes=30),
        recent_after=now - timedelta(hours=12),
        preflight=_preflight(now),
        runs=[],
        workers=[worker],
    )

    assert packet.next_action.kind == "codex-review-worker"
    assert packet.next_action.review_commit == commit
    assert packet.next_action.review_title == "demo commit"
    assert "unreviewed Codex commit abcdef123456" in packet.next_action.reason
    assert f"--commit {commit}" in packet.next_action.command
    assert "--title 'demo commit'" in packet.next_action.command


def test_next_action_supervises_dirty_codex_worker_before_review(isolated_kanban_home, monkeypatch):
    now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
    repo = Path("/srv/dev/repos/ludeme")
    worker = WorkerSession(
        session_name="ludeme-codex",
        window_index="0",
        pane_index="0",
        pane_pid=123,
        current_command="node",
        current_path=repo,
        active=True,
        dead=False,
        title="codex",
    )
    monkeypatch.setattr("hermes_cli.dev_manager_next_action._repo_has_uncommitted_changes", lambda repo: True)
    monkeypatch.setattr(
        "hermes_cli.dev_manager_next_action._latest_commit",
        lambda repo: (
            "abcdef1234567890",
            datetime(2026, 6, 2, 1, 44, tzinfo=timezone.utc),
            "demo commit",
        ),
    )
    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: [])
    monkeypatch.setattr("hermes_cli.dev_manager_next_action.capture_worker_pane", lambda target: [])

    packet = collect_manager_packet(
        now=now,
        repo_roots=[],
        stale_after=timedelta(minutes=30),
        recent_after=now - timedelta(hours=12),
        preflight=_preflight(now),
        runs=[],
        workers=[worker],
    )

    assert packet.next_action.kind == "supervise-interactive-worker"
    assert packet.next_action.worker == "ludeme-codex:0.0"
    assert "uncommitted repo changes" in packet.next_action.reason
    assert "status --short --branch" in packet.next_action.command


def test_next_action_does_not_duplicate_reviewed_worker_commit(isolated_kanban_home, monkeypatch):
    now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
    repo = Path("/srv/dev/repos/ludeme")
    worker = WorkerSession(
        session_name="ludeme-codex",
        window_index="0",
        pane_index="0",
        pane_pid=123,
        current_command="node",
        current_path=repo,
        active=True,
        dead=False,
        title="codex",
    )
    monkeypatch.setattr(
        "hermes_cli.dev_manager_next_action._latest_commit",
        lambda repo: ("abcdef1234567890", datetime(2026, 6, 2, 1, 40, tzinfo=timezone.utc), "demo commit"),
    )
    monkeypatch.setattr(
        "hermes_cli.dev_manager_next_action.matching_events",
        lambda **kwargs: [
            ManagerEvent(
                id="review",
                created_at=datetime(2026, 6, 2, 1, 44, tzinfo=timezone.utc),
                event_type="codex-review",
                repo=str(repo),
            worker_session="ludeme-codex",
            intent="review",
            notes="codex review returncode=0",
        )
        ],
    )
    monkeypatch.setattr("hermes_cli.dev_manager_next_action.capture_worker_pane", lambda target: [])

    packet = collect_manager_packet(
        now=now,
        repo_roots=[],
        stale_after=timedelta(minutes=30),
        recent_after=now - timedelta(hours=12),
        preflight=_preflight(now),
        runs=[],
        workers=[worker],
    )

    assert packet.next_action.kind == "supervise-interactive-worker"


def test_next_action_reports_failed_codex_review_event_before_repair(isolated_kanban_home, monkeypatch):
    now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
    repo = Path("/srv/dev/repos/ludeme")
    worker = WorkerSession(
        session_name="ludeme-codex",
        window_index="0",
        pane_index="0",
        pane_pid=123,
        current_command="node",
        current_path=repo,
        active=True,
        dead=False,
        title="codex",
    )
    monkeypatch.setattr(
        "hermes_cli.dev_manager_next_action._latest_commit",
        lambda repo: ("abcdef1234567890", datetime(2026, 6, 2, 1, 40, tzinfo=timezone.utc), "demo commit"),
    )
    monkeypatch.setattr(
        "hermes_cli.dev_manager_next_action.matching_events",
        lambda **kwargs: [
            ManagerEvent(
                id="review",
                created_at=datetime(2026, 6, 2, 1, 44, tzinfo=timezone.utc),
                event_type="codex-review",
                repo=str(repo),
                worker_session="ludeme-codex",
                intent="review",
                notes="codex review returncode=2",
            )
        ],
    )
    monkeypatch.setattr("hermes_cli.dev_manager_next_action.capture_worker_pane", lambda target: [])

    packet = collect_manager_packet(
        now=now,
        repo_roots=[],
        stale_after=timedelta(minutes=30),
        recent_after=now - timedelta(hours=12),
        preflight=_preflight(now),
        runs=[],
        workers=[worker],
    )

    assert packet.next_action.kind == "report-codex-review-result"
    assert packet.next_action.review_event_id == "review"


def test_next_action_waits_for_pending_codex_review(isolated_kanban_home, monkeypatch):
    now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
    repo = Path("/srv/dev/repos/ludeme")
    worker = WorkerSession(
        session_name="ludeme-codex",
        window_index="0",
        pane_index="0",
        pane_pid=123,
        current_command="node",
        current_path=repo,
        active=True,
        dead=False,
        title="codex",
    )
    commit = "abcdef1234567890"
    monkeypatch.setattr(
        "hermes_cli.dev_manager_next_action._latest_commit",
        lambda repo: (commit, datetime(2026, 6, 2, 1, 40, tzinfo=timezone.utc), "demo commit"),
    )
    monkeypatch.setattr(
        "hermes_cli.dev_manager_next_action.matching_events",
        lambda **kwargs: [
            ManagerEvent(
                id="start",
                created_at=datetime(2026, 6, 2, 1, 44, tzinfo=timezone.utc),
                event_type="codex-review-start",
                repo=str(repo),
                worker_session="ludeme-codex",
                intent="start review",
                notes=f"pid=42 commit={commit}",
            )
        ],
    )
    monkeypatch.setattr("hermes_cli.dev_manager_next_action.capture_worker_pane", lambda target: [])

    packet = collect_manager_packet(
        now=now,
        repo_roots=[],
        stale_after=timedelta(minutes=30),
        recent_after=now - timedelta(hours=12),
        preflight=_preflight(now),
        runs=[],
        workers=[worker],
    )

    assert packet.next_action.kind == "wait-for-codex-review"
    assert packet.next_action.review_event_id == "start"
    assert "already has Codex review start in progress" in packet.next_action.reason
    assert telegram_summary(packet, quiet_routine=True) == ""


def test_next_action_retries_stale_pending_codex_review(isolated_kanban_home, monkeypatch):
    now = datetime(2026, 6, 2, 4, 45, tzinfo=timezone.utc)
    repo = Path("/srv/dev/repos/ludeme")
    worker = WorkerSession(
        session_name="ludeme-codex",
        window_index="0",
        pane_index="0",
        pane_pid=123,
        current_command="node",
        current_path=repo,
        active=True,
        dead=False,
        title="codex",
    )
    commit = "abcdef1234567890"
    monkeypatch.setattr(
        "hermes_cli.dev_manager_next_action._latest_commit",
        lambda repo: (commit, datetime(2026, 6, 2, 1, 40, tzinfo=timezone.utc), "demo commit"),
    )
    monkeypatch.setattr(
        "hermes_cli.dev_manager_next_action.matching_events",
        lambda **kwargs: [
            ManagerEvent(
                id="start",
                created_at=datetime(2026, 6, 2, 1, 44, tzinfo=timezone.utc),
                event_type="codex-review-start",
                repo=str(repo),
                worker_session="ludeme-codex",
                intent="start review",
                notes=f"pid=42 commit={commit}",
            )
        ],
    )
    monkeypatch.setattr("hermes_cli.dev_manager_next_action.capture_worker_pane", lambda target: [])

    packet = collect_manager_packet(
        now=now,
        repo_roots=[],
        stale_after=timedelta(minutes=30),
        recent_after=now - timedelta(hours=12),
        preflight=_preflight(now),
        runs=[],
        workers=[worker],
    )

    assert packet.next_action.kind == "codex-review-worker"
    assert packet.next_action.review_commit == commit


def test_next_action_reports_completed_async_codex_review_once(isolated_kanban_home, monkeypatch, tmp_path):
    now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
    repo = Path("/srv/dev/repos/ludeme")
    output = tmp_path / "codex-review-output.md"
    output.write_text(
        "# Codex Run Review\n\n## Stdout\n\n```text\nVerdict: READY_TO_MERGE\nConfidence: high\n```\n",
        encoding="utf-8",
    )
    worker = WorkerSession(
        session_name="ludeme-codex",
        window_index="0",
        pane_index="0",
        pane_pid=123,
        current_command="node",
        current_path=repo,
        active=True,
        dead=False,
        title="codex",
    )
    events = [
        ManagerEvent(
            id="start",
            created_at=datetime(2026, 6, 2, 1, 42, tzinfo=timezone.utc),
            event_type="codex-review-start",
            repo=str(repo),
            worker_session="ludeme-codex",
            intent="start review",
        ),
        ManagerEvent(
            id="review",
            created_at=datetime(2026, 6, 2, 1, 44, tzinfo=timezone.utc),
            event_type="codex-review",
            repo=str(repo),
            worker_session="ludeme-codex",
            intent="review",
            resulting_artifacts=[str(output)],
            notes="codex review returncode=0",
        ),
    ]
    monkeypatch.setattr("hermes_cli.dev_manager_next_action._latest_commit", lambda repo: None)
    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: events)
    monkeypatch.setattr("hermes_cli.dev_manager_next_action.capture_worker_pane", lambda target: [])

    packet = collect_manager_packet(
        now=now,
        repo_roots=[],
        stale_after=timedelta(minutes=30),
        recent_after=now - timedelta(hours=12),
        preflight=_preflight(now),
        runs=[],
        workers=[worker],
    )

    assert packet.next_action.kind == "report-codex-review-result"
    assert packet.next_action.review_event_id == "review"


def test_next_action_does_not_repeat_reported_async_codex_review(isolated_kanban_home, monkeypatch, tmp_path):
    now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
    repo = Path("/srv/dev/repos/ludeme")
    output = tmp_path / "codex-review-output.md"
    output.write_text("Verdict: READY_TO_MERGE\n", encoding="utf-8")
    worker = WorkerSession(
        session_name="ludeme-codex",
        window_index="0",
        pane_index="0",
        pane_pid=123,
        current_command="node",
        current_path=repo,
        active=True,
        dead=False,
        title="codex",
    )
    events = [
        ManagerEvent(
            id="start",
            created_at=datetime(2026, 6, 2, 1, 42, tzinfo=timezone.utc),
            event_type="codex-review-start",
            repo=str(repo),
            worker_session="ludeme-codex",
            intent="start review",
        ),
        ManagerEvent(
            id="review",
            created_at=datetime(2026, 6, 2, 1, 44, tzinfo=timezone.utc),
            event_type="codex-review",
            repo=str(repo),
            worker_session="ludeme-codex",
            intent="review",
            resulting_artifacts=[str(output)],
            notes="codex review returncode=0",
        ),
        ManagerEvent(
            id="report",
            created_at=datetime(2026, 6, 2, 1, 44, 30, tzinfo=timezone.utc),
            event_type="codex-review-report",
            repo=str(repo),
            worker_session="ludeme-codex",
            intent="report",
            notes="reported_event=review returncode=0",
        ),
    ]
    monkeypatch.setattr("hermes_cli.dev_manager_next_action._latest_commit", lambda repo: None)
    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: events)
    monkeypatch.setattr("hermes_cli.dev_manager_next_action.capture_worker_pane", lambda target: [])

    packet = collect_manager_packet(
        now=now,
        repo_roots=[],
        stale_after=timedelta(minutes=30),
        recent_after=now - timedelta(hours=12),
        preflight=_preflight(now),
        runs=[],
        workers=[worker],
    )

    assert packet.next_action.kind == "supervise-interactive-worker"


def test_next_action_steers_worker_after_reported_blocked_codex_review(isolated_kanban_home, monkeypatch, tmp_path):
    now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
    repo = Path("/srv/dev/repos/ludeme")
    output = tmp_path / "codex-review-output.md"
    output.write_text("Verdict: BLOCKED\nConfidence: high\n", encoding="utf-8")
    worker = WorkerSession(
        session_name="ludeme-codex",
        window_index="0",
        pane_index="0",
        pane_pid=123,
        current_command="node",
        current_path=repo,
        active=True,
        dead=False,
        title="codex",
    )
    events = [
        ManagerEvent(
            id="review",
            created_at=datetime(2026, 6, 2, 1, 44, tzinfo=timezone.utc),
            event_type="codex-review",
            repo=str(repo),
            worker_session="ludeme-codex",
            intent="review",
            resulting_artifacts=[str(output)],
            notes="codex review returncode=0",
        ),
        ManagerEvent(
            id="report",
            created_at=datetime(2026, 6, 2, 1, 44, 30, tzinfo=timezone.utc),
            event_type="codex-review-report",
            repo=str(repo),
            worker_session="ludeme-codex",
            intent="report",
            notes="reported_event=review returncode=0",
        ),
    ]
    monkeypatch.setattr("hermes_cli.dev_manager_next_action._latest_commit", lambda repo: None)
    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: events)
    monkeypatch.setattr("hermes_cli.dev_manager_next_action.capture_worker_pane", lambda target: [])

    packet = collect_manager_packet(
        now=now,
        repo_roots=[],
        stale_after=timedelta(minutes=30),
        recent_after=now - timedelta(hours=12),
        preflight=_preflight(now),
        runs=[],
        workers=[worker],
    )

    assert packet.next_action.kind == "repair-codex-review-blocker"
    assert packet.next_action.review_event_id == "review"
    assert packet.next_action.human_action().startswith("None by default")


def test_next_action_repairs_failed_review_without_repeating_same_commit(isolated_kanban_home, monkeypatch, tmp_path):
    now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
    repo = Path("/srv/dev/repos/ludeme")
    output = tmp_path / "codex-review-output.md"
    output.write_text(
        "# Codex Run Review\n\n"
        "## Stdout\n\n"
        "- empty\n\n"
        "## Stderr\n\n"
        "```text\n"
        "Output exactly this shape:\n"
        "Verdict: READY_TO_MERGE | FIX_FIRST | BLOCKED\n"
        "bwrap: loopback: Failed RTM_NEWADDR\n"
        "```\n",
        encoding="utf-8",
    )
    worker = WorkerSession(
        session_name="ludeme-codex",
        window_index="0",
        pane_index="0",
        pane_pid=123,
        current_command="node",
        current_path=repo,
        active=True,
        dead=False,
        title="codex",
    )
    events = [
        ManagerEvent(
            id="review",
            created_at=datetime(2026, 6, 2, 1, 44, tzinfo=timezone.utc),
            event_type="codex-review",
            repo=str(repo),
            worker_session="ludeme-codex",
            intent="review",
            resulting_artifacts=[str(output)],
            notes="codex review returncode=1",
        ),
        ManagerEvent(
            id="report",
            created_at=datetime(2026, 6, 2, 1, 44, 30, tzinfo=timezone.utc),
            event_type="codex-review-report",
            repo=str(repo),
            worker_session="ludeme-codex",
            intent="report",
            notes="reported_event=review returncode=1",
        ),
    ]
    monkeypatch.setattr(
        "hermes_cli.dev_manager_next_action._latest_commit",
        lambda repo: ("abcdef1234567890", datetime(2026, 6, 2, 1, 43, tzinfo=timezone.utc), "demo"),
    )
    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: events)
    monkeypatch.setattr("hermes_cli.dev_manager_next_action.capture_worker_pane", lambda target: [])

    packet = collect_manager_packet(
        now=now,
        repo_roots=[],
        stale_after=timedelta(minutes=30),
        recent_after=now - timedelta(hours=12),
        preflight=_preflight(now),
        runs=[],
        workers=[worker],
    )

    assert packet.next_action.kind == "repair-codex-review-blocker"
    assert packet.next_action.review_event_id == "review"


def test_next_action_does_not_repeat_codex_review_repair_dispatch(isolated_kanban_home, monkeypatch, tmp_path):
    now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
    repo = Path("/srv/dev/repos/ludeme")
    output = tmp_path / "codex-review-output.md"
    output.write_text("Verdict: FIX_FIRST\nConfidence: medium\n", encoding="utf-8")
    worker = WorkerSession(
        session_name="ludeme-codex",
        window_index="0",
        pane_index="0",
        pane_pid=123,
        current_command="node",
        current_path=repo,
        active=True,
        dead=False,
        title="codex",
    )
    events = [
        ManagerEvent(
            id="review",
            created_at=datetime(2026, 6, 2, 1, 44, tzinfo=timezone.utc),
            event_type="codex-review",
            repo=str(repo),
            worker_session="ludeme-codex",
            intent="review",
            resulting_artifacts=[str(output)],
            notes="codex review returncode=0",
        ),
        ManagerEvent(
            id="report",
            created_at=datetime(2026, 6, 2, 1, 44, 30, tzinfo=timezone.utc),
            event_type="codex-review-report",
            repo=str(repo),
            worker_session="ludeme-codex",
            intent="report",
            notes="reported_event=review returncode=0",
        ),
        ManagerEvent(
            id="steer",
            created_at=datetime(2026, 6, 2, 1, 44, 45, tzinfo=timezone.utc),
            event_type="worker-steer",
            repo=str(repo),
            worker_session="ludeme-codex",
            intent="Repair Codex review `review`",
        ),
    ]
    monkeypatch.setattr("hermes_cli.dev_manager_next_action._latest_commit", lambda repo: None)
    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: events)
    monkeypatch.setattr("hermes_cli.dev_manager_next_action.capture_worker_pane", lambda target: [])

    packet = collect_manager_packet(
        now=now,
        repo_roots=[],
        stale_after=timedelta(minutes=30),
        recent_after=now - timedelta(hours=12),
        preflight=_preflight(now),
        runs=[],
        workers=[worker],
    )

    assert packet.next_action.kind == "supervise-interactive-worker"


def test_next_action_runs_codex_review_for_review_ready_worker(isolated_kanban_home, monkeypatch):
    now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
    repo = Path("/srv/dev/repos/ludeme")
    worker = WorkerSession(
        session_name="ludeme-codex",
        window_index="0",
        pane_index="0",
        pane_pid=123,
        current_command="node",
        current_path=repo,
        active=True,
        dead=False,
        title="codex",
    )
    monkeypatch.setattr("hermes_cli.dev_manager_next_action._latest_commit", lambda repo: None)
    monkeypatch.setattr(
        "hermes_cli.dev_manager_next_action.capture_worker_pane",
        lambda target: ["Implementation complete. Ready for review."],
    )
    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: [])

    packet = collect_manager_packet(
        now=now,
        repo_roots=[],
        stale_after=timedelta(minutes=30),
        recent_after=now - timedelta(hours=12),
        preflight=_preflight(now),
        runs=[],
        workers=[worker],
    )

    assert packet.next_action.kind == "codex-review-worker"
    assert packet.next_action.worker == "ludeme-codex:0.0"
    assert "scripts/hermes-dev-codex-run-review.py" in packet.next_action.command
    assert "--repo /srv/dev/repos/ludeme" in packet.next_action.command
    assert "--session ludeme-codex" in packet.next_action.command
    assert "Codex review" in packet.next_action.evidence_requirement()
    summary = telegram_summary(packet)
    assert "Dev manager status: codex-review-worker" in summary
    assert "let the next manager tick surface Codex's merge/fix/blocked verdict" in summary


def test_next_action_does_not_duplicate_current_codex_review(isolated_kanban_home, monkeypatch):
    now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
    repo = Path("/srv/dev/repos/ludeme")
    worker = WorkerSession(
        session_name="ludeme-codex",
        window_index="0",
        pane_index="0",
        pane_pid=123,
        current_command="node",
        current_path=repo,
        active=True,
        dead=False,
        title="codex",
    )
    monkeypatch.setattr("hermes_cli.dev_manager_next_action._latest_commit", lambda repo: None)
    events = [
        ManagerEvent(
            id="start",
            created_at=datetime(2026, 6, 2, 1, 40, tzinfo=timezone.utc),
            event_type="worker-start",
            repo=str(repo),
            worker_session="ludeme-codex",
            intent="start",
        ),
        ManagerEvent(
            id="review",
            created_at=datetime(2026, 6, 2, 1, 44, tzinfo=timezone.utc),
            event_type="codex-review",
            repo=str(repo),
            worker_session="ludeme-codex",
            intent="review",
            notes="codex review returncode=0",
        ),
    ]
    monkeypatch.setattr(
        "hermes_cli.dev_manager_next_action.capture_worker_pane",
        lambda target: ["Implementation complete. Ready for review."],
    )
    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: events)

    packet = collect_manager_packet(
        now=now,
        repo_roots=[],
        stale_after=timedelta(minutes=30),
        recent_after=now - timedelta(hours=12),
        preflight=_preflight(now),
        runs=[],
        workers=[worker],
    )

    assert packet.next_action.kind == "supervise-interactive-worker"


def test_next_action_dispatches_highest_priority_ready_task(isolated_kanban_home):
    now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
    kb.create_board("autonomy-bitino")
    conn = kb.connect(board="autonomy-bitino")
    try:
        low = kb.create_task(conn, title="low", assignee="reviewer", priority=10)
        high = kb.create_task(conn, title="high", assignee="codexworker", priority=90)
    finally:
        conn.close()

    packet = collect_manager_packet(
        now=now,
        repo_roots=[],
        stale_after=timedelta(minutes=30),
        recent_after=now - timedelta(hours=12),
        preflight=_preflight(now),
        runs=[],
        workers=[],
    )

    assert packet.next_action.kind == "dispatch-task"
    assert packet.next_action.task_id == high
    assert packet.next_action.task_id != low
    summary = telegram_summary(packet)
    assert "Dev manager status: dispatch-task" in summary
    assert "Command:" not in summary
    assert "Human action: none" in summary
    assert "Evidence:" in summary
    assert "not copied from prior runs" not in summary
    assert telegram_summary(packet, quiet_routine=True) == ""


def test_telegram_summary_keeps_blockers_visible(isolated_kanban_home):
    now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
    packet = collect_manager_packet(
        now=now,
        repo_roots=[],
        stale_after=timedelta(minutes=30),
        recent_after=now - timedelta(hours=12),
        preflight=_preflight(now, failing=True),
        runs=[],
        workers=[],
    )

    assert telegram_cadence(packet) == "blocked"
    summary = telegram_summary(packet, quiet_routine=True)
    assert "Dev manager blocked: fix-preflight" in summary
    assert "Human action: Only if Hermes cannot repair" in summary
    assert "Command:" not in summary


def test_packet_json_includes_required_evidence(isolated_kanban_home):
    from hermes_cli.dev_manager_next_action import packet_to_json

    now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
    kb.create_board("ludeme")
    conn = kb.connect(board="ludeme")
    try:
        tid = kb.create_task(conn, title="needs receipt", assignee="codexworker", priority=100)
    finally:
        conn.close()

    packet = collect_manager_packet(
        now=now,
        repo_roots=[],
        stale_after=timedelta(minutes=30),
        recent_after=now - timedelta(hours=12),
        preflight=_preflight(now),
        runs=[],
        workers=[],
    )
    data = packet_to_json(packet)

    assert tid in data
    assert '"evidence_after": "2026-06-02T01:45:00+00:00"' in data
    assert "required_evidence" in data
    assert "human_action" in data
    assert "manager_instruction" in data
    assert "durable evidence" in data
    assert "Evidence must be fresh" in data


def test_write_gbrain_page_uses_content_arg_and_neutral_cwd(monkeypatch):
    monkeypatch.setattr("hermes_cli.dev_manager_next_action.shutil.which", lambda command: f"/bin/{command}")
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(args, 0, "{}", "")

    monkeypatch.setattr("hermes_cli.dev_manager_next_action.subprocess.run", fake_run)

    assert write_gbrain_page("next-action-slug", "# next") is None

    assert seen["args"][:4] == ["gbrain", "put", "next-action-slug", "--content"]
    assert seen["kwargs"]["cwd"] == "/tmp"
    assert "input" not in seen["kwargs"]
    assert "Dev Orchestrator Manager Tick" in seen["args"][4]


def test_execute_safe_action_dispatches_codex_review_async(monkeypatch, tmp_path):
    now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
    repo = tmp_path / "repo"
    repo.mkdir()
    event_log = tmp_path / "events.jsonl"
    packet = ManagerPacket(
        generated_at=now,
        preflight=_preflight(now),
        workers=[],
        boards=[],
        runs=[],
        next_action=NextAction(
            kind="codex-review-worker",
            reason="ready",
            command="review",
            repo=str(repo),
            worker="repo-codex:0.0",
            review_commit="abcdef1234567890",
            review_title="demo commit",
        ),
    )
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("hermes_cli.dev_manager_next_action.DEFAULT_EVENT_LOG", event_log)
    monkeypatch.setattr("hermes_cli.dev_manager_next_action.shutil.which", lambda name: "/usr/bin/tmux")
    monkeypatch.setattr("hermes_cli.dev_manager_next_action.subprocess.run", fake_run)

    result = execute_safe_action(packet, report_dir=tmp_path / "reports")

    assert result is not None
    assert result.ok is True
    assert result.kind == "codex-review-worker"
    assert result.notify is False
    assert "Codex review started." in result.summary
    assert captured["args"][:4] == ["tmux", "new-session", "-d", "-s"]
    assert captured["args"][4].startswith("repo-codex-review-")
    run_script = Path(captured["args"][5])
    script_text = run_script.read_text(encoding="utf-8")
    assert "scripts/hermes-dev-codex-run-review.py" in script_text
    assert "--commit abcdef1234567890" in script_text
    events = load_events([event_log])
    assert len(events) == 1
    assert events[0].event_type == "codex-review-start"
    assert events[0].worker_session == "repo-codex"
    assert "tmux=repo-codex-review-" in str(events[0].notes)
    assert "commit=abcdef1234567890" in str(events[0].notes)
    assert events[0].resulting_artifacts[0].endswith("codex-review-tmux.log")
    assert events[0].resulting_artifacts[1].endswith("run-codex-review.sh")


def test_execute_safe_action_reports_completed_codex_review(monkeypatch, tmp_path):
    now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
    repo = tmp_path / "repo"
    repo.mkdir()
    output = tmp_path / "codex-review-output.md"
    output.write_text(
        "# Codex Run Review\n\n## Stdout\n\n```text\nVerdict: FIX_FIRST\nConfidence: medium\n```\n",
        encoding="utf-8",
    )
    event_log = tmp_path / "events.jsonl"
    review = ManagerEvent(
        id="review",
        created_at=datetime(2026, 6, 2, 1, 44, tzinfo=timezone.utc),
        event_type="codex-review",
        repo=str(repo),
        worker_session="repo-codex",
        intent="review",
        resulting_artifacts=[str(output)],
        notes="codex review returncode=0",
    )
    packet = ManagerPacket(
        generated_at=now,
        preflight=_preflight(now),
        workers=[],
        boards=[],
        runs=[],
        next_action=NextAction(
            kind="report-codex-review-result",
            reason="review finished",
            command="report",
            repo=str(repo),
            worker="repo-codex:0.0",
            review_event_id="review",
        ),
    )

    monkeypatch.setattr("hermes_cli.dev_manager_next_action.DEFAULT_EVENT_LOG", event_log)
    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: [review])

    result = execute_safe_action(packet, report_dir=tmp_path / "reports")

    assert result is not None
    assert result.ok is True
    assert result.notify is True
    assert "Verdict: FIX_FIRST" in result.summary
    assert "Confidence: medium" in result.summary
    assert "reported:" in result.summary
    events = load_events([event_log])
    assert len(events) == 1
    assert events[0].event_type == "codex-review-report"
    assert events[0].resulting_artifacts == [str(output)]
    assert "reported_event=review" in str(events[0].notes)


def test_quiet_routine_suppresses_review_report_telegram_output():
    routine = SafeExecutionResult(
        kind="report-codex-review-result",
        ok=True,
        summary="Codex review ready.\nVerdict: BLOCKED",
        notify=True,
    )
    failure = SafeExecutionResult(
        kind="report-codex-review-result",
        ok=False,
        summary="Codex review report failed.",
        notify=True,
    )

    assert _should_print_execution_result(routine, quiet_routine=True) is False
    assert _should_print_execution_result(routine, quiet_routine=False) is True
    assert _should_print_execution_result(failure, quiet_routine=True) is True


def test_execute_safe_action_reports_attention_without_cron_failure(monkeypatch, tmp_path):
    now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
    repo = tmp_path / "repo"
    repo.mkdir()
    output = tmp_path / "codex-review-output.md"
    output.write_text(
        "# Codex Run Review\n\n## Stderr\n\n```text\ncodex review command failed\n```\n",
        encoding="utf-8",
    )
    event_log = tmp_path / "events.jsonl"
    review = ManagerEvent(
        id="review",
        created_at=datetime(2026, 6, 2, 1, 44, tzinfo=timezone.utc),
        event_type="codex-review",
        repo=str(repo),
        worker_session="repo-codex",
        intent="review",
        resulting_artifacts=[str(output)],
        notes="codex review returncode=2",
    )
    packet = ManagerPacket(
        generated_at=now,
        preflight=_preflight(now),
        workers=[],
        boards=[],
        runs=[],
        next_action=NextAction(
            kind="report-codex-review-result",
            reason="review finished",
            command="report",
            repo=str(repo),
            worker="repo-codex:0.0",
            review_event_id="review",
        ),
    )

    monkeypatch.setattr("hermes_cli.dev_manager_next_action.DEFAULT_EVENT_LOG", event_log)
    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: [review])

    result = execute_safe_action(packet, report_dir=tmp_path / "reports")

    assert result is not None
    assert result.ok is True
    assert result.returncode == 0
    assert "Codex review attention." in result.summary
    assert "attention: codex review command returned 2" in result.summary
    events = load_events([event_log])
    assert len(events) == 1
    assert events[0].event_type == "codex-review-report"
    assert "reported_event=review returncode=2" in str(events[0].notes)


def test_execute_safe_action_dispatches_review_repair_to_worker(monkeypatch, tmp_path):
    now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
    repo = tmp_path / "repo"
    repo.mkdir()
    output = tmp_path / "codex-review-output.md"
    output.write_text("Verdict: FIX_FIRST\nConfidence: high\n", encoding="utf-8")
    review = ManagerEvent(
        id="review",
        created_at=datetime(2026, 6, 2, 1, 44, tzinfo=timezone.utc),
        event_type="codex-review",
        repo=str(repo),
        worker_session="repo-codex",
        intent="review",
        resulting_artifacts=[str(output)],
        notes="codex review returncode=0",
    )
    packet = ManagerPacket(
        generated_at=now,
        preflight=_preflight(now),
        workers=[],
        boards=[],
        runs=[],
        next_action=NextAction(
            kind="repair-codex-review-blocker",
            reason="review needs repair",
            command="dispatch",
            repo=str(repo),
            worker="repo-codex:0.0",
            review_event_id="review",
        ),
    )
    captured = {}

    def fake_dispatch_worker(**kwargs):
        captured.update(kwargs)
        class FakeDispatchResult:
            def __init__(self) -> None:
                self.repo = repo
                self.session = "repo-codex"
                self.event = ManagerEvent(
                    id="steer",
                    created_at=now,
                    event_type="worker-steer",
                    repo=str(repo),
                    worker_session="repo-codex",
                    intent="Repair Codex review `review`",
                )
                self.artifact_path = tmp_path / "dispatch.md"
                self.failed_results = []

        return FakeDispatchResult()

    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: [review])
    monkeypatch.setattr("hermes_cli.dev_manager_next_action.dispatch_worker", fake_dispatch_worker)

    result = execute_safe_action(packet, report_dir=tmp_path / "reports")

    assert result is not None
    assert result.ok is True
    assert result.notify is False
    assert captured["repo"] == repo
    assert captured["session"] == "repo-codex"
    assert captured["event_type"] == "worker-steer"
    assert captured["start_if_missing"] is False
    assert "Codex review `review` returned `FIX_FIRST`" in captured["message"]
    assert str(output) in captured["message"]
    assert "Do not ask the human" in captured["message"]


def test_execute_safe_action_dispatches_kanban_task(monkeypatch, tmp_path):
    now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
    packet = ManagerPacket(
        generated_at=now,
        preflight=_preflight(now),
        workers=[],
        boards=[],
        runs=[],
        next_action=NextAction(
            kind="dispatch-task",
            reason="ready task is highest priority",
            command="hermes kanban --board ludeme dispatch",
            board="ludeme",
            task_id="t_ready",
        ),
    )
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            args,
            0,
            '{"spawned":[{"task_id":"t_ready","assignee":"default","workspace":"/tmp/work"}]}',
            "",
        )

    monkeypatch.setattr("hermes_cli.dev_manager_next_action.subprocess.run", fake_run)

    result = execute_safe_action(packet, report_dir=tmp_path / "reports")

    assert result is not None
    assert result.ok is True
    assert result.notify is False
    assert captured["args"][2:] == [
        "hermes_cli.main",
        "kanban",
        "--board",
        "ludeme",
        "dispatch",
        "--max",
        "1",
        "--json",
    ]
    assert "t_ready -> default @ /tmp/work" in result.summary


def test_execute_safe_action_reports_kanban_dispatch_no_spawn(monkeypatch, tmp_path):
    now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
    packet = ManagerPacket(
        generated_at=now,
        preflight=_preflight(now),
        workers=[],
        boards=[],
        runs=[],
        next_action=NextAction(
            kind="run-review",
            reason="review task is ready",
            command="hermes kanban --board ludeme dispatch",
            board="ludeme",
            task_id="t_review",
        ),
    )

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, '{"spawned":[],"skipped_unassigned":["t_review"]}', "")

    monkeypatch.setattr("hermes_cli.dev_manager_next_action.subprocess.run", fake_run)

    result = execute_safe_action(packet, report_dir=tmp_path / "reports")

    assert result is not None
    assert result.ok is False
    assert result.notify is True
    assert result.returncode == 1
    assert "spawned no worker" in result.summary
    assert "skipped_unassigned" in result.summary
