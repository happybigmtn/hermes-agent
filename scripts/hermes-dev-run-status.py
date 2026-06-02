#!/usr/bin/env python3
"""Hermes cron wrapper for active dev orchestrator run status.

Install into ~/.hermes/scripts/ and schedule with:

hermes cron create "every 30m" --name dev-orchestrator-run-status --script dev-run-status.py --no-agent --deliver telegram
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

from hermes_cli.dev_run_status import main


raise SystemExit(main())
