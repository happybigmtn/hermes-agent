# Daily Development Teaching Report

Hermes can run a deterministic daily report that inventories GitHub commits,
builds a teaching artifact, writes a PDF, updates a running human-understanding
checklist, and sends a Telegram-ready message with the PDF attached.

This is intentionally a no-agent cron job. GitHub is the source of commit facts;
the report generator turns those facts into a teaching surface without asking a
model to remember or infer what landed.

When local repo checkouts have `pilot-dev` phase artifacts, the report also
includes orchestrator phase evidence from
`.auto/orchestrator/*/phase-history.jsonl` or `phase-heartbeat.json`. This lets
the daily PDF teach in-flight supervised work in addition to landed commits.

## Install On The Orchestrator

```bash
cd /srv/dev/repos/hermes-agent
install -m 0755 scripts/hermes-daily-dev-report.py ~/.hermes/scripts/daily-dev-report.py
install -m 0755 scripts/hermes-daily-mastery-check.py ~/.hermes/scripts/daily-mastery-check.py
install -m 0755 scripts/hermes-daily-mastery-answer.py ~/.hermes/scripts/mastery-check-answer.py
hermes cron create "0 13 * * *" \
  --name daily-dev-teaching-report \
  --script daily-dev-report.py \
  --no-agent \
  --deliver telegram
hermes cron create "15 13 * * *" \
  --name daily-dev-mastery-check \
  --script daily-mastery-check.py \
  --no-agent \
  --deliver telegram
```

The script uses `gh auth token`, so the machine running the job must already be
authenticated with GitHub.

## Configuration

Environment variables read by the script:

- `HERMES_DAILY_REPORT_OWNER`: GitHub user or org to scan.
- `HERMES_DAILY_REPORT_REPOS`: comma-separated `owner/repo` allowlist.
- `HERMES_DAILY_REPORT_LOOKBACK_HOURS`: default `24`.
- `HERMES_DAILY_REPORT_TIMEZONE`: default `America/New_York`.
- `HERMES_DAILY_REPORT_DIR`: default `~/.hermes/reports`.
- `HERMES_DAILY_REPORT_REPO_ROOTS`: comma-separated roots for local clones; default `/srv/dev/repos`, `~/coding`, and `~/Coding`.
- `GH_TOKEN` or `GITHUB_TOKEN`: optional explicit GitHub token override.

Without an owner or repo allowlist, the script scans repositories visible to the
authenticated GitHub account via `/user/repos`. Owner scans first filter to repos
updated during the report window, then prefer matching local clones so all refs
from the repo's own GitHub remote can be included without scanning every remote
repository branch one by one.

## Output Contract

Each run writes:

- `~/.hermes/reports/daily-dev-report-YYYYMMDD-HHMMSS.md`
- `~/.hermes/reports/daily-dev-report-YYYYMMDD-HHMMSS.pdf`
- `~/.hermes/reports/HUMAN-UNDERSTANDING-CHECKLIST.md`
- gbrain page `daily-development-teaching-report-YYYY-MM-DD`, when `gbrain` is available.
- gbrain page `development-human-understanding-checklist`, when `gbrain` is available.

The cron stdout is a compact Telegram-ready summary ending with `MEDIA:<pdf>`.
Hermes cron delivery strips that tag and sends the PDF as a native attachment.
The summary includes the number of local orchestrator phase histories found in
the reporting window.

The mastery-check cron runs shortly after the PDF. It reads the latest
`HUMAN-UNDERSTANDING-CHECKLIST.md` section, writes
`daily-mastery-check-YYYYMMDD-HHMMSS.md`, syncs the latest prompt to gbrain page
`development-mastery-check-latest`, and sends a Telegram prompt asking the human
to answer the first unresolved checklist stage before the next broad
orchestration run. Later stages remain visible, but the prompt deliberately
keeps only one current stage active so the teaching loop is incremental instead
of a single end-of-day quiz.

When the human answers, pipe that answer into:

```bash
~/.hermes/scripts/mastery-check-answer.py --answer-file -
```

The answer command writes `daily-mastery-answer-YYYYMMDD-HHMMSS.md`, updates the
running checklist only when the answer names concrete evidence, syncs the answer
and checklist to gbrain, and prints a Telegram-ready summary naming the next
unresolved mastery stage. Vague answers are recorded but do not mark the stage
complete.

## Teaching Standard

The report is written for comprehension, not status theater. For every active
repository it asks the human to understand:

- Problem: what changed and why the prior state was insufficient.
- Branches: which branches carried the work.
- Solution: why the resolution fits the codebase.
- Edge cases: what tests, CI, or proof boundaries should protect the change.
- Orchestrator: which phase artifacts prove active planning/execution/closeout
  state, and what next operator action follows from them.
- Context: what future work this enables or blocks.

The checklist is deliberately persistent. Do not mark an item complete until the
human can explain it in her own words.

The mastery prompt is deliberately active. Its job is to stop the workflow from
silently moving from "report generated" to "understanding achieved." The human
should answer the current stage with concrete repos, branches, commits,
artifacts, tests, and blockers. Hermes should leave later stages unresolved
until the current answer is concrete enough to show real understanding.

The answer recorder is intentionally conservative. By default it requires a
repo or repo path plus at least two other evidence categories such as commit,
branch, artifact, validation, or risk. Use `--force` only when a human reviewer
has decided that the answer is sufficient despite the heuristic warning.

## Manual Verification

```bash
python -m hermes_cli.daily_dev_report \
  --owner happybigmtn \
  --lookback-hours 24 \
  --out-dir /tmp/hermes-daily-report \
  --no-gbrain

printf '%s\n' "The problem in happybigmtn/hermes-agent was ... commit 95e420dd1 ... tests passed ..." \
  | python -m hermes_cli.daily_mastery_answer \
      --checklist /tmp/hermes-daily-report/HUMAN-UNDERSTANDING-CHECKLIST.md \
      --out-dir /tmp/hermes-daily-report \
      --answer-file - \
      --no-gbrain
```

Open the generated PDF and confirm the Telegram summary includes the PDF path as
a `MEDIA:` tag. Then confirm the answer command marks only the first unchecked
checklist item and leaves the next stage unresolved.
