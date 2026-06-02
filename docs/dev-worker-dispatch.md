# Dev Worker Dispatch

`dev-worker-dispatch.py` is the canonical helper for starting or steering a
supervised development worker from Hermes.

It does four things in order:

1. records a manager event in `~/.hermes/reports/dev-manager-events.jsonl`
2. creates or reuses the requested tmux session
3. optionally sends a literal message or command to the pane
4. writes a dispatch artifact under `.auto/orchestrator`

## Start Codex

```bash
~/.hermes/scripts/dev-worker-dispatch.py \
  --repo /srv/dev/repos/ludeme \
  --session ludeme-codex \
  --intent "Continue the Ludeme campaign from the current plan" \
  --message "codex --yolo"
```

## Steer Existing Worker

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
