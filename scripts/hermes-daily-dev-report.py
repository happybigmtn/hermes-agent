#!/usr/bin/env python3
"""Hermes cron wrapper for the daily development teaching report.

Install into ~/.hermes/scripts/ and schedule with:

hermes cron create "0 13 * * *" --name daily-dev-teaching-report --script daily-dev-report.py --no-agent --deliver telegram
"""

from hermes_cli.daily_dev_report import main

raise SystemExit(main())
