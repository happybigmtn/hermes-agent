# Dev Worker Dispatch

`dev-worker-dispatch.py` is the canonical helper for starting or steering a
supervised development worker from Hermes.

For daily use, prefer the safer shortcuts:

- `dnew`: create a supervised worker; refuses if it already exists
- `dsteer`: message an existing worker; refuses if it does not exist

It does four things in order:

1. records a manager event in `~/.hermes/reports/dev-manager-events.jsonl`
2. creates or reuses the requested tmux session
3. optionally sends a literal message or command to the pane
4. writes a dispatch artifact under `.auto/orchestrator`

## Start Codex

Short form:

```bash
dnew ludeme "Continue the Ludeme campaign from the current plan"
```

Underlying command:

```bash
~/.hermes/scripts/dev-worker-dispatch.py \
  --repo /srv/dev/repos/ludeme \
  --session ludeme-codex \
  --intent "Continue the Ludeme campaign from the current plan" \
  --message "codex --yolo"
```

## Steer Existing Worker

Short form:

```bash
dsteer nullspaceton "If auto gen is stalled, write closeout and stop cleanly."
```

Underlying command:

```bash
~/.hermes/scripts/dev-worker-dispatch.py \
  --repo /srv/dev/repos/nullspaceton \
  --session nullspaceton-codex \
  --intent "Ask the worker to close out if auto gen remains stalled" \
  --message-file /tmp/nullspaceton-steer.txt
```

## Contract

Use this helper instead of raw `tmux new-session` or `tmux send-keys` when the
worker is part of an orchestrated development campaign. Raw tmux is still fine
for local debugging, but it does not create durable manager evidence.

Closeout and active-run status can cite the dispatch artifact and manager
event, giving the operator a live chain from intent to worker to artifacts.
