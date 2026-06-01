"""Daily mastery prompt for development teaching reports.

This module turns the persistent human-understanding checklist into an active
Telegram prompt. It is deterministic: the latest checklist section is the
source of truth, and Hermes only asks the human to explain it.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python 3.11+ always has zoneinfo.
    ZoneInfo = None  # type: ignore[assignment]

from hermes_cli.daily_dev_report import CHECKLIST_NAME, DEFAULT_REPORT_DIR


DEFAULT_TIMEZONE = "America/New_York"
DEFAULT_GBRAIN_SLUG = "development-mastery-check-latest"


@dataclass(frozen=True)
class ChecklistSection:
    title: str
    fields: dict[str, str]
    unchecked_items: list[str]
    raw: str


@dataclass(frozen=True)
class MasteryCheckResult:
    markdown_path: Path
    checklist_path: Path
    section_title: str | None
    question_count: int
    gbrain_slug: str | None
    gbrain_error: str | None


def latest_checklist_section(text: str) -> ChecklistSection | None:
    sections: list[tuple[str, list[str]]] = []
    current_title: str | None = None
    current_lines: list[str] = []
    for raw in text.splitlines():
        if raw.startswith("## "):
            if current_title is not None:
                sections.append((current_title, current_lines))
            current_title = raw.removeprefix("## ").strip()
            current_lines = []
        elif current_title is not None:
            current_lines.append(raw)
    if current_title is not None:
        sections.append((current_title, current_lines))
    if not sections:
        return None
    title, lines = sections[-1]
    fields: dict[str, str] = {}
    unchecked_items: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("- [ ] "):
            unchecked_items.append(line.removeprefix("- [ ] ").strip())
            continue
        if ":" in line and not line.startswith("- "):
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    return ChecklistSection(
        title=title,
        fields=fields,
        unchecked_items=unchecked_items,
        raw="\n".join(lines).strip(),
    )


def build_mastery_markdown(
    section: ChecklistSection | None,
    *,
    checklist_path: Path,
    generated_at: datetime,
) -> str:
    lines = [
        "# Daily Development Mastery Check",
        "",
        f"Generated: {generated_at.isoformat()}",
        f"Checklist: {checklist_path}",
        "",
    ]
    if section is None:
        lines.extend(
            [
                "No checklist section was found.",
                "",
                "Next action: run the daily development teaching report first.",
                "",
            ]
        )
        return "\n".join(lines)

    lines.extend(
        [
            f"Report section: {section.title}",
            f"Markdown: {section.fields.get('Markdown', 'missing')}",
            f"PDF: {section.fields.get('PDF', 'missing')}",
            f"Repos: {section.fields.get('Repos', 'unknown')}",
            f"Commits: {section.fields.get('Commits', 'unknown')}",
            f"Orchestrator phases: {section.fields.get('Orchestrator phases', 'unknown')}",
            "",
            "## Mastery Questions",
            "",
            "Answer these before treating the development session as understood.",
            "",
        ]
    )
    for index, item in enumerate(section.unchecked_items, start=1):
        lines.append(f"{index}. {item}")
    if not section.unchecked_items:
        lines.append("No unchecked mastery items remain in the latest checklist section.")
    lines.extend(
        [
            "",
            "## Answer Standard",
            "",
            "- Name concrete repos, branches, commits, artifacts, tests, or blockers.",
            "- Explain the problem before the solution.",
            "- Distinguish landed commits from in-flight orchestrator work.",
            "- Leave checklist items unchecked when an answer is vague.",
            "",
            "## Next Action",
            "",
            "Reply in Telegram with answers in the same order, or open the PDF and checklist before continuing the next broad orchestration run.",
            "",
        ]
    )
    return "\n".join(lines)


def write_gbrain_page(slug: str, markdown: str) -> str | None:
    if not shutil.which("gbrain"):
        return "gbrain command not found"
    result = subprocess.run(
        ["gbrain", "put", slug],
        input=markdown,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        return result.stderr.strip() or result.stdout.strip() or "gbrain put failed"
    return None


def generate_mastery_check(
    *,
    checklist_path: Path,
    report_dir: Path,
    generated_at: datetime,
    write_gbrain: bool = True,
    gbrain_slug: str = DEFAULT_GBRAIN_SLUG,
) -> MasteryCheckResult:
    checklist_text = checklist_path.read_text(encoding="utf-8") if checklist_path.exists() else ""
    section = latest_checklist_section(checklist_text)
    markdown = build_mastery_markdown(section, checklist_path=checklist_path, generated_at=generated_at)
    report_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = report_dir / f"daily-mastery-check-{generated_at.strftime('%Y%m%d-%H%M%S')}.md"
    markdown_path.write_text(markdown, encoding="utf-8")
    gbrain_error = write_gbrain_page(gbrain_slug, markdown) if write_gbrain else None
    return MasteryCheckResult(
        markdown_path=markdown_path,
        checklist_path=checklist_path,
        section_title=section.title if section else None,
        question_count=len(section.unchecked_items) if section else 0,
        gbrain_slug=None if gbrain_error or not write_gbrain else gbrain_slug,
        gbrain_error=gbrain_error,
    )


def telegram_summary(result: MasteryCheckResult) -> str:
    lines = [
        "Daily development mastery check ready.",
        f"Report section: {result.section_title or 'missing'}",
        f"Questions: {result.question_count}",
        f"Prompt: {result.markdown_path}",
        f"Checklist: {result.checklist_path}",
    ]
    if result.gbrain_slug:
        lines.append(f"gbrain: {result.gbrain_slug}")
    elif result.gbrain_error:
        lines.append(f"gbrain: not synced ({result.gbrain_error})")
    lines.extend(
        [
            "",
            "Answer the prompt before starting the next broad orchestration run.",
            "Use concrete repos, branches, commits, artifacts, tests, and blockers.",
        ]
    )
    return "\n".join(lines)


def _timezone(name: str) -> timezone:
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)  # type: ignore[return-value]
        except Exception:
            pass
    return timezone.utc


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a daily development mastery check")
    parser.add_argument("--checklist", default=str(DEFAULT_REPORT_DIR / CHECKLIST_NAME))
    parser.add_argument("--out-dir", default=os.getenv("HERMES_DAILY_REPORT_DIR", str(DEFAULT_REPORT_DIR)))
    parser.add_argument("--timezone", default=os.getenv("HERMES_DAILY_REPORT_TIMEZONE", DEFAULT_TIMEZONE))
    parser.add_argument("--gbrain-slug", default=os.getenv("HERMES_MASTERY_CHECK_GBRAIN_SLUG", DEFAULT_GBRAIN_SLUG))
    parser.add_argument("--no-gbrain", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    local_tz = _timezone(args.timezone)
    generated_at = datetime.now(timezone.utc).astimezone(local_tz)
    result = generate_mastery_check(
        checklist_path=Path(args.checklist).expanduser(),
        report_dir=Path(args.out_dir).expanduser(),
        generated_at=generated_at,
        write_gbrain=not args.no_gbrain,
        gbrain_slug=args.gbrain_slug,
    )
    print(telegram_summary(result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
