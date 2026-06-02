"""Durable manager-event ledger for supervised development workers.

Hermes uses this lightweight JSONL registry to remember explicit manager
actions such as launching or steering a tmux worker. Closeout can then cite the
manager intent directly instead of reconstructing it from transcript search.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
from uuid import uuid4

from hermes_cli.dev_run_status import DEFAULT_REPORT_DIR


DEFAULT_EVENT_LOG = DEFAULT_REPORT_DIR / "dev-manager-events.jsonl"
DEFAULT_LIMIT = 20


@dataclass(frozen=True)
class ManagerEvent:
    id: str
    created_at: datetime
    event_type: str
    repo: str
    worker_session: str
    intent: str
    requested_by: str | None = None
    delivery_channel: str | None = None
    resulting_artifacts: list[str] = field(default_factory=list)
    notes: str | None = None

    @property
    def created_at_iso(self) -> str:
        return self.created_at.astimezone(timezone.utc).isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: object) -> datetime:
    text = str(value or "").strip()
    if not text:
        return _now()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return _now()
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_repo(repo: str | Path) -> str:
    try:
        return str(Path(repo).expanduser().resolve())
    except OSError:
        return str(repo)


def _repo_keys(repo: str | Path) -> set[str]:
    normalized = _normalize_repo(repo)
    path = Path(normalized)
    return {normalized, str(repo), path.name}


def _session_keys(session: str) -> set[str]:
    keys = {session}
    if ":" in session:
        keys.add(session.split(":", 1)[0])
    return {item for item in keys if item}


def event_from_dict(data: dict[str, object]) -> ManagerEvent:
    artifacts = data.get("resulting_artifacts") or []
    if isinstance(artifacts, str):
        artifacts = [artifacts]
    if not isinstance(artifacts, list):
        artifacts = []
    return ManagerEvent(
        id=str(data.get("id") or ""),
        created_at=_parse_datetime(data.get("created_at")),
        event_type=str(data.get("event_type") or "manager-event"),
        repo=str(data.get("repo") or ""),
        worker_session=str(data.get("worker_session") or ""),
        intent=str(data.get("intent") or ""),
        requested_by=str(data["requested_by"]) if data.get("requested_by") else None,
        delivery_channel=str(data["delivery_channel"]) if data.get("delivery_channel") else None,
        resulting_artifacts=[str(item) for item in artifacts if str(item).strip()],
        notes=str(data["notes"]) if data.get("notes") else None,
    )


def event_to_dict(event: ManagerEvent) -> dict[str, object]:
    data: dict[str, object] = {
        "id": event.id,
        "created_at": event.created_at_iso,
        "event_type": event.event_type,
        "repo": event.repo,
        "worker_session": event.worker_session,
        "intent": event.intent,
        "resulting_artifacts": event.resulting_artifacts,
    }
    if event.requested_by:
        data["requested_by"] = event.requested_by
    if event.delivery_channel:
        data["delivery_channel"] = event.delivery_channel
    if event.notes:
        data["notes"] = event.notes
    return data


def record_event(
    *,
    repo: str | Path,
    worker_session: str,
    intent: str,
    event_log: Path = DEFAULT_EVENT_LOG,
    event_type: str = "worker-start",
    requested_by: str | None = None,
    delivery_channel: str | None = None,
    resulting_artifacts: Sequence[str] | None = None,
    notes: str | None = None,
    created_at: datetime | None = None,
) -> ManagerEvent:
    event = ManagerEvent(
        id=uuid4().hex[:12],
        created_at=created_at or _now(),
        event_type=event_type,
        repo=_normalize_repo(repo),
        worker_session=worker_session,
        intent=" ".join(intent.split()),
        requested_by=requested_by,
        delivery_channel=delivery_channel,
        resulting_artifacts=list(resulting_artifacts or []),
        notes=notes,
    )
    event_log = event_log.expanduser()
    event_log.parent.mkdir(parents=True, exist_ok=True)
    with event_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event_to_dict(event), sort_keys=True) + "\n")
    return event


def load_events(event_logs: Sequence[Path] | None = None) -> list[ManagerEvent]:
    logs = list(event_logs) if event_logs is not None else [DEFAULT_EVENT_LOG]
    events: list[ManagerEvent] = []
    for event_log in logs:
        path = event_log.expanduser()
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(event_from_dict(payload))
    events.sort(key=lambda item: item.created_at, reverse=True)
    return events


def matching_events(
    *,
    repo: str | Path | None = None,
    worker_session: str | None = None,
    event_logs: Sequence[Path] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[ManagerEvent]:
    repo_match = _repo_keys(repo) if repo else None
    session_match = _session_keys(worker_session) if worker_session else None
    matches: list[ManagerEvent] = []
    for event in load_events(event_logs):
        if repo_match and not (_repo_keys(event.repo) & repo_match):
            continue
        if session_match and not (_session_keys(event.worker_session) & session_match):
            continue
        matches.append(event)
        if len(matches) >= limit:
            break
    return matches


def render_event_line(event: ManagerEvent) -> str:
    actor = event.requested_by or "unknown"
    channel = event.delivery_channel or "unknown"
    return (
        f"`{event.created_at_iso}` `{event.event_type}` "
        f"`{event.worker_session}` by `{actor}` via `{channel}`: {event.intent}"
    )


def _event_log_arg(value: str | None) -> Path:
    return Path(value).expanduser() if value else DEFAULT_EVENT_LOG


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record or list Hermes manager events")
    parser.add_argument("--event-log", default=os.getenv("HERMES_DEV_MANAGER_EVENT_LOG"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    record = subparsers.add_parser("record", help="Append one manager event")
    record.add_argument("--repo", required=True)
    record.add_argument("--worker-session", required=True)
    record.add_argument("--intent", required=True)
    record.add_argument("--event-type", default="worker-start")
    record.add_argument("--requested-by", default=os.getenv("HERMES_DEV_MANAGER_REQUESTED_BY", "hermes"))
    record.add_argument("--delivery-channel", default=os.getenv("HERMES_DEV_MANAGER_DELIVERY_CHANNEL", "telegram"))
    record.add_argument("--artifact", action="append", default=[], help="Resulting artifact path or URL")
    record.add_argument("--notes")

    list_parser = subparsers.add_parser("list", help="List matching manager events")
    list_parser.add_argument("--repo")
    list_parser.add_argument("--worker-session")
    list_parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    list_parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    event_log = _event_log_arg(args.event_log)
    if args.command == "record":
        event = record_event(
            repo=args.repo,
            worker_session=args.worker_session,
            intent=args.intent,
            event_log=event_log,
            event_type=args.event_type,
            requested_by=args.requested_by,
            delivery_channel=args.delivery_channel,
            resulting_artifacts=args.artifact,
            notes=args.notes,
        )
        print(f"manager event recorded: {event.id}")
        print(render_event_line(event))
        print(f"event log: {event_log}")
        return 0

    events = matching_events(
        repo=args.repo,
        worker_session=args.worker_session,
        event_logs=[event_log],
        limit=args.limit,
    )
    if args.json:
        print(json.dumps([event_to_dict(event) for event in events], indent=2, sort_keys=True))
    elif events:
        for event in events:
            print(render_event_line(event))
    else:
        print("no manager events found")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
