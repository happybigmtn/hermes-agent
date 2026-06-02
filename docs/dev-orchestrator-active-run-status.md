# Dev Orchestrator Active Run Status

Hermes can run a deterministic status reporter for active autodev/pilot runs.
This is the operator-facing complement to the daily teaching report: the daily
report explains what landed after commits exist, while this reporter explains
what is happening during a long-running campaign.

The reporter is intentionally no-agent. It reads process state, tmux worker
panes, `.auto` run artifacts, repo-level `gen-*` artifacts, git status, and
gbrain availability. It does not ask a model to infer progress.

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
- `HERMES_DEV_RUN_STATUS_DASHBOARD_URL`: externally visible dashboard URL. If
  unset, the reporter uses the first `tailscale ip -4` address and serves
  `dev-run-status-latest.html` on port `8765`.

## Tailscale Dashboard

The orchestrator can expose the latest report through a small static dashboard
bound to the tailnet address only:

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/dev-run-status-dashboard.service <<'EOF'
[Unit]
Description=Dev orchestrator status dashboard
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 -m http.server 8765 --bind 100.70.49.26 --directory /home/dev/.hermes/reports
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now dev-run-status-dashboard.service
```

Replace `100.70.49.26` with the current `tailscale ip -4` value if the node is
renamed or rebuilt. The Telegram summary includes the dashboard link when the
URL is configured or discoverable.

## Output Contract

Each run writes:

- `~/.hermes/reports/dev-run-status-YYYYMMDD-HHMMSS.md`
- `~/.hermes/reports/dev-run-status-latest.md`
- `~/.hermes/reports/dev-run-status-latest.html`
- gbrain page `dev-orchestrator-active-run-status`, when `gbrain` is available.

The Telegram summary includes:

- active run count
- attention count
- interactive tmux worker count
- dashboard URL or dashboard file path
- repo/run/phase for each active or recent run
- latest useful artifact
- whether the repo is dirty
- next operator action

## Interactive Worker Supervision

Hermes supervises interactive Codex, Claude, and autodev work through named
tmux sessions. The session name is the durable handle that lets Hermes report
state and send concise steer when needed.

For Codex on Ludeme:

```bash
ssh orch
tmux new -As ludeme-codex -c /srv/dev/repos/ludeme
codex --yolo
```

Hermes sees that pane in the active-run dashboard as a worker row with:

- target, for example `ludeme-codex:0.0`
- inferred kind, for example `codex`
- pane command and current path
- steering command, for example
  `tmux send-keys -t ludeme-codex:0.0 '<message>' C-m`

Use this pattern for other repos:

```bash
tmux new -As <repo>-codex -c /srv/dev/repos/<repo>
tmux new -As <repo>-claude -c /srv/dev/repos/<repo>
tmux new -As <repo>-auto -c /srv/dev/repos/<repo>
```

The operator should tell Hermes which tmux session owns the campaign. Hermes
should then treat stale or idle panes as supervision targets, not as missing
work.

## Operator Rule

If a campaign is active and the latest useful artifact is older than the stale
threshold, the next action is not to start another broad run. First inspect the
named process, tmux pane, run root, and latest generated artifact. Then either
wait with an
explicit reason, stop the stuck process, or resume from the latest artifact with
a narrower command.
