# Dev Manager Events

Hermes records explicit manager actions in a JSONL ledger so supervised worker
closeouts can cite why a Codex, Claude, or autodev tmux session exists.

Default ledger:

```text
~/.hermes/reports/dev-manager-events.jsonl
```

## Record

```bash
~/.hermes/scripts/dev-manager-events.py record \
  --repo /srv/dev/repos/nullspaceton \
  --worker-session nullspaceton-codex \
  --intent "Run autodev corpus/gen, then execute the queued winner WATER slice." \
  --requested-by hermes \
  --delivery-channel telegram \
  --artifact .auto/orchestrator/nullspaceton-codex-20260602T033603Z/prompt.md
```

## List

```bash
~/.hermes/scripts/dev-manager-events.py list \
  --repo /srv/dev/repos/nullspaceton \
  --worker-session nullspaceton-codex
```

## Closeout Integration

`dev-supervision-closeout.py` automatically includes matching manager events.
This gives the operator a durable chain from intent to worker to artifacts, even
when the originating Telegram or terminal transcript is noisy.
