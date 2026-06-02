"""Deterministic next-action packet for the dev orchestrator manager loop.

The active-run status report answers "what is running?". This module answers
"what should the manager do next?" from durable state: profile preflight,
Kanban boards, and recent orchestrator run artifacts. It is intentionally
no-agent friendly so Hermes can schedule it before any broad model-driven
campaign continuation.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
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
from hermes_cli.dev_run_status import (
    DEFAULT_RECENT_HOURS,
    DEFAULT_REPO_ROOTS,
    DEFAULT_REPORT_DIR,
    DEFAULT_STALE_MINUTES,
    RunSummary,
    _parse_csv,
    _repo_roots,
    collect_status,
)


DEFAULT_GBRAIN_SLUG = "dev-orchestrator-manager-next-action"
ACTIONABLE_STATUSES = {"running", "blocked", "review", "ready", "todo", "triage"}


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
    required_evidence: str | None = None

    def evidence_requirement(self) -> str:
        if self.required_evidence:
            return self.required_evidence
        if self.board and self.task_id:
            return (
                f"Before claiming progress, cite durable evidence for "
                f"`{self.board}/{self.task_id}`: a Kanban comment/status/run "
                "summary, receipt artifact, gbrain page, git commit/PR, or "
                "focused test/check output."
            )
        if self.repo:
            return (
                f"Before claiming progress, cite durable evidence for `{self.repo}`: "
                "a run artifact, gbrain page, git commit/PR, or focused "
                "test/check output."
            )
        return (
            "Before claiming progress, cite durable evidence: a Kanban update, "
            "run artifact, gbrain page, git commit/PR, or focused test/check output."
        )


@dataclass
class ManagerPacket:
    generated_at: datetime
    preflight: PreflightResult
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
) -> ManagerPacket:
    preflight_result = preflight or collect_preflight(now=now)
    boards = collect_board_snapshots(board_slugs=board_slugs, now=now, stale_after=stale_after)
    run_summaries = list(runs) if runs is not None else collect_status(
        repo_roots=repo_roots,
        now=now,
        stale_after=stale_after,
        recent_after=recent_after,
    )
    next_action = recommend_next_action(preflight=preflight_result, boards=boards, runs=run_summaries)
    return ManagerPacket(
        generated_at=now,
        preflight=preflight_result,
        boards=boards,
        runs=run_summaries,
        next_action=next_action,
    )


def render_markdown(packet: ManagerPacket) -> str:
    lines = [
        "# Dev Orchestrator Manager Next Action",
        "",
        f"Generated: {packet.generated_at.isoformat()}",
        f"Preflight failures: {packet.preflight.failure_count}",
        f"Boards: {len(packet.boards)}",
        f"Blocked tasks: {packet.blocked_count}",
        f"Running tasks: {packet.running_count}",
        f"Recent runs: {len(packet.runs)}",
        "",
        "## Recommended Next Action",
        "",
        f"- Kind: `{packet.next_action.kind}`",
        f"- Reason: {packet.next_action.reason}",
        f"- Command: `{packet.next_action.command}`",
        f"- Required evidence: {packet.next_action.evidence_requirement()}",
    ]
    if packet.next_action.board:
        lines.append(f"- Board: `{packet.next_action.board}`")
    if packet.next_action.task_id:
        lines.append(f"- Task: `{packet.next_action.task_id}`")
    if packet.next_action.repo:
        lines.append(f"- Repo: `{packet.next_action.repo}`")
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
    body = "---\ntype: report\ntitle: Dev Orchestrator Manager Next Action\n---\n\n" + markdown
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


def telegram_summary(packet: ManagerPacket) -> str:
    lines = [
        "Dev manager next-action packet ready.",
        f"Next: {packet.next_action.kind}",
        f"Reason: {packet.next_action.reason}",
        f"Command: {packet.next_action.command}",
        f"Evidence: {packet.next_action.evidence_requirement()}",
        f"Preflight failures: {packet.preflight.failure_count}",
        f"Blocked tasks: {packet.blocked_count}",
        f"Running tasks: {packet.running_count}",
    ]
    if packet.markdown_path:
        lines.append(f"Report: {packet.markdown_path}")
    if packet.gbrain_slug:
        lines.append(f"gbrain: {packet.gbrain_slug}")
    elif packet.gbrain_error:
        lines.append(f"gbrain: not synced ({packet.gbrain_error})")
    return "\n".join(lines)


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
            "required_evidence": packet.next_action.evidence_requirement(),
        },
        "preflight": {
            "failures": packet.preflight.failure_count,
            "warnings": packet.preflight.warning_count,
        },
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
    print(packet_to_json(packet) if args.json else telegram_summary(packet))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
