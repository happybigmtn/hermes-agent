from __future__ import annotations

from datetime import datetime, timezone

from hermes_cli.dev_manager_events import (
    load_events,
    main,
    matching_events,
    record_event,
    render_event_line,
)


def test_record_event_appends_jsonl_and_loads_newest_first(tmp_path):
    event_log = tmp_path / "events.jsonl"

    older = record_event(
        repo=tmp_path / "ludeme",
        worker_session="ludeme-codex",
        intent="start ludeme work",
        event_log=event_log,
        requested_by="hermes",
        delivery_channel="telegram",
        created_at=datetime(2026, 6, 2, 1, 0, tzinfo=timezone.utc),
    )
    newer = record_event(
        repo=tmp_path / "nullspaceton",
        worker_session="nullspaceton-codex",
        intent="start water slice",
        event_log=event_log,
        resulting_artifacts=[".auto/orchestrator/run/prompt.md"],
        created_at=datetime(2026, 6, 2, 2, 0, tzinfo=timezone.utc),
    )

    events = load_events([event_log])

    assert [event.id for event in events] == [newer.id, older.id]
    assert events[0].resulting_artifacts == [".auto/orchestrator/run/prompt.md"]


def test_matching_events_matches_repo_basename_and_tmux_target(tmp_path):
    event_log = tmp_path / "events.jsonl"
    repo = tmp_path / "nullspaceton"
    repo.mkdir()
    record_event(
        repo=repo,
        worker_session="nullspaceton-codex",
        intent="start water slice",
        event_log=event_log,
    )

    events = matching_events(
        repo="nullspaceton",
        worker_session="nullspaceton-codex:0.0",
        event_logs=[event_log],
    )

    assert len(events) == 1
    assert events[0].intent == "start water slice"


def test_main_record_and_list(tmp_path, capsys):
    event_log = tmp_path / "events.jsonl"

    assert main(
        [
            "--event-log",
            str(event_log),
            "record",
            "--repo",
            str(tmp_path / "repo"),
            "--worker-session",
            "repo-codex",
            "--intent",
            "  start   focused work  ",
            "--artifact",
            ".auto/run/prompt.md",
        ]
    ) == 0
    assert "manager event recorded:" in capsys.readouterr().out

    assert main(
        [
            "--event-log",
            str(event_log),
            "list",
            "--repo",
            "repo",
            "--worker-session",
            "repo-codex",
        ]
    ) == 0

    output = capsys.readouterr().out
    assert "start focused work" in output
    assert "repo-codex" in output


def test_render_event_line_includes_manager_context(tmp_path):
    event = record_event(
        repo=tmp_path / "repo",
        worker_session="repo-codex",
        intent="supervise work",
        event_log=tmp_path / "events.jsonl",
        requested_by="operator",
        delivery_channel="telegram",
        created_at=datetime(2026, 6, 2, 3, 0, tzinfo=timezone.utc),
    )

    line = render_event_line(event)

    assert "worker-start" in line
    assert "operator" in line
    assert "telegram" in line
    assert "supervise work" in line
