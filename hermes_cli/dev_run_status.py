"""Active autodev/pilot run status for Hermes cron.

This module is deterministic by design. It summarizes process state and run
artifacts so the operator can tell whether an ambitious campaign is active,
stale, or ready for intervention before commits exist.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python 3.11+ always has zoneinfo.
    ZoneInfo = None  # type: ignore[assignment]


DEFAULT_REPO_ROOTS = [Path("/srv/dev/repos"), Path.home() / "coding", Path.home() / "Coding"]
DEFAULT_REPORT_DIR = Path.home() / ".hermes" / "reports"
DEFAULT_STALE_MINUTES = 30
DEFAULT_RECENT_HOURS = 2
DEFAULT_DASHBOARD_PORT = 8765
DEFAULT_DASHBOARD_FILENAME = "dev-run-status-latest.html"
DEFAULT_TIMEZONE = "America/New_York"
DEFAULT_GBRAIN_SLUG = "dev-orchestrator-active-run-status"

PROCESS_MARKERS = (
    "pilot-dev",
    "auto pilot",
    "auto corpus",
    "auto gen",
    "auto parallel",
    "auto steward",
    "codex exec",
    "claude",
)


@dataclass(frozen=True)
class ProcessRecord:
    pid: int
    ppid: int
    stat: str
    elapsed: str
    cpu: float
    mem: float
    command: str

    def label(self) -> str:
        command = self.command
        if "auto gen" in command:
            return "auto gen"
        if "auto corpus" in command:
            return "auto corpus"
        if "auto parallel" in command:
            return "auto parallel"
        if "auto pilot" in command:
            if "--planning-only" in command:
                return "auto pilot planning"
            if "--execution-manifest-only" in command:
                return "auto pilot execution manifest"
            if "--closeout-only" in command:
                return "auto pilot closeout"
            return "auto pilot"
        if "codex exec" in command:
            return "codex exec"
        if "pilot-dev" in command:
            return "pilot-dev"
        if "claude" in command:
            return "claude"
        return command.split(" ", 1)[0]


@dataclass(frozen=True)
class ArtifactRecord:
    path: Path
    mtime: datetime
    size: int
    kind: str


@dataclass(frozen=True)
class WorkerSession:
    session_name: str
    window_index: str
    pane_index: str
    pane_pid: int | None
    current_command: str
    current_path: Path | None
    active: bool
    dead: bool
    title: str

    def target(self) -> str:
        return f"{self.session_name}:{self.window_index}.{self.pane_index}"

    def kind(self) -> str:
        haystack = " ".join(
            part.lower()
            for part in (
                self.session_name,
                self.title,
                self.current_command,
                str(self.current_path or ""),
            )
        )
        if "codex" in haystack:
            return "codex"
        if "claude" in haystack:
            return "claude"
        if "auto" in haystack:
            return "autodev"
        return "shell"

    def repo_slug(self, repo_roots: Sequence[Path]) -> str | None:
        if self.current_path is None:
            return None
        try:
            resolved = self.current_path.resolve()
        except OSError:
            resolved = self.current_path
        for root in repo_roots:
            try:
                relative = resolved.relative_to(root)
            except ValueError:
                continue
            return relative.parts[0] if relative.parts else None
        return None


@dataclass
class RunSummary:
    repo_slug: str
    repo_path: Path
    run_id: str
    run_root: Path
    branch: str | None
    run_started_at: datetime | None
    active_processes: list[ProcessRecord] = field(default_factory=list)
    latest_run_artifact: ArtifactRecord | None = None
    latest_repo_artifact: ArtifactRecord | None = None
    phase_heartbeat: dict[str, str] | None = None
    dirty_entries: list[str] = field(default_factory=list)
    execution_status: str | None = None
    closeout_status: str | None = None
    gbrain_context: Path | None = None
    implementation_plan: Path | None = None
    attention: bool = False
    attention_reason: str | None = None
    next_action: str = "Inspect run root before starting another broad command."

    @property
    def active(self) -> bool:
        return bool(self.active_processes)

    def phase(self) -> str:
        if self.phase_heartbeat:
            phase = self.phase_heartbeat.get("phase", "").strip()
            status = self.phase_heartbeat.get("status", "").strip()
            if phase and status:
                return f"{phase}: {status}"
            if phase:
                return phase
        labels: list[str] = []
        for process in self.active_processes:
            label = process.label()
            if label not in labels:
                labels.append(label)
        if labels:
            if "auto gen" in labels and "codex exec" in labels:
                return "planning: auto gen snapshot + Codex review"
            return ", ".join(labels[:4])
        if self.closeout_status:
            return f"closeout: {self.closeout_status}"
        if self.execution_status:
            return f"execution: {self.execution_status}"
        if self.implementation_plan:
            return "planned"
        return "inactive"

    def latest_artifact(self) -> ArtifactRecord | None:
        candidates = [item for item in (self.latest_run_artifact, self.latest_repo_artifact) if item]
        if not candidates:
            return None
        return max(candidates, key=lambda item: item.mtime)


@dataclass
class StatusResult:
    markdown_path: Path
    gbrain_slug: str | None
    gbrain_error: str | None
    runs: list[RunSummary]
    generated_at: datetime
    stale_after: timedelta
    worker_sessions: list[WorkerSession] = field(default_factory=list)
    dashboard_path: Path | None = None
    dashboard_url: str | None = None

    @property
    def active_count(self) -> int:
        return sum(1 for run in self.runs if run.active)

    @property
    def worker_count(self) -> int:
        return sum(1 for session in self.worker_sessions if not session.dead)

    @property
    def attention_count(self) -> int:
        return sum(1 for run in self.runs if run.attention)


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _repo_roots(values: Iterable[str] | None = None) -> list[Path]:
    roots = [Path(value).expanduser() for value in values or [] if value]
    if not roots:
        roots = [Path(value).expanduser() for value in _parse_csv(os.getenv("HERMES_DEV_RUN_STATUS_REPO_ROOTS"))]
    if not roots:
        roots = DEFAULT_REPO_ROOTS
    unique: list[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return unique


def _timezone(name: str) -> timezone:
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)  # type: ignore[return-value]
        except Exception:
            pass
    return timezone.utc


def _load_hermes_env() -> None:
    try:
        from hermes_cli.env_loader import load_hermes_dotenv

        load_hermes_dotenv()
    except Exception:
        pass


def parse_process_table(output: str) -> list[ProcessRecord]:
    records: list[ProcessRecord] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(None, 6)
        if len(parts) < 7:
            continue
        pid_s, ppid_s, stat, elapsed, cpu_s, mem_s, command = parts
        try:
            records.append(
                ProcessRecord(
                    pid=int(pid_s),
                    ppid=int(ppid_s),
                    stat=stat,
                    elapsed=elapsed,
                    cpu=float(cpu_s),
                    mem=float(mem_s),
                    command=command,
                )
            )
        except ValueError:
            continue
    return records


def list_orchestrator_processes() -> list[ProcessRecord]:
    result = subprocess.run(
        ["ps", "-eo", "pid=,ppid=,stat=,etime=,pcpu=,pmem=,args="],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        return []
    records = parse_process_table(result.stdout)
    filtered: list[ProcessRecord] = []
    for record in records:
        command = record.command
        if any(marker in command for marker in PROCESS_MARKERS) and "dev_run_status" not in command:
            filtered.append(record)
    return filtered


def parse_tmux_panes(output: str) -> list[WorkerSession]:
    sessions: list[WorkerSession] = []
    for raw in output.splitlines():
        parts = raw.rstrip("\n").split("\t")
        if len(parts) < 9:
            continue
        session_name, window_index, pane_index, pane_pid_s, command, path_s, active_s, dead_s, title = parts[:9]
        try:
            pane_pid = int(pane_pid_s)
        except ValueError:
            pane_pid = None
        sessions.append(
            WorkerSession(
                session_name=session_name,
                window_index=window_index,
                pane_index=pane_index,
                pane_pid=pane_pid,
                current_command=command,
                current_path=Path(path_s) if path_s else None,
                active=active_s == "1",
                dead=dead_s == "1",
                title=title,
            )
        )
    return sessions


def list_tmux_worker_sessions() -> list[WorkerSession]:
    if not shutil.which("tmux"):
        return []
    fmt = "\t".join(
        (
            "#{session_name}",
            "#{window_index}",
            "#{pane_index}",
            "#{pane_pid}",
            "#{pane_current_command}",
            "#{pane_current_path}",
            "#{pane_active}",
            "#{pane_dead}",
            "#{pane_title}",
        )
    )
    result = subprocess.run(
        ["tmux", "list-panes", "-a", "-F", fmt],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        return []
    return parse_tmux_panes(result.stdout)


def read_run_env(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def find_run_roots(repo_roots: Sequence[Path]) -> list[tuple[Path, Path]]:
    found: list[tuple[Path, Path]] = []
    for repo_root in repo_roots:
        if not repo_root.exists():
            continue
        for repo in repo_root.iterdir():
            orchestrator_root = repo / ".auto" / "orchestrator"
            if not orchestrator_root.is_dir():
                continue
            for run_root in orchestrator_root.iterdir():
                if run_root.is_dir() and (run_root / "run.env").exists():
                    found.append((repo, run_root))
    found.sort(key=lambda item: _path_mtime(item[1] / "run.env"), reverse=True)
    return found


def _path_mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _safe_stat(path: Path) -> os.stat_result | None:
    try:
        return path.stat()
    except OSError:
        return None


def _latest_file_under(root: Path, *, kind: str, max_depth: int = 4) -> ArtifactRecord | None:
    if not root.exists():
        return None
    best: ArtifactRecord | None = None
    root_depth = len(root.parts)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if len(path.parts) - root_depth > max_depth:
            continue
        stat = _safe_stat(path)
        if not stat:
            continue
        artifact = ArtifactRecord(path=path, mtime=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc), size=stat.st_size, kind=kind)
        if best is None or artifact.mtime > best.mtime:
            best = artifact
    return best


def _latest_repo_artifact(repo_path: Path) -> ArtifactRecord | None:
    candidates: list[ArtifactRecord] = []
    for gen_dir in repo_path.glob("gen-*"):
        if not gen_dir.is_dir():
            continue
        for relative in ("IMPLEMENTATION_PLAN.md", "GBRAIN-CONTEXT.md"):
            path = gen_dir / relative
            stat = _safe_stat(path)
            if stat:
                candidates.append(
                    ArtifactRecord(path=path, mtime=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc), size=stat.st_size, kind="gen")
                )
        latest_spec = _latest_file_under(gen_dir / "specs", kind="gen-spec", max_depth=2)
        if latest_spec:
            candidates.append(latest_spec)
    for path in (
        repo_path / "genesis" / "GBRAIN-CONTEXT.md",
        repo_path / "genesis" / "PLANS.md",
        repo_path / "IMPLEMENTATION_PLAN.md",
    ):
        stat = _safe_stat(path)
        if stat:
            candidates.append(
                ArtifactRecord(path=path, mtime=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc), size=stat.st_size, kind="repo")
            )
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.mtime)


def _git_output(repo_path: Path, args: list[str]) -> str | None:
    if not (repo_path / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _dirty_entries(repo_path: Path, limit: int = 20) -> list[str]:
    output = _git_output(repo_path, ["status", "--short"])
    if not output:
        return []
    return output.splitlines()[:limit]


def _branch(repo_path: Path) -> str | None:
    return _git_output(repo_path, ["branch", "--show-current"]) or None


def _processes_for_run(
    repo_path: Path,
    run_id: str,
    run_root: Path,
    processes: Sequence[ProcessRecord],
    *,
    allow_repo_only_processes: bool,
) -> list[ProcessRecord]:
    repo_s = str(repo_path)
    run_root_s = str(run_root)
    matched: list[ProcessRecord] = []
    for process in processes:
        command = process.command
        if run_id and run_id in command:
            matched.append(process)
        elif run_root_s in command:
            matched.append(process)
        elif (
            allow_repo_only_processes
            and repo_s in command
            and any(marker in command for marker in ("auto gen", "auto corpus", "auto parallel", "codex exec"))
        ):
            matched.append(process)
    matched.sort(key=lambda item: item.pid)
    return matched


def _find_named_file(repo_path: Path, name: str) -> Path | None:
    candidates = []
    for path in repo_path.glob(f"gen-*/{name}"):
        stat = _safe_stat(path)
        if stat:
            candidates.append((stat.st_mtime, path))
    candidates.sort(reverse=True)
    if candidates:
        return candidates[0][1]
    path = repo_path / name
    return path if path.exists() else None


def _read_json_status(path: Path, key: str = "status") -> str | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="replace")).get(key)
        return str(value) if value is not None else None
    except Exception:
        return None


def _read_phase_heartbeat(run_root: Path) -> dict[str, str] | None:
    path = run_root / "phase-heartbeat.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    phase = str(raw.get("phase") or "").strip()
    if not phase:
        return None
    return {str(key): str(value) for key, value in raw.items() if value is not None}


def _format_age(now: datetime, then: datetime | None) -> str:
    if then is None:
        return "unknown"
    seconds = max(0, int((now - then).total_seconds()))
    minutes = seconds // 60
    if minutes < 1:
        return "<1m ago"
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    rem = minutes % 60
    if hours < 24:
        return f"{hours}h{rem:02d}m ago"
    days = hours // 24
    return f"{days}d ago"


def _classify_run(run: RunSummary, now: datetime, stale_after: timedelta) -> None:
    latest = run.latest_artifact()
    if run.active and latest is None:
        run.attention = True
        run.attention_reason = "active process has no discoverable artifact"
        run.next_action = f"Inspect processes and run root: {run.run_root}"
        return
    if run.active and latest and now - latest.mtime > stale_after:
        run.attention = True
        run.attention_reason = f"latest artifact is {_format_age(now, latest.mtime)}"
        run.next_action = f"Inspect active process and latest artifact before launching another run: {latest.path}"
        return
    if run.active:
        run.next_action = "Wait, then re-check status. Do not start a competing broad run."
    elif run.closeout_status in {"ok", "complete", "completed"}:
        run.next_action = "Review closeout and daily teaching report."
    elif run.implementation_plan:
        run.next_action = f"Use latest plan for the next narrow execution command: {run.implementation_plan}"
    else:
        run.next_action = f"Inspect run root for incomplete state: {run.run_root}"


def collect_status(
    *,
    repo_roots: Sequence[Path],
    now: datetime,
    stale_after: timedelta,
    recent_after: datetime,
    processes: Sequence[ProcessRecord] | None = None,
) -> list[RunSummary]:
    process_records = list(processes) if processes is not None else list_orchestrator_processes()
    runs: list[RunSummary] = []
    run_entries = find_run_roots(repo_roots)
    newest_run_by_repo: dict[Path, Path] = {}
    for repo_path, run_root in run_entries:
        if repo_path not in newest_run_by_repo:
            newest_run_by_repo[repo_path] = run_root
    for repo_path, run_root in run_entries:
        run_env = read_run_env(run_root / "run.env")
        run_id = run_env.get("RUN_ID") or run_env.get("run_id") or run_root.name
        started = _path_mtime(run_root / "run.env") if (run_root / "run.env").exists() else None
        is_newest_run_for_repo = newest_run_by_repo.get(repo_path) == run_root
        active_processes = _processes_for_run(
            repo_path,
            run_id,
            run_root,
            process_records,
            allow_repo_only_processes=is_newest_run_for_repo,
        )
        latest_run_artifact = _latest_file_under(run_root, kind="run")
        latest_repo_artifact = _latest_repo_artifact(repo_path) if is_newest_run_for_repo else None
        latest_run_time = latest_run_artifact.mtime if latest_run_artifact else started
        if not active_processes and (latest_run_time is None or latest_run_time < recent_after):
            continue
        summary = RunSummary(
            repo_slug=run_env.get("REPO_SLUG") or run_env.get("repo") or repo_path.name,
            repo_path=repo_path,
            run_id=run_id,
            run_root=run_root,
            branch=_branch(repo_path),
            run_started_at=started,
            active_processes=active_processes,
            latest_run_artifact=latest_run_artifact,
            latest_repo_artifact=latest_repo_artifact,
            phase_heartbeat=_read_phase_heartbeat(run_root),
            dirty_entries=_dirty_entries(repo_path),
            execution_status=_read_json_status(run_root / "pilot-execution.json"),
            closeout_status=_read_json_status(run_root / "pilot-closeout.json"),
            gbrain_context=_find_named_file(repo_path, "GBRAIN-CONTEXT.md") if is_newest_run_for_repo else None,
            implementation_plan=_find_named_file(repo_path, "IMPLEMENTATION_PLAN.md") if is_newest_run_for_repo else None,
        )
        _classify_run(summary, now, stale_after)
        runs.append(summary)
    runs.sort(key=lambda run: (not run.active, not run.attention, run.run_started_at or datetime.fromtimestamp(0, tz=timezone.utc)))
    return runs


def _tailscale_ip() -> str | None:
    if not shutil.which("tailscale"):
        return None
    try:
        result = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        value = line.strip()
        if value:
            return value
    return None


def default_dashboard_url() -> str | None:
    configured = os.getenv("HERMES_DEV_RUN_STATUS_DASHBOARD_URL")
    if configured:
        return configured
    ip = _tailscale_ip()
    if not ip:
        return None
    return f"http://{ip}:{DEFAULT_DASHBOARD_PORT}/{DEFAULT_DASHBOARD_FILENAME}"


def render_dashboard_html(
    runs: Sequence[RunSummary],
    worker_sessions: Sequence[WorkerSession],
    *,
    generated_at: datetime,
    stale_after: timedelta,
    markdown: str,
) -> str:
    active_runs = sum(1 for run in runs if run.active)
    attention = sum(1 for run in runs if run.attention)
    live_workers = sum(1 for session in worker_sessions if not session.dead)
    worker_rows: list[str] = []
    for session in worker_sessions:
        state = "dead" if session.dead else "active" if session.active else "idle"
        steer = f"tmux send-keys -t {session.target()} '<message>' C-m"
        worker_rows.append(
            "<tr>"
            f"<td>{html.escape(session.target())}</td>"
            f"<td>{html.escape(session.kind())}</td>"
            f"<td>{html.escape(state)}</td>"
            f"<td>{html.escape(session.current_command or 'unknown')}</td>"
            f"<td>{html.escape(str(session.current_path or 'unknown'))}</td>"
            f"<td><code>{html.escape(steer)}</code></td>"
            "</tr>"
        )
    if not worker_rows:
        worker_rows.append('<tr><td colspan="6">No supervised tmux worker panes found.</td></tr>')

    run_rows: list[str] = []
    for run in runs[:12]:
        latest = run.latest_artifact()
        state = "attention" if run.attention else "active" if run.active else "recent"
        run_rows.append(
            "<tr>"
            f"<td>{html.escape(run.repo_slug)}</td>"
            f"<td>{html.escape(run.run_id)}</td>"
            f"<td>{html.escape(state)}</td>"
            f"<td>{html.escape(run.phase())}</td>"
            f"<td>{html.escape(str(latest.path if latest else 'none'))}</td>"
            f"<td>{html.escape(run.next_action)}</td>"
            "</tr>"
        )
    if not run_rows:
        run_rows.append('<tr><td colspan="6">No active or recent autodev run roots found.</td></tr>')

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="60">
  <title>Dev Orchestrator Status</title>
  <style>
    :root {{ color-scheme: dark; --bg: #090d10; --panel: #10161c; --line: #25313b; --text: #e6edf3; --muted: #8b9bab; --gold: #d6aa3f; --cyan: #48d7c8; }}
    body {{ margin: 0; background: var(--bg); color: var(--text); font: 14px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px; }}
    h1 {{ margin: 0 0 4px; font-size: 22px; letter-spacing: 0; }}
    h2 {{ margin: 28px 0 10px; color: var(--gold); font-size: 15px; }}
    .muted {{ color: var(--muted); }}
    .grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 18px; }}
    .metric {{ border: 1px solid var(--line); border-radius: 8px; background: var(--panel); padding: 14px; }}
    .metric strong {{ display: block; font-size: 28px; color: var(--cyan); }}
    table {{ width: 100%; border-collapse: collapse; border: 1px solid var(--line); background: var(--panel); }}
    th, td {{ padding: 9px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
    th {{ color: var(--gold); font-weight: 700; }}
    code {{ color: var(--cyan); }}
    pre {{ white-space: pre-wrap; border: 1px solid var(--line); background: var(--panel); border-radius: 8px; padding: 14px; overflow: auto; }}
    @media (max-width: 760px) {{ main {{ padding: 16px; }} .grid {{ grid-template-columns: 1fr; }} table {{ font-size: 12px; }} }}
  </style>
</head>
<body>
<main>
  <h1>Dev Orchestrator Status</h1>
  <div class="muted">Generated {html.escape(generated_at.isoformat())}; stale threshold {int(stale_after.total_seconds() // 60)} minutes.</div>
  <section class="grid">
    <div class="metric"><span>active runs</span><strong>{active_runs}</strong></div>
    <div class="metric"><span>attention</span><strong>{attention}</strong></div>
    <div class="metric"><span>tmux workers</span><strong>{live_workers}</strong></div>
  </section>
  <h2>Interactive Workers</h2>
  <table><thead><tr><th>target</th><th>kind</th><th>state</th><th>command</th><th>path</th><th>steer</th></tr></thead><tbody>{''.join(worker_rows)}</tbody></table>
  <h2>Autodev Runs</h2>
  <table><thead><tr><th>repo</th><th>run</th><th>state</th><th>phase</th><th>latest artifact</th><th>next action</th></tr></thead><tbody>{''.join(run_rows)}</tbody></table>
  <h2>Markdown Report</h2>
  <pre>{html.escape(markdown)}</pre>
</main>
</body>
</html>
"""


