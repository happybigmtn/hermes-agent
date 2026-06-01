#!/usr/bin/env python3
"""Hermes cron wrapper for the daily development mastery check.

Install into ~/.hermes/scripts/ and schedule with:

hermes cron create "15 13 * * *" --name daily-dev-mastery-check --script daily-mastery-check.py --no-agent --deliver telegram
"""

import os
import sys
from pathlib import Path

repo_root = Path(os.getenv("HERMES_AGENT_REPO", "/srv/dev/repos/hermes-agent")).expanduser()
if (repo_root / "hermes_cli").is_dir():
    sys.path.insert(0, str(repo_root))

from hermes_cli.daily_mastery_check import main

raise SystemExit(main())
