# Dev Supervision Closeout

Hermes can close out an interactive Codex, Claude, or autodev tmux worker with
deterministic evidence. This complements active-run status:

- active-run status answers: what is currently running?
- supervision closeout answers: what evidence did this worker leave behind?

The command is intentionally no-agent. It captures tmux pane output, repo git
state, recent commits, recent `.auto` and `gen-*` artifacts, writes a Markdown
report, attaches matching manager events and Hermes transcript snippets, and
optionally writes a gbrain page.

## Install

```bash
cd /srv/dev/repos/hermes-agent
install -m 0755 scripts/hermes-dev-supervision-closeout.py ~/.hermes/scripts/dev-supervision-closeout.py
```

## Ludeme Codex Closeout

```bash
~/.hermes/scripts/dev-supervision-closeout.py \
  --repo /srv/dev/repos/ludeme \
  --session ludeme-codex
```

The Telegram-ready summary includes:

- repo path
- tmux session / worker target
- branch
- clean/dirty repo state
- pane capture line count
- manager-event count
- steer-history snippet count
- Markdown report path
- dashboard URL
- gbrain slug, when synced

## Output Contract

Each run writes:

- `~/.hermes/reports/dev-supervision-closeout-<repo>-YYYYMMDD-HHMMSS.md`
- gbrain page `dev-supervision-closeout-<repo>-YYYY-MM-DD-HHMMSS`, unless
  `--no-gbrain` is passed.

The Markdown report includes:

- worker target, kind, state, command, and path
- branch, `git status --short`, recent commits, and diff stat
- recent `.auto`, `gen-*`, and `genesis` artifacts
- matching manager events from `~/.hermes/reports/dev-manager-events.jsonl`
- matching Hermes transcript snippets from root/orchestrator `state.db`
- captured tmux pane output
- deterministic manager assessment and next bottleneck

To add explicit transcript search terms:

```bash
~/.hermes/scripts/dev-supervision-closeout.py \
  --repo /srv/dev/repos/nullspaceton \
  --session nullspaceton-codex \
  --steer-query "WATER-001" \
  --steer-query "dev orchestrator"
```

Use `--no-steer-history` only when debugging the closeout command itself.

## Manager Event Ledger

Record an explicit manager action before launching or steering a worker:

```bash
~/.hermes/scripts/dev-manager-events.py record \
  --repo /srv/dev/repos/nullspaceton \
  --worker-session nullspaceton-codex \
  --intent "Run autodev corpus/gen, then execute the queued winner WATER slice." \
  --artifact .auto/orchestrator/nullspaceton-codex-20260602T033603Z/prompt.md
```

List recent matching events:

```bash
~/.hermes/scripts/dev-manager-events.py list \
  --repo /srv/dev/repos/nullspaceton \
  --worker-session nullspaceton-codex
```

The default ledger is `~/.hermes/reports/dev-manager-events.jsonl`.

## Operator Rule

Run closeout before claiming a supervised interactive worker made progress. A
clean closeout is not proof of success; it is a durable evidence bundle. Use
the captured git status, commits, tests, artifacts, and pane output to decide
whether to continue, steer, review, or commit.

## Current Gap

The closeout command can cite explicit manager events, but Hermes gateway and
tmux launch paths still need to record those events automatically at dispatch
time. Until that integration is complete, call `dev-manager-events.py record`
when launching or steering long-running workers.
