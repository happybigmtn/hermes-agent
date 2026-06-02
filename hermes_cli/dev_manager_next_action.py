"""Deterministic supervision tick for the dev orchestrator manager loop.

The active-run status report answers "what is running?". This module answers
"what should Hermes manage next?" from durable state: profile preflight,
interactive workers, Kanban boards, and recent orchestrator run artifacts. It
is intentionally no-agent friendly so Hermes can schedule it before any broad
model-driven campaign continuation.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from hermes_cli import kanban_db as kb
from hermes_cli.dev_orchestrator_preflight import (
    DEFAULT_ROLES,
    PreflightResult,
    collect_preflight,
)
from hermes_cli.dev_manager_events import (
    DEFAULT_EVENT_LOG,
    ManagerEvent,
    matching_events,
    record_event,
)
from hermes_cli.dev_run_status import (
    DEFAULT_RECENT_HOURS,
    DEFAULT_REPO_ROOTS,
    DEFAULT_REPORT_DIR,
    DEFAULT_STALE_MINUTES,
    RunSummary,
    WorkerSession,
    _parse_csv,
    _repo_roots,
    collect_status,
    list_tmux_worker_sessions,
)


DEFAULT_GBRAIN_SLUG = "dev-orchestrator-manager-next-action"
ACTIONABLE_STATUSES = {"running", "blocked", "review", "ready", "todo", "triage"}
ROUTINE_TELEGRAM_KINDS = {
    "supervise-interactive-worker",
    "codex-review-worker",
    "repair-blocked-task",
    "inspect-stale-task",
    "inspect-run-attention",
    "run-review",
    "dispatch-task",
    "wait-for-active-run",
    "wait-for-codex-review",
    "execute-plan-slice",
    "create-campaign-plan",
}
REVIEW_READY_MARKERS = (
    "ready for review",
    "review-ready",
    "review required",
    "review requested",
    "please review",
    "implementation complete",
    "implementation completed",
    "closeout complete",
    "handoff ready",
    "ready to review",
)
REVIEW_CAPTURE_LINES = 80
PENDING_REVIEW_STALE_MINUTES = float(os.getenv("HERMES_CODEX_REVIEW_PENDING_STALE_MINUTES", "120"))


@dataclass(frozen=True)
class TaskSnapshot:
    board: str
    task_id: str
    title: str
    status: str
    assignee: str | None
    priority: int
    workspace_path: str | None
    latest_run_status: str | None = None
    latest_run_outcome: str | None = None
    latest_summary: str | None = None
    risk: str | None = None
    stale: bool = False


@dataclass(frozen=True)
class BoardSnapshot:
    slug: str
    name: str
    counts: dict[str, int]
    tasks: list[TaskSnapshot] = field(default_factory=list)

    @property
    def active_count(self) -> int:
        return sum(self.counts.get(status, 0) for status in ACTIONABLE_STATUSES)


@dataclass(frozen=True)
class NextAction:
    kind: str
    reason: str
    command: str
    board: str | None = None
    task_id: str | None = None
    repo: str | None = None
    worker: str | None = None
    review_commit: str | None = None
    review_title: str | None = None
    review_event_id: str | None = None
    required_evidence: str | None = None

    def evidence_requirement(self, *, evidence_after: datetime | None = None) -> str:
        if self.required_evidence:
            base = self.required_evidence
        elif self.worker:
            base = (
                f"Before claiming progress, cite durable evidence for worker "
                f"`{self.worker}`: a manager event, supervision closeout, "
                "gbrain page, git commit/PR, run artifact, or focused "
                "test/check output."
            )
        elif self.board and self.task_id:
            base = (
                f"Before claiming progress, cite durable evidence for "
                f"`{self.board}/{self.task_id}`: a Kanban comment/status/run "
                "summary, receipt artifact, gbrain page, git commit/PR, or "
                "focused test/check output."
            )
        elif self.repo:
            base = (
                f"Before claiming progress, cite durable evidence for `{self.repo}`: "
                "a run artifact, gbrain page, git commit/PR, or focused "
                "test/check output."
            )
        else:
            base = (
                "Before claiming progress, cite durable evidence: a Kanban update, "
                "run artifact, gbrain page, git commit/PR, or focused test/check output."
            )
        if not evidence_after:
            return base
        return (
            f"{base} Evidence must be fresh: created or updated after "
            f"`{evidence_after.isoformat()}` for this packet, not copied from "
            "prior runs."
        )

    def manager_instruction(self) -> str:
        if self.kind == "codex-review-worker":
            return (
                "Start the Codex review gate for this worker in a detached "
                "tmux session, record a codex-review-start event, and let the "
                "next manager tick surface Codex's merge/fix/blocked verdict."
            )
        if self.kind == "wait-for-codex-review":
            return (
                "Keep monitoring; a Codex review is already running for this "
                "worker, so do not dispatch a duplicate review."
            )
        if self.kind == "report-codex-review-result":
            return (
                "Relay the completed Codex review result, record that it was "
                "reported, and only ask the human if Codex returned a blocker "
                "or fix-first decision."
            )
        if self.worker:
            return (
                "Inspect the worker pane, repo status, and recent artifacts. If "
                "the worker is still productive, keep monitoring. If it is idle, "
                "blocked, or complete, steer it or write a supervision closeout "
                "and sync the evidence to gbrain before reporting progress."
            )
        if self.kind == "fix-preflight":
            return "Repair the failing profile/tool setup before dispatching more work."
        if self.kind in {"repair-blocked-task", "inspect-stale-task"}:
            return (
                "Inspect the task evidence, decide whether to unblock, close out, "
                "or re-dispatch, then update Kanban/gbrain with fresh evidence."
            )
        if self.kind == "inspect-run-attention":
            return "Inspect the run root and latest artifacts, then either resume narrowly or close it out."
        if self.kind == "run-review":
            return "Run the review gate and record the decision before more implementation."
        if self.kind == "dispatch-task":
            return "Dispatch the highest-value unblocked task and require fresh evidence before claiming progress."
        if self.kind == "wait-for-active-run":
            return "Keep monitoring the active run and refresh status/gbrain instead of starting competing work."
        if self.kind == "execute-plan-slice":
            return "Execute the smallest useful slice from the plan, validate it, and close out with evidence."
        if self.kind == "create-campaign-plan":
            return "Read gbrain context, run autodev planning surfaces, and create precise executable work."
        return "Take the manager action and write fresh durable evidence before reporting progress."

    def human_action(self) -> str:
        if self.kind == "fix-preflight":
            return "Only if Hermes cannot repair the profile without new credentials or destructive access."
        return "None by default; Hermes should do this itself and report only evidence or a true blocker."


@dataclass
class ManagerPacket:
    generated_at: datetime
    preflight: PreflightResult
    workers: list[WorkerSession]
    boards: list[BoardSnapshot]
    runs: list[RunSummary]
    next_action: NextAction
    markdown_path: Path | None = None
    gbrain_slug: str | None = None
    gbrain_error: str | None = None

    @property
    def blocked_count(self) -> int:
        return sum(board.counts.get("blocked", 0) for board in self.boards)

    @property
    def running_count(self) -> int:
        return sum(board.counts.get("running", 0) for board in self.boards)

    @property
    def worker_count(self) -> int:
        return sum(1 for worker in self.workers if not worker.dead)


@dataclass(frozen=True)
class SafeExecutionResult:
    kind: str
    ok: bool
    summary: str
    returncode: int = 0
    notify: bool = True


def _live_supervised_workers(workers: Sequence[WorkerSession]) -> list[WorkerSession]:
    supervised = [
        worker
        for worker in workers
        if not worker.dead and worker.kind() in {"codex", "claude", "autodev"}
    ]
    supervised.sort(
        key=lambda worker: (
            not worker.active,
            worker.kind() != "codex",
            worker.session_name,
            worker.window_index,
            worker.pane_index,
        )
    )
    return supervised


def _session_name(session: str) -> str:
    return session.split(":", 1)[0]


def capture_worker_pane(target: str, *, lines: int = REVIEW_CAPTURE_LINES) -> list[str]:
    if not shutil.which("tmux"):
        return []
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-p", "-S", f"-{lines}", "-t", target],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0 or not result.stdout:
        return []
    return result.stdout.splitlines()


def _has_review_ready_marker(lines: Sequence[str]) -> bool:
    haystack = "\n".join(lines).lower()
    return any(marker in haystack for marker in REVIEW_READY_MARKERS)


def _latest_event(events: Sequence[ManagerEvent], event_types: set[str]) -> ManagerEvent | None:
    matches = [event for event in events if event.event_type in event_types]
    if not matches:
        return None
    return max(matches, key=lambda event: event.created_at)


def _codex_review_succeeded(event: ManagerEvent) -> bool:
    if event.event_type != "codex-review":
        return False
    match = re.search(r"\breturncode=(\d+)\b", event.notes or "")
    if match is None:
        return True
    return int(match.group(1)) == 0


def _review_returncode(event: ManagerEvent) -> int:
    match = re.search(r"\breturncode=(\d+)\b", event.notes or "")
    if match is None:
        return 0
    return int(match.group(1))


def _event_note_value(event: ManagerEvent, key: str) -> str | None:
    match = re.search(rf"(?:^|\s){re.escape(key)}=([^\s]+)", event.notes or "")
    return match.group(1) if match else None


def _has_current_codex_review(worker: WorkerSession) -> bool:
    if worker.current_path is None:
        return False
    events = matching_events(
        repo=worker.current_path,
        worker_session=worker.target(),
        limit=20,
    )
    latest_review = _latest_event([event for event in events if _codex_review_succeeded(event)], {"codex-review"})
    if latest_review is None:
        return False
    latest_dispatch = _latest_event(events, {"worker-start", "worker-steer", "worker-resume"})
    if latest_dispatch is None:
        return True
    return latest_review.created_at >= latest_dispatch.created_at


def _latest_codex_review_start(worker: WorkerSession) -> ManagerEvent | None:
    return _latest_event(_codex_review_events(worker), {"codex-review-start"})


def _pending_codex_review(worker: WorkerSession, *, now: datetime, since: datetime | None = None) -> ManagerEvent | None:
    latest_start = _latest_codex_review_start(worker)
    if latest_start is None:
        return None
    if since is not None and latest_start.created_at < since:
        return None
    if now - latest_start.created_at > timedelta(minutes=PENDING_REVIEW_STALE_MINUTES):
        return None
    latest_finish = _latest_event(_codex_review_events(worker), {"codex-review"})
    if latest_finish is not None and latest_finish.created_at >= latest_start.created_at:
        return None
    return latest_start


def _unreported_codex_review(worker: WorkerSession) -> ManagerEvent | None:
    latest_start = _latest_codex_review_start(worker)
    if latest_start is None:
        return None
    events = _codex_review_events(worker)
    finished = [
        event
        for event in events
        if event.event_type == "codex-review" and event.created_at >= latest_start.created_at
    ]
    latest_finish = _latest_event(finished, {"codex-review"})
    if latest_finish is None:
        return None
    reported = [
        event
        for event in events
        if event.event_type == "codex-review-report"
        and _event_note_value(event, "reported_event") == latest_finish.id
    ]
    if reported:
        return None
    return latest_finish


def _git_line(repo: Path, args: Sequence[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    text = result.stdout.strip()
    return text or None


def _latest_commit(repo: Path) -> tuple[str, datetime, str] | None:
    raw = _git_line(repo, ["log", "-1", "--format=%H%x00%ct%x00%s"])
    if not raw:
        return None
    parts = raw.split("\x00", 2)
    if len(parts) != 3:
        return None
    sha, timestamp_s, title = parts
    try:
        committed_at = datetime.fromtimestamp(int(timestamp_s), tz=timezone.utc)
    except ValueError:
        return None
    return sha, committed_at, title


def _codex_review_events(worker: WorkerSession) -> list[ManagerEvent]:
    if worker.current_path is None:
        return []
    return matching_events(
        repo=worker.current_path,
        worker_session=worker.target(),
        limit=20,
    )


def _latest_codex_review(worker: WorkerSession) -> ManagerEvent | None:
    return _latest_event(
        [event for event in _codex_review_events(worker) if _codex_review_succeeded(event)],
        {"codex-review"},
    )


def _latest_worker_dispatch(worker: WorkerSession) -> ManagerEvent | None:
    return _latest_event(_codex_review_events(worker), {"worker-start", "worker-steer", "worker-resume"})


def _unreviewed_worker_commit(worker: WorkerSession, *, now: datetime) -> tuple[str, str] | None:
    if worker.kind() != "codex" or worker.current_path is None:
        return None
    latest_commit = _latest_commit(worker.current_path)
    if latest_commit is None:
        return None
    sha, committed_at, title = latest_commit
    latest_dispatch = _latest_worker_dispatch(worker)
    if latest_dispatch is not None and committed_at < latest_dispatch.created_at:
        return None
    latest_review = _latest_codex_review(worker)
    if latest_review is not None and latest_review.created_at >= committed_at:
        return None
    if _pending_codex_review(worker, now=now, since=committed_at) is not None:
        return None
    return sha, title


def _worker_ready_for_codex_review_marker(worker: WorkerSession, *, now: datetime) -> bool:
    if worker.kind() != "codex" or worker.current_path is None:
        return False
    if _has_current_codex_review(worker):
        return False
    latest_dispatch = _latest_worker_dispatch(worker)
    since = latest_dispatch.created_at if latest_dispatch else None
    if _pending_codex_review(worker, now=now, since=since) is not None:
        return False
    return _has_review_ready_marker(capture_worker_pane(worker.target()))


def _unix_age(now: datetime, unix_time: int | None) -> timedelta | None:
    if unix_time is None:
        return None
    return now - datetime.fromtimestamp(int(unix_time), tz=timezone.utc)


def _excerpt(text: str | None, max_chars: int = 180) -> str | None:
    if not text:
        return None
    one_line = " ".join(str(text).split())
    if len(one_line) <= max_chars:
        return one_line
    return one_line[: max_chars - 3] + "..."


def _task_risk(task: kb.Task, latest_run: kb.Run | None, latest_summary: str | None) -> str | None:
    summary = latest_summary or (latest_run.summary if latest_run else "") or ""
    lowered = summary.lower()
    if task.assignee == "codexworker" and task.status == "blocked":
        if "review-required" in lowered or "not run" in lowered or "no compile" in lowered:
            return "codexworker blocked with unverified or review-required work"
        return "codexworker blocked; inspect blocker before dispatching more work"
    if task.assignee == "designcritic" and task.status not in {"done", "blocked"}:
        return "designcritic task should remain critique-only"
    if task.status == "review":
        return "review gate pending"
    if latest_run and latest_run.outcome in {"crashed", "timed_out", "spawn_failed"}:
        return f"latest run outcome: {latest_run.outcome}"
    return None


def _snapshot_task(conn, board: str, task: kb.Task, *, now: datetime, stale_after: timedelta) -> TaskSnapshot:
    latest_run = kb.latest_run(conn, task.id)
    latest_summary = kb.latest_summary(conn, task.id)
    heartbeat_age = _unix_age(now, task.last_heartbeat_at)
    stale = bool(task.status == "running" and heartbeat_age is not None and heartbeat_age > stale_after)
    risk = "running task heartbeat is stale" if stale else _task_risk(task, latest_run, latest_summary)
    return TaskSnapshot(
        board=board,
        task_id=task.id,
        title=task.title,
        status=task.status,
        assignee=task.assignee,
        priority=task.priority,
        workspace_path=task.workspace_path,
        latest_run_status=latest_run.status if latest_run else None,
        latest_run_outcome=latest_run.outcome if latest_run else None,
        latest_summary=_excerpt(latest_summary or (latest_run.summary if latest_run else None)),
        risk=risk,
        stale=stale,
    )


def collect_board_snapshots(
    *,
    board_slugs: Sequence[str] | None = None,
    now: datetime,
    stale_after: timedelta,
) -> list[BoardSnapshot]:
    wanted = {slug for slug in board_slugs or [] if slug}
    boards: list[BoardSnapshot] = []
    for meta in kb.list_boards(include_archived=False):
        slug = str(meta.get("slug") or "")
        if wanted and slug not in wanted:
            continue
        conn = kb.connect(board=slug)
        try:
            tasks = kb.list_tasks(conn, include_archived=False)
            counts = Counter(task.status for task in tasks)
            snapshots = [
                _snapshot_task(conn, slug, task, now=now, stale_after=stale_after)
                for task in tasks
                if task.status in ACTIONABLE_STATUSES
            ]
        finally:
            conn.close()
        snapshots.sort(
            key=lambda item: (
                not item.stale,
                not bool(item.risk),
                item.status != "blocked",
                item.status != "running",
                item.status != "review",
                -item.priority,
                item.task_id,
            )
        )
        boards.append(
            BoardSnapshot(
                slug=slug,
                name=str(meta.get("name") or slug),
                counts=dict(sorted(counts.items())),
                tasks=snapshots,
            )
        )
    boards.sort(key=lambda board: (board.slug == "default", -board.active_count, board.slug))
    return boards


def recommend_next_action(
    *,
    preflight: PreflightResult,
    workers: Sequence[WorkerSession] = (),
    boards: Sequence[BoardSnapshot],
    runs: Sequence[RunSummary],
) -> NextAction:
    for profile in preflight.profiles:
        if profile.failures:
            first = profile.failures[0]
            return NextAction(
                kind="fix-preflight",
                reason=f"{profile.name} profile failing `{first.name}`: {first.detail}",
                command="venv/bin/python scripts/hermes-dev-orchestrator-preflight.py",
            )

    live_workers = _live_supervised_workers(workers)
    if live_workers:
        worker = live_workers[0]
        target = worker.target()
        repo = str(worker.current_path) if worker.current_path else None
        now = preflight.generated_at
        unreported_review = _unreported_codex_review(worker)
        if unreported_review:
            return NextAction(
                kind="report-codex-review-result",
                repo=repo,
                worker=target,
                review_event_id=unreported_review.id,
                reason=f"{target} has completed Codex review {unreported_review.id} that has not been reported",
                command="venv/bin/python scripts/hermes-dev-manager-next-action.py --execute-safe --quiet-routine",
                required_evidence=(
                    f"Before claiming review result delivery, cite codex-review "
                    f"event `{unreported_review.id}` and its output artifact."
                ),
            )
        pending_review = _pending_codex_review(worker, now=now)
        if pending_review:
            return NextAction(
                kind="wait-for-codex-review",
                repo=repo,
                worker=target,
                review_event_id=pending_review.id,
                reason=f"{target} already has Codex review {pending_review.id} in progress",
                command="venv/bin/python scripts/hermes-dev-manager-events.py list",
            )
        unreviewed_commit = _unreviewed_worker_commit(worker, now=now)
        if unreviewed_commit:
            commit, title = unreviewed_commit
            return NextAction(
                kind="codex-review-worker",
                repo=repo,
                worker=target,
                review_commit=commit,
                review_title=title,
                reason=f"{target} has unreviewed Codex commit {commit[:12]}",
                command=(
                    "venv/bin/python scripts/hermes-dev-codex-run-review.py "
                    f"--repo {shlex.quote(str(worker.current_path))} "
                    f"--session {shlex.quote(_session_name(worker.session_name))} "
                    f"--commit {shlex.quote(commit)} "
                    f"--title {shlex.quote(title)}"
                    if worker.current_path
                    else "venv/bin/python scripts/hermes-dev-codex-run-review.py"
                ),
                required_evidence=(
                    f"Before claiming review completion, cite the Codex review "
                    f"output artifact and codex-review manager event for commit `{commit}`."
                ),
            )
        if _worker_ready_for_codex_review_marker(worker, now=now):
            return NextAction(
                kind="codex-review-worker",
                repo=repo,
                worker=target,
                reason=f"{target} has an explicit review-ready marker and no newer Codex review event",
                command=(
                    "venv/bin/python scripts/hermes-dev-codex-run-review.py "
                    f"--repo {shlex.quote(str(worker.current_path))} "
                    f"--session {shlex.quote(_session_name(worker.session_name))}"
                    if worker.current_path
                    else "venv/bin/python scripts/hermes-dev-codex-run-review.py"
                ),
                required_evidence=(
                    f"Before claiming review completion, cite the Codex review "
                    f"output artifact and codex-review manager event for `{target}`."
                ),
            )
        return NextAction(
            kind="supervise-interactive-worker",
            repo=repo,
            worker=target,
            reason=(
                f"{target} is a live {worker.kind()} worker; supervise the "
                "live session before acting on stale board packets or starting "
                "more work"
            ),
            command=(
                "venv/bin/python scripts/hermes-dev-run-status.py --no-gbrain"
                + (
                    f" && venv/bin/python scripts/hermes-dev-supervision-closeout.py --repo {worker.current_path} --session {worker.session_name} --no-gbrain"
                    if worker.current_path
                    else ""
                )
            ),
        )

    for board in boards:
        stale = [task for task in board.tasks if task.stale]
        if stale:
            task = stale[0]
            return NextAction(
                kind="inspect-stale-task",
                board=board.slug,
                task_id=task.task_id,
                reason=task.risk or "running task appears stale",
                command=f"venv/bin/hermes kanban --board {board.slug} show {task.task_id}",
            )

    for run in runs:
        if run.attention:
            return NextAction(
                kind="inspect-run-attention",
                repo=str(run.repo_path),
                reason=run.attention_reason or "active run needs attention",
                command=f"sed -n '1,220p' {run.run_root}/run.env && find {run.run_root} -maxdepth 2 -type f -printf '%TY-%Tm-%Td %TH:%TM %p\\n' | sort | tail -40",
            )

    risky_tasks = [
        task
        for board in boards
        for task in board.tasks
        if task.risk and task.status in {"blocked", "review"}
    ]
    if risky_tasks:
        task = risky_tasks[0]
        return NextAction(
            kind="repair-blocked-task",
            board=task.board,
            task_id=task.task_id,
            reason=task.risk or "blocked task needs manager intervention",
            command=f"venv/bin/hermes kanban --board {task.board} show {task.task_id}",
        )

    review_tasks = [task for board in boards for task in board.tasks if task.status == "review"]
    if review_tasks:
        task = review_tasks[0]
        return NextAction(
            kind="run-review",
            board=task.board,
            task_id=task.task_id,
            reason="review task is ready before more implementation",
            command=f"venv/bin/hermes kanban --board {task.board} dispatch",
        )

    executable = [
        task
        for board in boards
        for task in board.tasks
        if task.status in {"ready", "todo", "triage"}
    ]
    if executable:
        task = executable[0]
        return NextAction(
            kind="dispatch-task",
            board=task.board,
            task_id=task.task_id,
            reason=f"{task.status} task is the highest-priority unblocked work",
            command=f"venv/bin/hermes kanban --board {task.board} dispatch",
        )

    active_runs = [run for run in runs if run.active]
    if active_runs:
        run = active_runs[0]
        return NextAction(
            kind="wait-for-active-run",
            repo=str(run.repo_path),
            reason="active run has fresh artifacts; avoid competing broad work",
            command="venv/bin/python scripts/hermes-dev-run-status.py --no-gbrain",
        )

    planned_runs = [run for run in runs if run.implementation_plan]
    if planned_runs:
        run = planned_runs[0]
        return NextAction(
            kind="execute-plan-slice",
            repo=str(run.repo_path),
            reason="recent implementation plan exists but no board task is ready",
            command=f"cd {run.repo_path} && auto parallel status",
        )

    return NextAction(
        kind="create-campaign-plan",
        reason="no active/recent actionable board or run state was found",
        command="Read gbrain campaign context, run auto doctor/corpus/gen snapshot, then create precise Kanban tasks.",
    )


def collect_manager_packet(
    *,
    now: datetime,
    repo_roots: Sequence[Path],
    stale_after: timedelta,
    recent_after: datetime,
    board_slugs: Sequence[str] | None = None,
    preflight: PreflightResult | None = None,
    runs: Sequence[RunSummary] | None = None,
    workers: Sequence[WorkerSession] | None = None,
) -> ManagerPacket:
    preflight_result = preflight or collect_preflight(now=now)
    worker_summaries = list(workers) if workers is not None else list_tmux_worker_sessions()
    boards = collect_board_snapshots(board_slugs=board_slugs, now=now, stale_after=stale_after)
    run_summaries = list(runs) if runs is not None else collect_status(
        repo_roots=repo_roots,
        now=now,
        stale_after=stale_after,
        recent_after=recent_after,
    )
    next_action = recommend_next_action(
        preflight=preflight_result,
        workers=worker_summaries,
        boards=boards,
        runs=run_summaries,
    )
    return ManagerPacket(
        generated_at=now,
        preflight=preflight_result,
        workers=worker_summaries,
        boards=boards,
        runs=run_summaries,
        next_action=next_action,
    )


def render_markdown(packet: ManagerPacket) -> str:
    lines = [
        "# Dev Orchestrator Manager Tick",
        "",
        f"Generated: {packet.generated_at.isoformat()}",
        f"Preflight failures: {packet.preflight.failure_count}",
        f"Interactive workers: {packet.worker_count}",
        f"Boards: {len(packet.boards)}",
        f"Blocked tasks: {packet.blocked_count}",
        f"Running tasks: {packet.running_count}",
        f"Recent runs: {len(packet.runs)}",
        "",
        "## Hermes Manager Action",
        "",
        f"- Kind: `{packet.next_action.kind}`",
        f"- Reason: {packet.next_action.reason}",
        f"- Manager instruction: {packet.next_action.manager_instruction()}",
        f"- Human action: {packet.next_action.human_action()}",
        f"- Internal command: `{packet.next_action.command}`",
        f"- Evidence after: `{packet.generated_at.isoformat()}`",
        f"- Required evidence: {packet.next_action.evidence_requirement(evidence_after=packet.generated_at)}",
    ]
    if packet.next_action.board:
        lines.append(f"- Board: `{packet.next_action.board}`")
    if packet.next_action.task_id:
        lines.append(f"- Task: `{packet.next_action.task_id}`")
    if packet.next_action.repo:
        lines.append(f"- Repo: `{packet.next_action.repo}`")
    if packet.next_action.worker:
        lines.append(f"- Worker: `{packet.next_action.worker}`")
    lines.extend(["", "## Interactive Workers", ""])
    if not packet.workers:
        lines.append("No interactive tmux workers found.")
    for worker in packet.workers:
        state = "dead" if worker.dead else "active" if worker.active else "idle"
        lines.append(
            f"- `{worker.target()}` {worker.kind()} {state}; command=`{worker.current_command or 'unknown'}`; path=`{worker.current_path or 'unknown'}`"
        )
    lines.extend(["", "## Boards", ""])
    for board in packet.boards:
        if not board.counts:
            continue
        counts = ", ".join(f"{status}={count}" for status, count in board.counts.items())
        lines.extend([f"### {board.slug}", "", f"Counts: {counts}", ""])
        for task in board.tasks[:8]:
            lines.append(
                f"- `{task.task_id}` {task.status} @{task.assignee or 'unassigned'} "
                f"p{task.priority}: {task.title}"
            )
            if task.risk:
                lines.append(f"  Risk: {task.risk}")
            if task.latest_summary:
                lines.append(f"  Latest: {task.latest_summary}")
        lines.append("")
    lines.extend(["## Recent Runs", ""])
    if not packet.runs:
        lines.append("No active or recent `.auto/orchestrator` runs found.")
    for run in packet.runs[:8]:
        lines.append(
            f"- `{run.repo_slug}/{run.run_id}` phase={run.phase()} "
            f"active={str(run.active).lower()} attention={str(run.attention).lower()}"
        )
        if run.attention_reason:
            lines.append(f"  Attention: {run.attention_reason}")
        lines.append(f"  Next: {run.next_action}")
    return "\n".join(lines).rstrip() + "\n"


def write_gbrain_page(slug: str, markdown: str) -> str | None:
    if not shutil.which("gbrain"):
        return "gbrain binary not found"
    body = "---\ntype: report\ntitle: Dev Orchestrator Manager Tick\n---\n\n" + markdown
    try:
        result = subprocess.run(
            ["gbrain", "put", slug, "--content", body],
            cwd="/tmp",
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    if result.returncode != 0:
        return (result.stderr or result.stdout or "gbrain put failed").strip()[:400]
    return None


def write_packet_report(
    packet: ManagerPacket,
    *,
    report_dir: Path,
    write_gbrain: bool,
    gbrain_slug: str = DEFAULT_GBRAIN_SLUG,
) -> ManagerPacket:
    markdown = render_markdown(packet)
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"dev-manager-next-action-{packet.generated_at.strftime('%Y%m%d-%H%M%S')}.md"
    path.write_text(markdown, encoding="utf-8")
    packet.markdown_path = path
    if write_gbrain:
        packet.gbrain_error = write_gbrain_page(gbrain_slug, markdown)
        if packet.gbrain_error is None:
            packet.gbrain_slug = gbrain_slug
    return packet


def telegram_cadence(packet: ManagerPacket) -> str:
    if packet.next_action.kind == "fix-preflight":
        return "blocked"
    human_action = packet.next_action.human_action()
    if not human_action.startswith("None by default") and not human_action.startswith("Only if"):
        return "decision"
    return "status"


def _evidence_short(packet: ManagerPacket) -> str:
    timestamp = packet.generated_at.isoformat()
    action = packet.next_action
    if action.worker:
        return f"fresh worker closeout/receipt after {timestamp}"
    if action.board and action.task_id:
        return f"fresh receipt/comment/commit for {action.board}/{action.task_id} after {timestamp}"
    if action.repo:
        return f"fresh run artifact/commit/gbrain page for {action.repo} after {timestamp}"
    return f"fresh receipt/artifact/commit after {timestamp}"


def telegram_summary(packet: ManagerPacket, *, quiet_routine: bool = False) -> str:
    cadence = telegram_cadence(packet)
    if quiet_routine and cadence == "status" and packet.next_action.kind in ROUTINE_TELEGRAM_KINDS:
        return ""
    human_action = packet.next_action.human_action()
    human_line = "Human action: none" if human_action.startswith("None by default") else f"Human action: {human_action}"
    lines = [
        f"Dev manager {cadence}: {packet.next_action.kind} - {packet.next_action.reason}",
        f"Hermes next: {packet.next_action.manager_instruction()}",
        human_line,
        f"Evidence: {_evidence_short(packet)}",
    ]
    if packet.markdown_path:
        lines.append(f"Report: {packet.markdown_path}")
    if packet.gbrain_slug:
        lines.append(f"gbrain: {packet.gbrain_slug}")
    elif packet.gbrain_error:
        lines.append(f"gbrain: not synced ({packet.gbrain_error})")
    return "\n".join(lines)


def _slugify(value: str) -> str:
    chars: list[str] = []
    for char in value.lower():
        if char.isalnum():
            chars.append(char)
        elif chars and chars[-1] != "-":
            chars.append("-")
    return "".join(chars).strip("-") or "review"


def _review_script_path() -> Path:
    return Path(__file__).resolve().parents[1] / "scripts" / "hermes-dev-codex-run-review.py"


def _review_command_args(action: NextAction, *, report_dir: Path) -> list[str]:
    args = [
        sys.executable,
        str(_review_script_path()),
        "--repo",
        str(action.repo),
        "--session",
        _session_name(str(action.worker)),
        "--out-dir",
        str(report_dir),
        "--event-log",
        str(DEFAULT_EVENT_LOG),
    ]
    if action.review_commit:
        args.extend(["--commit", action.review_commit])
    if action.review_title:
        args.extend(["--title", action.review_title])
    return args


def _dispatch_codex_review(packet: ManagerPacket, *, report_dir: Path) -> SafeExecutionResult:
    action = packet.next_action
    assert action.repo is not None and action.worker is not None
    if not shutil.which("tmux"):
        return SafeExecutionResult(
            kind=action.kind,
            ok=False,
            summary="Codex review dispatch failed: tmux is required; refusing to run Codex in-process.",
            returncode=127,
        )
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = packet.generated_at.strftime("%Y%m%d-%H%M%S")
    run_dir = report_dir / f"codex-review-dispatch-{_slugify(Path(action.repo).name)}-{_slugify(action.worker)}-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    launch_log = run_dir / "codex-review-tmux.log"
    run_script = run_dir / "run-codex-review.sh"
    tmux_session = f"{_slugify(Path(action.repo).name)}-codex-review-{packet.generated_at.strftime('%Y%m%d%H%M%S')}"
    args = _review_command_args(action, report_dir=report_dir)
    try:
        run_script.write_text(
            "\n".join(
                [
                    "#!/usr/bin/env bash",
                    "set -o pipefail",
                    "set +e",
                    f"cd {shlex.quote(str(_review_script_path().parents[1]))}",
                    f"{shlex.join(args)} > {shlex.quote(str(launch_log))} 2>&1",
                    "status=$?",
                    (
                        "printf '\\n[hermes] codex review exited with %s at %s\\n' "
                        f'"$status" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> {shlex.quote(str(launch_log))}'
                    ),
                    'exit "$status"',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        run_script.chmod(0o755)
        dispatch = subprocess.run(
            ["tmux", "new-session", "-d", "-s", tmux_session, str(run_script)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except OSError as exc:
        return SafeExecutionResult(
            kind=action.kind,
            ok=False,
            summary=f"Codex review dispatch failed: {exc}",
            returncode=127,
        )
    if dispatch.returncode != 0:
        detail = (dispatch.stderr or dispatch.stdout or "").strip()
        return SafeExecutionResult(
            kind=action.kind,
            ok=False,
            summary=f"Codex review tmux dispatch failed: {detail or 'unknown tmux error'}",
            returncode=dispatch.returncode,
        )
    event = record_event(
        repo=action.repo,
        worker_session=_session_name(action.worker),
        intent=f"Start async Codex review for `{action.worker}`",
        event_log=DEFAULT_EVENT_LOG,
        event_type="codex-review-start",
        requested_by="hermes",
        delivery_channel="telegram",
        resulting_artifacts=[str(launch_log), str(run_script)],
        notes=" ".join(
            item
            for item in (
                f"tmux={tmux_session}",
                f"commit={action.review_commit}" if action.review_commit else "",
            )
            if item
        ),
        created_at=packet.generated_at,
    )
    return SafeExecutionResult(
        kind=action.kind,
        ok=True,
        summary=(
            "Codex review started.\n"
            f"repo: {action.repo}\n"
            f"session: {_session_name(action.worker)}\n"
            f"tmux: {tmux_session}\n"
            f"launch: {launch_log}\n"
            f"event: {event.id}"
        ),
        notify=False,
    )


def _review_output_path(event: ManagerEvent) -> Path | None:
    for artifact in event.resulting_artifacts:
        if artifact.endswith("codex-review-output.md"):
            return Path(artifact)
    return None


def _extract_prefixed_line(text: str, prefix: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(prefix.lower()):
            return stripped
    return None


def _first_review_line(text: str) -> str | None:
    ignored_prefixes = ("#", "command:", "return code:", "```", "- empty")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith(ignored_prefixes):
            continue
        return stripped
    return None


def _review_event_summary(event: ManagerEvent) -> str:
    output_path = _review_output_path(event)
    text = ""
    if output_path and output_path.exists():
        text = output_path.read_text(encoding="utf-8", errors="replace")
    verdict = _extract_prefixed_line(text, "Verdict:")
    confidence = _extract_prefixed_line(text, "Confidence:")
    review_line = None if verdict else _first_review_line(text)
    ok = _review_returncode(event) == 0
    lines = [
        "Codex review ready." if ok else "Codex review attention.",
        f"repo: {event.repo}",
        f"session: {event.worker_session}",
    ]
    if verdict:
        lines.append(verdict)
    if confidence:
        lines.append(confidence)
    if review_line:
        lines.append(f"review: {review_line}")
    if output_path:
        lines.append(f"output: {output_path}")
    lines.append(f"event: {event.id}")
    if not ok:
        lines.append(f"attention: codex review command returned {_review_returncode(event)}")
    return "\n".join(lines)


def _report_codex_review_result(packet: ManagerPacket) -> SafeExecutionResult:
    action = packet.next_action
    if not action.repo or not action.worker or not action.review_event_id:
        return SafeExecutionResult(
            kind=action.kind,
            ok=False,
            summary="Codex review report failed: missing repo, worker, or review event id",
            returncode=1,
        )
    events = matching_events(repo=action.repo, worker_session=action.worker, limit=50)
    review = next((event for event in events if event.id == action.review_event_id), None)
    if review is None:
        return SafeExecutionResult(
            kind=action.kind,
            ok=False,
            summary=f"Codex review report failed: event {action.review_event_id} not found",
            returncode=1,
        )
    report_event = record_event(
        repo=action.repo,
        worker_session=_session_name(action.worker),
        intent=f"Report Codex review result `{review.id}`",
        event_log=DEFAULT_EVENT_LOG,
        event_type="codex-review-report",
        requested_by="hermes",
        delivery_channel="telegram",
        resulting_artifacts=review.resulting_artifacts,
        notes=f"reported_event={review.id} returncode={_review_returncode(review)}",
        created_at=packet.generated_at,
    )
    return SafeExecutionResult(
        kind=action.kind,
        ok=True,
        summary=_review_event_summary(review) + f"\nreported: {report_event.id}",
        returncode=0,
    )


def execute_safe_action(packet: ManagerPacket, *, report_dir: Path) -> SafeExecutionResult | None:
    action = packet.next_action
    if action.kind == "report-codex-review-result":
        return _report_codex_review_result(packet)
    if action.kind != "codex-review-worker" or not action.repo or not action.worker:
        return None
    return _dispatch_codex_review(packet, report_dir=report_dir)


def packet_to_json(packet: ManagerPacket) -> str:
    data = {
        "generated_at": packet.generated_at.isoformat(),
        "next_action": {
            "kind": packet.next_action.kind,
            "reason": packet.next_action.reason,
            "command": packet.next_action.command,
            "board": packet.next_action.board,
            "task_id": packet.next_action.task_id,
            "repo": packet.next_action.repo,
            "worker": packet.next_action.worker,
            "review_commit": packet.next_action.review_commit,
            "review_title": packet.next_action.review_title,
            "review_event_id": packet.next_action.review_event_id,
            "manager_instruction": packet.next_action.manager_instruction(),
            "human_action": packet.next_action.human_action(),
            "evidence_after": packet.generated_at.isoformat(),
            "required_evidence": packet.next_action.evidence_requirement(
                evidence_after=packet.generated_at
            ),
        },
        "preflight": {
            "failures": packet.preflight.failure_count,
            "warnings": packet.preflight.warning_count,
        },
        "workers": [
            {
                "target": worker.target(),
                "kind": worker.kind(),
                "active": worker.active,
                "dead": worker.dead,
                "command": worker.current_command,
                "path": str(worker.current_path) if worker.current_path else None,
            }
            for worker in packet.workers
        ],
        "boards": [
            {
                "slug": board.slug,
                "name": board.name,
                "counts": board.counts,
                "tasks": [
                    {
                        "id": task.task_id,
                        "title": task.title,
                        "status": task.status,
                        "assignee": task.assignee,
                        "risk": task.risk,
                    }
                    for task in board.tasks
                ],
            }
            for board in packet.boards
        ],
    }
    return json.dumps(data, indent=2, sort_keys=True)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a deterministic dev manager next-action packet")
    parser.add_argument("--repo-root", action="append", help="Root containing repo checkouts. Repeatable or comma-separated")
    parser.add_argument("--board", action="append", help="Kanban board slug to include. Repeatable; default is all boards")
    parser.add_argument("--out-dir", default=os.getenv("HERMES_DEV_MANAGER_NEXT_ACTION_DIR", str(DEFAULT_REPORT_DIR)))
    parser.add_argument("--stale-minutes", type=float, default=float(os.getenv("HERMES_DEV_MANAGER_STALE_MINUTES", DEFAULT_STALE_MINUTES)))
    parser.add_argument("--recent-hours", type=float, default=float(os.getenv("HERMES_DEV_MANAGER_RECENT_HOURS", DEFAULT_RECENT_HOURS)))
    parser.add_argument("--gbrain-slug", default=os.getenv("HERMES_DEV_MANAGER_GBRAIN_SLUG", DEFAULT_GBRAIN_SLUG))
    parser.add_argument("--no-gbrain", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print machine-readable packet JSON instead of Telegram summary")
    parser.add_argument(
        "--quiet-routine",
        action="store_true",
        default=os.getenv("HERMES_DEV_MANAGER_QUIET_ROUTINE", "").lower() in {"1", "true", "yes", "on"},
        help="Print nothing for routine self-managed status packets",
    )
    parser.add_argument(
        "--execute-safe",
        action="store_true",
        default=os.getenv("HERMES_DEV_MANAGER_EXECUTE_SAFE", "").lower() in {"1", "true", "yes", "on"},
        help="Execute safe deterministic follow-up actions, currently Codex run review",
    )
    parser.add_argument("--profile", action="append", default=None, help="Preflight profile to check; repeatable")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    now = datetime.now(timezone.utc)
    stale_after = timedelta(minutes=args.stale_minutes)
    recent_after = now - timedelta(hours=args.recent_hours)
    root_values: list[str] = []
    for value in args.repo_root or []:
        root_values.extend(_parse_csv(value))
    profiles = args.profile if args.profile else list(DEFAULT_ROLES)
    preflight = collect_preflight(profiles=profiles, now=now)
    packet = collect_manager_packet(
        now=now,
        repo_roots=_repo_roots(root_values) if root_values else DEFAULT_REPO_ROOTS,
        stale_after=stale_after,
        recent_after=recent_after,
        board_slugs=args.board,
        preflight=preflight,
    )
    write_packet_report(
        packet,
        report_dir=Path(args.out_dir).expanduser(),
        write_gbrain=not args.no_gbrain,
        gbrain_slug=args.gbrain_slug,
    )
    execution_result = execute_safe_action(packet, report_dir=Path(args.out_dir).expanduser()) if args.execute_safe else None
    if args.json:
        print(packet_to_json(packet))
        if execution_result:
            print(
                json.dumps(
                    {
                        "safe_execution": {
                            "kind": execution_result.kind,
                            "ok": execution_result.ok,
                            "returncode": execution_result.returncode,
                        }
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
    elif execution_result and execution_result.notify:
        print(execution_result.summary)
    elif not execution_result:
        summary = telegram_summary(packet, quiet_routine=args.quiet_routine)
        if summary:
            print(summary)
    if execution_result and not execution_result.ok:
        return execution_result.returncode or 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
