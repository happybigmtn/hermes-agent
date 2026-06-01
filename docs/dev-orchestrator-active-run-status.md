# Dev Orchestrator Active Run Status

Hermes can run a deterministic status reporter for active autodev/pilot runs.
This is the operator-facing complement to the daily teaching report: the daily
report explains what landed after commits exist, while this reporter explains
what is happening during a long-running campaign.

The reporter is intentionally no-agent. It reads process state, `.auto` run
artifacts, repo-level `gen-*` artifacts, git status, and gbrain availability. It
does not ask a model to infer progress.

## Install On The Orchestrator

```bash
cd /srv/dev/repos/hermes-agent
install -m 0755 scripts/hermes-dev-run-status.py ~/.hermes/scripts/dev-run-status.py
hermes cron create "every 30m" \
  --name dev-orchestrator-run-status \
  --script dev-run-status.py \
  --no-agent \
  --deliver telegram
```

## Configuration

Environment variables read by the script:

- `HERMES_DEV_RUN_STATUS_REPO_ROOTS`: comma-separated repo roots; default `/srv/dev/repos`, `~/coding`, and `~/Coding`.
- `HERMES_DEV_RUN_STATUS_DIR`: output directory; default `~/.hermes/reports`.
- `HERMES_DEV_RUN_STATUS_STALE_MINUTES`: active-run artifact staleness threshold; default `30`.
- `HERMES_DEV_RUN_STATUS_RECENT_HOURS`: include inactive recent runs this many hours back; default `2`.
- `HERMES_DEV_RUN_STATUS_GBRAIN_SLUG`: gbrain page for latest status; default `dev-orchestrator-active-run-status`.

## Output Contract

Each run writes:

- `~/.hermes/reports/dev-run-status-YYYYMMDD-HHMMSS.md`
- gbrain page `dev-orchestrator-active-run-status`, when `gbrain` is available.

The Telegram summary includes:

- active run count
- attention count
- repo/run/phase for each active or recent run
- latest useful artifact
- whether the repo is dirty
- next operator action

## Operator Rule

If a campaign is active and the latest useful artifact is older than the stale
threshold, the next action is not to start another broad run. First inspect the
amed process, run root, and latest generated artifact. Then either wait with an
explicit reason, stop the stuck process, or resume from the latest artifact with
a narrower command.
