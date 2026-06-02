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
from hermes_cli.dev_manager_events import (
    DEFAULT_EVENT_LOG,
    ManagerEvent,
    matching_events,
    render_event_line,
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
class SteerHistoryEntry:
    db_path: Path
    session_id: str
    role: str
    timestamp: float | None
    source: str | None
    snippet: str

    @property
    def timestamp_iso(self) -> str:
        if self.timestamp is None:
            return "unknown"
        try:
            return datetime.fromtimestamp(float(self.timestamp), tz=timezone.utc).isoformat()
        except (OSError, ValueError, TypeError):
            return "unknown"


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
    manager_events: list[ManagerEvent]
    steer_history: list[SteerHistoryEntry]
    steer_history_error: str | None
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


def _one_line(value: object, *, max_chars: int = 220) -> str:
    text = " ".join(str(value or "").split())
    text = text.replace(">>>", "").replace("<<<", "")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _looks_like_tool_call_blob(snippet: str) -> bool:
    compact = snippet.lstrip()
    if not compact:
        return True
    if compact.startswith("[{") and ("call_id" in compact or "function" in compact):
        return True
    if "response_item_id" in compact or "call_id" in compact or '"function"' in compact:
        return True
    if '\\"' in compact and ("metadata" in compact or "arguments" in compact or "board" in compact):
        return True
    return False


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


def default_session_db_paths() -> list[Path]:
    home = Path(os.getenv("HERMES_HOME", "~/.hermes")).expanduser()
    return [
        home / "state.db",
        home / "profiles" / "orchestrator" / "state.db",
    ]


def default_steer_terms(repo: Path, session: str) -> list[str]:
    terms = [repo.name, session]
    if ":" in session:
        terms.append(session.split(":", 1)[0])
    normalized_session = session.replace("-", " ")
    if normalized_session != session:
        terms.append(normalized_session)
    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        clean = " ".join(str(term).split())
        if clean and clean not in seen:
            deduped.append(clean)
            seen.add(clean)
    return deduped


def collect_steer_history(
    *,
    repo: Path,
    session: str,
    db_paths: Sequence[Path] | None = None,
    search_terms: Sequence[str] | None = None,
    limit: int = 8,
) -> tuple[list[SteerHistoryEntry], str | None]:
    try:
        from hermes_state import SessionDB
    except Exception as exc:
        return [], f"SessionDB unavailable: {exc}"

    terms = list(search_terms) if search_terms else default_steer_terms(repo, session)
    paths = list(db_paths) if db_paths is not None else default_session_db_paths()
    entries: dict[tuple[str, int | str, str], SteerHistoryEntry] = {}
    errors: list[str] = []
    per_term_limit = max(limit * 2, 4)
    for db_path in paths:
        db_path = db_path.expanduser()
        if not db_path.exists():
            continue
        try:
            db = SessionDB(db_path=db_path)
        except Exception as exc:
            errors.append(f"{db_path}: open failed: {exc}")
            continue
        try:
            for term in terms:
                try:
                    matches = db.search_messages(
                        query=term,
                        role_filter=["user", "assistant"],
                        limit=per_term_limit,
                        sort="newest",
                    )
                except Exception as exc:
                    errors.append(f"{db_path}: search `{term}` failed: {exc}")
                    continue
                for match in matches:
                    snippet = _one_line(match.get("snippet") or "")
                    if not snippet or _looks_like_tool_call_blob(snippet):
                        continue
                    key = (str(db_path), match.get("id", ""), str(match.get("session_id", "")))
                    if key in entries:
                        continue
                    entries[key] = SteerHistoryEntry(
                        db_path=db_path,
                        session_id=str(match.get("session_id") or ""),
                        role=str(match.get("role") or "unknown"),
                        timestamp=match.get("timestamp"),
                        source=match.get("source"),
                        snippet=snippet,
                    )
        finally:
            try:
                db.close()
            except Exception:
                pass
    ordered = sorted(
        entries.values(),
        key=lambda item: float(item.timestamp or 0),
        reverse=True,
    )[:limit]
    return ordered, "; ".join(errors) if errors else None


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
    manager_events: Sequence[ManagerEvent],
    steer_history: Sequence[SteerHistoryEntry],
    steer_history_error: str | None,
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
            "## Manager Events",
            "",
            "Source: Hermes manager-event JSONL ledger.",
            "",
        ]
    )
    if manager_events:
        for event in manager_events:
            lines.append(f"- {render_event_line(event)}")
            if event.resulting_artifacts:
                artifact_text = ", ".join(f"`{item}`" for item in event.resulting_artifacts)
                lines.append(f"  Artifacts: {artifact_text}")
    else:
        lines.append("- no matching manager events found")
    lines.extend(
        [
            "",
            "## Steer History",
            "",
            "Source: Hermes SessionDB search over user/assistant transcript messages. Tool messages are omitted.",
            "",
        ]
    )
    if steer_history:
        for entry in steer_history:
            lines.append(
                f"- `{entry.timestamp_iso}` `{entry.role}` "
                f"`{entry.session_id}`: {entry.snippet}"
            )
    else:
        lines.append("- no matching transcript snippets found")
    if steer_history_error:
        lines.extend(["", f"Steer history warning: `{steer_history_error}`"])
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
            "Hermes has a durable manager-event ledger, but gateway/tmux launch paths still need to record events automatically at dispatch time.",
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
    include_steer_history: bool = True,
    steer_db_paths: Sequence[Path] | None = None,
    steer_search_terms: Sequence[str] | None = None,
    steer_limit: int = 8,
    manager_event_paths: Sequence[Path] | None = None,
    manager_event_limit: int = 8,
) -> CloseoutResult:
    repo = repo.expanduser().resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    worker_records = list(workers) if workers is not None else list_tmux_worker_sessions()
    worker = find_worker(session, worker_records)
    target = worker.target() if worker else session
    pane_lines = capture_pane(target, lines=capture_lines)
    repo_snapshot = collect_repo_snapshot(repo)
    manager_events = matching_events(
        repo=repo,
        worker_session=session,
        event_logs=manager_event_paths,
        limit=manager_event_limit,
    )
    steer_history: list[SteerHistoryEntry] = []
    steer_history_error = None
    if include_steer_history:
        steer_history, steer_history_error = collect_steer_history(
            repo=repo,
            session=session,
            db_paths=steer_db_paths,
            search_terms=steer_search_terms,
            limit=steer_limit,
        )
    effective_dashboard_url = dashboard_url if dashboard_url is not None else default_dashboard_url()
    stamp = generated_at.strftime("%Y%m%d-%H%M%S")
    path = report_dir / f"dev-supervision-closeout-{repo.name}-{stamp}.md"
    markdown = render_markdown(
        repo=repo,
        session=session,
        generated_at=generated_at,
        worker=worker,
        repo_snapshot=repo_snapshot,
        manager_events=manager_events,
        steer_history=steer_history,
        steer_history_error=steer_history_error,
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
        manager_events=manager_events,
        steer_history=steer_history,
        steer_history_error=steer_history_error,
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
        f"manager events: {len(result.manager_events)}",
        f"steer snippets: {len(result.steer_history)}",
        f"pane lines: {result.pane_capture_lines}",
        f"report: {result.markdown_path}",
    ]
    if result.dashboard_url:
        lines.append(f"dashboard: {result.dashboard_url}")
    if result.gbrain_slug:
        lines.append(f"gbrain: {result.gbrain_slug}")
    elif result.gbrain_error:
        lines.append(f"gbrain: not synced ({result.gbrain_error})")
    if result.steer_history_error:
        lines.append(f"steer history: warning ({result.steer_history_error})")
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
    parser.add_argument("--steer-query", action="append", default=[], help="Extra SessionDB search term for steer history")
    parser.add_argument("--steer-db", action="append", default=[], help="SessionDB path to search; defaults to root and orchestrator profile state.db")
    parser.add_argument("--steer-limit", type=int, default=int(os.getenv("HERMES_DEV_SUPERVISION_STEER_LIMIT", "8")))
    parser.add_argument("--manager-event-log", action="append", default=[], help="Manager event JSONL path; defaults to ~/.hermes/reports/dev-manager-events.jsonl")
    parser.add_argument("--manager-event-limit", type=int, default=int(os.getenv("HERMES_DEV_MANAGER_EVENT_LIMIT", "8")))
    parser.add_argument("--no-steer-history", action="store_true")
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
        include_steer_history=not args.no_steer_history,
        steer_db_paths=(
            [Path(item).expanduser() for item in args.steer_db]
            if args.steer_db
            else None
        ),
        steer_search_terms=args.steer_query or None,
        steer_limit=args.steer_limit,
        manager_event_paths=(
            [Path(item).expanduser() for item in args.manager_event_log]
            if args.manager_event_log
            else [DEFAULT_EVENT_LOG]
        ),
        manager_event_limit=args.manager_event_limit,
    )
    print(telegram_summary(result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
