from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml

from hermes_cli.dev_orchestrator_preflight import (
    collect_preflight,
    generate_preflight_report,
    profile_root,
    render_markdown,
    subprocess_home,
    telegram_summary,
)


def _write_skill(root: Path, name: str) -> None:
    path = root / "skills" / "builtin" / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")


def _write_config(root: Path, toolsets: list[str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text(yaml.safe_dump({"toolsets": toolsets}), encoding="utf-8")


def test_profile_paths_respect_isolated_worker_home(tmp_path):
    hermes_root = tmp_path / "hermes"
    default_home = tmp_path / "home"

    assert profile_root(hermes_root, "default") == hermes_root
    assert profile_root(hermes_root, "designcritic") == hermes_root / "profiles" / "designcritic"
    assert subprocess_home(default_home, hermes_root, "default") == default_home
    assert subprocess_home(default_home, hermes_root / "profiles" / "designcritic", "designcritic") == hermes_root / "profiles" / "designcritic" / "home"


def test_collect_preflight_flags_profile_specific_missing_codex_auth(tmp_path, monkeypatch):
    hermes_root = tmp_path / "hermes"
    default_home = tmp_path / "home"
    profile = hermes_root / "profiles" / "codexworker"
    (profile / "home").mkdir(parents=True)
    _write_config(profile, [])
    _write_skill(profile, "kanban-codex-lane")
    _write_skill(profile, "codex")
    (default_home / ".codex").mkdir(parents=True)
    (default_home / ".codex" / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr("hermes_cli.dev_orchestrator_preflight.shutil.which", lambda command: f"/bin/{command}")

    result = collect_preflight(
        hermes_root=hermes_root,
        default_home=default_home,
        profiles=["codexworker"],
        now=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )

    failures = [check.name for check in result.profiles[0].failures]
    assert "auth artifact:.codex/auth.json" in failures
    assert result.failure_count == 1


def test_collect_preflight_checks_orchestrator_kanban_toolset(tmp_path, monkeypatch):
    hermes_root = tmp_path / "hermes"
    default_home = tmp_path / "home"
    profile = hermes_root / "profiles" / "orchestrator"
    (profile / "home").mkdir(parents=True)
    _write_config(profile, ["hermes-cli"])
    _write_skill(profile, "kanban-orchestrator")
    monkeypatch.setattr("hermes_cli.dev_orchestrator_preflight.shutil.which", lambda command: f"/bin/{command}")

    result = collect_preflight(
        hermes_root=hermes_root,
        default_home=default_home,
        profiles=["orchestrator"],
        now=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )

    failures = [check.name for check in result.profiles[0].failures]
    assert "toolset:kanban" in failures


def test_designcritic_auth_probe_runs_under_profile_home(tmp_path, monkeypatch):
    hermes_root = tmp_path / "hermes"
    default_home = tmp_path / "home"
    profile = hermes_root / "profiles" / "designcritic"
    (profile / "home" / ".claude").mkdir(parents=True)
    (profile / "home" / ".claude" / ".credentials.json").write_text("{}", encoding="utf-8")
    _write_config(profile, [])
    _write_skill(profile, "claude-code")
    _write_skill(profile, "claude-design")
    monkeypatch.setattr("hermes_cli.dev_orchestrator_preflight.shutil.which", lambda command: f"/bin/{command}")
    seen = {}

    def fake_runner(*args, **kwargs):
        seen["HOME"] = kwargs["env"]["HOME"]
        return subprocess.CompletedProcess(args[0], 1, "", "Not logged in. Run claude auth login to authenticate.")

    result = collect_preflight(
        hermes_root=hermes_root,
        default_home=default_home,
        profiles=["designcritic"],
        runner=fake_runner,
        now=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )

    assert seen["HOME"] == str(profile / "home")
    assert "auth probe" in [check.name for check in result.profiles[0].failures]


def test_render_markdown_and_summary_include_actionable_failures(tmp_path, monkeypatch):
    hermes_root = tmp_path / "hermes"
    default_home = tmp_path / "home"
    profile = hermes_root / "profiles" / "orchestrator"
    (profile / "home").mkdir(parents=True)
    _write_config(profile, [])
    monkeypatch.setattr("hermes_cli.dev_orchestrator_preflight.shutil.which", lambda command: None)
    result = collect_preflight(
        hermes_root=hermes_root,
        default_home=default_home,
        profiles=["orchestrator"],
        now=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )

    markdown = render_markdown(result)
    summary = telegram_summary(result)

    assert "Dev Orchestrator Profile Preflight" in markdown
    assert "Required next action" in markdown
    assert "command:auto" in markdown
    assert "ATTENTION" in summary
    assert "orchestrator:" in summary


def test_generate_preflight_report_writes_markdown(tmp_path, monkeypatch):
    hermes_root = tmp_path / "hermes"
    default_home = tmp_path / "home"
    profile = hermes_root / "profiles" / "reviewer"
    (profile / "home").mkdir(parents=True)
    _write_config(profile, [])
    _write_skill(profile, "github-code-review")
    _write_skill(profile, "kanban-worker")
    monkeypatch.setattr("hermes_cli.dev_orchestrator_preflight.shutil.which", lambda command: f"/bin/{command}")

    result = generate_preflight_report(
        profiles=["reviewer"],
        report_dir=tmp_path / "reports",
        hermes_root=hermes_root,
        default_home=default_home,
        write_gbrain=False,
        now=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )

    assert result.markdown_path is not None
    assert result.markdown_path.exists()
    assert "reviewer: PASS" in result.markdown_path.read_text(encoding="utf-8")
