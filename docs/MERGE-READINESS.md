# Merge-Readiness — `rk/daily-dev-teaching-report`

Assessment date: 2026-06-17. Branch is ~45 commits ahead of `main`.
Author of this note: conservative verify-and-harden pass (Claude Opus 4.8).
Scope of this pass: VERIFY and HARDEN only. No risky merges, no scope
expansion, no deploy/push.

## What this branch contains

Four feature clusters, in commit order (oldest → newest):

1. **Daily development teaching report** (cron wrapper, orchestrator phase
   history, mastery-check prompts gated by stage, Telegram routing).
2. **Dev-manager next-action packet** (`hermes_cli/dev_manager_next_action.py`)
   — manager events, receipt verification (kanban/repo/gbrain/github/auto),
   goal-continuation packets, supervised tmux worker closeouts.
3. **Codex review automation** — async dispatch, structured prompts, the
   review gate, steering workers after blocked reviews, dirty-worker
   supervision.
4. **Kanban WIP rescue + board crash circuit breaker** (newest, the headline
   WIP): `hermes_cli/kanban_db.py::_rescue_workspace_wip` and
   `check_board_crash_breaker`, plus dispatch-discipline skill rules.

## Test status on this branch (verified)

Run via `scripts/run_tests.sh` (CI-parity: per-file subprocess isolation,
`env -i`, TZ=UTC, PYTHONHASHSEED=0) against the repo `venv` (Python 3.11.15).
Full ~17k-test suite was NOT run (time/disk budget — disk at 90%, ~56 GB
free). Targeted the modules the feature branch touches:

| Test file | Result |
|---|---|
| `tests/hermes_cli/test_kanban_rescue_and_breaker.py` | 10 passed (9 existing + 1 added here) |
| `tests/cli/test_dev_manager_next_action.py` | 27 passed |
| `tests/tools/test_memory_tool.py` | 68 passed |
| `tests/tools/test_memory_tool_schema.py` | 3 passed |
| `tests/agent/test_subdirectory_hints.py` | 25 passed |

All green. No failures attributable to the WIP in the modules exercised.

## Real-tool smoke test (verified)

- `python -m hermes_cli.main --help` → renders, lists all subcommands
  including `kanban`.
- `hermes kanban --help` → lists dispatch/boards/pause/resume/runs/etc.
- Against an isolated temp `HERMES_HOME` (real `~/.hermes` untouched):
  `kanban init` created the DB and seeded skills; `kanban boards list`
  rendered the board table; `kanban list` returned cleanly.

The CLI works for an end user.

## What is verified vs. still WIP

**Verified solid:**
- The kanban rescue helper is best-effort and never raises (defensive
  `try/except` + index restore in a `finally`). Covered for clean-tree skip,
  build-dir exclusion, idempotent rescue-section replacement, scratch/spawn
  skip, and (added this pass) the no-commits/no-HEAD repo path.
- The board crash circuit breaker is correctly **per-board scoped**: each
  board is its own `kanban.db` file (default board → `<root>/kanban.db` for
  pre-boards back-compat; others → `<root>/kanban/boards/<slug>/kanban.db`),
  so the `task_runs` query over one connection cannot leak runs between
  boards. The "across the whole board" docstring is accurate. Covered for
  trip-on-threshold, hold-below/mixed, paused-board-skips-spawn, and
  dispatch-trips-then-skips.
- Dev-manager kanban dispatch (`_dispatch_kanban_action`) is tested for the
  spawn and no-spawn paths.

**Still WIP / uncommitted in the working tree (NOT attributable to this
verify pass; deliberately left untouched):**
- `hermes_cli/dev_manager_next_action.py` + `tests/cli/test_dev_manager_next_action.py`
  — adds `_dispatch_kanban_action` for `dispatch-task`/`run-review`. Tests
  pass, but the change is uncommitted local WIP.
- `hermes_cli/config.py` + `tools/memory_tool.py` — memory char-limit
  defaults raised (memory 2200→12000, user 1375→4000). **No regression test
  pins these defaults**; this is an un-gated behavior/cost change (larger
  memory injected into every prompt). Should be reviewed and committed
  separately with intent, not swept into a merge.
- `agent/subdirectory_hints.py` — defensive fix for unset/unresolvable
  `$HOME` during `~`-expansion (incident 2026-06-03). Tests pass.

## Honest go / no-go

**NO-GO for merge to `main` as-is**, for governance/process reasons (not
because the tested code is broken):

1. The working tree carries uncommitted, un-gated WIP (notably the memory
   char-limit bump) mixed in with verified feature work. Merging now would
   either drop that WIP or merge it without review.
2. Only a targeted test subset was run. A full-suite green (or at least the
   full `tests/hermes_cli/`, `tests/cli/`, `tests/gateway/` roots) is the
   minimum bar before merging 45 commits that add durable state (manager
   events, kanban snapshots/runs).
3. New durable state added on this branch (manager events table, `task_runs`,
   board metadata, rescue branches) has functional coverage but **no
   explicit forward/back-compat migration test** against a pre-branch DB.

**Path to GO:** (a) land or revert the uncommitted working-tree changes
deliberately; (b) run the full suite (or the three roots above) and confirm
green; (c) add one DB-migration/back-compat test opening a pre-branch
`kanban.db`. None of these are blockers on the feature logic itself, which
is well-structured and well-tested.
