"""Record answers to the daily development mastery checklist.

This module is intentionally deterministic. It lets a Telegram/script caller
submit the current-stage answer, rejects vague answers, marks exactly one
checklist item complete when the answer is concrete, and writes an audit
artifact for gbrain.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python 3.11+ always has zoneinfo.
    ZoneInfo = None  # type: ignore[assignment]

from hermes_cli.daily_dev_report import CHECKLIST_NAME, DEFAULT_REPORT_DIR
from hermes_cli.daily_mastery_check import DEFAULT_TIMEZONE, latest_checklist_section


DEFAULT_ANSWER_SLUG_PREFIX = "development-mastery-answer"
DEFAULT_CHECKLIST_SLUG = "development-human-understanding-checklist"


@dataclass(frozen=True)
class AnswerAssessment:
    accepted: bool
    reasons: list[str]
    evidence_categories: list[str]


@dataclass(frozen=True)
class MasteryAnswerResult:
    accepted: bool
    markdown_path: Path
    checklist_path: Path
    section_title: str | None
    stage: str | None
    next_stage: str | None
    reasons: list[str]
    evidence_categories: list[str]
    gbrain_slug: str | None
    gbrain_error: str | None


def assess_answer(answer: str) -> AnswerAssessment:
    normalized = " ".join(answer.strip().split())
    reasons: list[str] = []
    categories = _evidence_categories(normalized)
    if len(normalized) < 120:
        reasons.append("answer is too short to prove mastery")
    if "repo" not in categories:
        reasons.append("answer must name a concrete repo or repo path")
    if len(categories - {"repo"}) < 2:
        reasons.append(
            "answer must include at least two concrete evidence types besides repo"
        )
    if not re.search(r"\b(problem|prior|before|because|why|blocked|gap)\b", normalized, re.I):
        reasons.append("answer must explain why the prior state was insufficient")
    return AnswerAssessment(
        accepted=not reasons,
        reasons=reasons,
        evidence_categories=sorted(categories),
    )


def _evidence_categories(answer: str) -> set[str]:
    categories: set[str] = set()
    if re.search(r"\b[\w.-]+/[\w.-]+\b", answer) or re.search(
        r"/(?:srv/dev/repos|home/\w+/coding)/[\w.-]+", answer
    ):
        categories.add("repo")
    if re.search(r"\b[0-9a-f]{7,40}\b", answer, re.I):
        categories.add("commit")
    if re.search(r"\b(branch|origin/|fork/|rk/|pilot/|main)\b", answer, re.I):
        categories.add("branch")
    if re.search(r"(/|\.)[\w./-]+", answer) or re.search(
        r"\b(artifact|gbrain|pdf|checklist|\\.auto|report|receipt)\b", answer, re.I
    ):
        categories.add("artifact")
    if re.search(
        r"\b(test|tests|pytest|cargo|ruff|compile|verification|passed|check)\b",
        answer,
        re.I,
    ):
        categories.add("validation")
    if re.search(r"\b(blocker|risk|remaining|manual|gap|edge case|failed)\b", answer, re.I):
        categories.add("risk")
    return categories


def record_mastery_answer(
    *,
    checklist_path: Path,
    report_dir: Path,
    answer: str,
    answered_at: datetime,
    force: bool = False,
    write_gbrain: bool = True,
) -> MasteryAnswerResult:
    checklist_text = checklist_path.read_text(encoding="utf-8") if checklist_path.exists() else ""
    section = latest_checklist_section(checklist_text)
    stage = section.unchecked_items[0] if section and section.unchecked_items else None
    assessment = assess_answer(answer)
    accepted = bool(stage) and (assessment.accepted or force)
    reasons = list(assessment.reasons)
    if section is None:
        reasons.append("no checklist section found")
    elif stage is None:
        reasons.append("no unchecked mastery stage remains")
    if force and not assessment.accepted:
        reasons.append("accepted by --force despite assessment warnings")

    updated_checklist = checklist_text
    next_stage: str | None = None
    if accepted and stage:
        updated_checklist = _mark_current_stage_complete(
            checklist_text,
            section_title=section.title,
            stage=stage,
            answer=answer.strip(),
            answered_at=answered_at,
            evidence_categories=assessment.evidence_categories,
        )
        checklist_path.write_text(updated_checklist, encoding="utf-8")
        next_section = latest_checklist_section(updated_checklist)
        next_stage = next_section.unchecked_items[0] if next_section and next_section.unchecked_items else None

    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = answered_at.strftime("%Y%m%d-%H%M%S")
    markdown_path = report_dir / f"daily-mastery-answer-{stamp}.md"
    markdown = _render_answer_markdown(
        accepted=accepted,
        section_title=section.title if section else None,
        stage=stage,
        next_stage=next_stage,
        answer=answer,
        reasons=reasons,
        evidence_categories=assessment.evidence_categories,
        checklist_path=checklist_path,
        answered_at=answered_at,
    )
    markdown_path.write_text(markdown, encoding="utf-8")

    gbrain_slug = None
    gbrain_error = None
    if write_gbrain:
        slug = f"{DEFAULT_ANSWER_SLUG_PREFIX}-{answered_at:%Y-%m-%d-%H%M%S}"
        gbrain_error = _sync_gbrain(
            slug=slug,
            answer_markdown=markdown,
            checklist_markdown=updated_checklist,
            checklist_path=checklist_path,
        )
        if not gbrain_error:
            gbrain_slug = slug

    return MasteryAnswerResult(
        accepted=accepted,
        markdown_path=markdown_path,
        checklist_path=checklist_path,
        section_title=section.title if section else None,
        stage=stage,
        next_stage=next_stage,
        reasons=reasons,
        evidence_categories=assessment.evidence_categories,
        gbrain_slug=gbrain_slug,
        gbrain_error=gbrain_error,
    )


def _mark_current_stage_complete(
    checklist_text: str,
    *,
    section_title: str,
    stage: str,
    answer: str,
    answered_at: datetime,
    evidence_categories: list[str],
) -> str:
    target = f"- [ ] {stage}"
    replacement = "\n".join(
        [
            f"- [x] {stage}",
            f"  Answered: {answered_at.isoformat()}",
            f"  Evidence: {', '.join(evidence_categories) if evidence_categories else 'none'}",
            "  Answer:",
            *[f"    {line}" for line in textwrap.wrap(answer, width=92)],
        ]
    )
    header = f"## {section_title}"
    section_start = checklist_text.rfind(header)
    if section_start < 0:
        return checklist_text.replace(target, replacement, 1)
    section_text = checklist_text[section_start:]
    next_section = section_text.find("\n## ", len(header))
    if next_section < 0:
        prefix = checklist_text[:section_start]
        return prefix + section_text.replace(target, replacement, 1)
    prefix = checklist_text[:section_start]
    body = section_text[:next_section]
    suffix = section_text[next_section:]
    return prefix + body.replace(target, replacement, 1) + suffix


def _render_answer_markdown(
    *,
    accepted: bool,
    section_title: str | None,
    stage: str | None,
    next_stage: str | None,
    answer: str,
    reasons: list[str],
    evidence_categories: list[str],
    checklist_path: Path,
    answered_at: datetime,
) -> str:
    lines = [
        "# Daily Development Mastery Answer",
        "",
        f"Answered: {answered_at.isoformat()}",
        f"Accepted: {'yes' if accepted else 'no'}",
        f"Report section: {section_title or 'missing'}",
        f"Stage: {stage or 'none'}",
        f"Next stage: {next_stage or 'none'}",
        f"Checklist: {checklist_path}",
        f"Evidence categories: {', '.join(evidence_categories) if evidence_categories else 'none'}",
        "",
        "## Assessment",
        "",
    ]
    if reasons:
        lines.extend(f"- {reason}" for reason in reasons)
    else:
        lines.append("- Answer met the concrete-evidence threshold.")
    lines.extend(["", "## Answer", "", answer.strip() or "(empty)", ""])
    return "\n".join(lines)


def _sync_gbrain(
    *,
    slug: str,
    answer_markdown: str,
    checklist_markdown: str,
    checklist_path: Path,
) -> str | None:
    if not shutil.which("gbrain"):
        return "gbrain command not found"
    answer_put = subprocess.run(
        ["gbrain", "put", slug],
        input=answer_markdown,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if answer_put.returncode != 0:
        return answer_put.stderr.strip() or answer_put.stdout.strip() or "gbrain answer put failed"
    checklist_source = checklist_markdown or (
        checklist_path.read_text(encoding="utf-8") if checklist_path.exists() else ""
    )
    checklist_put = subprocess.run(
        ["gbrain", "put", DEFAULT_CHECKLIST_SLUG],
        input=checklist_source,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if checklist_put.returncode != 0:
        return checklist_put.stderr.strip() or checklist_put.stdout.strip() or "gbrain checklist put failed"
    return None


def telegram_summary(result: MasteryAnswerResult) -> str:
    lines = [
        "Daily development mastery answer recorded.",
        f"Accepted: {'yes' if result.accepted else 'no'}",
        f"Report section: {result.section_title or 'missing'}",
        f"Stage: {result.stage or 'none'}",
        f"Next stage: {result.next_stage or 'none'}",
        f"Answer artifact: {result.markdown_path}",
        f"Checklist: {result.checklist_path}",
    ]
    if result.evidence_categories:
        lines.append(f"Evidence: {', '.join(result.evidence_categories)}")
    if result.gbrain_slug:
        lines.append(f"gbrain: {result.gbrain_slug}")
    elif result.gbrain_error:
        lines.append(f"gbrain: not synced ({result.gbrain_error})")
    if result.reasons:
        lines.append("")
        lines.append("Assessment notes:")
        lines.extend(f"- {reason}" for reason in result.reasons)
    if not result.accepted:
        lines.extend(
            [
                "",
                "Revise the current-stage answer with concrete repos, branches, commits, artifacts, tests, or blockers.",
            ]
        )
    return "\n".join(lines)


def _read_answer(args: argparse.Namespace) -> str:
    if args.answer:
        return args.answer
    if args.answer_file:
        if args.answer_file == "-":
            return sys.stdin.read()
        return Path(args.answer_file).expanduser().read_text(encoding="utf-8")
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit("mastery answer required: pass --answer, --answer-file, or stdin")


def _timezone(name: str) -> timezone:
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)  # type: ignore[return-value]
        except Exception:
            pass
    return timezone.utc


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record a daily development mastery answer")
    parser.add_argument("--answer", help="Answer text. If omitted, use --answer-file or stdin.")
    parser.add_argument("--answer-file", help="Path to answer text, or '-' for stdin.")
    parser.add_argument("--checklist", default=str(DEFAULT_REPORT_DIR / CHECKLIST_NAME))
    parser.add_argument("--out-dir", default=os.getenv("HERMES_DAILY_REPORT_DIR", str(DEFAULT_REPORT_DIR)))
    parser.add_argument("--timezone", default=os.getenv("HERMES_DAILY_REPORT_TIMEZONE", DEFAULT_TIMEZONE))
    parser.add_argument("--force", action="store_true", help="Mark the stage complete despite assessment warnings.")
    parser.add_argument("--no-gbrain", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    local_tz = _timezone(args.timezone)
    result = record_mastery_answer(
        checklist_path=Path(args.checklist).expanduser(),
        report_dir=Path(args.out_dir).expanduser(),
        answer=_read_answer(args),
        answered_at=datetime.now(timezone.utc).astimezone(local_tz),
        force=args.force,
        write_gbrain=not args.no_gbrain,
    )
    print(telegram_summary(result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
