from __future__ import annotations

from datetime import datetime, timezone

from hermes_cli.daily_mastery_answer import (
    assess_answer,
    record_mastery_answer,
    telegram_summary,
)


CHECKLIST = """\
# Human Understanding Checklist

## 2026-06-01 20:00 EDT Report

Window: current
Markdown: /tmp/current.md
PDF: /tmp/current.pdf
Repos: happybigmtn/autodev, happybigmtn/hermes-agent
Commits: 42
Orchestrator phases: 2

- [ ] Problem: she can explain what changed and why the prior state was insufficient.
- [ ] Branches: she can name the branches and whether work came from Hermes or elsewhere.
- [ ] Solution: she can explain the design decisions and why this approach fits.
"""


GOOD_ANSWER = """\
The problem existed in happybigmtn/hermes-agent because the prior mastery prompt
treated the daily report like a batch quiz instead of a staged teaching loop.
Branch rk/daily-dev-teaching-report now has commit 95e420dd1, and the concrete
artifact is /home/dev/.hermes/reports/HUMAN-UNDERSTANDING-CHECKLIST.md plus the
daily-mastery-check report. The validation was pytest on tests/cli plus ruff,
and the remaining risk is that Telegram replies still need to be recorded.
"""


def test_assess_answer_rejects_vague_answer():
    assessment = assess_answer("Looks good to me.")

    assert assessment.accepted is False
    assert "answer is too short to prove mastery" in assessment.reasons
    assert "answer must name a concrete repo or repo path" in assessment.reasons


def test_record_mastery_answer_marks_current_stage_only(tmp_path):
    checklist = tmp_path / "HUMAN-UNDERSTANDING-CHECKLIST.md"
    checklist.write_text(CHECKLIST, encoding="utf-8")

    result = record_mastery_answer(
        checklist_path=checklist,
        report_dir=tmp_path / "reports",
        answer=GOOD_ANSWER,
        answered_at=datetime(2026, 6, 1, 20, 30, tzinfo=timezone.utc),
        write_gbrain=False,
    )

    assert result.accepted is True
    assert result.stage is not None
    assert result.stage.startswith("Problem:")
    assert result.next_stage is not None
    assert result.next_stage.startswith("Branches:")
    updated = checklist.read_text(encoding="utf-8")
    assert "- [x] Problem:" in updated
    assert "- [ ] Branches:" in updated
    assert "95e420dd1" in updated
    assert "Evidence: artifact, branch, commit, repo, risk, validation" in updated
    assert result.markdown_path.exists()
    assert "Accepted: yes" in result.markdown_path.read_text(encoding="utf-8")
    assert "Accepted: yes" in telegram_summary(result)


def test_record_mastery_answer_updates_latest_section_when_stage_repeats(tmp_path):
    checklist = tmp_path / "HUMAN-UNDERSTANDING-CHECKLIST.md"
    checklist.write_text(
        """\
# Human Understanding Checklist

## 2026-06-01 18:00 EDT Report

- [ ] Problem: she can explain what changed and why the prior state was insufficient.

## 2026-06-01 20:00 EDT Report

- [ ] Problem: she can explain what changed and why the prior state was insufficient.
- [ ] Branches: she can name the branches and whether work came from Hermes or elsewhere.
""",
        encoding="utf-8",
    )

    result = record_mastery_answer(
        checklist_path=checklist,
        report_dir=tmp_path / "reports",
        answer=GOOD_ANSWER,
        answered_at=datetime(2026, 6, 1, 20, 30, tzinfo=timezone.utc),
        write_gbrain=False,
    )

    assert result.accepted is True
    updated = checklist.read_text(encoding="utf-8")
    old_section, latest_section = updated.split("## 2026-06-01 20:00 EDT Report")
    assert "- [ ] Problem:" in old_section
    assert "- [x] Problem:" in latest_section


def test_record_mastery_answer_rejects_without_changing_checklist(tmp_path):
    checklist = tmp_path / "HUMAN-UNDERSTANDING-CHECKLIST.md"
    checklist.write_text(CHECKLIST, encoding="utf-8")

    result = record_mastery_answer(
        checklist_path=checklist,
        report_dir=tmp_path / "reports",
        answer="Looks good.",
        answered_at=datetime(2026, 6, 1, 20, 30, tzinfo=timezone.utc),
        write_gbrain=False,
    )

    assert result.accepted is False
    assert result.next_stage is None
    assert checklist.read_text(encoding="utf-8") == CHECKLIST
    assert "Accepted: no" in result.markdown_path.read_text(encoding="utf-8")
    assert "Revise the current-stage answer" in telegram_summary(result)


def test_record_mastery_answer_force_accepts_with_warning(tmp_path):
    checklist = tmp_path / "HUMAN-UNDERSTANDING-CHECKLIST.md"
    checklist.write_text(CHECKLIST, encoding="utf-8")

    result = record_mastery_answer(
        checklist_path=checklist,
        report_dir=tmp_path / "reports",
        answer="Manual override.",
        answered_at=datetime(2026, 6, 1, 20, 30, tzinfo=timezone.utc),
        force=True,
        write_gbrain=False,
    )

    assert result.accepted is True
    assert "accepted by --force despite assessment warnings" in result.reasons
    assert "- [x] Problem:" in checklist.read_text(encoding="utf-8")
