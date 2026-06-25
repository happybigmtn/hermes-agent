OpenAI Codex v0.136.0
--------
workdir: /srv/dev/repos/hermes-agent
model: gpt-5.5
provider: openai
approval: never
sandbox: danger-full-access
reasoning effort: xhigh
reasoning summaries: none
session id: 019e8961-0304-7651-9a42-b463b17b76ba
--------
user
Hermes velocity steward: the last four-hour grade for this repo is C (activity exists, but no landed commit closed the loop in this window). Pick the highest-priority next development step already implied by the repo docs, Kanban board, gbrain/project page, or current dirty state. Implement one narrow slice, run focused validation, and commit only verified repo changes. Do not merely list issues or write a status report. The required deliverable is either a verified commit/PR-ready patch or a blocker artifact that proves credentials, external access, or a product decision is required. If the repo is dirty, first inspect git status and recent commits. Treat existing uncommitted changes as user or other-worker work. Do not revert, overwrite, clean, or stash them unless the change is clearly yours from this run. Either adopt the dirty state only when it is clearly the highest-priority active work and continue it to a verified commit, or write the blocker artifact explaining why it cannot be safely owned. Do not ask the human unless the blocker is credentials, missing external access, or a product decision. Attention context at 2026-06-02T13:28:15.872548-04:00: Uncommitted work remains: M hermes_cli/dev_manager_next_action.py; M tests/cli/test_dev_manager_next_action.py; ?? .gbrain-source; ?? hermes_cli/dev_progress_digest.py; plus 2 more.
codex
I’ll inspect the dirty state and recent commits first, then decide whether the existing work is safe to own or whether it needs a blocker artifact. After that I’ll make one narrow verified change and commit only the files that belong to this run.
exec
/bin/bash -lc 'git log --oneline -8' in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
9c199f1c0 test: isolate dev manager event state
9ea6b228a feat: steer Codex workers after blocked reviews
5d1863c3c fix: report Codex review attention without cron failure
5497d5ae1 feat: dispatch Codex reviews asynchronously
0a924620d feat: structure Codex review prompts
39eb8fc90 feat: review unreviewed Codex commits automatically
6f1534776 feat: auto-run Codex review for ready workers
4e1b48713 feat: add Codex run review gate

exec
/bin/bash -lc 'git diff --stat' in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
 hermes_cli/dev_manager_next_action.py     |  97 +++++++++++++++++---
 tests/cli/test_dev_manager_next_action.py | 146 +++++++++++++++++++++++++++++-
 2 files changed, 229 insertions(+), 14 deletions(-)

exec
/bin/bash -lc 'git status --short' in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
 M hermes_cli/dev_manager_next_action.py
 M tests/cli/test_dev_manager_next_action.py
?? .auto/
?? .gbrain-source
?? hermes_cli/dev_progress_digest.py
?? scripts/hermes-dev-progress-digest.py
?? tests/cli/test_dev_progress_digest.py

