#!/usr/bin/env python3
"""Hermes script wrapper for recording daily development mastery answers.

Install:
  install -m 0755 scripts/hermes-daily-mastery-answer.py ~/.hermes/scripts/mastery-check-answer.py

Use:
  printf '%s\n' "$ANSWER" | ~/.hermes/scripts/mastery-check-answer.py --answer-file -
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

repo_root = Path(os.getenv("HERMES_AGENT_REPO", "/srv/dev/repos/hermes-agent")).expanduser()
if (repo_root / "hermes_cli").is_dir():
    sys.path.insert(0, str(repo_root))

from hermes_cli.daily_mastery_answer import main


if __name__ == "__main__":
    raise SystemExit(main())
