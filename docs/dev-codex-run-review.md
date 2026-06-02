# Dev Codex Run Review

The manager role is defined in
[`dev-orchestrator-manager-contract.md`](dev-orchestrator-manager-contract.md).
Hermes orchestrates this workflow; Codex produces the code-review verdict.

The helper invokes `codex exec review` with the saved review prompt, so commit
reviews and uncommitted reviews both use the same structured verdict contract.

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

## Auto-Execute From Manager Tick

The manager tick can execute this gate automatically for live supervised Codex
workers. The primary trigger is a new latest commit in the worker's repo that
does not have a newer `codex-review` manager event. The fallback trigger is an
explicit review-ready marker such as `ready for review`, `review-ready`,
`please review`, `implementation complete`, or `handoff ready`.

```bash
~/.hermes/scripts/dev-manager-next-action.py
```

The installed wrapper defaults to safe autopilot mode, equivalent to
`--execute-safe --quiet-routine`. `--execute-safe` currently runs only this Codex
review gate. Hermes starts the review in a detached tmux session, records a
`codex-review-start` event with the tmux session and launch artifacts, stays
quiet while the review is running, then surfaces the finished `codex-review`
event once and records `codex-review-report`. Hermes still does not write the
verdict; it relays Codex's review output. This is not a global "review every
pushed commit" hook; it is scoped to the supervised Codex worker Hermes is
currently managing.

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
- `~/.hermes/reports/codex-review-dispatch-<repo>-<session>-YYYYMMDD-HHMMSS/codex-review-tmux.log`
- `~/.hermes/reports/codex-review-dispatch-<repo>-<session>-YYYYMMDD-HHMMSS/run-codex-review.sh`
- a `codex-review` manager event in `~/.hermes/reports/dev-manager-events.jsonl`

The Telegram-ready summary relays Codex's verdict line and confidence line when
they are present. Hermes should not write or rewrite the verdict.

## Operator Rule

Receipt grade is not merge-readiness. A `verified` receipt means the run left
fresh machine evidence. A Codex review pass decides whether the code is
`READY_TO_MERGE`, needs `FIX_FIRST`, or is `BLOCKED`.

Hermes must not run Codex as a foreground subprocess inside a gateway,
Telegram, cron, or agent turn. Codex implementation, receipt, review, repair,
and closeout work must run in a named tmux worker or detached tmux dispatch so
Hermes can return quickly and supervise via panes, events, receipts, artifacts,
and gbrain. Foreground shell remains acceptable only for short deterministic
inspection commands that are not Codex workers.