codex
The tracked dirty files are an in-progress `dev_manager_next_action` change with tests; untracked files include a progress digest module/script/test plus local metadata under `.auto/` and `.gbrain-source`. I’m reading those next to identify whether this is a coherent active slice or mixed work.
exec
/bin/bash -lc 'git diff -- tests/cli/test_dev_manager_next_action.py' in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
diff --git a/tests/cli/test_dev_manager_next_action.py b/tests/cli/test_dev_manager_next_action.py
index 587003966..cdbb1e809 100644
--- a/tests/cli/test_dev_manager_next_action.py
+++ b/tests/cli/test_dev_manager_next_action.py
@@ -10,6 +10,8 @@ from hermes_cli import kanban_db as kb
 from hermes_cli.dev_manager_next_action import (
     ManagerPacket,
     NextAction,
+    SafeExecutionResult,
+    _should_print_execution_result,
     collect_board_snapshots,
     collect_manager_packet,
     execute_safe_action,
@@ -34,6 +36,14 @@ def isolated_kanban_home(tmp_path, monkeypatch):
     return home
 
 
+@pytest.fixture(autouse=True)
+def clean_repo_status_by_default(monkeypatch):
+    monkeypatch.setattr(
+        "hermes_cli.dev_manager_next_action._repo_has_uncommitted_changes",
+        lambda repo: False,
+    )
+
+
 def _preflight(now: datetime, *, failing: bool = False) -> PreflightResult:
     profile = ProfilePreflight(
         name="orchestrator",
@@ -202,6 +212,48 @@ def test_next_action_runs_codex_review_for_unreviewed_worker_commit(isolated_kan
     assert "--title 'demo commit'" in packet.next_action.command
 
 
+def test_next_action_supervises_dirty_codex_worker_before_review(isolated_kanban_home, monkeypatch):
+    now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
+    repo = Path("/srv/dev/repos/ludeme")
+    worker = WorkerSession(
+        session_name="ludeme-codex",
+        window_index="0",
+        pane_index="0",
+        pane_pid=123,
+        current_command="node",
+        current_path=repo,
+        active=True,
+        dead=False,
+        title="codex",
+    )
+    monkeypatch.setattr("hermes_cli.dev_manager_next_action._repo_has_uncommitted_changes", lambda repo: True)
+    monkeypatch.setattr(
+        "hermes_cli.dev_manager_next_action._latest_commit",
+        lambda repo: (
+            "abcdef1234567890",
+            datetime(2026, 6, 2, 1, 44, tzinfo=timezone.utc),
+            "demo commit",
+        ),
+    )
+    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: [])
+    monkeypatch.setattr("hermes_cli.dev_manager_next_action.capture_worker_pane", lambda target: [])
+
+    packet = collect_manager_packet(
+        now=now,
+        repo_roots=[],
+        stale_after=timedelta(minutes=30),
+        recent_after=now - timedelta(hours=12),
+        preflight=_preflight(now),
+        runs=[],
+        workers=[worker],
+    )
+
+    assert packet.next_action.kind == "supervise-interactive-worker"
+    assert packet.next_action.worker == "ludeme-codex:0.0"
+    assert "uncommitted repo changes" in packet.next_action.reason
+    assert "status --short --branch" in packet.next_action.command
+
+
 def test_next_action_does_not_duplicate_reviewed_worker_commit(isolated_kanban_home, monkeypatch):
     now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
     repo = Path("/srv/dev/repos/ludeme")
@@ -249,7 +301,7 @@ def test_next_action_does_not_duplicate_reviewed_worker_commit(isolated_kanban_h
     assert packet.next_action.kind == "supervise-interactive-worker"
 
 
-def test_next_action_retries_failed_codex_review_event(isolated_kanban_home, monkeypatch):
+def test_next_action_reports_failed_codex_review_event_before_repair(isolated_kanban_home, monkeypatch):
     now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
     repo = Path("/srv/dev/repos/ludeme")
     worker = WorkerSession(
@@ -293,8 +345,8 @@ def test_next_action_retries_failed_codex_review_event(isolated_kanban_home, mon
         workers=[worker],
     )
 
-    assert packet.next_action.kind == "codex-review-worker"
-    assert packet.next_action.review_commit == "abcdef1234567890"
+    assert packet.next_action.kind == "report-codex-review-result"
+    assert packet.next_action.review_event_id == "review"
 
 
 def test_next_action_waits_for_pending_codex_review(isolated_kanban_home, monkeypatch):
@@ -572,6 +624,75 @@ def test_next_action_steers_worker_after_reported_blocked_codex_review(isolated_
     assert packet.next_action.human_action().startswith("None by default")
 
 
+def test_next_action_repairs_failed_review_without_repeating_same_commit(isolated_kanban_home, monkeypatch, tmp_path):
+    now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
+    repo = Path("/srv/dev/repos/ludeme")
+    output = tmp_path / "codex-review-output.md"
+    output.write_text(
+        "# Codex Run Review\n\n"
+        "## Stdout\n\n"
+        "- empty\n\n"
+        "## Stderr\n\n"
+        "```text\n"
+        "Output exactly this shape:\n"
+        "Verdict: READY_TO_MERGE | FIX_FIRST | BLOCKED\n"
+        "bwrap: loopback: Failed RTM_NEWADDR\n"
+        "```\n",
+        encoding="utf-8",
+    )
+    worker = WorkerSession(
+        session_name="ludeme-codex",
+        window_index="0",
+        pane_index="0",
+        pane_pid=123,
+        current_command="node",
+        current_path=repo,
+        active=True,
+        dead=False,
+        title="codex",
+    )
+    events = [
+        ManagerEvent(
+            id="review",
+            created_at=datetime(2026, 6, 2, 1, 44, tzinfo=timezone.utc),
+            event_type="codex-review",
+            repo=str(repo),
+            worker_session="ludeme-codex",
+            intent="review",
+            resulting_artifacts=[str(output)],
+            notes="codex review returncode=1",
+        ),
+        ManagerEvent(
+            id="report",
+            created_at=datetime(2026, 6, 2, 1, 44, 30, tzinfo=timezone.utc),
+            event_type="codex-review-report",
+            repo=str(repo),
+            worker_session="ludeme-codex",
+            intent="report",
+            notes="reported_event=review returncode=1",
+        ),
+    ]
+    monkeypatch.setattr(
+        "hermes_cli.dev_manager_next_action._latest_commit",
+        lambda repo: ("abcdef1234567890", datetime(2026, 6, 2, 1, 43, tzinfo=timezone.utc), "demo"),
+    )
+    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: events)
+    monkeypatch.setattr("hermes_cli.dev_manager_next_action.capture_worker_pane", lambda target: [])
+
+    packet = collect_manager_packet(
+        now=now,
+        repo_roots=[],
+        stale_after=timedelta(minutes=30),
+        recent_after=now - timedelta(hours=12),
+        preflight=_preflight(now),
+        runs=[],
+        workers=[worker],
+    )
+
+    assert packet.next_action.kind == "repair-codex-review-blocker"
+    assert packet.next_action.review_event_id == "review"
+
+
 def test_next_action_does_not_repeat_codex_review_repair_dispatch(isolated_kanban_home, monkeypatch, tmp_path):
     now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
     repo = Path("/srv/dev/repos/ludeme")
@@ -939,6 +1060,25 @@ def test_execute_safe_action_reports_completed_codex_review(monkeypatch, tmp_pat
     assert "reported_event=review" in str(events[0].notes)
 
 
+def test_quiet_routine_suppresses_review_report_telegram_output():
+    routine = SafeExecutionResult(
+        kind="report-codex-review-result",
+        ok=True,
+        summary="Codex review ready.\nVerdict: BLOCKED",
+        notify=True,
+    )
+    failure = SafeExecutionResult(
+        kind="report-codex-review-result",
+        ok=False,
+        summary="Codex review report failed.",
+        notify=True,
+    )
+
+    assert _should_print_execution_result(routine, quiet_routine=True) is False
+    assert _should_print_execution_result(routine, quiet_routine=False) is True
+    assert _should_print_execution_result(failure, quiet_routine=True) is True
+
+
 def test_execute_safe_action_reports_attention_without_cron_failure(monkeypatch, tmp_path):
     now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
     repo = tmp_path / "repo"

exec
/bin/bash -lc "rg --files .auto hermes_cli scripts tests/cli | rg 'dev_progress_digest|\\.gbrain-source|\\.auto'" in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
tests/cli/test_dev_progress_digest.py
.auto/orchestrator/velocity-steward-20260602T132815Z/codex-velocity-output.md
.auto/orchestrator/velocity-steward-20260602T132815Z/run-codex-velocity.sh
.auto/orchestrator/velocity-steward-20260602T132815Z/velocity-prompt.md
hermes_cli/dev_progress_digest.py

exec
/bin/bash -lc 'git diff -- hermes_cli/dev_manager_next_action.py' in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
diff --git a/hermes_cli/dev_manager_next_action.py b/hermes_cli/dev_manager_next_action.py
index a7bc88dd7..a20959c72 100644
--- a/hermes_cli/dev_manager_next_action.py
+++ b/hermes_cli/dev_manager_next_action.py
@@ -62,6 +62,7 @@ ROUTINE_TELEGRAM_KINDS = {
     "dispatch-task",
     "wait-for-active-run",
     "wait-for-codex-review",
+    "report-codex-review-result",
     "repair-codex-review-blocker",
     "execute-plan-slice",
     "create-campaign-plan",
@@ -360,10 +361,20 @@ def _pending_codex_review(worker: WorkerSession, *, now: datetime, since: dateti
 
 
 def _unreported_codex_review(worker: WorkerSession) -> ManagerEvent | None:
+    events = _codex_review_events(worker)
     latest_start = _latest_codex_review_start(worker)
     if latest_start is None:
+        latest_finish = _latest_event(
+            [event for event in events if event.event_type == "codex-review"],
+            {"codex-review"},
+        )
+        if (
+            latest_finish is not None
+            and _review_returncode(latest_finish) != 0
+            and not _codex_review_was_reported(events, latest_finish)
+        ):
+            return latest_finish
         return None
-    events = _codex_review_events(worker)
     finished = [
         event
         for event in events
@@ -390,8 +401,25 @@ def _review_output_text(event: ManagerEvent) -> str:
     return output_path.read_text(encoding="utf-8", errors="replace")
 
 
+def _review_stdout_text(event: ManagerEvent) -> str:
+    text = _review_output_text(event)
+    if "## Stdout" not in text:
+        return text
+    stdout = text.split("## Stdout", 1)[1]
+    stdout = stdout.split("## Stderr", 1)[0]
+    stdout = stdout.strip()
+    if stdout.startswith("```"):
+        lines = stdout.splitlines()
+        if lines and lines[0].startswith("```"):
+            lines = lines[1:]
+        if lines and lines[-1].startswith("```"):
+            lines = lines[:-1]
+        stdout = "\n".join(lines).strip()
+    return stdout
+
+
 def _review_verdict(event: ManagerEvent) -> str | None:
-    line = _extract_prefixed_line(_review_output_text(event), "Verdict:")
+    line = _extract_prefixed_line(_review_stdout_text(event), "Verdict:")
     if not line:
         return None
     return line.split(":", 1)[1].strip().split()[0].upper()
@@ -400,10 +428,12 @@ def _review_verdict(event: ManagerEvent) -> str | None:
 def _codex_review_requires_repair(event: ManagerEvent) -> bool:
     if event.event_type != "codex-review":
         return False
+    if _review_returncode(event) != 0:
+        return True
     verdict = _review_verdict(event)
     if verdict:
         return verdict != "READY_TO_MERGE"
-    return _review_returncode(event) != 0
+    return False
 
 
 def _codex_review_was_reported(events: Sequence[ManagerEvent], review: ManagerEvent) -> bool:
@@ -440,6 +470,13 @@ def _unrepaired_codex_review_blocker(worker: WorkerSession) -> ManagerEvent | No
 
 
 def _git_line(repo: Path, args: Sequence[str]) -> str | None:
+    lines = _git_lines(repo, args)
+    if not lines:
+        return None
+    return "\n".join(lines).strip() or None
+
+
+def _git_lines(repo: Path, args: Sequence[str]) -> list[str]:
     try:
         result = subprocess.run(
             ["git", "-C", str(repo), *args],
@@ -449,11 +486,15 @@ def _git_line(repo: Path, args: Sequence[str]) -> str | None:
             check=False,
         )
     except (OSError, subprocess.TimeoutExpired):
-        return None
+        return []
     if result.returncode != 0:
-        return None
+        return []
     text = result.stdout.strip()
-    return text or None
+    return text.splitlines() if text else []
+
+
+def _repo_has_uncommitted_changes(repo: Path) -> bool:
+    return bool(_git_lines(repo, ["status", "--porcelain"]))
 
 
 def _latest_commit(repo: Path) -> tuple[str, datetime, str] | None:
@@ -488,6 +529,13 @@ def _latest_codex_review(worker: WorkerSession) -> ManagerEvent | None:
     )
 
 
+def _latest_any_codex_review(worker: WorkerSession) -> ManagerEvent | None:
+    return _latest_event(
+        [event for event in _codex_review_events(worker) if event.event_type == "codex-review"],
+        {"codex-review"},
+    )
+
+
 def _latest_worker_dispatch(worker: WorkerSession) -> ManagerEvent | None:
     return _latest_event(_codex_review_events(worker), {"worker-start", "worker-steer", "worker-resume"})
 
@@ -502,7 +550,7 @@ def _unreviewed_worker_commit(worker: WorkerSession, *, now: datetime) -> tuple[
     latest_dispatch = _latest_worker_dispatch(worker)
     if latest_dispatch is not None and committed_at < latest_dispatch.created_at:
         return None
-    latest_review = _latest_codex_review(worker)
+    latest_review = _latest_any_codex_review(worker)
     if latest_review is not None and latest_review.created_at >= committed_at:
         return None
     if _pending_codex_review(worker, now=now, since=committed_at) is not None:
@@ -667,6 +715,22 @@ def recommend_next_action(
                 reason=f"{target} already has Codex review {pending_review.id} in progress",
                 command="venv/bin/python scripts/hermes-dev-manager-events.py list",
             )
+        if worker.kind() == "codex" and worker.current_path and _repo_has_uncommitted_changes(worker.current_path):
+            return NextAction(
+                kind="supervise-interactive-worker",
+                repo=repo,
+                worker=target,
+                reason=(
+                    f"{target} has uncommitted repo changes; supervise the live "
+                    "worker before reviewing a stale commit"
+                ),
+                command=f"git -C {shlex.quote(str(worker.current_path))} status --short --branch",
+                required_evidence=(
+                    f"Before claiming the dirty worker is handled, cite a fresh "
+                    f"closeout, commit, or status artifact for `{target}` after "
+                    f"`{now.isoformat()}`."
+                ),
+            )
         unreviewed_commit = _unreviewed_worker_commit(worker, now=now)
         if unreviewed_commit:
             commit, title = unreviewed_commit
@@ -1179,9 +1243,7 @@ def _first_review_line(text: str) -> str | None:
 
 def _review_event_summary(event: ManagerEvent) -> str:
     output_path = _review_output_path(event)
-    text = ""
-    if output_path and output_path.exists():
-        text = output_path.read_text(encoding="utf-8", errors="replace")
+    text = _review_stdout_text(event)
     verdict = _extract_prefixed_line(text, "Verdict:")
     confidence = _extract_prefixed_line(text, "Confidence:")
     review_line = None if verdict else _first_review_line(text)
@@ -1313,6 +1375,16 @@ def execute_safe_action(packet: ManagerPacket, *, report_dir: Path) -> SafeExecu
     return _dispatch_codex_review(packet, report_dir=report_dir)
 
 
+def _should_print_execution_result(
+    result: SafeExecutionResult,
+    *,
+    quiet_routine: bool,
+) -> bool:
+    if quiet_routine and result.ok and result.kind in ROUTINE_TELEGRAM_KINDS:
+        return False
+    return result.notify
+
+
 def packet_to_json(packet: ManagerPacket) -> str:
     data = {
         "generated_at": packet.generated_at.isoformat(),
@@ -1438,7 +1510,10 @@ def main(argv: list[str] | None = None) -> int:
                     sort_keys=True,
                 )
             )
-    elif execution_result and execution_result.notify:
+    elif execution_result and _should_print_execution_result(
+        execution_result,
+        quiet_routine=args.quiet_routine,
+    ):
         print(execution_result.summary)
     elif not execution_result:
         summary = telegram_summary(packet, quiet_routine=args.quiet_routine)

exec
/bin/bash -lc 'git status --ignored --short .auto .gbrain-source' in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
?? .auto/
?? .gbrain-source

codex
This looks like one coherent active area: dev manager routing around Codex review results and dirty worker supervision, plus an untracked progress-digest utility. I’m checking the untracked files now before choosing the narrow slice to finish.
exec
/bin/bash -lc "sed -n '1,220p' scripts/hermes-dev-progress-digest.py" in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
#!/usr/bin/env python3
"""Hermes cron wrapper for the four-hour dev progress digest.

Install into ~/.hermes/scripts/ and schedule with:

hermes cron create "every 4h" --name dev-progress-digest --script dev-progress-digest.py --no-agent --deliver telegram
"""

from pathlib import Path
import os
import sys

for candidate in (
    os.getenv("HERMES_AGENT_REPO"),
    "/srv/dev/repos/hermes-agent",
):
    if not candidate:
        continue
    root = Path(candidate)
    if (root / "hermes_cli").is_dir():
        sys.path.insert(0, str(root))
        break

from hermes_cli.dev_progress_digest import main


DEFAULT_ARGS = ["--lookback-hours", "4", "--steward"]


raise SystemExit(main(sys.argv[1:] or DEFAULT_ARGS))

exec
/bin/bash -lc "sed -n '1,240p' hermes_cli/dev_progress_digest.py" in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
"""Four-hour plain-English progress digest for the dev orchestrator.

This report is intentionally fact-based: local git commits, dirty state,
manager events, and orchestrator run artifacts. Telegram gets a short pointer;
the readable detail lives in Markdown plus a stable HTML page.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python 3.11+ always has zoneinfo.
    ZoneInfo = None  # type: ignore[assignment]

from hermes_cli import kanban_db as kb
from hermes_cli.dev_manager_events import DEFAULT_EVENT_LOG, ManagerEvent, load_events, record_event, render_event_line
from hermes_cli.dev_run_status import (
    DEFAULT_REPORT_DIR,
    DEFAULT_REPO_ROOTS,
    DEFAULT_STALE_MINUTES,
    RunSummary,
    WorkerSession,
    collect_status,
    default_dashboard_url,
    list_tmux_worker_sessions,
)
from hermes_cli.dev_worker_dispatch import dispatch_worker


DEFAULT_LOOKBACK_HOURS = 4
DEFAULT_TIMEZONE = "America/New_York"
DEFAULT_GBRAIN_SLUG = "dev-orchestrator-progress-digest"
DEFAULT_DASHBOARD_FILENAME = "dev-progress-digest-latest.html"
DEFAULT_MAX_REMEDIATIONS = 13


@dataclass(frozen=True)
class VelocityGrade:
    grade: str
    sufficient: bool
    score: int
    reason: str


@dataclass(frozen=True)
class RemedialAction:
    repo: str
    kind: str
    ok: bool
    summary: str
    command: str | None = None
    artifact: str | None = None


@dataclass(frozen=True)
class DigestCommit:
    repo: str
    sha: str
    authored_at: datetime
    author: str
    subject: str
    files: list[str] = field(default_factory=list)

    def short_sha(self) -> str:
        return self.sha[:7]

    def plain_subject(self) -> str:
        subject = re.sub(r"^[a-z]+(?:\([^)]*\))?:\s*", "", self.subject.strip(), flags=re.I)
        return subject[:1].upper() + subject[1:] if subject else "(no commit message)"

    def touches_tests(self) -> bool:
        return any(_is_validation_path(path) for path in self.files)


@dataclass
class RepoProgress:
    name: str
    path: Path
    branch: str | None
    commits: list[DigestCommit] = field(default_factory=list)
    dirty_entries: list[str] = field(default_factory=list)
    events: list[ManagerEvent] = field(default_factory=list)
    runs: list[RunSummary] = field(default_factory=list)
    velocity_grade: VelocityGrade | None = None
    remedial_actions: list[RemedialAction] = field(default_factory=list)

    def has_activity(self) -> bool:
        return bool(self.commits or self.dirty_entries or self.events or self.runs)

    def plain_summary(self) -> str:
        if self.commits:
            subjects = "; ".join(commit.plain_subject() for commit in self.commits[:3])
            more = f"; plus {len(self.commits) - 3} more" if len(self.commits) > 3 else ""
            return f"{_plural(len(self.commits), 'commit')} landed: {subjects}{more}."
        if self.dirty_entries:
            verb = "is" if len(self.dirty_entries) == 1 else "are"
            return f"No commit landed yet, but {_plural(len(self.dirty_entries), 'uncommitted change')} {verb} present."
        if self.runs:
            run = self.runs[0]
            return f"No commit landed yet; orchestrator activity is at {run.phase()}."
        if self.events:
            return f"No commit landed yet; Hermes recorded {_plural(len(self.events), 'manager event')}."
        return "No visible activity in this window."

    def validation_summary(self) -> str:
        validating = [commit for commit in self.commits if commit.touches_tests()]
        if validating:
            return f"Validation is visible in {_plural(len(validating), 'commit')} that touched tests or check scripts."
        if self.commits:
            return "No commit in this window visibly touched a test or check script; check receipts before trusting it."
        if self.runs:
            latest = self.runs[0].latest_artifact()
            if latest:
                return f"Latest run artifact to inspect: {latest.path}."
        return "No landed validation signal was found in this window."

    def attention_lines(self) -> list[str]:
        lines: list[str] = []
        if self.dirty_entries:
            shown = "; ".join(entry.strip() for entry in self.dirty_entries[:4])
            more = f"; plus {len(self.dirty_entries) - 4} more" if len(self.dirty_entries) > 4 else ""
            lines.append(f"Uncommitted work remains: {shown}{more}.")
        for run in self.runs:
            if run.attention:
                lines.append(run.attention_reason or "Run needs attention.")
        blocked_events = [
            event
            for event in self.events
            if "blocked" in f"{event.intent} {event.notes or ''}".lower()
        ]
        if blocked_events and not lines:
            lines.append(f"Hermes recorded {_plural(len(blocked_events), 'blocked manager event')}; see the event log before dispatching more work.")
        return lines or ["No immediate operator attention from the collected facts."]

    def grade(self) -> VelocityGrade:
        if self.velocity_grade is None:
            return grade_repo_velocity(self)
        return self.velocity_grade


@dataclass
class ProgressDigestResult:
    markdown_path: Path
    html_path: Path
    dashboard_url: str | None
    gbrain_slug: str | None
    gbrain_error: str | None
    generated_at: datetime
    since: datetime
    repos: list[RepoProgress]
    scanned_repo_count: int
    remedial_actions: list[RemedialAction] = field(default_factory=list)
    remediation_cap: int = 0
    remediation_dry_run: bool = False

    @property
    def active_repo_count(self) -> int:
        return len(self.repos)

    @property
    def commit_count(self) -> int:
        return sum(len(repo.commits) for repo in self.repos)

    @property
    def insufficient_repo_count(self) -> int:
        return sum(1 for repo in self.repos if not repo.grade().sufficient)

    @property
    def actual_remedial_action_count(self) -> int:
        return sum(
            1
            for action in self.remedial_actions
            if action.kind not in {"no-safe-surface", "already-remediated", "cap-reached"}
        )


def _plural(count: int, noun: str) -> str:
    suffix = "" if count == 1 else "s"
    return f"{count} {noun}{suffix}"


def grade_repo_velocity(repo: RepoProgress) -> VelocityGrade:
    validation_commits = [commit for commit in repo.commits if commit.touches_tests()]
    has_attention = any(repo.attention_lines()) and repo.attention_lines()[0] != "No immediate operator attention from the collected facts."
    has_fresh_run = any(run.active and not run.attention for run in repo.runs)
    has_manager_activity = bool(repo.events)
    blocked = any(
        "blocked" in f"{event.intent} {event.notes or ''}".lower()
        for event in repo.events
    ) or any(run.attention for run in repo.runs)

    if validation_commits and not blocked:
        grade = "A" if not repo.dirty_entries else "B"
        reason = "validated commits landed" if grade == "A" else "validated commits landed, but uncommitted work remains"
        return VelocityGrade(grade=grade, sufficient=True, score=4 if grade == "A" else 3, reason=reason)
    if repo.commits:
        return VelocityGrade(
            grade="B",
            sufficient=True,
            score=3,
            reason="commits landed, but no visible test/check surface was touched",
        )
    if has_fresh_run or has_manager_activity:
        return VelocityGrade(
            grade="C",
            sufficient=False,
            score=2,
            reason="activity exists, but no landed commit closed the loop in this window",
        )
    if repo.dirty_entries:
        return VelocityGrade(
            grade="D",
            sufficient=False,
            score=1,
            reason="dirty worktree exists with no landed commit or fresh run evidence",
        )
    if has_attention:
        return VelocityGrade(
            grade="D",
            sufficient=False,
            score=1,
            reason="attention or blocker state remains unresolved",
        )
    return VelocityGrade(
        grade="F",

exec
/bin/bash -lc 'git branch --show-current' in /srv/dev/repos/hermes-agent
exec
/bin/bash -lc "sed -n '1,260p' tests/cli/test_dev_progress_digest.py" in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
rk/daily-dev-teaching-report

 succeeded in 0ms:
from __future__ import annotations

from datetime import datetime, timezone
import os
import subprocess

from hermes_cli.dev_manager_events import ManagerEvent
from hermes_cli.dev_progress_digest import _remediation_message, generate_progress_digest, telegram_summary
from hermes_cli.dev_run_status import WorkerSession


def test_generate_progress_digest_writes_plain_english_markdown_and_html(tmp_path):
    repo_root = tmp_path / "repos"
    repo = repo_root / "demo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "r@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "R"], cwd=repo, check=True)
    (repo / "src.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_src.py").write_text(
        "def test_value():\n    assert True\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    env = {
        "GIT_AUTHOR_DATE": "2026-06-02T13:00:00+00:00",
        "GIT_COMMITTER_DATE": "2026-06-02T13:00:00+00:00",
    }
    subprocess.run(
        ["git", "commit", "-m", "feat(demo): demo behavior"],
        cwd=repo,
        check=True,
        capture_output=True,
        env={**os.environ, **env},
    )
    (repo / "src.py").write_text("def value():\n    return 2\n", encoding="utf-8")

    event = ManagerEvent(
        id="event1",
        created_at=datetime(2026, 6, 2, 13, 30, tzinfo=timezone.utc),
        event_type="worker-steer",
        repo=str(repo),
        worker_session="demo-codex",
        intent="Continue demo work",
        requested_by="hermes",
        delivery_channel="telegram",
    )

    result = generate_progress_digest(
        repo_roots=[repo_root],
        report_dir=tmp_path / "reports",
        now=datetime(2026, 6, 2, 14, 0, tzinfo=timezone.utc),
        since=datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc),
        write_gbrain=False,
        dashboard_url="http://example.test/dev-progress-digest-latest.html",
        events=[event],
        runs=[],
    )

    assert result.active_repo_count == 1
    assert result.commit_count == 1
    markdown = result.markdown_path.read_text(encoding="utf-8")
    html = result.html_path.read_text(encoding="utf-8")
    assert "Four-Hour Dev Progress Digest" in markdown
    assert "1 commit landed: Demo behavior." in markdown
    assert "Velocity grade: `B`" in markdown
    assert "Validation is visible in 1 commit" in markdown
    assert "Uncommitted work remains: M src.py." in markdown
    assert "Manager Events" in markdown
    assert "Dev Progress Digest" in html
    assert "Demo behavior" in html

    summary = telegram_summary(result)
    assert "4-hour dev velocity digest ready." in summary
    assert "Web (tailnet): http://example.test/dev-progress-digest-latest.html" in summary
    assert f"MEDIA:{result.markdown_path}" in summary
    assert "- demo: B - 1 commit landed: Demo behavior." in summary


def test_steward_steers_existing_worker_for_below_target_repo(tmp_path, monkeypatch):
    repo_root = tmp_path / "repos"
    repo = repo_root / "demo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "r@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "R"], cwd=repo, check=True)
    (repo / "src.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    env = {
        "GIT_AUTHOR_DATE": "2026-06-01T13:00:00+00:00",
        "GIT_COMMITTER_DATE": "2026-06-01T13:00:00+00:00",
    }
    subprocess.run(
        ["git", "commit", "-m", "feat(demo): old work"],
        cwd=repo,
        check=True,
        capture_output=True,
        env={**os.environ, **env},
    )
    (repo / "src.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    worker = WorkerSession(
        session_name="demo-codex",
        window_index="0",
        pane_index="0",
        pane_pid=123,
        current_command="node",
        current_path=repo,
        active=True,
        dead=False,
        title="codex",
    )
    captured = {}

    def fake_dispatch_worker(**kwargs):
        captured.update(kwargs)

        class FakeDispatchResult:
            failed_results = []
            sent_message = True
            artifact_path = tmp_path / "dispatch.md"

        return FakeDispatchResult()

    monkeypatch.setattr("hermes_cli.dev_progress_digest.dispatch_worker", fake_dispatch_worker)

    result = generate_progress_digest(
        repo_roots=[repo_root],
        report_dir=tmp_path / "reports",
        now=datetime(2026, 6, 2, 14, 0, tzinfo=timezone.utc),
        since=datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc),
        remediate=True,
        write_gbrain=False,
        events=[],
        runs=[],
        worker_sessions=[worker],
    )

    repo_result = result.repos[0]
    assert repo_result.grade().grade == "D"
    assert repo_result.grade().sufficient is False
    assert result.remedial_actions[0].kind == "worker-steer"
    assert result.remedial_actions[0].ok is True
    assert captured["session"] == "demo-codex:0.0"
    assert "Hermes velocity steward" in captured["message"]
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "Velocity grade: `D`" in markdown
    assert "Steered existing codex worker demo-codex:0.0" in markdown


def test_steward_dry_run_starts_codex_for_clean_idle_repo(tmp_path):
    repo_root = tmp_path / "repos"
    repo = repo_root / "demo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "r@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "R"], cwd=repo, check=True)
    (repo / "src.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    env = {
        "GIT_AUTHOR_DATE": "2026-06-01T13:00:00+00:00",
        "GIT_COMMITTER_DATE": "2026-06-01T13:00:00+00:00",
    }
    subprocess.run(
        ["git", "commit", "-m", "feat(demo): old work"],
        cwd=repo,
        check=True,
        capture_output=True,
        env={**os.environ, **env},
    )

    result = generate_progress_digest(
        repo_roots=[repo_root],
        report_dir=tmp_path / "reports",
        now=datetime(2026, 6, 2, 14, 0, tzinfo=timezone.utc),
        since=datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc),
        remediate=True,
        dry_run_remediation=True,
        write_gbrain=False,
        events=[],
        runs=[],
        worker_sessions=[],
    )

    repo_result = result.repos[0]
    assert repo_result.grade().grade == "F"
    assert result.remedial_actions[0].kind == "codex-start"
    assert result.remedial_actions[0].summary.startswith("Would start bounded Codex velocity worker")
    assert "Velocity grade: `F`" in result.markdown_path.read_text(encoding="utf-8")


def test_steward_dry_run_starts_codex_for_dirty_idle_repo_with_dirty_safety_prompt(tmp_path):
    repo_root = tmp_path / "repos"
    repo = repo_root / "demo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "r@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "R"], cwd=repo, check=True)
    (repo / "src.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    env = {
        "GIT_AUTHOR_DATE": "2026-06-01T13:00:00+00:00",
        "GIT_COMMITTER_DATE": "2026-06-01T13:00:00+00:00",
    }
    subprocess.run(
        ["git", "commit", "-m", "feat(demo): old work"],
        cwd=repo,
        check=True,
        capture_output=True,
        env={**os.environ, **env},
    )
    (repo / "src.py").write_text("def value():\n    return 2\n", encoding="utf-8")

    now = datetime(2026, 6, 2, 14, 0, tzinfo=timezone.utc)
    old_noop = ManagerEvent(
        id="old-noop",
        created_at=datetime(2026, 6, 2, 13, 30, tzinfo=timezone.utc),
        event_type="velocity-remediation",
        repo=str(repo),
        worker_session="velocity-steward",
        intent="Velocity remediation for `demo` grade D: no-safe-surface",
        requested_by="hermes-velocity-steward",
        delivery_channel="telegram",
        notes="No existing live worker or runnable same-name Kanban board task.",
    )
    result = generate_progress_digest(
        repo_roots=[repo_root],
        report_dir=tmp_path / "reports",
        now=now,
        since=datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc),
        remediate=True,
        dry_run_remediation=True,
        write_gbrain=False,
        events=[old_noop],
        runs=[],
        worker_sessions=[],
    )

    repo_result = result.repos[0]
    assert repo_result.grade().sufficient is False
    assert result.remedial_actions[0].kind == "codex-start"
    assert result.remedial_actions[0].summary.startswith("Would start bounded Codex velocity worker")
    prompt = _remediation_message(repo_result, repo_result.grade(), now)
    assert "Do not merely list issues" in prompt
    assert "Do not revert, overwrite, clean, or stash" in prompt
    assert "continue it to a verified commit" in prompt

exec
/bin/bash -lc 'ls -la .gbrain-source .auto/orchestrator/velocity-steward-20260602T132815Z' in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
-rw-r--r-- 1 dev dev   13 Jun  1 05:52 .gbrain-source

.auto/orchestrator/velocity-steward-20260602T132815Z:
total 48
drwxrwxr-x 2 dev dev  4096 Jun  2 17:28 .
drwxrwxr-x 3 dev dev  4096 Jun  2 17:28 ..
-rw-rw-r-- 1 dev dev 29414 Jun  2 17:28 codex-velocity-output.md
-rwxr-xr-x 1 dev dev   626 Jun  2 17:28 run-codex-velocity.sh
-rw-rw-r-- 1 dev dev  1357 Jun  2 17:28 velocity-prompt.md

codex
The untracked digest code is substantial, not just scratch output. I’m going to validate whether it already has a CLI entry point and tests wired well enough to finish as the narrow slice, while leaving `.auto/` and `.gbrain-source` alone unless the repo already expects them.
exec
/bin/bash -lc "sed -n '240,520p' hermes_cli/dev_progress_digest.py" in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
        grade="F",
        sufficient=False,
        score=0,
        reason="no visible development activity in this four-hour window",
    )


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _repo_roots(values: Iterable[str] | None = None) -> list[Path]:
    roots = [Path(value).expanduser() for value in values or [] if value]
    if not roots:
        roots = [Path(value).expanduser() for value in _parse_csv(os.getenv("HERMES_DEV_PROGRESS_REPO_ROOTS"))]
    return roots or DEFAULT_REPO_ROOTS


def _timezone(name: str) -> timezone:
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)  # type: ignore[return-value]
        except Exception:
            pass
    return timezone.utc


def _parse_datetime(value: str | None, *, default: datetime) -> datetime:
    if not value:
        return default
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _git_output(repo: Path, args: Sequence[str], *, timeout: int = 20) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _git_lines(repo: Path, args: Sequence[str], *, timeout: int = 20) -> list[str]:
    output = _git_output(repo, args, timeout=timeout)
    if not output or not output.strip():
        return []
    return output.strip().splitlines()


def _git_line(repo: Path, args: Sequence[str], *, timeout: int = 20) -> str | None:
    lines = _git_lines(repo, args, timeout=timeout)
    if not lines:
        return None
    return "\n".join(lines).strip() or None


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def discover_repo_paths(repo_roots: Sequence[Path], repo_filters: Sequence[str] = ()) -> list[Path]:
    candidates: list[Path] = []
    roots = [_safe_resolve(root.expanduser()) for root in repo_roots]
    filters = [item for raw in repo_filters for item in _parse_csv(raw)]
    if filters:
        for raw in filters:
            path = Path(raw).expanduser()
            if (path / ".git").exists():
                candidates.append(_safe_resolve(path))
                continue
            name = raw.rsplit("/", 1)[-1]
            for root in roots:
                candidate = root / name
                if (candidate / ".git").exists():
                    candidates.append(_safe_resolve(candidate))
                    break
    else:
        for root in roots:
            if (root / ".git").exists():
                candidates.append(root)
            if not root.is_dir():
                continue
            for child in sorted(root.iterdir()):
                if (child / ".git").exists():
                    candidates.append(_safe_resolve(child))
    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def _branch(repo: Path) -> str | None:
    return _git_line(repo, ["branch", "--show-current"]) or None


def _dirty_entries(repo: Path, *, limit: int = 12) -> list[str]:
    return _git_lines(repo, ["status", "--short"])[:limit]


def _commit_files(repo: Path, sha: str) -> list[str]:
    return _git_lines(repo, ["show", "--name-only", "--format=", sha], timeout=30)


def _is_validation_path(path: str) -> bool:
    lowered = path.lower()
    return (
        "test" in lowered
        or "spec" in lowered
        or "check" in lowered
        or lowered.endswith((".receipt", ".snap"))
    )


def collect_repo_commits(
    repo: Path,
    *,
    since: datetime,
    until: datetime,
) -> list[DigestCommit]:
    since_text = since.astimezone(timezone.utc).isoformat()
    until_text = until.astimezone(timezone.utc).isoformat()
    raw = _git_output(
        repo,
        [
            "log",
            "--all",
            f"--since={since_text}",
            f"--until={until_text}",
            "--format=%H%x1f%aI%x1f%an%x1f%s%x1e",
        ],
        timeout=60,
    ) or ""
    commits: list[DigestCommit] = []
    seen: set[str] = set()
    for entry in raw.split("\x1e"):
        entry = entry.strip("\n")
        if not entry:
            continue
        parts = entry.split("\x1f")
        if len(parts) < 4:
            continue
        sha, authored_at_s, author, subject = parts[:4]
        if sha in seen:
            continue
        seen.add(sha)
        try:
            authored_at = datetime.fromisoformat(authored_at_s.replace("Z", "+00:00"))
        except ValueError:
            authored_at = until
        if authored_at.tzinfo is None:
            authored_at = authored_at.replace(tzinfo=timezone.utc)
        commits.append(
            DigestCommit(
                repo=repo.name,
                sha=sha,
                authored_at=authored_at.astimezone(timezone.utc),
                author=author or "unknown",
                subject=subject or "(no commit message)",
                files=_commit_files(repo, sha),
            )
        )
    commits.sort(key=lambda item: item.authored_at, reverse=True)
    return commits


def _event_matches_repo(event: ManagerEvent, repo: Path) -> bool:
    repo_text = str(event.repo)
    return repo_text in {str(repo), repo.name} or repo.name == Path(repo_text).name


def _events_for_repo(
    repo: Path,
    *,
    since: datetime,
    until: datetime,
    events: Sequence[ManagerEvent],
) -> list[ManagerEvent]:
    matched = [
        event
        for event in events
        if since <= event.created_at <= until and _event_matches_repo(event, repo)
    ]
    matched.sort(key=lambda item: item.created_at, reverse=True)
    return matched[:5]


def _runs_by_repo(runs: Sequence[RunSummary]) -> dict[str, list[RunSummary]]:
    grouped: dict[str, list[RunSummary]] = defaultdict(list)
    for run in runs:
        grouped[str(_safe_resolve(run.repo_path))].append(run)
    return grouped


def _worker_matches_repo(worker: WorkerSession, repo: RepoProgress) -> bool:
    if worker.dead or worker.current_path is None:
        return False
    return str(_safe_resolve(worker.current_path)) == str(repo.path)


def _worker_for_repo(
    repo: RepoProgress,
    worker_sessions: Sequence[WorkerSession],
) -> WorkerSession | None:
    matches = [worker for worker in worker_sessions if _worker_matches_repo(worker, repo)]
    matches.sort(
        key=lambda worker: (
            not worker.active,
            worker.kind() not in {"codex", "claude", "autodev"},
            worker.session_name,
        )
    )
    return matches[0] if matches else None


def _slugify(value: str) -> str:
    chars: list[str] = []
    for char in value.lower():
        if char.isalnum():
            chars.append(char)
        elif chars and chars[-1] != "-":
            chars.append("-")
    return "".join(chars).strip("-") or "repo"


def _board_exists(slug: str) -> bool:
    try:
        return kb.board_exists(slug)
    except Exception:
        return False


def _board_has_runnable_work(slug: str) -> bool:
    if not _board_exists(slug):
        return False
    conn = kb.connect(board=slug)
    try:
        tasks = kb.list_tasks(conn, include_archived=False)
    finally:
        conn.close()
    return any(task.status in {"ready", "review"} for task in tasks)


def _remediation_message(repo: RepoProgress, grade: VelocityGrade, now: datetime) -> str:
    attention = " ".join(repo.attention_lines())
    return (
        "Hermes velocity steward: the last four-hour grade for this repo is "
        f"{grade.grade} ({grade.reason}). Pick the highest-priority next "
        "development step already implied by the repo docs, Kanban board, "
        "gbrain/project page, or current dirty state. Implement one narrow "
        "slice, run focused validation, and commit only verified repo changes. "
        "Do not merely list issues or write a status report. The required "
        "deliverable is either a verified commit/PR-ready patch or a blocker "
        "artifact that proves credentials, external access, or a product decision "
        "is required. If the repo is dirty, first inspect git status and recent "
        "commits. Treat existing uncommitted changes as user or other-worker work. "
        "Do not revert, overwrite, clean, or stash them unless the change is "
        "clearly yours from this run. Either adopt the dirty state only when it is "
        "clearly the highest-priority active work and continue it to a verified "
        "commit, or write the blocker artifact explaining why it cannot be safely "
        "owned. Do not ask the human unless "
        "the blocker is credentials, missing external access, or a product decision. "
        f"Attention context at {now.isoformat()}: {attention}"

exec
/bin/bash -lc "sed -n '520,920p' hermes_cli/dev_progress_digest.py" in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
        f"Attention context at {now.isoformat()}: {attention}"
    )


def _dispatch_board_remediation(repo: RepoProgress) -> RemedialAction:
    command = [
        sys.executable,
        "-m",
        "hermes_cli.main",
        "kanban",
        "--board",
        repo.name,
        "dispatch",
        "--max",
        "1",
        "--json",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return RemedialAction(
            repo=repo.name,
            kind="kanban-dispatch",
            ok=False,
            summary=f"Kanban dispatch failed before execution: {exc}",
            command=" ".join(command),
        )
    detail = (result.stdout or result.stderr or "").strip()
    spawned = 0
    try:
        parsed = json.loads(result.stdout or "{}")
        spawned = len(parsed.get("spawned") or [])
    except json.JSONDecodeError:
        pass
    return RemedialAction(
        repo=repo.name,
        kind="kanban-dispatch",
        ok=result.returncode == 0 and spawned > 0,
        summary=(
            f"Ran matching Kanban board dispatch; spawned {spawned} worker(s)."
            if result.returncode == 0
            else f"Kanban dispatch returned {result.returncode}: {detail[:240]}"
        ),
        command=" ".join(command),
    )


def _start_codex_velocity_worker(
    repo: RepoProgress,
    *,
    grade: VelocityGrade,
    now: datetime,
    dry_run: bool,
) -> RemedialAction:
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    session = f"{_slugify(repo.name)}-velocity-codex-{now.strftime('%Y%m%d%H%M%S')}"
    run_dir = repo.path / ".auto" / "orchestrator" / f"velocity-steward-{stamp}"
    prompt_path = run_dir / "velocity-prompt.md"
    output_path = run_dir / "codex-velocity-output.md"
    script_path = run_dir / "run-codex-velocity.sh"
    prompt = _remediation_message(repo, grade, now)
    if dry_run:
        return RemedialAction(
            repo=repo.name,
            kind="codex-start",
            ok=True,
            summary=f"Would start bounded Codex velocity worker `{session}`.",
            artifact=str(prompt_path),
        )
    tmux = shutil.which("tmux")
    codex = shutil.which("codex")
    if not tmux or not codex:
        missing = "tmux" if not tmux else "codex"
        return RemedialAction(
            repo=repo.name,
            kind="codex-start",
            ok=False,
            summary=f"Could not start Codex velocity worker because `{missing}` is unavailable.",
        )
    run_dir.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt, encoding="utf-8")
    script_path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -o pipefail",
                f"cd {shlex.quote(str(repo.path))}",
                (
                    f"{shlex.quote(codex)} exec "
                    "--dangerously-bypass-approvals-and-sandbox "
                    f"-C {shlex.quote(str(repo.path))} - "
                    f"< {shlex.quote(str(prompt_path))} "
                    f"> {shlex.quote(str(output_path))} 2>&1"
                ),
                "status=$?",
                (
                    "printf '\\n[hermes] codex velocity worker exited with %s at %s\\n' "
                    f'"$status" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> {shlex.quote(str(output_path))}'
                ),
                'exit "$status"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    script_path.chmod(0o755)
    dispatch = subprocess.run(
        [tmux, "new-session", "-d", "-s", session, str(script_path)],
        capture_output=True,
        text=True,
        timeout=12,
        check=False,
    )
    if dispatch.returncode != 0:
        detail = (dispatch.stderr or dispatch.stdout or "").strip()
        return RemedialAction(
            repo=repo.name,
            kind="codex-start",
            ok=False,
            summary=f"Codex velocity tmux dispatch failed: {detail or 'unknown tmux error'}",
            command=" ".join([tmux, "new-session", "-d", "-s", session, str(script_path)]),
            artifact=str(output_path),
        )
    event = record_event(
        repo=repo.path,
        worker_session=session,
        intent=f"Start Codex velocity remediation for `{repo.name}` grade {grade.grade}",
        event_log=DEFAULT_EVENT_LOG,
        event_type="worker-start",
        requested_by="hermes-velocity-steward",
        delivery_channel="telegram",
        resulting_artifacts=[str(prompt_path), str(output_path), str(script_path)],
        notes=f"tmux={session}",
        created_at=now,
    )
    return RemedialAction(
        repo=repo.name,
        kind="codex-start",
        ok=True,
        summary=f"Started bounded Codex velocity worker `{session}`; event `{event.id}`.",
        command=" ".join([tmux, "new-session", "-d", "-s", session, str(script_path)]),
        artifact=str(output_path),
    )


def _is_velocity_action_event(event: ManagerEvent) -> bool:
    if event.requested_by != "hermes-velocity-steward":
        return False
    if event.event_type in {"worker-start", "worker-steer", "worker-resume"}:
        return True
    if event.event_type != "velocity-remediation":
        return False
    text = f"{event.intent} {event.notes or ''}".lower()
    if "no-safe-surface" in text or "no existing live worker" in text:
        return False
    if "codex fallback is disabled" in text or "could not start remedial work" in text:
        return False
    return "kanban-dispatch" in text or "ran matching kanban board dispatch" in text


def remediate_repo_velocity(
    repo: RepoProgress,
    *,
    grade: VelocityGrade,
    now: datetime,
    worker_sessions: Sequence[WorkerSession],
    allow_codex_start: bool = True,
    dry_run: bool = False,
) -> RemedialAction:
    worker = _worker_for_repo(repo, worker_sessions)
    if worker and worker.kind() in {"codex", "claude", "autodev"}:
        if dry_run:
            return RemedialAction(
                repo=repo.name,
                kind="worker-steer",
                ok=True,
                summary=f"Would steer existing {worker.kind()} worker {worker.target()}.",
            )
        result = dispatch_worker(
            repo=repo.path,
            session=worker.target(),
            intent=f"Velocity remediation for `{repo.name}` grade {grade.grade}",
            message=_remediation_message(repo, grade, now),
            event_log=DEFAULT_EVENT_LOG,
            event_type="worker-steer",
            requested_by="hermes-velocity-steward",
            delivery_channel="telegram",
            start_if_missing=False,
            created_at=now,
        )
        return RemedialAction(
            repo=repo.name,
            kind="worker-steer",
            ok=not result.failed_results and result.sent_message,
            summary=f"Steered existing {worker.kind()} worker {worker.target()} for velocity remediation.",
            artifact=str(result.artifact_path),
        )

    if _board_has_runnable_work(repo.name):
        if dry_run:
            return RemedialAction(
                repo=repo.name,
                kind="kanban-dispatch",
                ok=True,
                summary=f"Would dispatch highest-priority ready/review task on board `{repo.name}`.",
        )
        return _dispatch_board_remediation(repo)

    if allow_codex_start:
        return _start_codex_velocity_worker(
            repo,
            grade=grade,
            now=now,
            dry_run=dry_run,
        )

    return RemedialAction(
        repo=repo.name,
        kind="no-safe-surface",
        ok=False,
        summary=(
            "No existing live worker or runnable same-name Kanban board task; "
            "Codex fallback is disabled, so Hermes could not start remedial work."
        ),
    )


def apply_velocity_remediation(
    repos: Sequence[RepoProgress],
    *,
    now: datetime,
    worker_sessions: Sequence[WorkerSession],
    max_remediations: int,
    allow_codex_start: bool = True,
    dry_run: bool = False,
) -> list[RemedialAction]:
    actions: list[RemedialAction] = []
    action_count = 0
    for repo in repos:
        grade = repo.grade()
        if grade.sufficient:
            continue
        if any(_is_velocity_action_event(event) for event in repo.events):
            action = RemedialAction(
                repo=repo.name,
                kind="already-remediated",
                ok=True,
                summary="A velocity remediation event already exists in this window.",
            )
        elif action_count >= max_remediations:
            action = RemedialAction(
                repo=repo.name,
                kind="cap-reached",
                ok=False,
                summary=f"Remediation cap of {max_remediations} action(s) reached.",
            )
        else:
            action = remediate_repo_velocity(
                repo,
                grade=grade,
                now=now,
                worker_sessions=worker_sessions,
                allow_codex_start=allow_codex_start,
                dry_run=dry_run,
            )
            if action.kind not in {"no-safe-surface", "already-remediated", "cap-reached"}:
                action_count += 1
            if action.kind == "kanban-dispatch" and not dry_run:
                record_event(
                    repo=repo.path,
                    worker_session="velocity-steward",
                    intent=f"Velocity remediation for `{repo.name}` grade {grade.grade}: {action.kind}",
                    event_log=DEFAULT_EVENT_LOG,
                    event_type="velocity-remediation",
                    requested_by="hermes-velocity-steward",
                    delivery_channel="telegram",
                    notes=action.summary,
                    created_at=now,
                )
        repo.remedial_actions.append(action)
        actions.append(action)
    return actions


def collect_progress(
    *,
    repo_roots: Sequence[Path],
    since: datetime,
    until: datetime,
    repo_filters: Sequence[str] = (),
    include_idle: bool = False,
    events: Sequence[ManagerEvent] | None = None,
    runs: Sequence[RunSummary] | None = None,
) -> tuple[list[RepoProgress], int]:
    repos = discover_repo_paths(repo_roots, repo_filters)
    all_events = list(events) if events is not None else load_events([DEFAULT_EVENT_LOG])
    run_records = list(runs) if runs is not None else collect_status(
        repo_roots=repo_roots,
        now=until,
        stale_after=timedelta(minutes=DEFAULT_STALE_MINUTES),
        recent_after=since,
    )
    runs_for_repo = _runs_by_repo(run_records)
    progress: list[RepoProgress] = []
    explicit = bool(repo_filters)
    for repo in repos:
        item = RepoProgress(
            name=repo.name,
            path=repo,
            branch=_branch(repo),
            commits=collect_repo_commits(repo, since=since, until=until),
            dirty_entries=_dirty_entries(repo),
            events=_events_for_repo(repo, since=since, until=until, events=all_events),
            runs=runs_for_repo.get(str(repo), []),
        )
        item.velocity_grade = grade_repo_velocity(item)
        if item.has_activity() or explicit or include_idle:
            progress.append(item)
    progress.sort(key=lambda item: (item.grade().sufficient, -len(item.dirty_entries), item.name))
    return progress, len(repos)


def _digest_url(configured: str | None = None) -> str | None:
    if configured:
        return configured
    env_url = os.getenv("HERMES_DEV_PROGRESS_DASHBOARD_URL")
    if env_url:
        return env_url
    status_url = default_dashboard_url()
    if not status_url:
        return None
    return status_url.rsplit("/", 1)[0] + "/" + DEFAULT_DASHBOARD_FILENAME


def render_markdown(result: ProgressDigestResult) -> str:
    local_since = result.since
    local_until = result.generated_at
    remedial_action_label = "Remedial actions planned" if result.remediation_dry_run else "Remedial actions taken"
    lines = [
        "# Four-Hour Dev Progress Digest",
        "",
        f"Window: {local_since.isoformat()} to {local_until.isoformat()}",
        f"Repos scanned: {result.scanned_repo_count}",
        f"Repos reported: {result.active_repo_count}",
        f"Repos below velocity target: {result.insufficient_repo_count}",
        f"Commits landed: {result.commit_count}",
        f"{remedial_action_label}: {result.actual_remedial_action_count}",
        f"Remedial findings: {len(result.remedial_actions)}",
    ]
    if result.dashboard_url:
        lines.append(f"Web view: {result.dashboard_url}")
    lines.append("")
    if not result.repos:
        lines.extend(
            [
                "No local repo activity was found in this window.",
                "",
                "Check active worker panes if progress was expected but no commits, dirty files, or run artifacts appeared.",
            ]
        )
        return "\n".join(lines).rstrip() + "\n"
    for repo in result.repos:
        grade = repo.grade()
        lines.extend([f"## {repo.name}", "", repo.plain_summary(), ""])
        lines.append(f"- Path: `{repo.path}`")
        lines.append(f"- Branch: `{repo.branch or 'unknown'}`")
        lines.append(
            f"- Velocity grade: `{grade.grade}` "
            f"({'sufficient' if grade.sufficient else 'below target'}) - {grade.reason}"
        )
        lines.append(f"- Validation: {repo.validation_summary()}")
        lines.append("- Needs attention: " + " ".join(repo.attention_lines()))
        if repo.remedial_actions:
            lines.append("- Remedial action: " + " ".join(action.summary for action in repo.remedial_actions))
        if repo.commits:
            lines.extend(["", "### Commits", ""])
            for commit in repo.commits[:8]:
                files = ", ".join(commit.files[:5]) if commit.files else "no files listed"
                more = f", plus {len(commit.files) - 5} more" if len(commit.files) > 5 else ""
                lines.append(
                    f"- `{commit.short_sha()}` {commit.plain_subject()} "
                    f"by {commit.author}; files: {files}{more}"
                )
        if repo.runs:
            lines.extend(["", "### Orchestrator Runs", ""])
            for run in repo.runs[:3]:
                latest = run.latest_artifact()
                lines.append(
                    f"- `{run.run_id}` phase={run.phase()}; "
                    f"latest=`{latest.path if latest else 'none'}`; next={run.next_action}"
                )
        if repo.events:
            lines.extend(["", "### Manager Events", ""])
            for event in repo.events[:3]:
                lines.append(f"- {render_event_line(event)}")

exec
/bin/bash -lc "sed -n '1,40p' .gbrain-source" in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
hermes-agent

exec
/bin/bash -lc "sed -n '260,620p' tests/cli/test_dev_progress_digest.py" in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
exec
/bin/bash -lc 'rg "dev_progress_digest|progress digest|dev-progress-digest|dev_manager_next_action|hermes-dev-manager" -n hermes_cli scripts tests pyproject.toml .gitignore' in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
scripts/hermes-dev-manager-next-action.py:29:from hermes_cli.dev_manager_next_action import main
scripts/hermes-dev-progress-digest.py:2:"""Hermes cron wrapper for the four-hour dev progress digest.
scripts/hermes-dev-progress-digest.py:6:hermes cron create "every 4h" --name dev-progress-digest --script dev-progress-digest.py --no-agent --deliver telegram
scripts/hermes-dev-progress-digest.py:24:from hermes_cli.dev_progress_digest import main
hermes_cli/goals.py:363:def _dev_manager_next_action_packet_data() -> Optional[Dict[str, Any]]:
hermes_cli/goals.py:373:        from hermes_cli.dev_manager_next_action import collect_manager_packet, packet_to_json
hermes_cli/goals.py:394:def _dev_manager_next_action_context(packet: Optional[Dict[str, Any]] = None) -> Optional[str]:
hermes_cli/goals.py:395:    packet = packet if packet is not None else _dev_manager_next_action_packet_data()
hermes_cli/goals.py:1251:        manager_packet = _dev_manager_next_action_packet_data()
hermes_cli/goals.py:1258:        manager_context = _dev_manager_next_action_context(manager_packet) if manager_packet else None
tests/cli/test_dev_manager_next_action.py:10:from hermes_cli.dev_manager_next_action import (
tests/cli/test_dev_manager_next_action.py:42:        "hermes_cli.dev_manager_next_action._repo_has_uncommitted_changes",
tests/cli/test_dev_manager_next_action.py:150:    monkeypatch.setattr("hermes_cli.dev_manager_next_action._latest_commit", lambda repo: None)
tests/cli/test_dev_manager_next_action.py:151:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: [])
tests/cli/test_dev_manager_next_action.py:152:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.capture_worker_pane", lambda target: [])
tests/cli/test_dev_manager_next_action.py:191:        "hermes_cli.dev_manager_next_action._latest_commit",
tests/cli/test_dev_manager_next_action.py:194:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: [])
tests/cli/test_dev_manager_next_action.py:195:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.capture_worker_pane", lambda target: [])
tests/cli/test_dev_manager_next_action.py:229:    monkeypatch.setattr("hermes_cli.dev_manager_next_action._repo_has_uncommitted_changes", lambda repo: True)
tests/cli/test_dev_manager_next_action.py:231:        "hermes_cli.dev_manager_next_action._latest_commit",
tests/cli/test_dev_manager_next_action.py:238:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: [])
tests/cli/test_dev_manager_next_action.py:239:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.capture_worker_pane", lambda target: [])
tests/cli/test_dev_manager_next_action.py:272:        "hermes_cli.dev_manager_next_action._latest_commit",
tests/cli/test_dev_manager_next_action.py:276:        "hermes_cli.dev_manager_next_action.matching_events",
tests/cli/test_dev_manager_next_action.py:289:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.capture_worker_pane", lambda target: [])
tests/cli/test_dev_manager_next_action.py:319:        "hermes_cli.dev_manager_next_action._latest_commit",
tests/cli/test_dev_manager_next_action.py:323:        "hermes_cli.dev_manager_next_action.matching_events",
tests/cli/test_dev_manager_next_action.py:336:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.capture_worker_pane", lambda target: [])
tests/cli/test_dev_manager_next_action.py:368:        "hermes_cli.dev_manager_next_action._latest_commit",
tests/cli/test_dev_manager_next_action.py:372:        "hermes_cli.dev_manager_next_action.matching_events",
tests/cli/test_dev_manager_next_action.py:385:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.capture_worker_pane", lambda target: [])
tests/cli/test_dev_manager_next_action.py:419:        "hermes_cli.dev_manager_next_action._latest_commit",
tests/cli/test_dev_manager_next_action.py:423:        "hermes_cli.dev_manager_next_action.matching_events",
tests/cli/test_dev_manager_next_action.py:436:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.capture_worker_pane", lambda target: [])
tests/cli/test_dev_manager_next_action.py:491:    monkeypatch.setattr("hermes_cli.dev_manager_next_action._latest_commit", lambda repo: None)
tests/cli/test_dev_manager_next_action.py:492:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: events)
tests/cli/test_dev_manager_next_action.py:493:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.capture_worker_pane", lambda target: [])
tests/cli/test_dev_manager_next_action.py:554:    monkeypatch.setattr("hermes_cli.dev_manager_next_action._latest_commit", lambda repo: None)
tests/cli/test_dev_manager_next_action.py:555:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: events)
tests/cli/test_dev_manager_next_action.py:556:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.capture_worker_pane", lambda target: [])
tests/cli/test_dev_manager_next_action.py:608:    monkeypatch.setattr("hermes_cli.dev_manager_next_action._latest_commit", lambda repo: None)
tests/cli/test_dev_manager_next_action.py:609:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: events)
tests/cli/test_dev_manager_next_action.py:610:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.capture_worker_pane", lambda target: [])
tests/cli/test_dev_manager_next_action.py:676:        "hermes_cli.dev_manager_next_action._latest_commit",
tests/cli/test_dev_manager_next_action.py:679:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: events)
tests/cli/test_dev_manager_next_action.py:680:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.capture_worker_pane", lambda target: [])
tests/cli/test_dev_manager_next_action.py:741:    monkeypatch.setattr("hermes_cli.dev_manager_next_action._latest_commit", lambda repo: None)
tests/cli/test_dev_manager_next_action.py:742:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: events)
tests/cli/test_dev_manager_next_action.py:743:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.capture_worker_pane", lambda target: [])
tests/cli/test_dev_manager_next_action.py:772:    monkeypatch.setattr("hermes_cli.dev_manager_next_action._latest_commit", lambda repo: None)
tests/cli/test_dev_manager_next_action.py:774:        "hermes_cli.dev_manager_next_action.capture_worker_pane",
tests/cli/test_dev_manager_next_action.py:777:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: [])
tests/cli/test_dev_manager_next_action.py:814:    monkeypatch.setattr("hermes_cli.dev_manager_next_action._latest_commit", lambda repo: None)
tests/cli/test_dev_manager_next_action.py:835:        "hermes_cli.dev_manager_next_action.capture_worker_pane",
tests/cli/test_dev_manager_next_action.py:838:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: events)
tests/cli/test_dev_manager_next_action.py:905:    from hermes_cli.dev_manager_next_action import packet_to_json
tests/cli/test_dev_manager_next_action.py:936:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.shutil.which", lambda command: f"/bin/{command}")
tests/cli/test_dev_manager_next_action.py:944:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.subprocess.run", fake_run)
tests/cli/test_dev_manager_next_action.py:982:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.DEFAULT_EVENT_LOG", event_log)
tests/cli/test_dev_manager_next_action.py:983:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.shutil.which", lambda name: "/usr/bin/tmux")
tests/cli/test_dev_manager_next_action.py:984:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.subprocess.run", fake_run)
tests/cli/test_dev_manager_next_action.py:1045:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.DEFAULT_EVENT_LOG", event_log)
tests/cli/test_dev_manager_next_action.py:1046:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: [review])
tests/cli/test_dev_manager_next_action.py:1118:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.DEFAULT_EVENT_LOG", event_log)
tests/cli/test_dev_manager_next_action.py:1119:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: [review])
tests/cli/test_dev_manager_next_action.py:1186:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: [review])
tests/cli/test_dev_manager_next_action.py:1187:    monkeypatch.setattr("hermes_cli.dev_manager_next_action.dispatch_worker", fake_dispatch_worker)
tests/cli/test_dev_progress_digest.py:8:from hermes_cli.dev_progress_digest import _remediation_message, generate_progress_digest, telegram_summary
tests/cli/test_dev_progress_digest.py:56:        dashboard_url="http://example.test/dev-progress-digest-latest.html",
tests/cli/test_dev_progress_digest.py:76:    assert "Web (tailnet): http://example.test/dev-progress-digest-latest.html" in summary
tests/cli/test_dev_progress_digest.py:125:    monkeypatch.setattr("hermes_cli.dev_progress_digest.dispatch_worker", fake_dispatch_worker)
tests/hermes_cli/test_goals.py:376:            "_dev_manager_next_action_packet_data",
hermes_cli/dev_manager_next_action.py:702:                command="venv/bin/python scripts/hermes-dev-manager-next-action.py --execute-safe --quiet-routine",
hermes_cli/dev_manager_next_action.py:716:                command="venv/bin/python scripts/hermes-dev-manager-events.py list",
hermes_cli/dev_progress_digest.py:1:"""Four-hour plain-English progress digest for the dev orchestrator.
hermes_cli/dev_progress_digest.py:48:DEFAULT_DASHBOARD_FILENAME = "dev-progress-digest-latest.html"
hermes_cli/dev_progress_digest.py:1041:    markdown_path = report_dir / f"dev-progress-digest-{stamp}.md"
hermes_cli/dev_progress_digest.py:1072:    (report_dir / "dev-progress-digest-latest.md").write_text(markdown, encoding="utf-8")
hermes_cli/dev_progress_digest.py:1115:    parser = argparse.ArgumentParser(description="Generate a four-hour dev progress digest")

codex
The digest utility appears designed to close the exact four-hour velocity loop that produced this prompt. I’m going to run the focused tests now; if they fail, I’ll fix the smallest issue needed to make this slice commit-ready.
exec
/bin/bash -lc 'wc -l hermes_cli/dev_progress_digest.py tests/cli/test_dev_progress_digest.py tests/cli/test_dev_manager_next_action.py' in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
 1224 hermes_cli/dev_progress_digest.py
  246 tests/cli/test_dev_progress_digest.py
 1200 tests/cli/test_dev_manager_next_action.py
 2670 total

exec
/bin/bash -lc 'scripts/run_tests.sh tests/cli/test_dev_manager_next_action.py tests/cli/test_dev_progress_digest.py' in /srv/dev/repos/hermes-agent
 succeeded in 2144ms:
Discovered 2 test files (29 tests) under ['tests/cli/test_dev_manager_next_action.py', 'tests/cli/test_dev_progress_digest.py']; running with -j 64
[ 13.8% |     4/29 | ✓4 | ✗0] ✓ tests/cli/test_dev_progress_digest.py (4✓, 0.7s)
[100.0% |    29/29 | ✓29 | ✗ 0] ✓ tests/cli/test_dev_manager_next_action.py (25✓, 1.7s)

=== Summary: 2 files, 29 tests passed, 0 failed (100% complete) in 1.7s (64 workers) ===
  Durations cached to test_durations.json (2 files)

=== Per-file subprocess time distribution ===
  Files:   2
  Total subprocess CPU-wall: 2.5s  (runner wall: 1.7s, parallelism: 64x)
  P50: 1.74s  P90: 1.74s  P95: 1.74s  P99: 1.74s  Max: 1.74s
  <1s: 1 files (50%)  <2s: 2 files (100%)
  Top 10 slowest:
      1.74s  tests/cli/test_dev_manager_next_action.py
      0.72s  tests/cli/test_dev_progress_digest.py

codex
Focused tests already pass for the dirty slice. Before committing, I’m reading the CLI/rendering tail and checking for any obvious integration gap, then I’ll run one command-level smoke test if the options support it.
exec
/bin/bash -lc "sed -n '1,220p' scripts/hermes-dev-manager-next-action.py" in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
#!/usr/bin/env python3
"""Hermes cron wrapper for the dev manager next-action packet.

