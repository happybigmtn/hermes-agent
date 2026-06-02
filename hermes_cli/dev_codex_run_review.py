"""Dispatch a Codex-only review pass for a supervised dev run.

Hermes owns orchestration. Codex owns implementation and code review. This
module prepares the review packet, invokes ``codex exec review``, and stores
the resulting artifacts without Hermes inventing a merge-readiness verdict.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from hermes_cli.dev_manager_events import (
    DEFAULT_EVENT_LOG,
    ManagerEvent,
    record_event,
)
from hermes_cli.dev_run_status import DEFAULT_REPORT_DIR, DEFAULT_TIMEZONE
from hermes_cli.dev_supervision_closeout import (
    DEFAULT_CAPTURE_LINES,
    CloseoutResult,
    CommandResult,
    _timezone,
    collect_closeout,
)


DEFAULT_REQUESTED_BY = "hermes"
DEFAULT_DELIVERY_CHANNEL = "telegram"


@dataclass(frozen=True)
class CodexReviewResult:
    repo: Path
    session: str
    generated_at: datetime
    run_dir: Path
    prompt_path: Path
    output_path: Path
    closeout: CloseoutResult
    event: ManagerEvent
    command_result: CommandResult

    @property
    def ok(self) -> bool:
        return self.command_result.returncode == 0


def _slugify(value: str) -> str:
    chars: list[str] = []
    for char in value.lower():
        if char.isalnum():
            chars.append(char)
        elif chars and chars[-1] != "-":
            chars.append("-")
    return "".join(chars).strip("-") or "review"


def _one_line(value: object, *, max_chars: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _run_review_command(
    args: Sequence[str],
    *,
    cwd: Path,
    prompt: str | None,
    timeout: int,
) -> CommandResult:
    if not shutil.which(args[0]):
        return CommandResult(tuple(args), 127, "", f"{args[0]} binary not found")
    try:
        result = subprocess.run(
            list(args),
            cwd=str(cwd),
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return CommandResult(
            tuple(args), result.returncode, result.stdout.strip(), result.stderr.strip()
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(tuple(args), 127, "", str(exc))


def _review_target_args(
    *, base: str | None, commit: str | None, title: str | None
) -> list[str]:
    if commit:
        args = ["--commit", commit]
        if title:
            args.extend(["--title", title])
        return args
    if base:
        return ["--base", base]
    return ["--uncommitted"]


def build_review_prompt(
    *,
    closeout: CloseoutResult,
    base: str | None,
    commit: str | None,
    extra_instructions: str | None = None,
) -> str:
    receipt_paths = [
        f"- {receipt.path} status={receipt.status or 'unknown'} modified={receipt.modified_at.isoformat()}"
        for receipt in closeout.evidence_grade.receipts
    ]
    target = (
        f"commit {commit}"
        if commit
        else f"base {base}"
        if base
        else "uncommitted changes"
    )
    lines = [
        "You are Codex acting only as an independent code reviewer.",
        "",
        "Hermes is the orchestrator. Do not modify files. Do not produce new implementation code.",
        "Review the supervised development run and return a concise operator verdict Hermes can relay.",
        "",
        "Output exactly this shape:",
        "",
        "Verdict: READY_TO_MERGE | FIX_FIRST | BLOCKED",
        "Confidence: high | medium | low",
        "Operator look: <one file path or 'none'>",
        "Why:",
        "- <1-3 bullets about the highest-signal review findings>",
        "Required next action: <one sentence>",
        "",
        "Review context:",
        f"- Repo: {closeout.repo}",
        f"- Session: {closeout.session}",
        f"- Target: {target}",
        f"- Closeout artifact: {closeout.markdown_path}",
        f"- Receipt grade: {closeout.evidence_grade.grade} ({closeout.evidence_grade.reason})",
        f"- Branch: {closeout.repo_snapshot.branch}",
        f"- Repo clean: {closeout.repo_snapshot.clean}",
        "",
        "Machine receipts:",
    ]
    lines.extend(receipt_paths or ["- none"])
    lines.extend([
        "",
        "Review rules:",
        "- Base the verdict on the diff/commit, receipt grade, tests/checks, and closeout artifact.",
        "- Treat weak, stale, or failed receipt evidence as not ready unless there is no implementation delta.",
        "- Flag missing tests, unsafe behavior, role-boundary violations, and uncommitted changes.",
        "- Keep the answer short enough to read on Telegram.",
    ])
    if extra_instructions:
        lines.extend(["", "Extra instructions:", extra_instructions.strip()])
    return "\n".join(lines).rstrip() + "\n"


def write_review_output(path: Path, *, result: CommandResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Codex Run Review",
        "",
        f"Command: `{' '.join(result.args)}`",
        f"Return code: `{result.returncode}`",
        "",
        "## Stdout",
        "",
    ]
    if result.stdout:
        lines.extend(["```text", result.stdout, "```"])
    else:
        lines.append("- empty")
    lines.extend(["", "## Stderr", ""])
    if result.stderr:
        lines.extend(["```text", result.stderr, "```"])
    else:
        lines.append("- empty")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def run_codex_review(
    *,
    repo: Path,
    session: str,
    generated_at: datetime,
    out_dir: Path,
    event_log: Path = DEFAULT_EVENT_LOG,
    base: str | None = None,
    commit: str | None = None,
    title: str | None = None,
    extra_instructions: str | None = None,
    capture_lines: int = DEFAULT_CAPTURE_LINES,
    timeout: int = 900,
    requested_by: str | None = DEFAULT_REQUESTED_BY,
    delivery_channel: str | None = DEFAULT_DELIVERY_CHANNEL,
) -> CodexReviewResult:
    repo = repo.expanduser().resolve()
    stamp = generated_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = (
        out_dir.expanduser()
        / f"codex-run-review-{_slugify(repo.name)}-{_slugify(session)}-{stamp}"
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    closeout = collect_closeout(
        repo=repo,
        session=session,
        report_dir=run_dir,
        generated_at=generated_at,
        capture_lines=capture_lines,
        write_gbrain=False,
        include_steer_history=False,
    )
    prompt = build_review_prompt(
        closeout=closeout,
        base=base,
        commit=commit,
        extra_instructions=extra_instructions,
    )
    prompt_path = run_dir / "codex-review-prompt.md"
    output_path = run_dir / "codex-review-output.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    args = [
        "codex",
        "exec",
        "review",
        *_review_target_args(base=base, commit=commit, title=title),
        "-",
    ]
    command_result = _run_review_command(
        args,
        cwd=repo,
        prompt=prompt,
        timeout=timeout,
    )
    write_review_output(output_path, result=command_result)
    event = record_event(
        repo=repo,
        worker_session=session,
        intent=f"Run Codex review for supervised development run `{session}`",
        event_log=event_log,
        event_type="codex-review",
        requested_by=requested_by,
        delivery_channel=delivery_channel,
        resulting_artifacts=[
            str(prompt_path),
            str(output_path),
            str(closeout.markdown_path),
        ],
        notes=f"codex review returncode={command_result.returncode}",
        created_at=generated_at,
    )
    return CodexReviewResult(
        repo=repo,
        session=session,
        generated_at=generated_at,
        run_dir=run_dir,
        prompt_path=prompt_path,
        output_path=output_path,
        closeout=closeout,
        event=event,
        command_result=command_result,
    )


def _extract_prefixed_line(text: str, prefix: str) -> str | None:
    for line in text.splitlines():
        if line.strip().lower().startswith(prefix.lower()):
            return _one_line(line)
    return None


def _first_review_line(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return _one_line(stripped)
    return None


def telegram_summary(result: CodexReviewResult) -> str:
    verdict = _extract_prefixed_line(result.command_result.stdout, "Verdict:")
    confidence = _extract_prefixed_line(result.command_result.stdout, "Confidence:")
    review_line = None if verdict else _first_review_line(result.command_result.stdout)
    lines = [
        "Codex review ready." if result.ok else "Codex review attention.",
        f"repo: {result.repo}",
        f"session: {result.session}",
    ]
    if verdict:
        lines.append(verdict)
    if confidence:
        lines.append(confidence)
    if review_line:
        lines.append(f"review: {review_line}")
    lines.extend([
        f"receipt grade: {result.closeout.evidence_grade.grade}",
        f"output: {result.output_path}",
        f"prompt: {result.prompt_path}",
        f"closeout: {result.closeout.markdown_path}",
        f"event: {result.event.id}",
    ])
    if not result.ok:
        lines.append(
            f"attention: codex review command returned {result.command_result.returncode}"
        )
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a Codex-only review pass for a supervised dev run"
    )
    parser.add_argument("--repo", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument(
        "--out-dir",
        default=os.getenv("HERMES_CODEX_RUN_REVIEW_DIR", str(DEFAULT_REPORT_DIR)),
    )
    parser.add_argument(
        "--event-log",
        default=os.getenv("HERMES_DEV_MANAGER_EVENT_LOG", str(DEFAULT_EVENT_LOG)),
    )
    parser.add_argument("--base")
    parser.add_argument("--commit")
    parser.add_argument("--title")
    parser.add_argument("--instructions")
    parser.add_argument("--instructions-file")
    parser.add_argument(
        "--capture-lines",
        type=int,
        default=int(
            os.getenv("HERMES_CODEX_RUN_REVIEW_CAPTURE_LINES", DEFAULT_CAPTURE_LINES)
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.getenv("HERMES_CODEX_RUN_REVIEW_TIMEOUT", "900")),
    )
    parser.add_argument(
        "--timezone",
        default=os.getenv("HERMES_CODEX_RUN_REVIEW_TIMEZONE", DEFAULT_TIMEZONE),
    )
    parser.add_argument(
        "--requested-by",
        default=os.getenv("HERMES_DEV_MANAGER_REQUESTED_BY", DEFAULT_REQUESTED_BY),
    )
    parser.add_argument(
        "--delivery-channel",
        default=os.getenv(
            "HERMES_DEV_MANAGER_DELIVERY_CHANNEL", DEFAULT_DELIVERY_CHANNEL
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    instructions = args.instructions
    if args.instructions_file:
        instructions = (
            Path(args.instructions_file).expanduser().read_text(encoding="utf-8")
        )
    now = datetime.now(timezone.utc).astimezone(_timezone(args.timezone))
    result = run_codex_review(
        repo=Path(args.repo),
        session=args.session,
        generated_at=now,
        out_dir=Path(args.out_dir),
        event_log=Path(args.event_log).expanduser(),
        base=args.base,
        commit=args.commit,
        title=args.title,
        extra_instructions=instructions,
        capture_lines=args.capture_lines,
        timeout=args.timeout,
        requested_by=args.requested_by,
        delivery_channel=args.delivery_channel,
    )
    print(telegram_summary(result))
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
