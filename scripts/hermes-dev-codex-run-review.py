#!/usr/bin/env python3
"""Hermes wrapper for Codex-only supervised run review."""

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

from hermes_cli.dev_codex_run_review import main


raise SystemExit(main())