Install into ~/.hermes/scripts/ and schedule with:

hermes cron create "every 30m" --name dev-manager-next-action --script dev-manager-next-action.py --no-agent --deliver telegram

With no CLI args this wrapper runs the safe autopilot path:

- execute the Codex review gate when a Codex worker explicitly asks for review
- suppress routine status packets Hermes can handle itself
"""

from pathlib import Path
import os
import sys

for candidate in (
    os.getenv("HERMES_AGENT_REPO"),
    "/srv/dev/repos/hermes-agent",
):
    if not candidate:
        continue
    root = Path(candidate)
    if (root / "hermes_cli").is_dir():
        sys.path.insert(0, str(root))
        break

from hermes_cli.dev_manager_next_action import main


DEFAULT_ARGS = ["--execute-safe", "--quiet-routine"]


raise SystemExit(main(sys.argv[1:] or DEFAULT_ARGS))

exec
/bin/bash -lc "sed -n '920,1224p' hermes_cli/dev_progress_digest.py" in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
                lines.append(f"- {render_event_line(event)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_html(result: ProgressDigestResult) -> str:
    rows: list[str] = []
    for repo in result.repos:
        grade = repo.grade()
        attention = "<br>".join(html.escape(line) for line in repo.attention_lines())
        commits = "<br>".join(
            html.escape(f"{commit.short_sha()} {commit.plain_subject()}")
            for commit in repo.commits[:5]
        ) or html.escape(repo.plain_summary())
        validation = html.escape(repo.validation_summary())
        remediation = "<br>".join(html.escape(action.summary) for action in repo.remedial_actions) or "none"
        rows.append(
            "<tr>"
            f"<td><strong>{html.escape(repo.name)}</strong><br><span>{html.escape(str(repo.path))}</span></td>"
            f"<td><strong>{html.escape(grade.grade)}</strong><br>{html.escape(grade.reason)}</td>"
            f"<td>{html.escape(repo.plain_summary())}</td>"
            f"<td>{commits}</td>"
            f"<td>{validation}</td>"
            f"<td>{attention}</td>"
            f"<td>{remediation}</td>"
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="7">No local repo activity was found in this window.</td></tr>')
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="300">
  <title>Dev Progress Digest</title>
  <style>
    :root {{ color-scheme: light; --bg: #f6f7f8; --panel: #ffffff; --text: #1d2428; --muted: #627078; --line: #d7ddd8; --accent: #176b5c; --warn: #9a5a12; }}
    body {{ margin: 0; background: var(--bg); color: var(--text); font: 14px/1.45 Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    main {{ max-width: 1220px; margin: 0 auto; padding: 28px; }}
    h1 {{ margin: 0 0 4px; font-size: 24px; letter-spacing: 0; }}
    .muted {{ color: var(--muted); }}
    .metrics {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin: 18px 0; }}
    .metric {{ border: 1px solid var(--line); border-radius: 8px; background: var(--panel); padding: 12px; }}
    .metric strong {{ display: block; color: var(--accent); font-size: 26px; }}
    table {{ width: 100%; border-collapse: collapse; background: var(--panel); border: 1px solid var(--line); }}
    th, td {{ padding: 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
    th {{ color: var(--accent); font-size: 12px; text-transform: uppercase; letter-spacing: 0; }}
    td span {{ color: var(--muted); font-size: 12px; }}
    @media (max-width: 760px) {{ main {{ padding: 16px; }} .metrics {{ grid-template-columns: 1fr; }} table {{ font-size: 12px; }} }}
  </style>
</head>
<body>
<main>
  <h1>Dev Progress Digest</h1>
  <div class="muted">Window {html.escape(result.since.isoformat())} to {html.escape(result.generated_at.isoformat())}</div>
  <section class="metrics">
    <div class="metric"><span>repos reported</span><strong>{result.active_repo_count}</strong></div>
    <div class="metric"><span>commits landed</span><strong>{result.commit_count}</strong></div>
    <div class="metric"><span>below target</span><strong>{result.insufficient_repo_count}</strong></div>
  </section>
  <table>
    <thead><tr><th>Repo</th><th>Grade</th><th>Plain English</th><th>Commits</th><th>Validation</th><th>Needs Attention</th><th>Remediation</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</main>
</body>
</html>
"""


