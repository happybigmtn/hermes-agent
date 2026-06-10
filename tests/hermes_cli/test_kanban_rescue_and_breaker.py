"""Tests for WIP rescue on worker failure and the board crash breaker."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME with an empty kanban DB."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=False,
    )


@pytest.fixture
def workspace_repo(tmp_path) -> Path:
    """A git repo with one commit, standing in for a dir: workspace."""
    repo = tmp_path / "workspace"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "test")
    _git(repo, "config", "user.email", "test@localhost")
    (repo / "tracked.txt").write_text("original\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def _make_dir_task(conn, workspace: Path, body: str = "do the work") -> str:
    return kb.create_task(
        conn, title="big slice", body=body,
        workspace_kind="dir", workspace_path=str(workspace),
    )


# ---------------------------------------------------------------------------
# WIP rescue
# ---------------------------------------------------------------------------

def test_timeout_failure_rescues_wip_to_branch(kanban_home, workspace_repo):
    with kb.connect() as conn:
        tid = _make_dir_task(conn, workspace_repo)
        (workspace_repo / "tracked.txt").write_text("worker progress\n")
        (workspace_repo / "new_module.py").write_text("def done(): pass\n")
        head_before = _git(workspace_repo, "rev-parse", "HEAD").stdout.strip()

        kb._record_task_failure(
            conn, tid, "elapsed 100s > limit 10s",
            outcome="timed_out", failure_limit=5,
        )

        branch = f"kanban-rescue/{tid}"
        # Rescue branch exists and contains both the edit and the new file.
        show = _git(workspace_repo, "show", f"{branch}:tracked.txt")
        assert show.stdout == "worker progress\n"
        show = _git(workspace_repo, "show", f"{branch}:new_module.py")
        assert "def done" in show.stdout
        # HEAD and the working tree are untouched.
        assert _git(workspace_repo, "rev-parse", "HEAD").stdout.strip() == head_before
        assert (workspace_repo / "tracked.txt").read_text() == "worker progress\n"
        # Index restored: nothing staged.
        staged = _git(workspace_repo, "diff", "--cached", "--name-only").stdout
        assert staged.strip() == ""
        # Body rewritten into a finish-from-WIP brief.
        task = kb.get_task(conn, tid)
        assert kb._RESCUE_SECTION_MARKER in task.body
        assert branch in task.body
        # Event recorded.
        events = [e for e in kb.list_events(conn, tid) if e.kind == "wip_rescued"]
        assert len(events) == 1


def test_clean_tree_is_not_rescued(kanban_home, workspace_repo):
    with kb.connect() as conn:
        tid = _make_dir_task(conn, workspace_repo)
        kb._record_task_failure(
            conn, tid, "crash", outcome="crashed", failure_limit=5,
        )
        assert _git(workspace_repo, "rev-parse", "--verify",
                    f"kanban-rescue/{tid}").returncode != 0
        task = kb.get_task(conn, tid)
        assert kb._RESCUE_SECTION_MARKER not in (task.body or "")


def test_rescue_excludes_build_dirs(kanban_home, workspace_repo):
    with kb.connect() as conn:
        tid = _make_dir_task(conn, workspace_repo)
        target = workspace_repo / "target" / "debug"
        target.mkdir(parents=True)
        (target / "huge.bin").write_text("x" * 1024)
        (workspace_repo / "real_work.rs").write_text("fn main() {}\n")

        kb._record_task_failure(
            conn, tid, "boom", outcome="crashed", failure_limit=5,
        )

        branch = f"kanban-rescue/{tid}"
        files = _git(
            workspace_repo, "ls-tree", "-r", "--name-only", branch,
        ).stdout
        assert "real_work.rs" in files
        assert "target/debug/huge.bin" not in files


def test_rescue_section_is_replaced_not_accumulated(kanban_home, workspace_repo):
    with kb.connect() as conn:
        tid = _make_dir_task(conn, workspace_repo, body="original brief")
        (workspace_repo / "tracked.txt").write_text("attempt 1\n")
        kb._record_task_failure(
            conn, tid, "t1", outcome="timed_out", failure_limit=5,
        )
        (workspace_repo / "tracked.txt").write_text("attempt 2\n")
        kb._record_task_failure(
            conn, tid, "t2", outcome="timed_out", failure_limit=5,
        )
        task = kb.get_task(conn, tid)
        assert task.body.count(kb._RESCUE_SECTION_MARKER) == 1
        assert task.body.startswith("original brief")
        # Branch points at the latest attempt.
        show = _git(
            workspace_repo, "show", f"kanban-rescue/{tid}:tracked.txt",
        )
        assert show.stdout == "attempt 2\n"


def test_rescue_skips_scratch_and_spawn_failures(kanban_home, workspace_repo):
    with kb.connect() as conn:
        # scratch workspace: no rescue even with a dirty-looking error.
        scratch_id = kb.create_task(conn, title="scratch task")
        kb._record_task_failure(
            conn, scratch_id, "crash", outcome="crashed", failure_limit=5,
        )
        assert not [
            e for e in kb.list_events(conn, scratch_id)
            if e.kind in ("wip_rescued", "wip_rescue_failed")
        ]
        # dir workspace but spawn failure: nothing ran, nothing rescued.
        tid = _make_dir_task(conn, workspace_repo)
        (workspace_repo / "tracked.txt").write_text("dirty\n")
        kb._record_task_failure(
            conn, tid, "no such profile", outcome="spawn_failed",
            failure_limit=5,
        )
        assert _git(workspace_repo, "rev-parse", "--verify",
                    f"kanban-rescue/{tid}").returncode != 0


# ---------------------------------------------------------------------------
# Board crash circuit breaker
# ---------------------------------------------------------------------------

def _insert_run(conn, task_id: str, outcome: str, ts: int) -> None:
    conn.execute(
        "INSERT INTO task_runs (task_id, status, started_at, ended_at, outcome) "
        "VALUES (?, ?, ?, ?, ?)",
        (task_id, outcome, ts - 10, ts, outcome),
    )
    conn.commit()


def test_breaker_trips_on_consecutive_crashes(kanban_home):
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="crashy")
        now = int(time.time())
        for i in range(8):
            _insert_run(conn, tid, "crashed", now + i)

        assert kb.check_board_crash_breaker(conn, None, threshold=8) is True
        meta = kb.read_board_metadata(None)
        assert meta["paused"] is True
        assert "circuit breaker" in (meta["paused_reason"] or "")
        events = [e for e in kb.list_events(conn, tid) if e.kind == "board_paused"]
        assert len(events) == 1


def test_breaker_holds_below_threshold_or_mixed_outcomes(kanban_home):
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="mostly crashy")
        now = int(time.time())
        for i in range(7):
            _insert_run(conn, tid, "crashed", now + i)
        # Below threshold.
        assert kb.check_board_crash_breaker(conn, None, threshold=8) is False
        # A success inside the window breaks the streak.
        _insert_run(conn, tid, "completed", now + 7)
        assert kb.check_board_crash_breaker(conn, None, threshold=8) is False
        assert kb.read_board_metadata(None)["paused"] is False
        # Disabled threshold never trips.
        assert kb.check_board_crash_breaker(conn, None, threshold=0) is False


def test_dispatch_once_spawns_nothing_on_paused_board(
    kanban_home, all_assignees_spawnable,
):
    spawned = []

    def spawn_stub(task, workspace):
        spawned.append(task.id)
        return None

    with kb.connect() as conn:
        kb.create_task(conn, title="ready work", assignee="codexworker")
        kb.write_board_metadata(None, paused=True, paused_reason="test hold")
        result = kb.dispatch_once(conn, spawn_fn=spawn_stub)
        assert result.board_paused is True
        assert spawned == []
        # Resume restores dispatch.
        kb.write_board_metadata(None, paused=False)
        result = kb.dispatch_once(conn, spawn_fn=spawn_stub)
        assert result.board_paused is False
        assert len(spawned) == 1


def test_dispatch_once_trips_breaker_then_skips_spawn(
    kanban_home, all_assignees_spawnable,
):
    spawned = []

    def spawn_stub(task, workspace):
        spawned.append(task.id)
        return None

    with kb.connect() as conn:
        tid = kb.create_task(conn, title="crashy history")
        now = int(time.time())
        for i in range(8):
            _insert_run(conn, tid, "crashed", now + i)
        kb.create_task(conn, title="next victim", assignee="codexworker")

        result = kb.dispatch_once(
            conn, spawn_fn=spawn_stub, crash_pause_threshold=8,
        )
        assert result.board_paused is True
        assert spawned == []
        assert kb.read_board_metadata(None)["paused"] is True
