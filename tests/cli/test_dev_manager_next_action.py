from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli.dev_manager_next_action import (
    collect_board_snapshots,
    collect_manager_packet,
    recommend_next_action,
    render_markdown,
    telegram_summary,
)
from hermes_cli.dev_orchestrator_preflight import Check, PreflightResult, ProfilePreflight


@pytest.fixture
def isolated_kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


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
    )

    assert packet.next_action.kind == "repair-blocked-task"
    assert packet.next_action.board == "ludeme"
    assert packet.next_action.task_id == tid
    assert "unverified" in packet.next_action.reason
    markdown = render_markdown(packet)
    assert "Dev Orchestrator Manager Next Action" in markdown
    assert tid in markdown
    assert "review-required" in markdown


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
    )

    assert packet.next_action.kind == "dispatch-task"
    assert packet.next_action.task_id == high
    assert packet.next_action.task_id != low
    summary = telegram_summary(packet)
    assert "Next: dispatch-task" in summary
    assert "Command:" in summary
