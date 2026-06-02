# Dev Orchestrator Profile Preflight

Hermes role profiles isolate their subprocess `HOME`. That is the correct
security boundary, but it means a CLI can work in the default shell while
failing inside a dispatched worker. This report checks the worker-visible
environment for each development role.

## What It Checks

- `orchestrator`: `auto`, `gbrain`, `git`, `kanban` toolset, and `kanban-orchestrator` skill.
- `codexworker`: `codex`, `git`, Codex auth artifact, `codex`, and `kanban-codex-lane` skills.
- `designcritic`: `claude`, `git`, Claude credentials, Claude auth probe, `claude-code`, and `claude-design` skills.
- `reviewer`: `gbrain`, `git`, `github-code-review`, and `kanban-worker` skills.

The check never prints credential contents. It reports only whether the profile
can see the required file or command.

## Install On The Orchestrator

```bash
cd /srv/dev/repos/hermes-agent
install -m 0755 scripts/hermes-dev-orchestrator-preflight.py ~/.hermes/scripts/dev-orchestrator-preflight.py
hermes cron create "every 6h" \
  --name dev-orchestrator-profile-preflight \
  --script dev-orchestrator-preflight.py \
  --no-agent \
  --deliver telegram
```

## Manual Verification

```bash
~/.hermes/scripts/dev-orchestrator-preflight.py --no-gbrain
~/.hermes/scripts/dev-orchestrator-preflight.py
```

Expected healthy summary:

```text
Dev orchestrator profile preflight: PASS
profiles: 4
failures: 0
warnings: 0
gbrain: dev-orchestrator-profile-preflight
```

## Output Contract

Each run writes:

- `~/.hermes/reports/dev-orchestrator-profile-preflight-YYYYMMDD-HHMMSS.md`
- gbrain page `dev-orchestrator-profile-preflight`, when `gbrain` is available.

The Telegram summary reports PASS/ATTENTION, profile count, failure count,
warning count, report path, and gbrain slug.

## Operator Rule

Run this before judging worker quality. If a role profile fails preflight, fix
the profile setup first. A blocked Codex or Claude worker is not meaningful
evidence about orchestration ability until the profile-visible CLI auth and
skills pass.
