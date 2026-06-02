from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import subprocess
from types import SimpleNamespace

from hermes_cli.dev_codex_run_review import (
    run_codex_review,
    telegram_summary,
)
from hermes_cli.dev_manager_events import load_events
from hermes_cli.dev_supervision_closeout import CommandResult


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "r@example.com"], cwd=path, check=True
    )
    subprocess.run(["git", "config", "user.name", "R"], cwd=path, check=True)
    (path / "README.md").write_text("demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True
    )


def test_run_codex_review_writes_artifacts_and_records_event(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    receipts = repo / "verification-receipts"
    receipts.mkdir()
    (receipts / "t_123.json").write_text('{"status": "passed"}\n', encoding="utf-8")
    event_log = tmp_path / "events.jsonl"
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "hermes_cli.dev_supervision_closeout.capture_pane",
        lambda target, lines: ["worker done"],
    )

    def fake_run_review_command(args, *, cwd, prompt, timeout):
        captured["args"] = args
        captured["cwd"] = cwd
        captured["prompt"] = prompt
        captured["timeout"] = timeout
        return CommandResult(
            tuple(args),
            0,
            "Verdict: FIX_FIRST\nConfidence: high\nOperator look: README.md\nWhy:\n- demo\nRequired next action: fix README",
            "",
        )

    monkeypatch.setattr(
        "hermes_cli.dev_codex_run_review._run_review_command", fake_run_review_command
    )

    result = run_codex_review(
        repo=repo,
        session="repo-codex",
        generated_at=datetime(2026, 6, 2, 5, 30, tzinfo=timezone.utc),
        out_dir=tmp_path / "reports",
        event_log=event_log,
        timeout=123,
    )

    assert captured["args"] == ["codex", "exec", "--sandbox", "read-only", "-"]
    assert captured["cwd"] == repo
    assert captured["timeout"] == 123
    assert "Hermes is the orchestrator" in str(captured["prompt"])
    assert "Receipt grade: verified" in str(captured["prompt"])
    assert result.prompt_path.exists()
    assert result.output_path.exists()
    assert "Verdict: FIX_FIRST" in result.output_path.read_text(encoding="utf-8")
    events = load_events([event_log])
    assert len(events) == 1
    assert events[0].event_type == "codex-review"
    assert str(result.output_path) in events[0].resulting_artifacts
    summary = telegram_summary(result)
    assert "Codex review ready." in summary
    assert "Verdict: FIX_FIRST" in summary
    assert "Confidence: high" in summary
    assert "receipt grade: verified" in summary


def test_run_codex_review_can_target_commit_with_title(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_repo(repo)
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "hermes_cli.dev_supervision_closeout.capture_pane", lambda target, lines: []
    )

    def fake_run_review_command(args, *, cwd, prompt, timeout):
        captured["args"] = args
        captured["prompt"] = prompt
        return CommandResult(
            tuple(args), 0, "Verdict: READY_TO_MERGE\nConfidence: medium", ""
        )

    monkeypatch.setattr(
        "hermes_cli.dev_codex_run_review._run_review_command", fake_run_review_command
    )

    run_codex_review(
        repo=repo,
        session="repo-codex",
        generated_at=datetime(2026, 6, 2, 5, 30, tzinfo=timezone.utc),
        out_dir=tmp_path / "reports",
        event_log=tmp_path / "events.jsonl",
        commit="abc123",
        title="demo commit",
    )

    assert captured["args"] == ["codex", "exec", "--sandbox", "read-only", "-"]
    assert "Hermes is the orchestrator" in str(captured["prompt"])
    assert "Target: commit abc123" in str(captured["prompt"])
    assert "Title: demo commit" in str(captured["prompt"])
    assert "git show --stat --patch --find-renames abc123" in str(captured["prompt"])


def test_telegram_summary_relays_native_commit_review_line(tmp_path):
    result = SimpleNamespace(
        ok=True,
        repo=tmp_path / "repo",
        session="repo-codex",
        command_result=CommandResult(
            ("codex", "review"),
            0,
            "No actionable correctness issues were found.",
            "",
        ),
        closeout=SimpleNamespace(
            evidence_grade=SimpleNamespace(grade="weak"),
            markdown_path=tmp_path / "closeout.md",
        ),
        output_path=tmp_path / "output.md",
        prompt_path=tmp_path / "prompt.md",
        event=SimpleNamespace(id="evt1"),
    )

    assert "review: No actionable correctness issues were found." in telegram_summary(
        result
    )
