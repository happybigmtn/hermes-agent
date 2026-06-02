"""Close out a supervised tmux worker session into durable memory.

This module is deterministic and no-agent friendly. It snapshots a tmux worker,
repo state, recent commits, and nearby orchestration artifacts, then writes a
Markdown closeout and optionally stores it in gbrain.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python 3.11+ always has zoneinfo.
    ZoneInfo = None  # type: ignore[assignment]

from hermes_cli.dev_run_status import (
    DEFAULT_DASHBOARD_FILENAME,
    DEFAULT_DASHBOARD_PORT,
    DEFAULT_REPORT_DIR,
    DEFAULT_TIMEZONE,
    WorkerSession,
    default_dashboard_url,
    list_tmux_worker_sessions,
)


DEFAULT_CAPTURE_LINES = 220
DEFAULT_GBRAIN_PREFIX = "dev-supervision-closeout"


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class RepoSnapshot:
    path: Path
    branch: str
    status_short: list[str]
    recent_commits: list[str]
    diff_stat: str
    recent_artifacts: list[Path] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.status_short


@dataclass(frozen=True)
class CloseoutResult:
    repo: Path
    session: str
    generated_at: datetime
    markdown_path: Path
    dashboard_url: str | None
    worker: WorkerSession | None
    repo_snapshot: RepoSnapshot
    pane_capture_lines: int
    gbrain_slug: str | None
    gbrain_error: str | None


def _run(args: Sequence[str], *, cwd: Path | None = None, timeout: int = 20) -> CommandResult:
    try:
        result = subprocess.run(
            list(args),
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return CommandResult(tuple(args), result.returncode, result.stdout.strip(), result.stderr.strip())
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(tuple(args), 127, "", str(exc))


def _timezone(name: str) -> timezone:
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)  # type: ignore[return-value]
        except Exception:
            pass
    return timezone.utc


def _slugify(value: str) -> str:
    chars: list[str] = []
    for char in value.lower():
        if char.isalnum():
            chars.append(char)
        elif chars and chars[-1] != "-":
            chars.append("-")
    return "".join(chars).strip("-") or "session"


def _git_lines(repo: Path, args: Sequence[str]) -> list[str]:
    result = _run(["git", "-C", str(repo), *args], timeout=20)
    if result.returncode != 0 or not result.stdout:
        return []
    return result.stdout.splitlines()


def collect_repo_snapshot(repo: Path, *, artifact_limit: int = 12) -> RepoSnapshot:
    branch = "\n".join(_git_lines(repo, ["branch", "--show-current"])) or "unknown"
    status_short = _git_lines(repo, ["status", "--short"])
    recent_commits = _git_lines(repo, ["log", "--oneline", "--decorate", "--max-count=8"])
    diff_stat = "\n".join(_git_lines(repo, ["diff", "--stat"]))
    artifacts: list[Path] = []
    candidates = [
        repo / ".auto",
        repo / "genesis",
    ]
    candidates.extend(path for path in repo.glob("gen-*") if path.is_dir())
    found: list[tuple[float, Path]] = []
    for root in candidates:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file():
                try:
                    found.append((path.stat().st_mtime, path))
                except OSError:
                    continue
    found.sort(reverse=True)
    artifacts = [path for _, path in found[:artifact_limit]]
    return RepoSnapshot(
        path=repo,
        branch=branch,
        status_short=status_short,
        recent_commits=recent_commits,
        diff_stat=diff_stat,
        recent_artifacts=artifacts,
    )


def find_worker(session: str, workers: Sequence[WorkerSession]) -> WorkerSession | None:
    for worker in workers:
        if worker.session_name == session or worker.target() == session:
            return worker
    return None


def capture_pane(target: str, *, lines: int = DEFAULT_CAPTURE_LINES) -> list[str]:
    if not shutil.which("tmux"):
        return []
    result = _run(
        ["tmux", "capture-pane", "-p", "-S", f"-{lines}", "-t", target],
        timeout=10,
    )
    if result.returncode != 0 or not result.stdout:
        return []
    return result.stdout.splitlines()


def write_gbrain_page(slug: str, markdown: str) -> str | None:
    if not shutil.which("gbrain"):
        return "gbrain binary not found"
    body = "---\ntype: closeout\ntitle: Dev Supervision Closeout\n---\n\n" + markdown
    result = _run(["gbrain", "put", slug, "--content", body], cwd=Path("/tmp"), timeout=45)
    if result.returncode != 0:
        return (result.stderr or result.stdout or "gbrain put failed")[:500]
    return None


def render_markdown(
    *,
    repo: Path,
    session: str,
    generated_at: datetime,
    worker: WorkerSession | None,
    repo_snapshot: RepoSnapshot,
    pane_lines: Sequence[str],
    dashboard_url: str | None,
) -> str:
    worker_target = worker.target() if worker else session
    worker_kind = worker.kind() if worker else "unknown"
    worker_path = str(worker.current_path) if worker and worker.current_path else "unknown"
    worker_command = worker.current_command if worker else "unknown"
    state = "missing"
    if worker:
        state = "dead" if worker.dead else "active" if worker.active else "idle"

    lines = [
        "# Dev Supervision Closeout",
        "",
        f"Generated: {generated_at.isoformat()}",
        f"Repo: `{repo}`",
        f"Session: `{session}`",
        f"Worker target: `{worker_target}`",
        f"Worker kind: `{worker_kind}`",
        f"Worker state: `{state}`",
        f"Worker command: `{worker_command}`",
        f"Worker path: `{worker_path}`",
    ]
    if dashboard_url:
        lines.append(f"Dashboard: {dashboard_url}")
    lines.extend(
        [
            "",
            "## Repo State",
            "",
            f"Branch: `{repo_snapshot.branch}`",
            f"Clean: `{repo_snapshot.clean}`",
            "",
            "### Status",
            "",
        ]
    )
    if repo_snapshot.status_short:
        lines.extend(f"- `{item}`" for item in repo_snapshot.status_short)
    else:
        lines.append("- clean")
    lines.extend(["", "### Recent Commits", ""])
    if repo_snapshot.recent_commits:
        lines.extend(f"- `{item}`" for item in repo_snapshot.recent_commits)
    else:
        lines.append("- none")
    lines.extend(["", "### Diff Stat", ""])
    if repo_snapshot.diff_stat:
        lines.extend(["```text", repo_snapshot.diff_stat, "```"])
    else:
        lines.append("No unstaged diff.")
    lines.extend(["", "### Recent Artifacts", ""])
    if repo_snapshot.recent_artifacts:
        lines.extend(f"- `{path}`" for path in repo_snapshot.recent_artifacts)
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Tmux Capture",
            "",
            f"Captured lines: `{len(pane_lines)}`",
            "",
        ]
    )
    if pane_lines:
        lines.append("```text")
        lines.extend(pane_lines[-DEFAULT_CAPTURE_LINES:])
        lines.append("```")
    else:
        lines.append("No pane output captured.")
    lines.extend(
        [
            "",
            "## Manager Assessment",
            "",
            "- This closeout is deterministic evidence only; it does not claim the worker succeeded.",
            "- Treat dirty repo state, missing tests, or missing commits as follow-up work.",
            "- Use this page as the handoff anchor before sending more broad steer.",
            "",
            "## Next Bottleneck",
            "",
            "Hermes still needs automatic steer-history capture. This closeout records pane and repo evidence, but not the Telegram messages that instructed the worker.",
            "",
        ]
    )
    return "\n".join(lines)


def collect_closeout(
    *,
    repo: Path,
    session: str,
    report_dir: Path,
    generated_at: datetime,
    capture_lines: int = DEFAULT_CAPTURE_LINES,
    write_gbrain: bool = True,
    gbrain_slug: str | None = None,
    dashboard_url: str | None = None,
    workers: Sequence[WorkerSession] | None = None,
) -> CloseoutResult:
    repo = repo.expanduser().resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    worker_records = list(workers) if workers is not None else list_tmux_worker_sessions()
    worker = find_worker(session, worker_records)
    target = worker.target() if worker else session
    pane_lines = capture_pane(target, lines=capture_lines)
    repo_snapshot = collect_repo_snapshot(repo)
    effective_dashboard_url = dashboard_url if dashboard_url is not None else default_dashboard_url()
    stamp = generated_at.strftime("%Y%m%d-%H%M%S")
    path = report_dir / f"dev-supervision-closeout-{repo.name}-{stamp}.md"
    markdown = render_markdown(
        repo=repo,
        session=session,
        generated_at=generated_at,
        worker=worker,
        repo_snapshot=repo_snapshot,
        pane_lines=pane_lines,
        dashboard_url=effective_dashboard_url,
    )
    path.write_text(markdown, encoding="utf-8")

    slug = gbrain_slug or f"{DEFAULT_GBRAIN_PREFIX}-{_slugify(repo.name)}-{generated_at:%Y-%m-%d-%H%M%S}"
    gbrain_error = write_gbrain_page(slug, markdown) if write_gbrain else None
    return CloseoutResult(
        repo=repo,
        session=session,
        generated_at=generated_at,
        markdown_path=path,
        dashboard_url=effective_dashboard_url,
        worker=worker,
        repo_snapshot=repo_snapshot,
        pane_capture_lines=len(pane_lines),
        gbrain_slug=None if gbrain_error or not write_gbrain else slug,
        gbrain_error=gbrain_error,
    )


def telegram_summary(result: CloseoutResult) -> str:
    lines = [
        "Dev supervision closeout ready.",
        f"repo: {result.repo}",
        f"session: {result.session}",
        f"worker: {result.worker.target() if result.worker else 'not found'}",
        f"branch: {result.repo_snapshot.branch}",
        f"clean: {result.repo_snapshot.clean}",
        f"pane lines: {result.pane_capture_lines}",
        f"report: {result.markdown_path}",
    ]
    if result.dashboard_url:
        lines.append(f"dashboard: {result.dashboard_url}")
    if result.gbrain_slug:
        lines.append(f"gbrain: {result.gbrain_slug}")
    elif result.gbrain_error:
        lines.append(f"gbrain: not synced ({result.gbrain_error})")
    if result.repo_snapshot.status_short:
        lines.append("attention: repo has uncommitted changes")
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Close out a supervised tmux worker session")
    parser.add_argument("--repo", required=True, help="Repository path, e.g. /srv/dev/repos/ludeme")
    parser.add_argument("--session", required=True, help="tmux session or pane target, e.g. ludeme-codex")
    parser.add_argument("--out-dir", default=os.getenv("HERMES_DEV_SUPERVISION_CLOSEOUT_DIR", str(DEFAULT_REPORT_DIR)))
    parser.add_argument("--timezone", default=os.getenv("HERMES_DEV_SUPERVISION_TIMEZONE", DEFAULT_TIMEZONE))
    parser.add_argument("--capture-lines", type=int, default=int(os.getenv("HERMES_DEV_SUPERVISION_CAPTURE_LINES", DEFAULT_CAPTURE_LINES)))
    parser.add_argument("--gbrain-slug")
    parser.add_argument("--dashboard-url", default=os.getenv("HERMES_DEV_RUN_STATUS_DASHBOARD_URL"))
    parser.add_argument("--no-gbrain", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    now = datetime.now(timezone.utc).astimezone(_timezone(args.timezone))
    result = collect_closeout(
        repo=Path(args.repo),
        session=args.session,
        report_dir=Path(args.out_dir).expanduser(),
        generated_at=now,
        capture_lines=args.capture_lines,
        write_gbrain=not args.no_gbrain,
        gbrain_slug=args.gbrain_slug,
        dashboard_url=args.dashboard_url,
    )
    print(telegram_summary(result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
