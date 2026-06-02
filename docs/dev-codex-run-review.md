# Dev Codex Run Review

The manager role is defined in
[`dev-orchestrator-manager-contract.md`](dev-orchestrator-manager-contract.md).
Hermes orchestrates this workflow; Codex produces the code-review verdict.

Use this helper after a supervised Codex/autodev implementation run reaches a
completion marker, quiet terminal state, or review handoff.

## Install

```bash
cd /srv/dev/repos/hermes-agent
install -m 0755 scripts/hermes-dev-codex-run-review.py ~/.hermes/scripts/dev-codex-run-review.py
```

## Review Uncommitted Worker Delta

```bash
~/.hermes/scripts/dev-codex-run-review.py \
  --repo /srv/dev/repos/ludeme \
  --session ludeme-codex
```

## Review A Commit

```bash
~/.hermes/scripts/dev-codex-run-review.py \
  --repo /srv/dev/repos/ludeme \
  --session ludeme-codex \
  --commit <sha> \
  --title "commit title"
```

## Output Contract

Each run writes:

- `~/.hermes/reports/codex-run-review-<repo>-<session>-YYYYMMDDTHHMMSSZ/codex-review-prompt.md`
- `~/.hermes/reports/codex-run-review-<repo>-<session>-YYYYMMDDTHHMMSSZ/codex-review-output.md`
- `~/.hermes/reports/codex-run-review-<repo>-<session>-YYYYMMDDTHHMMSSZ/dev-supervision-closeout-*.md`
- a `codex-review` manager event in `~/.hermes/reports/dev-manager-events.jsonl`

The Telegram-ready summary relays Codex's verdict line and confidence line when
they are present. Hermes should not rewrite the verdict.

## Operator Rule

Receipt grade is not merge-readiness. A `verified` receipt means the run left
fresh machine evidence. A Codex review pass decides whether the code is
`READY_TO_MERGE`, needs `FIX_FIRST`, or is `BLOCKED`.
