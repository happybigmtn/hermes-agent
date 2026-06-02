#!/usr/bin/env python3
"""Hermes wrapper for supervised tmux worker closeout.

Example:

~/.hermes/scripts/dev-supervision-closeout.py \
  --repo /srv/dev/repos/ludeme \
  --session ludeme-codex
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

from hermes_cli.dev_supervision_closeout import main


raise SystemExit(main())