def render_markdown(
    runs: Sequence[RunSummary],
    *,
    generated_at: datetime,
    stale_after: timedelta,
    worker_sessions: Sequence[WorkerSession] = (),
    repo_roots: Sequence[Path] = (),
    dashboard_url: str | None = None,
) -> str:
    lines = [
        "# Dev Orchestrator Active Run Status",
        "",
        f"Generated: {generated_at.isoformat()}",
        f"Active runs: {sum(1 for run in runs if run.active)}",
        f"Attention needed: {sum(1 for run in runs if run.attention)}",
        f"Interactive tmux workers: {sum(1 for session in worker_sessions if not session.dead)}",
        f"Stale threshold: {int(stale_after.total_seconds() // 60)} minutes",
        "",
    ]
    if dashboard_url:
        lines.extend([f"Dashboard: {dashboard_url}", ""])
    lines.extend(["## Interactive Workers", ""])
    if worker_sessions:
        for session in worker_sessions:
            state = "dead" if session.dead else "active" if session.active else "idle"
            repo = session.repo_slug(repo_roots) or "unknown"
            lines.append(
                f"- `{session.target()}` {session.kind()} {state}; repo={repo}; command=`{session.current_command or 'unknown'}`; path=`{session.current_path or 'unknown'}`"
            )
            lines.append(f"  Steer: `tmux send-keys -t {session.target()} '<message>' C-m`")
    else:
        lines.append("No supervised tmux worker panes found.")
    lines.append("")
    if not runs:
        lines.extend([
            "## Autodev Runs",
            "",
            "No active or recent orchestrator runs were found.",
            "",
        ])
        return "\n".join(lines)
    for run in runs:
        latest = run.latest_artifact()
        lines.extend(
            [
                f"## {run.repo_slug} / {run.run_id}",
                "",
                f"Status: {'attention' if run.attention else 'active' if run.active else 'recent'}",
                f"Phase: {run.phase()}",
                f"Branch: {run.branch or 'unknown'}",
                f"Run root: `{run.run_root}`",
                f"Started: {_format_age(generated_at, run.run_started_at)}",
                f"Latest artifact: `{latest.path if latest else 'none'}` ({_format_age(generated_at, latest.mtime) if latest else 'unknown'})",
                f"Run-root latest: `{run.latest_run_artifact.path if run.latest_run_artifact else 'none'}` ({_format_age(generated_at, run.latest_run_artifact.mtime) if run.latest_run_artifact else 'unknown'})",
                f"Repo/gen latest: `{run.latest_repo_artifact.path if run.latest_repo_artifact else 'none'}` ({_format_age(generated_at, run.latest_repo_artifact.mtime) if run.latest_repo_artifact else 'unknown'})",
                f"Implementation plan: `{run.implementation_plan if run.implementation_plan else 'missing'}`",
                f"GBrain context: `{run.gbrain_context if run.gbrain_context else 'missing'}`",
                f"Dirty repo entries: {len(run.dirty_entries)} shown / {'dirty' if run.dirty_entries else 'clean'}",
            ]
        )
        if run.attention_reason:
            lines.append(f"Attention reason: {run.attention_reason}")
        if run.phase_heartbeat:
            updated = run.phase_heartbeat.get("updated_at") or "unknown"
            detail = run.phase_heartbeat.get("detail") or "none"
            artifact = run.phase_heartbeat.get("artifact") or "none"
            lines.append(f"Phase heartbeat: updated={updated}; detail={detail}; artifact=`{artifact}`")
        lines.append(f"Next action: {run.next_action}")
        lines.append("")
        if run.active_processes:
            lines.append("### Processes")
            lines.append("")
            for process in run.active_processes[:8]:
                lines.append(f"- `{process.pid}` {process.label()} elapsed={process.elapsed} cpu={process.cpu:.1f}%")
            lines.append("")
        if run.dirty_entries:
            lines.append("### Dirty Entries")
            lines.append("")
            for entry in run.dirty_entries:
                lines.append(f"- `{entry}`")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_gbrain_page(slug: str, markdown: str) -> str | None:
    if not shutil.which("gbrain"):
        return "gbrain binary not found"
    body = "---\ntype: report\ntitle: Dev Orchestrator Active Run Status\n---\n\n" + markdown
    try:
        result = subprocess.run(
            ["gbrain", "put", slug, "--content", body],
            cwd="/tmp",
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    if result.returncode != 0:
        return (result.stderr or result.stdout or "gbrain put failed").strip()[:400]
    return None


def generate_status_report(
    *,
    repo_roots: Sequence[Path],
    report_dir: Path,
    now: datetime,
    stale_after: timedelta,
    recent_after: datetime,
    write_gbrain: bool = True,
    gbrain_slug: str = DEFAULT_GBRAIN_SLUG,
    processes: Sequence[ProcessRecord] | None = None,
    worker_sessions: Sequence[WorkerSession] | None = None,
    dashboard_url: str | None = None,
) -> StatusResult:
    session_records = list(worker_sessions) if worker_sessions is not None else list_tmux_worker_sessions() if processes is None else []
    effective_dashboard_url = dashboard_url if dashboard_url is not None else default_dashboard_url()
    runs = collect_status(
        repo_roots=repo_roots,
        now=now,
        stale_after=stale_after,
        recent_after=recent_after,
        processes=processes,
    )
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"dev-run-status-{now.strftime('%Y%m%d-%H%M%S')}.md"
    markdown = render_markdown(
        runs,
        generated_at=now,
        stale_after=stale_after,
        worker_sessions=session_records,
        repo_roots=repo_roots,
        dashboard_url=effective_dashboard_url,
    )
    path.write_text(markdown, encoding="utf-8")
    (report_dir / "dev-run-status-latest.md").write_text(markdown, encoding="utf-8")
    dashboard_path = report_dir / DEFAULT_DASHBOARD_FILENAME
    dashboard_path.write_text(
        render_dashboard_html(runs, session_records, generated_at=now, stale_after=stale_after, markdown=markdown),
        encoding="utf-8",
    )
    gbrain_error = write_gbrain_page(gbrain_slug, markdown) if write_gbrain else None
    return StatusResult(
        markdown_path=path,
        gbrain_slug=None if gbrain_error or not write_gbrain else gbrain_slug,
        gbrain_error=gbrain_error,
        runs=runs,
        generated_at=now,
        stale_after=stale_after,
        worker_sessions=session_records,
        dashboard_path=dashboard_path,
        dashboard_url=effective_dashboard_url,
    )


def telegram_summary(result: StatusResult) -> str:
    lines = [
        "Dev orchestrator active-run status ready.",
        f"Active runs: {result.active_count}",
        f"Attention needed: {result.attention_count}",
        f"Interactive workers: {result.worker_count}",
        f"Report: {result.markdown_path}",
    ]
    if result.dashboard_url:
        lines.append(f"Dashboard: {result.dashboard_url}")
    elif result.dashboard_path:
        lines.append(f"Dashboard file: {result.dashboard_path}")
    if result.gbrain_slug:
        lines.append(f"gbrain: {result.gbrain_slug}")
    elif result.gbrain_error:
        lines.append(f"gbrain: not synced ({result.gbrain_error})")
    display_runs = [run for run in result.runs if run.active or run.attention]
    if not display_runs:
        display_runs = result.runs[:5]
    for run in display_runs[:5]:
        latest = run.latest_artifact()
        lines.append("")
        lines.append(f"{run.repo_slug} / {run.run_id}")
        lines.append(f"phase: {run.phase()}")
        lines.append(f"latest: {latest.path if latest else 'none'}")
        if latest:
            lines.append(f"age: {_format_age(result.generated_at, latest.mtime)}")
        lines.append(f"next: {run.next_action}")
    hidden = len(result.runs) - min(len(display_runs), 5)
    if hidden > 0:
        lines.append(f"\n... plus {hidden} recent inactive run(s) in the Markdown report.")
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Report active dev orchestrator runs")
    parser.add_argument("--repo-root", action="append", help="Root containing repo checkouts. Repeatable or comma-separated")
    parser.add_argument("--out-dir", default=os.getenv("HERMES_DEV_RUN_STATUS_DIR", str(DEFAULT_REPORT_DIR)))
    parser.add_argument("--timezone", default=os.getenv("HERMES_DEV_RUN_STATUS_TIMEZONE", DEFAULT_TIMEZONE))
    parser.add_argument(
        "--stale-minutes",
        type=float,
        default=float(os.getenv("HERMES_DEV_RUN_STATUS_STALE_MINUTES", DEFAULT_STALE_MINUTES)),
    )
    parser.add_argument(
        "--recent-hours",
        type=float,
        default=float(os.getenv("HERMES_DEV_RUN_STATUS_RECENT_HOURS", DEFAULT_RECENT_HOURS)),
    )
    parser.add_argument("--gbrain-slug", default=os.getenv("HERMES_DEV_RUN_STATUS_GBRAIN_SLUG", DEFAULT_GBRAIN_SLUG))
    parser.add_argument(
        "--dashboard-url",
        default=os.getenv("HERMES_DEV_RUN_STATUS_DASHBOARD_URL"),
        help="Public or tailnet URL for the generated status dashboard",
    )
    parser.add_argument("--no-gbrain", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    _load_hermes_env()
    args = build_arg_parser().parse_args(argv)
    local_tz = _timezone(args.timezone)
    now = datetime.now(timezone.utc).astimezone(local_tz)
    stale_after = timedelta(minutes=args.stale_minutes)
    recent_after = now - timedelta(hours=args.recent_hours)
    root_values: list[str] = []
    for value in args.repo_root or []:
        root_values.extend(_parse_csv(value))
    result = generate_status_report(
        repo_roots=_repo_roots(root_values),
        report_dir=Path(args.out_dir).expanduser(),
        now=now,
        stale_after=stale_after,
        recent_after=recent_after,
        write_gbrain=not args.no_gbrain,
        gbrain_slug=args.gbrain_slug,
        dashboard_url=args.dashboard_url,
    )
    print(telegram_summary(result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
