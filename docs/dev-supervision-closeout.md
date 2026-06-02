# Dev Supervision Closeout

Hermes can close out an interactive Codex, Claude, or autodev tmux worker with
deterministic evidence. This complements active-run status:

- active-run status answers: what is currently running?
- supervision closeout answers: what evidence did this worker leave behind?

The command is intentionally no-agent. It captures tmux pane output, repo git
state, recent commits, recent `.auto` and `gen-*` artifacts, writes a Markdown
report, attaches matching Hermes transcript snippets, and optionally writes a
gbrain page.

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

## Operator Rule

Run closeout before claiming a supervised interactive worker made progress. A
clean closeout is not proof of success; it is a durable evidence bundle. Use
the captured git status, commits, tests, artifacts, and pane output to decide
whether to continue, steer, review, or commit.

## Current Gap

The command captures matching transcript snippets, but it does not yet have a
first-class manager-event registry. The next improvement is to persist explicit
`worker_session`, `repo`, and `intent` fields when Hermes launches or steers a
tmux worker so closeout can cite exact manager actions rather than relying on
transcript search.
