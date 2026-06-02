#!/usr/bin/env python3
"""Hermes wrapper for the dev manager-event ledger.

Example:

~/.hermes/scripts/dev-manager-events.py record \
  --repo /srv/dev/repos/nullspaceton \
  --worker-session nullspaceton-codex \
  --intent "Run autodev corpus/gen then execute WATER queued winner slice"
"""

from pathlib import Path
import os
import sys

for candidate in (
    os.getenv("HERMES_AGENT_REPO"),
    "/srv/dev/repos/hermes-agent",
):
    if not candidate:
        continue
    root = Path(candidate)
    if (root / "hermes_cli").is_dir():
        sys.path.insert(0, str(root))
        break

from hermes_cli.dev_manager_events import main


raise SystemExit(main())
