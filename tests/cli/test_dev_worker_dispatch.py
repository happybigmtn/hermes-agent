from __future__ import annotations

from pathlib import Path

from hermes_cli.dev_manager_events import load_events, record_event
from hermes_cli.dev_worker_dispatch import (
    CommandResult,
    DispatchResult,
    dispatch_worker,
    main,
    telegram_summary,
)


def test_dispatch_records_event_before_tmux_commands(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    event_log = tmp_path / "events.jsonl"
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr("hermes_cli.dev_worker_dispatch.shutil.which", lambda command: "/bin/tmux")

    def fake_run(args, **kwargs):
        calls.append(tuple(args))
        if args[:2] == ["tmux", "has-session"]:
            assert event_log.exists() is False
            return CommandResult(tuple(args), 1, "", "missing")
        assert event_log.exists() is True
        return CommandResult(tuple(args), 0, "", "")

    monkeypatch.setattr("hermes_cli.dev_worker_dispatch._run", fake_run)

    result = dispatch_worker(
        repo=repo,
        session="repo-codex",
        intent="start focused work",
        message="codex --yolo",
        event_log=event_log,
        artifact_root=tmp_path / "artifacts",
    )

    assert result.created_session is True
    assert result.sent_message is True
    assert calls[0][:2] == ("tmux", "has-session")
    assert calls[1][:2] == ("tmux", "new-session")
    assert calls[2][:2] == ("tmux", "send-keys")
    events = load_events([event_log])
    assert len(events) == 1
    assert events[0].event_type == "worker-start"
    assert events[0].intent == "start focused work"
    assert str(result.artifact_path) in events[0].resulting_artifacts
    assert "codex --yolo" in result.artifact_path.read_text(encoding="utf-8")


def test_dispatch_reuses_existing_session_as_steer(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    event_log = tmp_path / "events.jsonl"
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr("hermes_cli.dev_worker_dispatch.shutil.which", lambda command: "/bin/tmux")

    def fake_run(args, **kwargs):
        calls.append(tuple(args))
        return CommandResult(tuple(args), 0, "", "")

    monkeypatch.setattr("hermes_cli.dev_worker_dispatch._run", fake_run)

    result = dispatch_worker(
        repo=repo,
        session="repo-codex:0.0",
        intent="nudge worker",
        message="continue",
        event_log=event_log,
        artifact_root=tmp_path / "artifacts",
    )

    assert result.created_session is False
    assert result.sent_message is True
    assert not any(call[:2] == ("tmux", "new-session") for call in calls)
    assert load_events([event_log])[0].event_type == "worker-steer"


def test_dispatch_reports_missing_session_when_no_start(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr("hermes_cli.dev_worker_dispatch.shutil.which", lambda command: "/bin/tmux")
    monkeypatch.setattr(
        "hermes_cli.dev_worker_dispatch._run",
        lambda args, **kwargs: CommandResult(tuple(args), 1, "", "missing"),
    )

    result = dispatch_worker(
        repo=repo,
        session="repo-codex",
        intent="steer only",
        message="continue",
        event_log=tmp_path / "events.jsonl",
        artifact_root=tmp_path / "artifacts",
        start_if_missing=False,
    )

    assert result.created_session is False
    assert result.sent_message is False
    assert result.failed_results
    assert "attention:" in telegram_summary(result)


def test_main_reads_message_file(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    message = tmp_path / "message.txt"
    message.write_text("continue carefully\n", encoding="utf-8")

    def fake_dispatch_worker(**kwargs):
        assert kwargs["message"] == "continue carefully\n"
        event = record_event(
            repo=repo,
            worker_session="repo-codex",
            intent="demo",
            event_log=tmp_path / "events.jsonl",
            resulting_artifacts=[str(tmp_path / "artifact.md")],
        )
        artifact = tmp_path / "artifact.md"
        artifact.write_text("demo\n", encoding="utf-8")
        return DispatchResult(
            repo=repo,
            session="repo-codex",
            target="repo-codex:0.0",
            event=event,
            artifact_path=artifact,
            created_session=False,
            sent_message=False,
        )

    monkeypatch.setattr("hermes_cli.dev_worker_dispatch.dispatch_worker", fake_dispatch_worker)

    assert main(["--repo", str(repo), "--session", "repo-codex", "--intent", "demo", "--message-file", str(message)]) == 0
    assert "Dev worker dispatch ready." in capsys.readouterr().out
