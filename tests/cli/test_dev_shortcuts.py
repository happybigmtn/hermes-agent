from __future__ import annotations

from pathlib import Path
import subprocess


def test_dev_shortcuts_have_help_text():
    root = Path(__file__).resolve().parents[2]
    shortcuts = root / "scripts" / "dev-shortcuts"

    for name in ("dhelp", "dnew", "dstart", "dsteer", "dclose", "dtail", "dattach"):
        result = subprocess.run(
            ["bash", str(shortcuts / name), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert "usage:" in result.stdout or "Dev orchestrator shortcuts" in result.stdout


def test_shortcut_installer_dry_run_lists_expected_commands():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["bash", str(root / "scripts" / "install-dev-shortcuts.sh"), "--dry-run", "--no-global"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    for name in ("ds", "db", "dw", "dnew", "dstart", "dsteer", "dclose", "dattach", "dtail", "dhelp"):
        assert f"/{name}" in result.stdout
    assert "Dev shortcuts installed." in result.stdout


def test_shortcut_semantics_are_safe():
    root = Path(__file__).resolve().parents[2]
    shortcuts = root / "scripts" / "dev-shortcuts"

    assert "exec dnew" in (shortcuts / "dstart").read_text(encoding="utf-8")
    assert "session already exists" in (shortcuts / "dnew").read_text(encoding="utf-8")
    assert "--no-start-if-missing" in (shortcuts / "dsteer").read_text(encoding="utf-8")
    assert "session does not exist" in (shortcuts / "dsteer").read_text(encoding="utf-8")
