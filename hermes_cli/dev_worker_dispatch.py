"""Dispatch or steer a supervised tmux worker with durable manager evidence."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from hermes_cli.dev_manager_events import (
    DEFAULT_EVENT_LOG,
    ManagerEvent,
    record_event,
    render_event_line,
)


DEFAULT_REQUESTED_BY = "hermes"
DEFAULT_DELIVERY_CHANNEL = "telegram"


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class DispatchResult:
    repo: Path
    session: str
    target: str
    event: ManagerEvent
    artifact_path: Path
    created_session: bool
    sent_message: bool
    tmux_results: list[CommandResult] = field(default_factory=list)

    @property
    def failed_results(self) -> list[CommandResult]:
        return [result for result in self.tmux_results if result.returncode != 0]


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


def _slugify(value: str) -> str:
    chars: list[str] = []
    for char in value.lower():
        if char.isalnum():
            chars.append(char)
        elif chars and chars[-1] != "-":
            chars.append("-")
    return "".join(chars).strip("-") or "worker"


def _session_name(session: str) -> str:
    return session.split(":", 1)[0]


def _target(session: str) -> str:
    return session if ":" in session else f"{session}:0.0"


def tmux_session_exists(session: str) -> bool:
    if not shutil.which("tmux"):
        return False
    result = _run(["tmux", "has-session", "-t", _session_name(session)], timeout=8)
    return result.returncode == 0


def _write_dispatch_artifact(
    *,
    path: Path,
    event: ManagerEvent,
    repo: Path,
    session: str,
    target: str,
    intent: str,
    message: str | None,
    created_session: bool,
    tmux_results: Sequence[CommandResult],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Dev Worker Dispatch",
        "",
        f"Generated: {event.created_at_iso}",
        f"Repo: `{repo}`",
        f"Session: `{session}`",
        f"Target: `{target}`",
        f"Event: `{event.id}`",
        f"Event type: `{event.event_type}`",
        f"Created session: `{created_session}`",
        "",
        "## Intent",
        "",
        intent,
        "",
        "## Manager Event",
        "",
        render_event_line(event),
        "",
    ]
    if event.resulting_artifacts:
        lines.extend(["## Resulting Artifacts", ""])
        lines.extend(f"- `{artifact}`" for artifact in event.resulting_artifacts)
        lines.append("")
    if message:
        lines.extend(["## Sent Message", "", "```text", message, "```", ""])
    lines.extend(["## Tmux Results", ""])
    if tmux_results:
        for result in tmux_results:
            lines.append(f"- `{result.returncode}` `{' '.join(result.args)}`")
            if result.stderr:
                lines.append(f"  stderr: `{result.stderr}`")
    else:
        lines.append("- none")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def dispatch_worker(
    *,
    repo: Path,
    session: str,
    intent: str,
    message: str | None = None,
    event_log: Path = DEFAULT_EVENT_LOG,
    event_type: str | None = None,
    requested_by: str | None = DEFAULT_REQUESTED_BY,
    delivery_channel: str | None = DEFAULT_DELIVERY_CHANNEL,
    artifact_root: Path | None = None,
    start_if_missing: bool = True,
    enter: bool = True,
    created_at: datetime | None = None,
) -> DispatchResult:
    repo = repo.expanduser().resolve()
    artifact_root = artifact_root or repo / ".auto" / "orchestrator"
    stamp = (created_at or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    run_dir = artifact_root / f"{_slugify(session)}-dispatch-{stamp}"
    artifact_path = run_dir / "manager-dispatch.md"
    target = _target(session)
    exists_before = tmux_session_exists(session)
    inferred_event_type = event_type or ("worker-start" if not exists_before and start_if_missing else "worker-steer")
    event = record_event(
        repo=repo,
        worker_session=_session_name(session),
        intent=intent,
        event_log=event_log,
        event_type=inferred_event_type,
        requested_by=requested_by,
        delivery_channel=delivery_channel,
        resulting_artifacts=[str(artifact_path)],
        created_at=created_at,
    )

    tmux_results: list[CommandResult] = []
    created_session = False
    if not exists_before and start_if_missing:
        tmux_results.append(
            _run(["tmux", "new-session", "-d", "-s", _session_name(session), "-c", str(repo)], timeout=12)
        )
        created_session = tmux_results[-1].returncode == 0
    elif not exists_before and not start_if_missing:
        tmux_results.append(CommandResult(("tmux", "has-session", "-t", _session_name(session)), 1, "", "session missing"))

    sent_message = False
    if message and (exists_before or created_session):
        for line in message.splitlines() or [""]:
            tmux_results.append(_run(["tmux", "send-keys", "-l", "-t", target, line], timeout=12))
            if enter:
                tmux_results.append(_run(["tmux", "send-keys", "-t", target, "C-m"], timeout=12))
        sent_message = all(result.returncode == 0 for result in tmux_results[-(2 * max(1, len(message.splitlines()))):])

    _write_dispatch_artifact(
        path=artifact_path,
        event=event,
        repo=repo,
        session=session,
        target=target,
        intent=intent,
        message=message,
        created_session=created_session,
        tmux_results=tmux_results,
    )
    return DispatchResult(
        repo=repo,
        session=session,
        target=target,
        event=event,
        artifact_path=artifact_path,
        created_session=created_session,
        sent_message=sent_message,
        tmux_results=tmux_results,
    )


def telegram_summary(result: DispatchResult) -> str:
    lines = [
        "Dev worker dispatch ready.",
        f"repo: {result.repo}",
        f"session: {result.session}",
        f"target: {result.target}",
        f"event: {result.event.id}",
        f"event type: {result.event.event_type}",
        f"created session: {result.created_session}",
        f"sent message: {result.sent_message}",
        f"artifact: {result.artifact_path}",
    ]
    if result.failed_results:
        lines.append(f"attention: {len(result.failed_results)} tmux command(s) failed")
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record a manager event and dispatch/steer a tmux worker")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--session", required=True, help="tmux session or target, e.g. ludeme-codex or ludeme-codex:0.0")
    parser.add_argument("--intent", required=True)
    parser.add_argument("--message", help="Literal message or command to send to the pane")
    parser.add_argument("--message-file", help="Read message text from this file")
    parser.add_argument("--event-log", default=os.getenv("HERMES_DEV_MANAGER_EVENT_LOG", str(DEFAULT_EVENT_LOG)))
    parser.add_argument("--event-type", choices=["worker-start", "worker-steer", "worker-resume"])
    parser.add_argument("--requested-by", default=os.getenv("HERMES_DEV_MANAGER_REQUESTED_BY", DEFAULT_REQUESTED_BY))
    parser.add_argument("--delivery-channel", default=os.getenv("HERMES_DEV_MANAGER_DELIVERY_CHANNEL", DEFAULT_DELIVERY_CHANNEL))
    parser.add_argument("--artifact-root", help="Defaults to <repo>/.auto/orchestrator")
    parser.add_argument("--no-start-if-missing", action="store_true")
    parser.add_argument("--no-enter", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    message = args.message
    if args.message_file:
        message = Path(args.message_file).expanduser().read_text(encoding="utf-8")
    result = dispatch_worker(
        repo=Path(args.repo),
        session=args.session,
        intent=args.intent,
        message=message,
        event_log=Path(args.event_log).expanduser(),
        event_type=args.event_type,
        requested_by=args.requested_by,
        delivery_channel=args.delivery_channel,
        artifact_root=Path(args.artifact_root).expanduser() if args.artifact_root else None,
        start_if_missing=not args.no_start_if_missing,
        enter=not args.no_enter,
    )
    print(telegram_summary(result))
    return 1 if result.failed_results else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
