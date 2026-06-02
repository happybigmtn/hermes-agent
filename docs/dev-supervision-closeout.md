# Dev Supervision Closeout

Hermes can close out an interactive Codex, Claude, or autodev tmux worker with
deterministic evidence. This complements active-run status:

- active-run status answers: what is currently running?
- supervision closeout answers: what evidence did this worker leave behind?

The command is intentionally no-agent. It captures tmux pane output, repo git
state, recent commits, recent `.auto` and `gen-*` artifacts, writes a Markdown
report, and optionally writes a gbrain page.

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
- captured tmux pane output
- deterministic manager assessment and next bottleneck

## Operator Rule

Run closeout before claiming a supervised interactive worker made progress. A
clean closeout is not proof of success; it is a durable evidence bundle. Use
the captured git status, commits, tests, artifacts, and pane output to decide
whether to continue, steer, review, or commit.

## Current Gap

The command captures pane and repo evidence, but it does not yet capture Hermes
Telegram steer history. The next improvement is to attach gateway steer events
to the closeout so gbrain remembers not only what the worker did, but what the
manager asked it to do.