def write_gbrain_page(slug: str, markdown: str) -> str | None:
    if not shutil.which("gbrain"):
        return "gbrain command not found"
    body = "---\ntype: report\ntitle: Dev Progress Digest\n---\n\n" + markdown
    try:
        result = subprocess.run(
            ["gbrain", "put", slug, "--content", body],
            cwd="/tmp",
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    if result.returncode != 0:
        return (result.stderr or result.stdout or "gbrain put failed").strip()[:400]
    return None


def generate_progress_digest(
    *,
    repo_roots: Sequence[Path],
    report_dir: Path,
    now: datetime,
    since: datetime,
    repo_filters: Sequence[str] = (),
    include_idle: bool = False,
    remediate: bool = False,
    dry_run_remediation: bool = False,
    max_remediations: int = DEFAULT_MAX_REMEDIATIONS,
    allow_codex_start: bool = True,
    write_gbrain: bool = True,
    gbrain_slug: str = DEFAULT_GBRAIN_SLUG,
    dashboard_url: str | None = None,
    events: Sequence[ManagerEvent] | None = None,
    runs: Sequence[RunSummary] | None = None,
    worker_sessions: Sequence[WorkerSession] | None = None,
) -> ProgressDigestResult:
    report_dir.mkdir(parents=True, exist_ok=True)
    repos, scanned = collect_progress(
        repo_roots=repo_roots,
        since=since,
        until=now,
        repo_filters=repo_filters,
        include_idle=include_idle or remediate,
        events=events,
        runs=runs,
    )
    stamp = now.strftime("%Y%m%d-%H%M%S")
    markdown_path = report_dir / f"dev-progress-digest-{stamp}.md"
    html_path = report_dir / DEFAULT_DASHBOARD_FILENAME
    result = ProgressDigestResult(
        markdown_path=markdown_path,
        html_path=html_path,
        dashboard_url=_digest_url(dashboard_url),
        gbrain_slug=None,
        gbrain_error=None,
        generated_at=now,
        since=since,
        repos=repos,
        scanned_repo_count=scanned,
        remediation_cap=max_remediations if remediate else 0,
        remediation_dry_run=dry_run_remediation,
    )
    if remediate:
        session_records = (
            list(worker_sessions)
            if worker_sessions is not None
            else list_tmux_worker_sessions()
        )
        result.remedial_actions = apply_velocity_remediation(
            repos,
            now=now,
            worker_sessions=session_records,
            max_remediations=max_remediations,
            allow_codex_start=allow_codex_start,
            dry_run=dry_run_remediation,
        )
    markdown = render_markdown(result)
    markdown_path.write_text(markdown, encoding="utf-8")
    (report_dir / "dev-progress-digest-latest.md").write_text(markdown, encoding="utf-8")
    html_path.write_text(render_html(result), encoding="utf-8")
    if write_gbrain:
        gbrain_error = write_gbrain_page(gbrain_slug, markdown)
        result.gbrain_error = gbrain_error
        if not gbrain_error:
            result.gbrain_slug = gbrain_slug
    return result


def telegram_summary(result: ProgressDigestResult) -> str:
    remedial_action_label = "Remedial actions planned" if result.remediation_dry_run else "Remedial actions taken"
    lines = [
        "4-hour dev velocity digest ready.",
        f"Window: {result.since.isoformat()} to {result.generated_at.isoformat()}",
        f"Repos reported: {result.active_repo_count} / {result.scanned_repo_count}",
        f"Repos below target: {result.insufficient_repo_count}",
        f"Commits landed: {result.commit_count}",
        f"{remedial_action_label}: {result.actual_remedial_action_count}",
        f"Remedial findings: {len(result.remedial_actions)}",
    ]
    if result.dashboard_url:
        lines.append(f"Web (tailnet): {result.dashboard_url}")
    else:
        lines.append(f"HTML: {result.html_path}")
    lines.append(f"Attached Markdown: {result.markdown_path}")
    if result.gbrain_slug:
        lines.append(f"gbrain: {result.gbrain_slug}")
    elif result.gbrain_error:
        lines.append(f"gbrain: not synced ({result.gbrain_error})")
    for repo in result.repos[:5]:
        grade = repo.grade()
        action_text = ""
        if repo.remedial_actions:
            action_text = f" Remediation: {repo.remedial_actions[0].summary}"
        lines.append(f"- {repo.name}: {grade.grade} - {repo.plain_summary()}{action_text}")
    if len(result.repos) > 5:
        lines.append(f"- plus {len(result.repos) - 5} more repo(s) in the web view")
    lines.append(f"MEDIA:{result.markdown_path}")
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a four-hour dev progress digest")
    parser.add_argument("--repo-root", action="append", help="Root containing repo checkouts. Repeatable or comma-separated")
    parser.add_argument("--repo", action="append", help="Specific local repo path or repo name. Repeatable or comma-separated")
    parser.add_argument("--lookback-hours", type=float, default=float(os.getenv("HERMES_DEV_PROGRESS_LOOKBACK_HOURS", DEFAULT_LOOKBACK_HOURS)))
    parser.add_argument("--since", help="ISO timestamp override for window start")
    parser.add_argument("--until", help="ISO timestamp override for window end")
    parser.add_argument("--timezone", default=os.getenv("HERMES_DEV_PROGRESS_TIMEZONE", DEFAULT_TIMEZONE))
    parser.add_argument("--out-dir", default=os.getenv("HERMES_DEV_PROGRESS_DIR", str(DEFAULT_REPORT_DIR)))
    parser.add_argument("--dashboard-url", default=os.getenv("HERMES_DEV_PROGRESS_DASHBOARD_URL"))
    parser.add_argument("--gbrain-slug", default=os.getenv("HERMES_DEV_PROGRESS_GBRAIN_SLUG", DEFAULT_GBRAIN_SLUG))
    parser.add_argument(
        "--include-idle",
        action="store_true",
        help="Include discovered repos even when they had no commits, dirty files, events, or runs in the window",
    )
    parser.add_argument(
        "--steward",
        action="store_true",
        default=os.getenv("HERMES_DEV_PROGRESS_STEWARD", "").lower() in {"1", "true", "yes", "on"},
        help="Grade repo velocity and remediate below-target repos via existing workers or matching Kanban boards",
    )
    parser.add_argument(
        "--dry-run-remediation",
        action="store_true",
        help="Grade and report would-be remedial actions without steering or dispatching workers",
    )
    parser.add_argument(
        "--max-remediations",
        type=int,
        default=int(os.getenv("HERMES_DEV_PROGRESS_MAX_REMEDIATIONS", str(DEFAULT_MAX_REMEDIATIONS))),
        help="Maximum worker steers or Kanban dispatches per digest run",
    )
    parser.add_argument(
        "--no-codex-start",
        action="store_true",
        default=os.getenv("HERMES_DEV_PROGRESS_NO_CODEX_START", "").lower() in {"1", "true", "yes", "on"},
        help="Do not start fallback Codex velocity workers for repos without a board/live worker",
    )
    parser.add_argument("--no-gbrain", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def _load_hermes_env() -> None:
    try:
        from hermes_cli.env_loader import load_hermes_dotenv

        load_hermes_dotenv()
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    _load_hermes_env()
    args = build_arg_parser().parse_args(argv)
    local_tz = _timezone(args.timezone)
    until = _parse_datetime(args.until, default=datetime.now(timezone.utc)).astimezone(local_tz)
    since_default = until - timedelta(hours=args.lookback_hours)
    since = _parse_datetime(args.since, default=since_default).astimezone(local_tz)
    root_values: list[str] = []
    for value in args.repo_root or []:
        root_values.extend(_parse_csv(value))
    result = generate_progress_digest(
        repo_roots=_repo_roots(root_values),
        report_dir=Path(args.out_dir).expanduser(),
        now=until,
        since=since,
        repo_filters=args.repo or [],
        include_idle=args.include_idle,
        remediate=args.steward,
        dry_run_remediation=args.dry_run_remediation,
        max_remediations=max(0, args.max_remediations),
        allow_codex_start=not args.no_codex_start,
        write_gbrain=not args.no_gbrain,
        gbrain_slug=args.gbrain_slug,
        dashboard_url=args.dashboard_url,
    )
    if args.json:
        print(
            json.dumps(
                {
                    "active_repos": result.active_repo_count,
                    "commits": result.commit_count,
                    "insufficient_repos": result.insufficient_repo_count,
                    "actual_remedial_actions": result.actual_remedial_action_count,
                    "remedial_actions": [
                        {
                            "repo": action.repo,
                            "kind": action.kind,
                            "ok": action.ok,
                            "summary": action.summary,
                            "artifact": action.artifact,
                        }
                        for action in result.remedial_actions
                    ],
                    "dashboard_url": result.dashboard_url,
                    "html_path": str(result.html_path),
                    "markdown_path": str(result.markdown_path),
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(telegram_summary(result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

exec
/bin/bash -lc 'rg "DEFAULT_REPORT_DIR|dev-progress|report-dir|dashboard" -n hermes_cli/dev_run_status.py hermes_cli/dev_progress_digest.py' in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
hermes_cli/dev_progress_digest.py:33:    DEFAULT_REPORT_DIR,
hermes_cli/dev_progress_digest.py:39:    default_dashboard_url,
hermes_cli/dev_progress_digest.py:48:DEFAULT_DASHBOARD_FILENAME = "dev-progress-digest-latest.html"
hermes_cli/dev_progress_digest.py:160:    dashboard_url: str | None
hermes_cli/dev_progress_digest.py:854:    status_url = default_dashboard_url()
hermes_cli/dev_progress_digest.py:875:    if result.dashboard_url:
hermes_cli/dev_progress_digest.py:876:        lines.append(f"Web view: {result.dashboard_url}")
hermes_cli/dev_progress_digest.py:1025:    dashboard_url: str | None = None,
hermes_cli/dev_progress_digest.py:1041:    markdown_path = report_dir / f"dev-progress-digest-{stamp}.md"
hermes_cli/dev_progress_digest.py:1046:        dashboard_url=_digest_url(dashboard_url),
hermes_cli/dev_progress_digest.py:1072:    (report_dir / "dev-progress-digest-latest.md").write_text(markdown, encoding="utf-8")
hermes_cli/dev_progress_digest.py:1093:    if result.dashboard_url:
hermes_cli/dev_progress_digest.py:1094:        lines.append(f"Web (tailnet): {result.dashboard_url}")
hermes_cli/dev_progress_digest.py:1122:    parser.add_argument("--out-dir", default=os.getenv("HERMES_DEV_PROGRESS_DIR", str(DEFAULT_REPORT_DIR)))
hermes_cli/dev_progress_digest.py:1123:    parser.add_argument("--dashboard-url", default=os.getenv("HERMES_DEV_PROGRESS_DASHBOARD_URL"))
hermes_cli/dev_progress_digest.py:1190:        dashboard_url=args.dashboard_url,
hermes_cli/dev_progress_digest.py:1210:                    "dashboard_url": result.dashboard_url,
hermes_cli/dev_run_status.py:28:DEFAULT_REPORT_DIR = Path.home() / ".hermes" / "reports"
hermes_cli/dev_run_status.py:206:    dashboard_path: Path | None = None
hermes_cli/dev_run_status.py:207:    dashboard_url: str | None = None
hermes_cli/dev_run_status.py:654:def default_dashboard_url() -> str | None:
hermes_cli/dev_run_status.py:686:def render_dashboard_html(
hermes_cli/dev_run_status.py:787:    dashboard_url: str | None = None,
hermes_cli/dev_run_status.py:799:    if dashboard_url:
hermes_cli/dev_run_status.py:800:        lines.extend([f"Dashboard: {dashboard_url}", ""])
hermes_cli/dev_run_status.py:902:    dashboard_url: str | None = None,
hermes_cli/dev_run_status.py:905:    effective_dashboard_url = dashboard_url if dashboard_url is not None else default_dashboard_url()
hermes_cli/dev_run_status.py:921:        dashboard_url=effective_dashboard_url,
hermes_cli/dev_run_status.py:925:    dashboard_path = report_dir / DEFAULT_DASHBOARD_FILENAME
hermes_cli/dev_run_status.py:926:    dashboard_path.write_text(
hermes_cli/dev_run_status.py:927:        render_dashboard_html(runs, session_records, generated_at=now, stale_after=stale_after, markdown=markdown),
hermes_cli/dev_run_status.py:939:        dashboard_path=dashboard_path,
hermes_cli/dev_run_status.py:940:        dashboard_url=effective_dashboard_url,
hermes_cli/dev_run_status.py:952:    if result.dashboard_url:
hermes_cli/dev_run_status.py:953:        lines.append(f"Dashboard: {result.dashboard_url}")
hermes_cli/dev_run_status.py:954:    elif result.dashboard_path:
hermes_cli/dev_run_status.py:955:        lines.append(f"Dashboard file: {result.dashboard_path}")
hermes_cli/dev_run_status.py:981:    parser.add_argument("--out-dir", default=os.getenv("HERMES_DEV_RUN_STATUS_DIR", str(DEFAULT_REPORT_DIR)))
hermes_cli/dev_run_status.py:995:        "--dashboard-url",
hermes_cli/dev_run_status.py:997:        help="Public or tailnet URL for the generated status dashboard",
hermes_cli/dev_run_status.py:1021:        dashboard_url=args.dashboard_url,

codex
The CLI supports a non-mutating steward smoke run, so I’ll use that with a temp report directory and gbrain disabled. That gives validation beyond imports and unit tests without steering workers or starting Codex.
exec
/bin/bash -lc 'git status --short' in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
 M hermes_cli/dev_manager_next_action.py
 M tests/cli/test_dev_manager_next_action.py
?? .auto/
?? .gbrain-source
?? hermes_cli/dev_progress_digest.py
?? scripts/hermes-dev-progress-digest.py
?? tests/cli/test_dev_progress_digest.py

exec
/bin/bash -lc 'scripts/hermes-dev-progress-digest.py --repo-root /srv/dev/repos --repo hermes-agent --out-dir /tmp/hermes-progress-digest-smoke --no-gbrain --steward --dry-run-remediation --no-codex-start --json' in /srv/dev/repos/hermes-agent
 succeeded in 222ms:
{
  "active_repos": 1,
  "actual_remedial_actions": 0,
  "commits": 0,
  "dashboard_url": "http://100.70.49.26:8765/dev-progress-digest-latest.html",
  "html_path": "/tmp/hermes-progress-digest-smoke/dev-progress-digest-latest.html",
  "insufficient_repos": 1,
  "markdown_path": "/tmp/hermes-progress-digest-smoke/dev-progress-digest-20260602-132949.md",
  "remedial_actions": [
    {
      "artifact": null,
      "kind": "already-remediated",
      "ok": true,
      "repo": "hermes-agent",
      "summary": "A velocity remediation event already exists in this window."
    }
  ]
}

codex
The smoke run succeeded and wrote only to `/tmp`; repo status is unchanged except the pre-existing dirty files. I’m doing one quick code review pass over the new digest logic now, looking for a narrow correctness gap before staging.
exec
/bin/bash -lc "sed -n '1,220p' hermes_cli/dev_run_status.py" in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
"""Active autodev/pilot run status for Hermes cron.

This module is deterministic by design. It summarizes process state and run
artifacts so the operator can tell whether an ambitious campaign is active,
stale, or ready for intervention before commits exist.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python 3.11+ always has zoneinfo.
    ZoneInfo = None  # type: ignore[assignment]


DEFAULT_REPO_ROOTS = [Path("/srv/dev/repos"), Path.home() / "coding", Path.home() / "Coding"]
DEFAULT_REPORT_DIR = Path.home() / ".hermes" / "reports"
DEFAULT_STALE_MINUTES = 30
DEFAULT_RECENT_HOURS = 2
DEFAULT_DASHBOARD_PORT = 8765
DEFAULT_DASHBOARD_FILENAME = "dev-run-status-latest.html"
DEFAULT_TIMEZONE = "America/New_York"
DEFAULT_GBRAIN_SLUG = "dev-orchestrator-active-run-status"

PROCESS_MARKERS = (
    "pilot-dev",
    "auto pilot",
    "auto corpus",
    "auto gen",
    "auto parallel",
    "auto steward",
    "codex exec",
    "claude",
)


@dataclass(frozen=True)
class ProcessRecord:
    pid: int
    ppid: int
    stat: str
    elapsed: str
    cpu: float
    mem: float
    command: str

    def label(self) -> str:
        command = self.command
        if "auto gen" in command:
            return "auto gen"
        if "auto corpus" in command:
            return "auto corpus"
        if "auto parallel" in command:
            return "auto parallel"
        if "auto pilot" in command:
            if "--planning-only" in command:
                return "auto pilot planning"
            if "--execution-manifest-only" in command:
                return "auto pilot execution manifest"
            if "--closeout-only" in command:
                return "auto pilot closeout"
            return "auto pilot"
        if "codex exec" in command:
            return "codex exec"
        if "pilot-dev" in command:
            return "pilot-dev"
        if "claude" in command:
            return "claude"
        return command.split(" ", 1)[0]


@dataclass(frozen=True)
class ArtifactRecord:
    path: Path
    mtime: datetime
    size: int
    kind: str


@dataclass(frozen=True)
class WorkerSession:
    session_name: str
    window_index: str
    pane_index: str
    pane_pid: int | None
    current_command: str
    current_path: Path | None
    active: bool
    dead: bool
    title: str

    def target(self) -> str:
        return f"{self.session_name}:{self.window_index}.{self.pane_index}"

    def kind(self) -> str:
        haystack = " ".join(
            part.lower()
            for part in (
                self.session_name,
                self.title,
                self.current_command,
                str(self.current_path or ""),
            )
        )
        if "codex" in haystack:
            return "codex"
        if "claude" in haystack:
            return "claude"
        if "auto" in haystack:
            return "autodev"
        return "shell"

    def repo_slug(self, repo_roots: Sequence[Path]) -> str | None:
        if self.current_path is None:
            return None
        try:
            resolved = self.current_path.resolve()
        except OSError:
            resolved = self.current_path
        for root in repo_roots:
            try:
                relative = resolved.relative_to(root)
            except ValueError:
                continue
            return relative.parts[0] if relative.parts else None
        return None


@dataclass
class RunSummary:
    repo_slug: str
    repo_path: Path
    run_id: str
    run_root: Path
    branch: str | None
    run_started_at: datetime | None
    active_processes: list[ProcessRecord] = field(default_factory=list)
    latest_run_artifact: ArtifactRecord | None = None
    latest_repo_artifact: ArtifactRecord | None = None
    phase_heartbeat: dict[str, str] | None = None
    dirty_entries: list[str] = field(default_factory=list)
    execution_status: str | None = None
    closeout_status: str | None = None
    gbrain_context: Path | None = None
    implementation_plan: Path | None = None
    attention: bool = False
    attention_reason: str | None = None
    next_action: str = "Inspect run root before starting another broad command."

    @property
    def active(self) -> bool:
        return bool(self.active_processes)

    def phase(self) -> str:
        if self.phase_heartbeat:
            phase = self.phase_heartbeat.get("phase", "").strip()
            status = self.phase_heartbeat.get("status", "").strip()
            if phase and status:
                return f"{phase}: {status}"
            if phase:
                return phase
        labels: list[str] = []
        for process in self.active_processes:
            label = process.label()
            if label not in labels:
                labels.append(label)
        if labels:
            if "auto gen" in labels and "codex exec" in labels:
                return "planning: auto gen snapshot + Codex review"
            return ", ".join(labels[:4])
        if self.closeout_status:
            return f"closeout: {self.closeout_status}"
        if self.execution_status:
            return f"execution: {self.execution_status}"
        if self.implementation_plan:
            return "planned"
        return "inactive"

    def latest_artifact(self) -> ArtifactRecord | None:
        candidates = [item for item in (self.latest_run_artifact, self.latest_repo_artifact) if item]
        if not candidates:
            return None
        return max(candidates, key=lambda item: item.mtime)


@dataclass
class StatusResult:
    markdown_path: Path
    gbrain_slug: str | None
    gbrain_error: str | None
    runs: list[RunSummary]
    generated_at: datetime
    stale_after: timedelta
    worker_sessions: list[WorkerSession] = field(default_factory=list)
    dashboard_path: Path | None = None
    dashboard_url: str | None = None

    @property
    def active_count(self) -> int:
        return sum(1 for run in self.runs if run.active)

    @property
    def worker_count(self) -> int:
        return sum(1 for session in self.worker_sessions if not session.dead)

    @property
    def attention_count(self) -> int:
        return sum(1 for run in self.runs if run.attention)


exec
/bin/bash -lc "sed -n '1,260p' tests/cli/test_dev_progress_digest.py" in /srv/dev/repos/hermes-agent
 exited 2 in 0ms:
sed: can't read tests/cli/test_dev_progress_digest.py: No such file or directory

exec
/bin/bash -lc 'rg "def phase|class RunSummary|class WorkerSession|attention" -n hermes_cli/dev_run_status.py' in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
92:class WorkerSession:
141:class RunSummary:
157:    attention: bool = False
158:    attention_reason: str | None = None
165:    def phase(self) -> str:
218:    def attention_count(self) -> int:
219:        return sum(1 for run in self.runs if run.attention)
564:        run.attention = True
565:        run.attention_reason = "active process has no discoverable artifact"
569:        run.attention = True
570:        run.attention_reason = f"latest artifact is {_format_age(now, latest.mtime)}"
634:    runs.sort(key=lambda run: (not run.active, not run.attention, run.run_started_at or datetime.fromtimestamp(0, tz=timezone.utc)))
695:    attention = sum(1 for run in runs if run.attention)
720:        state = "attention" if run.attention else "active" if run.active else "recent"
765:    <div class="metric"><span>attention</span><strong>{attention}</strong></div>
794:        f"Attention needed: {sum(1 for run in runs if run.attention)}",
834:                f"Status: {'attention' if run.attention else 'active' if run.active else 'recent'}",
847:        if run.attention_reason:
848:            lines.append(f"Attention reason: {run.attention_reason}")
948:        f"Attention needed: {result.attention_count}",
960:    display_runs = [run for run in result.runs if run.active or run.attention]

exec
/bin/bash -lc 'rg "def record_event|class ManagerEvent|matching_events|DEFAULT_EVENT_LOG" -n hermes_cli/dev_manager_events.py' in /srv/dev/repos/hermes-agent
exec
/bin/bash -lc 'git diff --check -- hermes_cli/dev_manager_next_action.py tests/cli/test_dev_manager_next_action.py hermes_cli/dev_progress_digest.py scripts/hermes-dev-progress-digest.py tests/cli/test_dev_progress_digest.py' in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
22:DEFAULT_EVENT_LOG = DEFAULT_REPORT_DIR / "dev-manager-events.jsonl"
27:class ManagerEvent:
122:def record_event(
127:    event_log: Path = DEFAULT_EVENT_LOG,
155:    logs = list(event_logs) if event_logs is not None else [DEFAULT_EVENT_LOG]
174:def matching_events(
205:    return Path(value).expanduser() if value else DEFAULT_EVENT_LOG
251:    events = matching_events(

 succeeded in 0ms:
codex
One oddity appeared during the review pass: a parallel `sed` read reported the untracked digest test missing even though pytest just ran it and `git status` still lists it. I’m checking the path directly before making edits or staging, because I don’t want to commit a half-visible file state.
exec
/bin/bash -lc 'ls -l tests/cli/test_dev_progress_digest.py hermes_cli/dev_progress_digest.py scripts/hermes-dev-progress-digest.py' in /srv/dev/repos/hermes-agent
 exited 2 in 0ms:
ls: cannot access 'tests/cli/test_dev_progress_digest.py': No such file or directory
ls: cannot access 'hermes_cli/dev_progress_digest.py': No such file or directory
ls: cannot access 'scripts/hermes-dev-progress-digest.py': No such file or directory

exec
/bin/bash -lc "python - <<'PY'
from pathlib import Path
for path in ['tests/cli/test_dev_progress_digest.py','hermes_cli/dev_progress_digest.py','scripts/hermes-dev-progress-digest.py']:
    p=Path(path)
    print(path, p.exists(), p.stat().st_size if p.exists() else None)
PY" in /srv/dev/repos/hermes-agent
 exited 127 in 0ms:
/bin/bash: line 1: python: command not found

exec
/bin/bash -lc 'git status --short --untracked-files=all' in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
 M hermes_cli/dev_manager_next_action.py
 M tests/cli/test_dev_manager_next_action.py
?? .auto/orchestrator/velocity-steward-20260602T132815Z/codex-velocity-output.md
?? .auto/orchestrator/velocity-steward-20260602T132815Z/run-codex-velocity.sh
?? .auto/orchestrator/velocity-steward-20260602T132815Z/velocity-prompt.md
?? .gbrain-source

codex
The untracked digest module/script/test disappeared between checks, so I’m not going to chase or recreate that work. The remaining ownable slice is the tracked `dev_manager_next_action` update, which is coherent and already covered by focused tests; I’ll re-run validation against the current file state before staging only those tracked files.
exec
/bin/bash -lc 'git diff --check -- hermes_cli/dev_manager_next_action.py tests/cli/test_dev_manager_next_action.py' in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
exec
/bin/bash -lc 'git diff --stat -- hermes_cli/dev_manager_next_action.py tests/cli/test_dev_manager_next_action.py' in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
 hermes_cli/dev_manager_next_action.py     |  97 +++++++++++++++++---
 tests/cli/test_dev_manager_next_action.py | 146 +++++++++++++++++++++++++++++-
 2 files changed, 229 insertions(+), 14 deletions(-)

exec
/bin/bash -lc 'scripts/run_tests.sh tests/cli/test_dev_manager_next_action.py' in /srv/dev/repos/hermes-agent
 succeeded in 1824ms:
Discovered 1 test files (25 tests) under ['tests/cli/test_dev_manager_next_action.py']; running with -j 64
[100.0% |    25/25 | ✓25 | ✗ 0] ✓ tests/cli/test_dev_manager_next_action.py (25✓, 1.4s)

=== Summary: 1 files, 25 tests passed, 0 failed (100% complete) in 1.4s (64 workers) ===
  Durations cached to test_durations.json (1 files)

=== Per-file subprocess time distribution ===
  Files:   1
  Total subprocess CPU-wall: 1.4s  (runner wall: 1.4s, parallelism: 64x)
  P50: 1.45s  P90: 1.45s  P95: 1.45s  P99: 1.45s  Max: 1.45s
  <1s: 0 files (0%)  <2s: 1 files (100%)
  Top 10 slowest:
      1.45s  tests/cli/test_dev_manager_next_action.py

codex
The tracked slice passes its focused suite and whitespace checks. I’m doing one read-only CLI smoke run next, then I’ll stage just the two tracked files and commit them if the smoke run is clean.
exec
/bin/bash -lc 'rg "add_argument|execute-safe|quiet-routine|json" -n hermes_cli/dev_manager_next_action.py' in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
13:import json
702:                command="venv/bin/python scripts/hermes-dev-manager-next-action.py --execute-safe --quiet-routine",
1388:def packet_to_json(packet: ManagerPacket) -> str:
1443:    return json.dumps(data, indent=2, sort_keys=True)
1448:    parser.add_argument("--repo-root", action="append", help="Root containing repo checkouts. Repeatable or comma-separated")
1449:    parser.add_argument("--board", action="append", help="Kanban board slug to include. Repeatable; default is all boards")
1450:    parser.add_argument("--out-dir", default=os.getenv("HERMES_DEV_MANAGER_NEXT_ACTION_DIR", str(DEFAULT_REPORT_DIR)))
1451:    parser.add_argument("--stale-minutes", type=float, default=float(os.getenv("HERMES_DEV_MANAGER_STALE_MINUTES", DEFAULT_STALE_MINUTES)))
1452:    parser.add_argument("--recent-hours", type=float, default=float(os.getenv("HERMES_DEV_MANAGER_RECENT_HOURS", DEFAULT_RECENT_HOURS)))
1453:    parser.add_argument("--gbrain-slug", default=os.getenv("HERMES_DEV_MANAGER_GBRAIN_SLUG", DEFAULT_GBRAIN_SLUG))
1454:    parser.add_argument("--no-gbrain", action="store_true")
1455:    parser.add_argument("--json", action="store_true", help="Print machine-readable packet JSON instead of Telegram summary")
1456:    parser.add_argument(
1457:        "--quiet-routine",
1462:    parser.add_argument(
1463:        "--execute-safe",
1468:    parser.add_argument("--profile", action="append", default=None, help="Preflight profile to check; repeatable")
1497:    if args.json:
1498:        print(packet_to_json(packet))
1501:                json.dumps(

exec
/bin/bash -lc 'venv/bin/python scripts/hermes-dev-manager-next-action.py --json' in /srv/dev/repos/hermes-agent
 succeeded in 1125ms:
{
  "boards": [
    {
      "counts": {
        "blocked": 3,
        "done": 3,
        "todo": 1
      },
      "name": "Ludeme",
      "slug": "ludeme",
      "tasks": [
        {
          "assignee": "codexworker",
          "id": "t_8470ff2e",
          "risk": "codexworker blocked with unverified or review-required work",
          "status": "blocked",
          "title": "Implement expressive motion vocabulary for boardlab-tui (TuiEffectTargetKind + high_contrast + BoardReady dissolve)"
        },
        {
          "assignee": "codexworker",
          "id": "t_d091b4e6",
          "risk": "codexworker blocked; inspect blocker before dispatching more work",
          "status": "blocked",
          "title": "codexworker: small process slice - harden TUI snapshot regression gate with explicit paper-bg invariant test"
        },
        {
          "assignee": "designcritic",
          "id": "t_5a21919e",
          "risk": null,
          "status": "blocked",
          "title": "designcritic: review current TUI implementation state against DESIGN.md Atelier spec and tui-design-plan-2026-05-30.md; produce gap report + next 2-step recommendation"
        },
        {
          "assignee": "reviewer",
          "id": "t_e2e5a2a3",
          "risk": null,
          "status": "todo",
          "title": "parent-gated review after t_d091b4e6 Codex slice"
        }
      ]
    },
    {
      "counts": {
        "blocked": 1,
        "done": 3
      },
      "name": "Autonomy Bitino",
      "slug": "autonomy-bitino",
      "tasks": [
        {
          "assignee": "codexworker",
          "id": "t_ff21d5b2",
          "risk": "codexworker blocked with unverified or review-required work",
          "status": "blocked",
          "title": "TASK-013a Regenerate `auto design --apply` output once OpenAI auth is restored"
        }
      ]
    },
    {
      "counts": {},
      "name": "Default",
      "slug": "default",
      "tasks": []
    }
  ],
  "generated_at": "2026-06-02T17:31:04.470834+00:00",
  "next_action": {
    "board": null,
    "command": "git -C /srv/dev/repos/bitino status --short --branch",
    "evidence_after": "2026-06-02T17:31:04.470834+00:00",
    "human_action": "None by default; Hermes should do this itself and report only evidence or a true blocker.",
    "kind": "supervise-interactive-worker",
    "manager_instruction": "Inspect the worker pane, repo status, and recent artifacts. If the worker is still productive, keep monitoring. If it is idle, blocked, or complete, steer it or write a supervision closeout and sync the evidence to gbrain before reporting progress.",
    "reason": "bitino-velocity-codex-20260602132815:0.0 has uncommitted repo changes; supervise the live worker before reviewing a stale commit",
    "repo": "/srv/dev/repos/bitino",
    "required_evidence": "Before claiming the dirty worker is handled, cite a fresh closeout, commit, or status artifact for `bitino-velocity-codex-20260602132815:0.0` after `2026-06-02T17:31:04.470834+00:00`. Evidence must be fresh: created or updated after `2026-06-02T17:31:04.470834+00:00` for this packet, not copied from prior runs.",
    "review_commit": null,
    "review_event_id": null,
    "review_title": null,
    "task_id": null,
    "worker": "bitino-velocity-codex-20260602132815:0.0"
  },
  "preflight": {
    "failures": 0,
    "warnings": 0
  },
  "workers": [
    {
      "active": true,
      "command": "bash",
      "dead": false,
      "kind": "shell",
      "path": "/srv/dev/repos/ludeme",
      "target": "0:0.0"
    },
    {
      "active": true,
      "command": "bash",
      "dead": false,
      "kind": "codex",
      "path": "/srv/dev/repos/bitino",
      "target": "bitino-velocity-codex-20260602132815:0.0"
    },
    {
      "active": true,
      "command": "bash",
      "dead": false,
      "kind": "codex",
      "path": "/srv/dev/repos/gbrain",
      "target": "gbrain-velocity-codex-20260602132815:0.0"
    },
    {
      "active": true,
      "command": "bash",
      "dead": false,
      "kind": "codex",
      "path": "/srv/dev/repos/hermes-agent",
      "target": "hermes-agent-velocity-codex-20260602132815:0.0"
    },
    {
      "active": true,
      "command": "node",
      "dead": false,
      "kind": "codex",
      "path": "/srv/dev/repos/ludeme",
      "target": "ludeme-codex:0.0"
    },
    {
      "active": true,
      "command": "bash",
      "dead": false,
      "kind": "codex",
      "path": "/srv/dev/repos/nullspaceton",
      "target": "nullspaceton-velocity-codex-20260602132815:0.0"
    },
    {
      "active": true,
      "command": "bash",
      "dead": false,
      "kind": "codex",
      "path": "/srv/dev/repos/poker-arena",
      "target": "poker-arena-velocity-codex-20260602132815:0.0"
    },
    {
      "active": true,
      "command": "bash",
      "dead": false,
      "kind": "codex",
      "path": "/srv/dev/repos/robopoker",
      "target": "robopoker-velocity-codex-20260602132815:0.0"
    },
    {
      "active": true,
      "command": "bash",
      "dead": false,
      "kind": "codex",
      "path": "/srv/dev/repos/trustlessminer",
      "target": "trustlessminer-velocity-codex-20260602132815:0.0"
    },
    {
      "active": true,
      "command": "bash",
      "dead": false,
      "kind": "codex",
      "path": "/srv/dev/repos/xmr1-market-maker",
      "target": "xmr1-market-maker-velocity-codex-20260602132815:0.0"
    }
  ]
}

codex
The read-only smoke run is clean and shows the new dirty-worker supervision branch in the packet output. I’m staging only `hermes_cli/dev_manager_next_action.py` and its focused test file now; `.auto/` and `.gbrain-source` are pre-existing local artifacts and will remain uncommitted.
exec
/bin/bash -lc 'git diff -- hermes_cli/dev_manager_next_action.py tests/cli/test_dev_manager_next_action.py' in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
diff --git a/hermes_cli/dev_manager_next_action.py b/hermes_cli/dev_manager_next_action.py
index a7bc88dd7..a20959c72 100644
--- a/hermes_cli/dev_manager_next_action.py
+++ b/hermes_cli/dev_manager_next_action.py
@@ -62,6 +62,7 @@ ROUTINE_TELEGRAM_KINDS = {
     "dispatch-task",
     "wait-for-active-run",
     "wait-for-codex-review",
+    "report-codex-review-result",
     "repair-codex-review-blocker",
     "execute-plan-slice",
     "create-campaign-plan",
@@ -360,10 +361,20 @@ def _pending_codex_review(worker: WorkerSession, *, now: datetime, since: dateti
 
 
 def _unreported_codex_review(worker: WorkerSession) -> ManagerEvent | None:
+    events = _codex_review_events(worker)
     latest_start = _latest_codex_review_start(worker)
     if latest_start is None:
+        latest_finish = _latest_event(
+            [event for event in events if event.event_type == "codex-review"],
+            {"codex-review"},
+        )
+        if (
+            latest_finish is not None
+            and _review_returncode(latest_finish) != 0
+            and not _codex_review_was_reported(events, latest_finish)
+        ):
+            return latest_finish
         return None
-    events = _codex_review_events(worker)
     finished = [
         event
         for event in events
@@ -390,8 +401,25 @@ def _review_output_text(event: ManagerEvent) -> str:
     return output_path.read_text(encoding="utf-8", errors="replace")
 
 
+def _review_stdout_text(event: ManagerEvent) -> str:
+    text = _review_output_text(event)
+    if "## Stdout" not in text:
+        return text
+    stdout = text.split("## Stdout", 1)[1]
+    stdout = stdout.split("## Stderr", 1)[0]
+    stdout = stdout.strip()
+    if stdout.startswith("```"):
+        lines = stdout.splitlines()
+        if lines and lines[0].startswith("```"):
+            lines = lines[1:]
+        if lines and lines[-1].startswith("```"):
+            lines = lines[:-1]
+        stdout = "\n".join(lines).strip()
+    return stdout
+
+
 def _review_verdict(event: ManagerEvent) -> str | None:
-    line = _extract_prefixed_line(_review_output_text(event), "Verdict:")
+    line = _extract_prefixed_line(_review_stdout_text(event), "Verdict:")
     if not line:
         return None
     return line.split(":", 1)[1].strip().split()[0].upper()
@@ -400,10 +428,12 @@ def _review_verdict(event: ManagerEvent) -> str | None:
 def _codex_review_requires_repair(event: ManagerEvent) -> bool:
     if event.event_type != "codex-review":
         return False
+    if _review_returncode(event) != 0:
+        return True
     verdict = _review_verdict(event)
     if verdict:
         return verdict != "READY_TO_MERGE"
-    return _review_returncode(event) != 0
+    return False
 
 
 def _codex_review_was_reported(events: Sequence[ManagerEvent], review: ManagerEvent) -> bool:
@@ -440,6 +470,13 @@ def _unrepaired_codex_review_blocker(worker: WorkerSession) -> ManagerEvent | No
 
 
 def _git_line(repo: Path, args: Sequence[str]) -> str | None:
+    lines = _git_lines(repo, args)
+    if not lines:
+        return None
+    return "\n".join(lines).strip() or None
+
+
+def _git_lines(repo: Path, args: Sequence[str]) -> list[str]:
     try:
         result = subprocess.run(
             ["git", "-C", str(repo), *args],
@@ -449,11 +486,15 @@ def _git_line(repo: Path, args: Sequence[str]) -> str | None:
             check=False,
         )
     except (OSError, subprocess.TimeoutExpired):
-        return None
+        return []
     if result.returncode != 0:
-        return None
+        return []
     text = result.stdout.strip()
-    return text or None
+    return text.splitlines() if text else []
+
+
+def _repo_has_uncommitted_changes(repo: Path) -> bool:
+    return bool(_git_lines(repo, ["status", "--porcelain"]))
 
 
 def _latest_commit(repo: Path) -> tuple[str, datetime, str] | None:
@@ -488,6 +529,13 @@ def _latest_codex_review(worker: WorkerSession) -> ManagerEvent | None:
     )
 
 
+def _latest_any_codex_review(worker: WorkerSession) -> ManagerEvent | None:
+    return _latest_event(
+        [event for event in _codex_review_events(worker) if event.event_type == "codex-review"],
+        {"codex-review"},
+    )
+
+
 def _latest_worker_dispatch(worker: WorkerSession) -> ManagerEvent | None:
     return _latest_event(_codex_review_events(worker), {"worker-start", "worker-steer", "worker-resume"})
 
@@ -502,7 +550,7 @@ def _unreviewed_worker_commit(worker: WorkerSession, *, now: datetime) -> tuple[
     latest_dispatch = _latest_worker_dispatch(worker)
     if latest_dispatch is not None and committed_at < latest_dispatch.created_at:
         return None
-    latest_review = _latest_codex_review(worker)
+    latest_review = _latest_any_codex_review(worker)
     if latest_review is not None and latest_review.created_at >= committed_at:
         return None
     if _pending_codex_review(worker, now=now, since=committed_at) is not None:
@@ -667,6 +715,22 @@ def recommend_next_action(
                 reason=f"{target} already has Codex review {pending_review.id} in progress",
                 command="venv/bin/python scripts/hermes-dev-manager-events.py list",
             )
+        if worker.kind() == "codex" and worker.current_path and _repo_has_uncommitted_changes(worker.current_path):
+            return NextAction(
+                kind="supervise-interactive-worker",
+                repo=repo,
+                worker=target,
+                reason=(
+                    f"{target} has uncommitted repo changes; supervise the live "
+                    "worker before reviewing a stale commit"
+                ),
+                command=f"git -C {shlex.quote(str(worker.current_path))} status --short --branch",
+                required_evidence=(
+                    f"Before claiming the dirty worker is handled, cite a fresh "
+                    f"closeout, commit, or status artifact for `{target}` after "
+                    f"`{now.isoformat()}`."
+                ),
+            )
         unreviewed_commit = _unreviewed_worker_commit(worker, now=now)
         if unreviewed_commit:
             commit, title = unreviewed_commit
@@ -1179,9 +1243,7 @@ def _first_review_line(text: str) -> str | None:
 
 def _review_event_summary(event: ManagerEvent) -> str:
     output_path = _review_output_path(event)
-    text = ""
-    if output_path and output_path.exists():
-        text = output_path.read_text(encoding="utf-8", errors="replace")
+    text = _review_stdout_text(event)
     verdict = _extract_prefixed_line(text, "Verdict:")
     confidence = _extract_prefixed_line(text, "Confidence:")
     review_line = None if verdict else _first_review_line(text)
@@ -1313,6 +1375,16 @@ def execute_safe_action(packet: ManagerPacket, *, report_dir: Path) -> SafeExecu
     return _dispatch_codex_review(packet, report_dir=report_dir)
 
 
+def _should_print_execution_result(
+    result: SafeExecutionResult,
+    *,
+    quiet_routine: bool,
+) -> bool:
+    if quiet_routine and result.ok and result.kind in ROUTINE_TELEGRAM_KINDS:
+        return False
+    return result.notify
+
+
 def packet_to_json(packet: ManagerPacket) -> str:
     data = {
         "generated_at": packet.generated_at.isoformat(),
@@ -1438,7 +1510,10 @@ def main(argv: list[str] | None = None) -> int:
                     sort_keys=True,
                 )
             )
-    elif execution_result and execution_result.notify:
+    elif execution_result and _should_print_execution_result(
+        execution_result,
+        quiet_routine=args.quiet_routine,
+    ):
         print(execution_result.summary)
     elif not execution_result:
         summary = telegram_summary(packet, quiet_routine=args.quiet_routine)
diff --git a/tests/cli/test_dev_manager_next_action.py b/tests/cli/test_dev_manager_next_action.py
index 587003966..cdbb1e809 100644
--- a/tests/cli/test_dev_manager_next_action.py
+++ b/tests/cli/test_dev_manager_next_action.py
@@ -10,6 +10,8 @@ from hermes_cli import kanban_db as kb
 from hermes_cli.dev_manager_next_action import (
     ManagerPacket,
     NextAction,
+    SafeExecutionResult,
+    _should_print_execution_result,
     collect_board_snapshots,
     collect_manager_packet,
     execute_safe_action,
@@ -34,6 +36,14 @@ def isolated_kanban_home(tmp_path, monkeypatch):
     return home
 
 
+@pytest.fixture(autouse=True)
+def clean_repo_status_by_default(monkeypatch):
+    monkeypatch.setattr(
+        "hermes_cli.dev_manager_next_action._repo_has_uncommitted_changes",
+        lambda repo: False,
+    )
+
+
 def _preflight(now: datetime, *, failing: bool = False) -> PreflightResult:
     profile = ProfilePreflight(
         name="orchestrator",
@@ -202,6 +212,48 @@ def test_next_action_runs_codex_review_for_unreviewed_worker_commit(isolated_kan
     assert "--title 'demo commit'" in packet.next_action.command
 
 
+def test_next_action_supervises_dirty_codex_worker_before_review(isolated_kanban_home, monkeypatch):
+    now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
+    repo = Path("/srv/dev/repos/ludeme")
+    worker = WorkerSession(
+        session_name="ludeme-codex",
+        window_index="0",
+        pane_index="0",
+        pane_pid=123,
+        current_command="node",
+        current_path=repo,
+        active=True,
+        dead=False,
+        title="codex",
+    )
+    monkeypatch.setattr("hermes_cli.dev_manager_next_action._repo_has_uncommitted_changes", lambda repo: True)
+    monkeypatch.setattr(
+        "hermes_cli.dev_manager_next_action._latest_commit",
+        lambda repo: (
+            "abcdef1234567890",
+            datetime(2026, 6, 2, 1, 44, tzinfo=timezone.utc),
+            "demo commit",
+        ),
+    )
+    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: [])
+    monkeypatch.setattr("hermes_cli.dev_manager_next_action.capture_worker_pane", lambda target: [])
+
+    packet = collect_manager_packet(
+        now=now,
+        repo_roots=[],
+        stale_after=timedelta(minutes=30),
+        recent_after=now - timedelta(hours=12),
+        preflight=_preflight(now),
+        runs=[],
+        workers=[worker],
+    )
+
+    assert packet.next_action.kind == "supervise-interactive-worker"
+    assert packet.next_action.worker == "ludeme-codex:0.0"
+    assert "uncommitted repo changes" in packet.next_action.reason
+    assert "status --short --branch" in packet.next_action.command
+
+
 def test_next_action_does_not_duplicate_reviewed_worker_commit(isolated_kanban_home, monkeypatch):
     now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
     repo = Path("/srv/dev/repos/ludeme")
@@ -249,7 +301,7 @@ def test_next_action_does_not_duplicate_reviewed_worker_commit(isolated_kanban_h
     assert packet.next_action.kind == "supervise-interactive-worker"
 
 
-def test_next_action_retries_failed_codex_review_event(isolated_kanban_home, monkeypatch):
+def test_next_action_reports_failed_codex_review_event_before_repair(isolated_kanban_home, monkeypatch):
     now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
     repo = Path("/srv/dev/repos/ludeme")
     worker = WorkerSession(
@@ -293,8 +345,8 @@ def test_next_action_retries_failed_codex_review_event(isolated_kanban_home, mon
         workers=[worker],
     )
 
-    assert packet.next_action.kind == "codex-review-worker"
-    assert packet.next_action.review_commit == "abcdef1234567890"
+    assert packet.next_action.kind == "report-codex-review-result"
+    assert packet.next_action.review_event_id == "review"
 
 
 def test_next_action_waits_for_pending_codex_review(isolated_kanban_home, monkeypatch):
@@ -572,6 +624,75 @@ def test_next_action_steers_worker_after_reported_blocked_codex_review(isolated_
     assert packet.next_action.human_action().startswith("None by default")
 
 
+def test_next_action_repairs_failed_review_without_repeating_same_commit(isolated_kanban_home, monkeypatch, tmp_path):
+    now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
+    repo = Path("/srv/dev/repos/ludeme")
+    output = tmp_path / "codex-review-output.md"
+    output.write_text(
+        "# Codex Run Review\n\n"
+        "## Stdout\n\n"
+        "- empty\n\n"
+        "## Stderr\n\n"
+        "```text\n"
+        "Output exactly this shape:\n"
+        "Verdict: READY_TO_MERGE | FIX_FIRST | BLOCKED\n"
+        "bwrap: loopback: Failed RTM_NEWADDR\n"
+        "```\n",
+        encoding="utf-8",
+    )
+    worker = WorkerSession(
+        session_name="ludeme-codex",
+        window_index="0",
+        pane_index="0",
+        pane_pid=123,
+        current_command="node",
+        current_path=repo,
+        active=True,
+        dead=False,
+        title="codex",
+    )
+    events = [
+        ManagerEvent(
+            id="review",
+            created_at=datetime(2026, 6, 2, 1, 44, tzinfo=timezone.utc),
+            event_type="codex-review",
+            repo=str(repo),
+            worker_session="ludeme-codex",
+            intent="review",
+            resulting_artifacts=[str(output)],
+            notes="codex review returncode=1",
+        ),
+        ManagerEvent(
+            id="report",
+            created_at=datetime(2026, 6, 2, 1, 44, 30, tzinfo=timezone.utc),
+            event_type="codex-review-report",
+            repo=str(repo),
+            worker_session="ludeme-codex",
+            intent="report",
+            notes="reported_event=review returncode=1",
+        ),
+    ]
+    monkeypatch.setattr(
+        "hermes_cli.dev_manager_next_action._latest_commit",
+        lambda repo: ("abcdef1234567890", datetime(2026, 6, 2, 1, 43, tzinfo=timezone.utc), "demo"),
+    )
+    monkeypatch.setattr("hermes_cli.dev_manager_next_action.matching_events", lambda **kwargs: events)
+    monkeypatch.setattr("hermes_cli.dev_manager_next_action.capture_worker_pane", lambda target: [])
+
+    packet = collect_manager_packet(
+        now=now,
+        repo_roots=[],
+        stale_after=timedelta(minutes=30),
+        recent_after=now - timedelta(hours=12),
+        preflight=_preflight(now),
+        runs=[],
+        workers=[worker],
+    )
+
+    assert packet.next_action.kind == "repair-codex-review-blocker"
+    assert packet.next_action.review_event_id == "review"
+
+
 def test_next_action_does_not_repeat_codex_review_repair_dispatch(isolated_kanban_home, monkeypatch, tmp_path):
     now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
     repo = Path("/srv/dev/repos/ludeme")
@@ -939,6 +1060,25 @@ def test_execute_safe_action_reports_completed_codex_review(monkeypatch, tmp_pat
     assert "reported_event=review" in str(events[0].notes)
 
 
+def test_quiet_routine_suppresses_review_report_telegram_output():
+    routine = SafeExecutionResult(
+        kind="report-codex-review-result",
+        ok=True,
+        summary="Codex review ready.\nVerdict: BLOCKED",
+        notify=True,
+    )
+    failure = SafeExecutionResult(
+        kind="report-codex-review-result",
+        ok=False,
+        summary="Codex review report failed.",
+        notify=True,
+    )
+
+    assert _should_print_execution_result(routine, quiet_routine=True) is False
+    assert _should_print_execution_result(routine, quiet_routine=False) is True
+    assert _should_print_execution_result(failure, quiet_routine=True) is True
+
+
 def test_execute_safe_action_reports_attention_without_cron_failure(monkeypatch, tmp_path):
     now = datetime(2026, 6, 2, 1, 45, tzinfo=timezone.utc)
     repo = tmp_path / "repo"

exec
/bin/bash -lc 'git status --short --untracked-files=all' in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
 M hermes_cli/dev_manager_next_action.py
 M tests/cli/test_dev_manager_next_action.py
?? .auto/orchestrator/velocity-steward-20260602T132815Z/codex-velocity-output.md
?? .auto/orchestrator/velocity-steward-20260602T132815Z/run-codex-velocity.sh
?? .auto/orchestrator/velocity-steward-20260602T132815Z/velocity-prompt.md
?? .gbrain-source

codex
The final diff is limited to the manager next-action logic and its tests. I’m committing this as a verified slice: dirty Codex workers get supervised before stale-review dispatch, failed review results are reported/handled without duplicate reviews, and routine review reports respect quiet mode.
exec
/bin/bash -lc 'git add hermes_cli/dev_manager_next_action.py tests/cli/test_dev_manager_next_action.py && git diff --cached --stat' in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
 hermes_cli/dev_manager_next_action.py     |  97 +++++++++++++++++---
 tests/cli/test_dev_manager_next_action.py | 146 +++++++++++++++++++++++++++++-
 2 files changed, 229 insertions(+), 14 deletions(-)

codex
The staged set is exactly the two verified files. I’m creating the commit now, then I’ll confirm the final worktree state and commit SHA.
exec
/bin/bash -lc 'git commit -m "fix: supervise dirty Codex review workers"' in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
[rk/daily-dev-teaching-report f019947d7] fix: supervise dirty Codex review workers
 2 files changed, 229 insertions(+), 14 deletions(-)

codex
Commit succeeded. I’m doing the closing checks now: status, latest commit summary, and no accidental staged changes.
exec
/bin/bash -lc 'git diff --cached --stat' in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
exec
/bin/bash -lc 'git status --short --untracked-files=all' in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
?? .auto/orchestrator/velocity-steward-20260602T132815Z/codex-velocity-output.md
?? .auto/orchestrator/velocity-steward-20260602T132815Z/run-codex-velocity.sh
?? .auto/orchestrator/velocity-steward-20260602T132815Z/velocity-prompt.md
?? .gbrain-source

exec
/bin/bash -lc 'git show --stat --oneline --no-renames HEAD' in /srv/dev/repos/hermes-agent
 succeeded in 0ms:
f019947d7 fix: supervise dirty Codex review workers
 hermes_cli/dev_manager_next_action.py     |  97 +++++++++++++++++---
 tests/cli/test_dev_manager_next_action.py | 146 +++++++++++++++++++++++++++++-
 2 files changed, 229 insertions(+), 14 deletions(-)

codex
Committed verified slice `f019947d7 fix: supervise dirty Codex review workers`.

Changed [dev_manager_next_action.py](/srv/dev/repos/hermes-agent/hermes_cli/dev_manager_next_action.py) and [test_dev_manager_next_action.py](/srv/dev/repos/hermes-agent/tests/cli/test_dev_manager_next_action.py) to:
- supervise dirty live Codex workers before dispatching stale commit reviews
- report failed Codex review results before repair
- avoid treating review stderr template text as a verdict
- suppress successful routine review reports under `--quiet-routine`

Validation passed:
- `scripts/run_tests.sh tests/cli/test_dev_manager_next_action.py` -> 25 passed
- `git diff --check`
- `venv/bin/python scripts/hermes-dev-manager-next-action.py --json`

Final status has no tracked or staged changes. I left the pre-existing untracked local artifacts untouched: `.auto/...` and `.gbrain-source`. The initially observed untracked digest files disappeared before staging, so I did not recreate or commit them.
tokens used
81,616
Committed verified slice `f019947d7 fix: supervise dirty Codex review workers`.

Changed [dev_manager_next_action.py](/srv/dev/repos/hermes-agent/hermes_cli/dev_manager_next_action.py) and [test_dev_manager_next_action.py](/srv/dev/repos/hermes-agent/tests/cli/test_dev_manager_next_action.py) to:
- supervise dirty live Codex workers before dispatching stale commit reviews
- report failed Codex review results before repair
- avoid treating review stderr template text as a verdict
- suppress successful routine review reports under `--quiet-routine`

Validation passed:
- `scripts/run_tests.sh tests/cli/test_dev_manager_next_action.py` -> 25 passed
- `git diff --check`
- `venv/bin/python scripts/hermes-dev-manager-next-action.py --json`

Final status has no tracked or staged changes. I left the pre-existing untracked local artifacts untouched: `.auto/...` and `.gbrain-source`. The initially observed untracked digest files disappeared before staging, so I did not recreate or commit them.

[hermes] codex velocity worker exited with 0 at 2026-06-02T17:31:50Z
