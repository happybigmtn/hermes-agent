from __future__ import annotations

from datetime import datetime, timezone

from hermes_cli.daily_mastery_check import (
    build_mastery_markdown,
    generate_mastery_check,
    latest_checklist_section,
    telegram_summary,
)


CHECKLIST = """\
# Human Understanding Checklist

## 2026-06-01 18:48 EDT Report

Window: old
Markdown: /tmp/old.md
PDF: /tmp/old.pdf
Repos: old/repo
Commits: 1

- [ ] Problem: old question.

## 2026-06-01 20:00 EDT Report

Window: current
Markdown: /tmp/current.md
PDF: /tmp/current.pdf
Repos: happybigmtn/autodev, happybigmtn/hermes-agent
Commits: 42
Orchestrator phases: 2

- [ ] Problem: she can explain what changed and why the prior state was insufficient.
- [ ] Branches: she can name the branches and whether work came from Hermes or elsewhere.
- [ ] Orchestrator: she can connect phase history to artifacts, commits, blockers, or next action.
"""


def test_latest_checklist_section_reads_newest_report():
    section = latest_checklist_section(CHECKLIST)

    assert section is not None
    assert section.title == "2026-06-01 20:00 EDT Report"
    assert section.fields["Markdown"] == "/tmp/current.md"
    assert section.fields["Orchestrator phases"] == "2"
    assert len(section.unchecked_items) == 3
    assert section.unchecked_items[-1].startswith("Orchestrator:")


def test_build_mastery_markdown_turns_checklist_into_questions(tmp_path):
    section = latest_checklist_section(CHECKLIST)
    markdown = build_mastery_markdown(
        section,
        checklist_path=tmp_path / "HUMAN-UNDERSTANDING-CHECKLIST.md",
        generated_at=datetime(2026, 6, 1, 20, 15, tzinfo=timezone.utc),
    )

    assert "Daily Development Mastery Check" in markdown
    assert "Report section: 2026-06-01 20:00 EDT Report" in markdown
    assert "Orchestrator phases: 2" in markdown
    assert "## Current Mastery Stage" in markdown
    assert "1. Problem:" in markdown
    assert "## Later Stages" in markdown
    assert "3. Orchestrator:" in markdown
    assert "Distinguish landed commits from in-flight orchestrator work" in markdown
    assert "Do not move to later stages until the current stage is answered concretely" in markdown


def test_generate_mastery_check_writes_artifact_and_summary(tmp_path):
    checklist = tmp_path / "HUMAN-UNDERSTANDING-CHECKLIST.md"
    checklist.write_text(CHECKLIST, encoding="utf-8")

    result = generate_mastery_check(
        checklist_path=checklist,
        report_dir=tmp_path / "reports",
        generated_at=datetime(2026, 6, 1, 20, 15, tzinfo=timezone.utc),
        write_gbrain=False,
    )

    assert result.markdown_path.exists()
    assert result.section_title == "2026-06-01 20:00 EDT Report"
    assert result.question_count == 1
    assert result.current_stage is not None
    assert result.current_stage.startswith("Problem:")
    assert result.remaining_stage_count == 2
    summary = telegram_summary(result)
    assert "Daily development mastery check ready." in summary
    assert "Current stage: Problem:" in summary
    assert "Later stages remaining: 2" in summary
    assert str(result.markdown_path) in summary


def test_generate_mastery_check_handles_missing_checklist(tmp_path):
    result = generate_mastery_check(
        checklist_path=tmp_path / "missing.md",
        report_dir=tmp_path / "reports",
        generated_at=datetime(2026, 6, 1, 20, 15, tzinfo=timezone.utc),
        write_gbrain=False,
    )

    assert result.section_title is None
    assert result.question_count == 0
    assert result.current_stage is None
    assert result.remaining_stage_count == 0
    assert "run the daily development teaching report first" in result.markdown_path.read_text(
        encoding="utf-8"
    )
