#!/usr/bin/env bash
set -o pipefail
cd /srv/dev/repos/hermes-agent
/usr/local/bin/codex exec --dangerously-bypass-approvals-and-sandbox -C /srv/dev/repos/hermes-agent - < /srv/dev/repos/hermes-agent/.auto/orchestrator/velocity-steward-20260602T132815Z/velocity-prompt.md > /srv/dev/repos/hermes-agent/.auto/orchestrator/velocity-steward-20260602T132815Z/codex-velocity-output.md 2>&1
status=$?
printf '\n[hermes] codex velocity worker exited with %s at %s\n' "$status" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> /srv/dev/repos/hermes-agent/.auto/orchestrator/velocity-steward-20260602T132815Z/codex-velocity-output.md
exit "$status"
