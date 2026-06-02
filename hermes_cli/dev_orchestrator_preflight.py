"""Profile-aware preflight for the dev orchestrator.

This module is deterministic and safe to run from cron. It checks whether the
Hermes profiles used by the dev orchestrator can actually see the tools,
credentials, skills, and Kanban config their role requires.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

try:
    import yaml
except Exception:  # pragma: no cover - PyYAML is a Hermes dependency.
    yaml = None  # type: ignore[assignment]


DEFAULT_REPORT_DIR = Path.home() / ".hermes" / "reports"
DEFAULT_GBRAIN_SLUG = "dev-orchestrator-profile-preflight"
DEFAULT_ROLES = ("orchestrator", "codexworker", "designcritic", "reviewer")

ROLE_CONTRACTS: Mapping[str, Mapping[str, object]] = {
    "orchestrator": {
        "purpose": "routes repo campaigns through Kanban/autodev/gbrain without implementing directly",
        "commands": ("auto", "gbrain", "git"),
        "toolsets": ("kanban",),
        "skills": ("kanban-orchestrator",),
        "auth_artifacts": (),
    },
    "codexworker": {
        "purpose": "runs bounded Codex implementation lanes and hands diffs back to Hermes",
        "commands": ("codex", "git"),
        "toolsets": (),
        "skills": ("kanban-codex-lane", "codex"),
        "auth_artifacts": (".codex/auth.json",),
    },
    "designcritic": {
        "purpose": "uses Claude only for design/product-plan critique artifacts",
        "commands": ("claude", "git"),
        "toolsets": (),
        "skills": ("claude-code", "claude-design"),
        "auth_artifacts": (".claude/.credentials.json",),
        "auth_probe": ("claude", "auth", "status", "--text"),
        "auth_probe_ok_markers": ("Login method:", "Email:"),
    },
    "reviewer": {
        "purpose": "independently reviews diffs, tests, artifacts, and safety gates",
        "commands": ("gbrain", "git"),
        "toolsets": (),
        "skills": ("github-code-review", "kanban-worker"),
        "auth_artifacts": (),
    },
}

SECRET_MARKERS = ("token", "secret", "password", "authorization", "api_key", "apikey", "bearer")


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    severity: str = "fail"  # fail | warn | info


@dataclass
class ProfilePreflight:
    name: str
    profile_root: Path
    subprocess_home: Path
    purpose: str
    checks: list[Check] = field(default_factory=list)

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if not check.ok and check.severity == "fail"]

    @property
    def warnings(self) -> list[Check]:
        return [check for check in self.checks if not check.ok and check.severity == "warn"]

    @property
    def ok(self) -> bool:
        return not self.failures


@dataclass
class PreflightResult:
    generated_at: datetime
    hermes_root: Path
    default_home: Path
    profiles: list[ProfilePreflight]
    markdown_path: Path | None = None
    gbrain_slug: str | None = None
    gbrain_error: str | None = None

    @property
    def failure_count(self) -> int:
        return sum(len(profile.failures) for profile in self.profiles)

    @property
    def warning_count(self) -> int:
        return sum(len(profile.warnings) for profile in self.profiles)


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _default_hermes_root() -> Path:
    try:
        from hermes_constants import get_default_hermes_root

        return Path(get_default_hermes_root())
    except Exception:
        return Path(os.getenv("HERMES_HOME", Path.home() / ".hermes")).expanduser()


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def profile_root(hermes_root: Path, profile: str) -> Path:
    if profile == "default":
        return hermes_root
    return hermes_root / "profiles" / profile


def subprocess_home(default_home: Path, root: Path, profile: str) -> Path:
    if profile == "default":
        return default_home
    return root / "home"


def profile_env(default_env: Mapping[str, str], profile: str, root: Path, home: Path) -> dict[str, str]:
    env = dict(default_env)
    env["HERMES_HOME"] = str(root)
    env["HOME"] = str(home)
    if profile != "default":
        env["HERMES_PROFILE"] = profile
    return env


def _command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def _safe_excerpt(text: str, max_chars: int = 220) -> str:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        lowered = line.lower()
        if any(marker in lowered for marker in SECRET_MARKERS):
            line = "[redacted sensitive line]"
        lines.append(line)
        if sum(len(item) for item in lines) >= max_chars:
            break
    excerpt = " | ".join(lines)
    if len(excerpt) > max_chars:
        excerpt = excerpt[: max_chars - 3] + "..."
    return excerpt


def _read_toolsets(root: Path) -> list[str]:
    config_path = root / "config.yaml"
    if not config_path.exists() or yaml is None:
        return []
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    toolsets = data.get("toolsets") if isinstance(data, dict) else None
    if not isinstance(toolsets, list):
        return []
    return [str(item).strip() for item in toolsets if str(item).strip()]


def _has_skill(root: Path, skill_name: str) -> bool:
    skills = root / "skills"
    if not skills.is_dir():
        return False
    for path in skills.rglob("SKILL.md"):
        if path.parent.name == skill_name:
            return True
    return False


def _check_auth_probe(
    probe: Sequence[str],
    ok_markers: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: int,
    runner: Runner,
) -> Check:
    if not probe:
        return Check("auth probe", True, "not required", "info")
    try:
        result = runner(
            list(probe),
            cwd=str(cwd),
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return Check("auth probe", False, f"{probe[0]} not found")
    except subprocess.TimeoutExpired:
        return Check("auth probe", False, f"{probe[0]} auth probe timed out after {timeout}s")
    output = f"{result.stdout}\n{result.stderr}"
    missing_login = "not logged in" in output.lower()
    marker_ok = not ok_markers or any(marker in output for marker in ok_markers)
    if result.returncode == 0 and marker_ok and not missing_login:
        return Check("auth probe", True, f"{probe[0]} authenticated")
    return Check("auth probe", False, _safe_excerpt(output) or f"{probe[0]} auth probe failed")


def check_profile(
    profile: str,
    *,
    hermes_root: Path,
    default_home: Path,
    timeout: int = 15,
    runner: Runner = subprocess.run,
) -> ProfilePreflight:
    contract = ROLE_CONTRACTS.get(profile, {})
    root = profile_root(hermes_root, profile)
    home = subprocess_home(default_home, root, profile)
    purpose = str(contract.get("purpose") or "custom Hermes profile")
    result = ProfilePreflight(profile, root, home, purpose)

    result.checks.append(Check("profile root", root.exists(), str(root)))
    result.checks.append(Check("subprocess HOME", home.exists(), str(home)))

    for command in contract.get("commands", ()):
        text = str(command)
        result.checks.append(Check(f"command:{text}", _command_exists(text), shutil.which(text) or "missing"))

    toolsets = _read_toolsets(root)
    for toolset in contract.get("toolsets", ()):
        text = str(toolset)
        result.checks.append(
            Check(f"toolset:{text}", text in toolsets, f"configured toolsets: {', '.join(toolsets) or '(none)'}")
        )

    for skill in contract.get("skills", ()):
        text = str(skill)
        result.checks.append(Check(f"skill:{text}", _has_skill(root, text), f"{root / 'skills'}"))

    for rel in contract.get("auth_artifacts", ()):
        text = str(rel)
        artifact = home / text
        result.checks.append(Check(f"auth artifact:{text}", artifact.exists(), str(artifact)))

    probe = tuple(str(item) for item in contract.get("auth_probe", ()))
    ok_markers = tuple(str(item) for item in contract.get("auth_probe_ok_markers", ()))
    if probe:
        env = profile_env(os.environ, profile, root, home)
        result.checks.append(
            _check_auth_probe(probe, ok_markers, cwd=root if root.exists() else Path.cwd(), env=env, timeout=timeout, runner=runner)
        )

    return result


def collect_preflight(
    *,
    hermes_root: Path | None = None,
    default_home: Path | None = None,
    profiles: Sequence[str] = DEFAULT_ROLES,
    timeout: int = 15,
    runner: Runner = subprocess.run,
    now: datetime | None = None,
) -> PreflightResult:
    root = (hermes_root or _default_hermes_root()).expanduser()
    home = (default_home or Path.home()).expanduser()
    items = [
        check_profile(profile, hermes_root=root, default_home=home, timeout=timeout, runner=runner)
        for profile in profiles
    ]
    return PreflightResult(generated_at=now or _now(), hermes_root=root, default_home=home, profiles=items)


def render_markdown(result: PreflightResult) -> str:
    lines = [
        "# Dev Orchestrator Profile Preflight",
        "",
        f"Generated: {result.generated_at.isoformat()}",
        f"Hermes root: `{result.hermes_root}`",
        f"Default Unix HOME: `{result.default_home}`",
        "",
        f"Failures: **{result.failure_count}**",
        f"Warnings: **{result.warning_count}**",
        "",
        "## Profiles",
    ]
    for profile in result.profiles:
        status = "PASS" if profile.ok else "FAIL"
        lines.extend(
            [
                "",
                f"### {profile.name}: {status}",
                "",
                profile.purpose,
                "",
                f"- Profile root: `{profile.profile_root}`",
                f"- Subprocess HOME: `{profile.subprocess_home}`",
                "",
                "| Check | Status | Detail |",
                "|---|---:|---|",
            ]
        )
        for check in profile.checks:
            icon = "ok" if check.ok else check.severity
            lines.append(f"| `{check.name}` | {icon} | `{check.detail}` |")
        if profile.failures:
            lines.extend(["", "Required next action:"])
            for check in profile.failures:
                lines.append(f"- Fix `{check.name}` for `{profile.name}`: {check.detail}")
    lines.append("")
    return "\n".join(lines)


def telegram_summary(result: PreflightResult) -> str:
    status = "PASS" if result.failure_count == 0 else "ATTENTION"
    lines = [
        f"Dev orchestrator profile preflight: {status}",
        f"profiles: {len(result.profiles)}",
        f"failures: {result.failure_count}",
        f"warnings: {result.warning_count}",
    ]
    for profile in result.profiles:
        if profile.failures:
            first = profile.failures[0]
            lines.append(f"{profile.name}: {len(profile.failures)} failure(s), first `{first.name}`")
    if result.markdown_path:
        lines.append(f"report: {result.markdown_path}")
    if result.gbrain_slug:
        lines.append(f"gbrain: {result.gbrain_slug}")
    elif result.gbrain_error:
        lines.append(f"gbrain: not synced ({result.gbrain_error})")
    return "\n".join(lines)


def write_gbrain_page(slug: str, markdown: str) -> str | None:
    if not shutil.which("gbrain"):
        return "gbrain binary not found"
    body = "---\ntype: report\ntitle: Dev Orchestrator Profile Preflight\n---\n\n" + markdown
    try:
        result = subprocess.run(
            ["gbrain", "put", slug, "--content", body],
            cwd="/tmp",
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception as exc:
        return str(exc)
    if result.returncode != 0:
        return (result.stderr or result.stdout or "gbrain put failed").strip()[:400]
    return None


def generate_preflight_report(
    *,
    profiles: Sequence[str] = DEFAULT_ROLES,
    report_dir: Path = DEFAULT_REPORT_DIR,
    hermes_root: Path | None = None,
    default_home: Path | None = None,
    timeout: int = 15,
    write_gbrain: bool = True,
    gbrain_slug: str = DEFAULT_GBRAIN_SLUG,
    runner: Runner = subprocess.run,
    now: datetime | None = None,
) -> PreflightResult:
    result = collect_preflight(
        hermes_root=hermes_root,
        default_home=default_home,
        profiles=profiles,
        timeout=timeout,
        runner=runner,
        now=now,
    )
    markdown = render_markdown(result)
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"dev-orchestrator-profile-preflight-{result.generated_at.strftime('%Y%m%d-%H%M%S')}.md"
    path.write_text(markdown, encoding="utf-8")
    result.markdown_path = path
    if write_gbrain:
        result.gbrain_error = write_gbrain_page(gbrain_slug, markdown)
        if result.gbrain_error is None:
            result.gbrain_slug = gbrain_slug
    return result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Profile-aware dev orchestrator preflight")
    parser.add_argument("--profile", action="append", default=None, help="Profile to check; repeatable")
    parser.add_argument("--hermes-root", type=Path, default=None)
    parser.add_argument("--default-home", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=Path(os.getenv("HERMES_DEV_ORCH_PREFLIGHT_DIR", DEFAULT_REPORT_DIR)))
    parser.add_argument("--timeout", type=int, default=int(os.getenv("HERMES_DEV_ORCH_PREFLIGHT_TIMEOUT", "15")))
    parser.add_argument("--gbrain-slug", default=os.getenv("HERMES_DEV_ORCH_PREFLIGHT_GBRAIN_SLUG", DEFAULT_GBRAIN_SLUG))
    parser.add_argument("--no-gbrain", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    profiles = args.profile or list(DEFAULT_ROLES)
    result = generate_preflight_report(
        profiles=profiles,
        report_dir=args.out_dir,
        hermes_root=args.hermes_root,
        default_home=args.default_home,
        timeout=args.timeout,
        write_gbrain=not args.no_gbrain,
        gbrain_slug=args.gbrain_slug,
    )
    if args.json:
        print(
            json.dumps(
                {
                    "markdown_path": str(result.markdown_path) if result.markdown_path else None,
                    "gbrain_slug": result.gbrain_slug,
                    "gbrain_error": result.gbrain_error,
                    "failure_count": result.failure_count,
                    "warning_count": result.warning_count,
                    "profiles": [
                        {
                            "name": profile.name,
                            "ok": profile.ok,
                            "failures": [check.name for check in profile.failures],
                            "warnings": [check.name for check in profile.warnings],
                        }
                        for profile in result.profiles
                    ],
                },
                indent=2,
            )
        )
    else:
        print(telegram_summary(result))
    return 1 if result.failure_count else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
