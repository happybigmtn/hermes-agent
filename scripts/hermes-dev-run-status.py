#!/usr/bin/env python3
"""Hermes cron wrapper for active dev orchestrator run status.

Install into ~/.hermes/scripts/ and schedule with:

hermes cron create "every 30m" --name dev-orchestrator-run-status --script dev-run-status.py --no-agent --deliver telegram
"""

from hermes_cli.dev_run_status import main


raise SystemExit(main())
