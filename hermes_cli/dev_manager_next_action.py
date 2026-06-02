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
    "repair-blocked-task",
    "inspect-stale-task",
    "inspect-run-attention",
    "run-review",
    "dispatch-task",
    "wait-for-active-run",
    "execute-plan-slice",
    "create-campaign-plan",
}


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
    if args.json:
        print(packet_to_json(packet))
    else:
        summary = telegram_summary(packet, quiet_routine=args.quiet_routine)
        if summary:
            print(summary)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
