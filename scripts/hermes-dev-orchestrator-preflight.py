#!/usr/bin/env python3
"""Hermes cron wrapper for dev orchestrator profile preflight.

Install into ~/.hermes/scripts/ and schedule with:

hermes cron create "every 6h" --name dev-orchestrator-profile-preflight --script dev-orchestrator-preflight.py --no-agent --deliver telegram
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

from hermes_cli.dev_orchestrator_preflight import main


raise SystemExit(main())
