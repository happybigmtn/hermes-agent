"""Daily development teaching report for Hermes cron.

The report is intentionally deterministic. GitHub is the source of commit
facts; Hermes turns those facts into a teaching artifact, a running mastery
checklist, and a PDF attachment tag that cron delivery can send to Telegram.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python 3.11+ always has zoneinfo.
    ZoneInfo = None  # type: ignore[assignment]


DEFAULT_LOOKBACK_HOURS = 24
DEFAULT_TIMEZONE = "America/New_York"
DEFAULT_REPORT_DIR = Path.home() / ".hermes" / "reports"
CHECKLIST_NAME = "HUMAN-UNDERSTANDING-CHECKLIST.md"
GITHUB_API_BASE = "https://api.github.com"


@dataclass(frozen=True)
class RepoRef:
    full_name: str
    archived: bool = False
    private: bool = False
    updated_at: str = ""


@dataclass
class CommitRecord:
    repo: str
    sha: str
    subject: str
    message: str
    authored_at: str
    author: str
    url: str
    branches: set[str] = field(default_factory=set)
    additions: int = 0
    deletions: int = 0
    files: list[dict[str, Any]] = field(default_factory=list)

    def short_sha(self) -> str:
        return self.sha[:7]

    def kind(self) -> str:
        lowered = self.subject.lower().strip()
        match = re.match(r"([a-z]+)(?:\([^)]*\))?:", lowered)
        if match:
            return match.group(1)
        first = lowered.split(" ", 1)[0]
        if first in {"fix", "add", "remove", "update", "document", "test"}:
            return first
        return "change"

    def touches_tests(self) -> bool:
        for item in self.files:
            path = str(item.get("filename", "")).lower()
            if (
                "/test" in path
                or path.startswith("test")
                or "tests/" in path
                or path.endswith("_test.py")
                or path.endswith(".test.ts")
                or path.endswith(".spec.ts")
            ):
                return True
        return False


@dataclass
class OrchestratorPhaseRecord:
    repo: str
    run_id: str
    run_root: Path
    phases: list[str]
    started_at: datetime | None
    updated_at: datetime | None
    latest_phase: str
    latest_status: str
    latest_detail: str
    latest_artifact: str
    entry_count: int

    def phase_path(self) -> str:
        return " -> ".join(self.phases) if self.phases else "unknown"


@dataclass
class ReportResult:
    markdown_path: Path
    pdf_path: Path
    checklist_path: Path
    gbrain_slug: str | None
    gbrain_error: str | None
    commit_count: int
    repo_count: int
    repo_names: list[str]
    orchestrator_run_count: int
    errors: list[str]


class GitHubAPIError(RuntimeError):
    pass


class GitHubClient:
    def __init__(self, token: str, base_url: str = GITHUB_API_BASE) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        pages = self.paginate(path, params=params, stop_after_first=True)
        return pages[0] if pages else None

    def paginate(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        stop_after_first: bool = False,
    ) -> list[Any]:
        url = self._url(path, params)
        out: list[Any] = []
        while url:
            data, next_url = self._request(url)
            if isinstance(data, list):
                out.extend(data)
            else:
                out.append(data)
            if stop_after_first:
                break
            url = next_url
        return out

    def _url(self, path: str, params: dict[str, Any] | None = None) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            base = path
        else:
            base = self.base_url + "/" + path.lstrip("/")
        if not params:
            return base
        query = urllib.parse.urlencode(
            {key: value for key, value in params.items() if value is not None}
        )
        if not query:
            return base
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}{query}"

    def _request(self, url: str) -> tuple[Any, str | None]:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "hermes-daily-dev-report",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8")
                data = json.loads(body) if body else None
                return data, _next_link(response.headers.get("Link", ""))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            message = body
            try:
                parsed = json.loads(body)
                message = parsed.get("message", body)
            except Exception:
                pass
            raise GitHubAPIError(f"GitHub API {exc.code} for {url}: {message}") from exc
        except urllib.error.URLError as exc:
            raise GitHubAPIError(f"GitHub API request failed for {url}: {exc}") from exc


def _next_link(link_header: str) -> str | None:
    for item in link_header.split(","):
        if "rel=\"next\"" not in item:
            continue
        match = re.search(r"<([^>]+)>", item)
        if match:
            return match.group(1)
    return None


def resolve_github_token() -> str:
    for key in ("GH_TOKEN", "GITHUB_TOKEN"):
        value = os.getenv(key, "").strip()
        if value:
            return value
    gh = shutil.which("gh")
    if not gh:
        raise RuntimeError("GitHub token unavailable: set GH_TOKEN or install gh")
    result = subprocess.run(
        [gh, "auth", "token"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    token = result.stdout.strip()
    if result.returncode != 0 or not token:
        raise RuntimeError("GitHub token unavailable: gh auth token failed")
    return token


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _parse_repo_list(values: Iterable[str] | None) -> list[str]:
    repos: list[str] = []
    for raw in values or []:
        for item in _parse_csv(raw):
            if item not in repos:
                repos.append(item)
    for item in _parse_csv(os.getenv("HERMES_DAILY_REPORT_REPOS")):
        if item not in repos:
            repos.append(item)
    return repos


def discover_repos(
    client: GitHubClient,
    *,
    owner: str | None = None,
    explicit_repos: list[str] | None = None,
    include_archived: bool = False,
) -> list[RepoRef]:
    if explicit_repos:
        return [RepoRef(full_name=repo) for repo in explicit_repos]

    viewer = client.get("/user") or {}
    viewer_login = str(viewer.get("login") or "").lower()
    owner_filter = (owner or os.getenv("HERMES_DAILY_REPORT_OWNER") or "").strip()

    raw_repos: list[Any]
    if owner_filter and owner_filter.lower() != viewer_login:
        try:
            raw_repos = client.paginate(
                f"/orgs/{owner_filter}/repos",
                params={"per_page": 100, "type": "all", "sort": "updated"},
            )
        except GitHubAPIError:
            raw_repos = client.paginate(
                "/user/repos",
                params={
                    "per_page": 100,
                    "affiliation": "owner,collaborator,organization_member",
                    "sort": "updated",
                    "direction": "desc",
                },
            )
            raw_repos = [
                repo
                for repo in raw_repos
                if str(repo.get("owner", {}).get("login", "")).lower()
                == owner_filter.lower()
            ]
    else:
        raw_repos = client.paginate(
            "/user/repos",
            params={
                "per_page": 100,
                "affiliation": "owner,collaborator,organization_member",
                "sort": "updated",
                "direction": "desc",
            },
        )
        if owner_filter:
            raw_repos = [
                repo
                for repo in raw_repos
                if str(repo.get("owner", {}).get("login", "")).lower()
                == owner_filter.lower()
            ]

    repos: list[RepoRef] = []
    for repo in raw_repos:
        full_name = str(repo.get("full_name") or "").strip()
        if not full_name:
            continue
        archived = bool(repo.get("archived"))
        if archived and not include_archived:
            continue
        repos.append(
            RepoRef(
                full_name=full_name,
                archived=archived,
                private=bool(repo.get("private")),
                updated_at=str(repo.get("updated_at") or ""),
            )
        )
    return repos


def _repo_updated_in_window(repo: RepoRef, since: datetime) -> bool:
    if not repo.updated_at:
        return True
    try:
        updated = datetime.fromisoformat(repo.updated_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return updated >= since.astimezone(timezone.utc)


def collect_commits(
    client: GitHubClient,
    repos: list[RepoRef],
    *,
    since: datetime,
    until: datetime,
) -> tuple[list[CommitRecord], list[str]]:
    commit_map: dict[tuple[str, str], CommitRecord] = {}
    errors: list[str] = []
    since_text = since.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    until_text = until.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    for repo in repos:
        local_repo = _find_local_repo(repo.full_name)
        if local_repo is not None:
            local_commits, local_errors = _collect_local_repo_commits(
                repo.full_name,
                local_repo,
                since=since,
                until=until,
            )
            errors.extend(local_errors)
            for record in local_commits:
                commit_map[(record.repo, record.sha)] = record
            continue

        try:
            branches = client.paginate(
                f"/repos/{repo.full_name}/branches", params={"per_page": 100}
            )
        except GitHubAPIError as exc:
            errors.append(f"{repo.full_name}: could not list branches: {exc}")
            continue

        for branch in branches:
            branch_name = str(branch.get("name") or "").strip()
            if not branch_name:
                continue
            try:
                commits = client.paginate(
                    f"/repos/{repo.full_name}/commits",
                    params={
                        "sha": branch_name,
                        "since": since_text,
                        "until": until_text,
                        "per_page": 100,
                    },
                )
            except GitHubAPIError as exc:
                errors.append(
                    f"{repo.full_name}:{branch_name}: could not list commits: {exc}"
                )
                continue
            for raw in commits:
                record = _commit_from_api(repo.full_name, branch_name, raw)
                key = (record.repo, record.sha)
                if key in commit_map:
                    commit_map[key].branches.add(branch_name)
                    continue
                try:
                    detail = client.get(f"/repos/{repo.full_name}/commits/{record.sha}")
                    _apply_commit_detail(record, detail or {})
                except GitHubAPIError as exc:
                    errors.append(
                        f"{repo.full_name}@{record.short_sha()}: "
                        f"could not load file stats: {exc}"
                    )
                commit_map[key] = record

    records = sorted(
        commit_map.values(),
        key=lambda item: (item.authored_at or "", item.repo, item.sha),
        reverse=True,
    )
    return records, errors


def _repo_search_roots() -> list[Path]:
    configured = os.getenv("HERMES_DAILY_REPORT_REPO_ROOTS", "").strip()
    if configured:
        return [Path(item).expanduser() for item in _parse_csv(configured)]
    return [Path("/srv/dev/repos"), Path.home() / "coding", Path.home() / "Coding"]


def _find_local_repo(full_name: str) -> Path | None:
    repo_name = full_name.rsplit("/", 1)[-1]
    for root in _repo_search_roots():
        path = root / repo_name
        if not (path / ".git").exists():
            continue
        try:
            remotes = subprocess.run(
                ["git", "-C", str(path), "remote", "-v"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except Exception:
            continue
        if remotes.returncode == 0 and _remote_text_matches(remotes.stdout, full_name):
            return path
    return None


def collect_orchestrator_phase_runs(
    repos: list[RepoRef],
    *,
    since: datetime,
    until: datetime,
) -> list[OrchestratorPhaseRecord]:
    records: list[OrchestratorPhaseRecord] = []
    for repo in repos:
        local_repo = _find_local_repo(repo.full_name)
        if local_repo is None:
            continue
        orchestrator_root = local_repo / ".auto" / "orchestrator"
        if not orchestrator_root.is_dir():
            continue
        for run_root in sorted(orchestrator_root.iterdir()):
            if not run_root.is_dir():
                continue
            record = _phase_record_from_run_root(
                repo.full_name,
                run_root,
                since=since,
                until=until,
            )
            if record is not None:
                records.append(record)
    records.sort(
        key=lambda item: item.updated_at or item.started_at or datetime.fromtimestamp(0, tz=timezone.utc),
        reverse=True,
    )
    return records


def _phase_record_from_run_root(
    repo: str,
    run_root: Path,
    *,
    since: datetime,
    until: datetime,
) -> OrchestratorPhaseRecord | None:
    entries = _read_phase_entries(run_root)
    if not entries:
        return None
    dated_entries = [
        (entry, parsed)
        for entry in entries
        if (parsed := _parse_phase_timestamp(str(entry.get("updated_at") or ""))) is not None
    ]
    if dated_entries:
        first_seen = min(parsed for _, parsed in dated_entries)
        last_seen = max(parsed for _, parsed in dated_entries)
        if last_seen < since.astimezone(timezone.utc) or first_seen > until.astimezone(timezone.utc):
            return None
        latest_entry, latest_at = max(dated_entries, key=lambda item: item[1])
    else:
        stat = _safe_file_stat(run_root / "phase-history.jsonl") or _safe_file_stat(run_root / "phase-heartbeat.json")
        if stat is None:
            return None
        observed = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        if observed < since.astimezone(timezone.utc) or observed > until.astimezone(timezone.utc):
            return None
        first_seen = observed
        last_seen = observed
        latest_entry = entries[-1]
        latest_at = observed

    phases: list[str] = []
    for entry in entries:
        phase = str(entry.get("phase") or "").strip()
        if phase and phase not in phases:
            phases.append(phase)
    run_id = str(latest_entry.get("run_id") or run_root.name)
    return OrchestratorPhaseRecord(
        repo=repo,
        run_id=run_id,
        run_root=run_root,
        phases=phases,
        started_at=first_seen,
        updated_at=latest_at or last_seen,
        latest_phase=str(latest_entry.get("phase") or "unknown"),
        latest_status=str(latest_entry.get("status") or "unknown"),
        latest_detail=str(latest_entry.get("detail") or ""),
        latest_artifact=str(latest_entry.get("artifact") or ""),
        entry_count=len(entries),
    )


def _read_phase_entries(run_root: Path) -> list[dict[str, Any]]:
    history = run_root / "phase-history.jsonl"
    entries: list[dict[str, Any]] = []
    if history.exists():
        for raw in history.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                entries.append(parsed)
    if entries:
        return entries
    heartbeat = run_root / "phase-heartbeat.json"
    if not heartbeat.exists():
        return []
    try:
        parsed = json.loads(heartbeat.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return []
    return [parsed] if isinstance(parsed, dict) else []


def _parse_phase_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_file_stat(path: Path) -> os.stat_result | None:
    try:
        return path.stat()
    except OSError:
        return None


def _remote_text_matches(remote_text: str, full_name: str) -> bool:
    wanted = full_name.lower().removesuffix(".git")
    normalized = remote_text.lower().replace(":", "/")
    normalized = normalized.replace("git@github.com/", "github.com/")
    normalized = normalized.replace("https://", "")
    normalized = normalized.replace("http://", "")
    return wanted in normalized.replace(".git", "")


def _matching_remote_names(path: Path, full_name: str) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(path), "remote", "-v"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        return []
    remotes: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        remote, url = parts[:2]
        if remote not in remotes and _remote_text_matches(url, full_name):
            remotes.append(remote)
    return remotes


def _collect_local_repo_commits(
    full_name: str,
    path: Path,
    *,
    since: datetime,
    until: datetime,
) -> tuple[list[CommitRecord], list[str]]:
    errors: list[str] = []
    remotes = _matching_remote_names(path, full_name)
    if not remotes:
        return [], [f"{full_name}: local clone has no matching GitHub remote"]
    for remote in remotes:
        fetch = subprocess.run(
            ["git", "-C", str(path), "fetch", remote, "--prune", "--quiet"],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        if fetch.returncode != 0:
            errors.append(
                f"{full_name}: git fetch {remote} failed; using existing refs: "
                f"{(fetch.stderr or fetch.stdout).strip()}"
            )

    refs = _remote_refs(path, remotes)
    if not refs:
        remote_text = ", ".join(remotes)
        return [], errors + [f"{full_name}: no remote refs found for {remote_text}"]

    since_text = since.astimezone(timezone.utc).isoformat()
    until_text = until.astimezone(timezone.utc).isoformat()
    log = subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "log",
            "--all",
            f"--since={since_text}",
            f"--until={until_text}",
            "--format=%H%x1f%aI%x1f%an%x1f%s%x1e",
            *refs,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if log.returncode != 0:
        return [], errors + [f"{full_name}: git log failed: {log.stderr.strip()}"]

    records: list[CommitRecord] = []
    seen: set[str] = set()
    for entry in log.stdout.split("\x1e"):
        entry = entry.strip("\n")
        if not entry:
            continue
        parts = entry.split("\x1f")
        if len(parts) < 4:
            errors.append(f"{full_name}: malformed git log entry: {entry[:120]}")
            continue
        sha, authored_at, author, subject = parts[:4]
        if sha in seen:
            continue
        seen.add(sha)
        record = CommitRecord(
            repo=full_name,
            sha=sha,
            subject=subject or "(no commit message)",
            message=subject or "",
            authored_at=authored_at,
            author=author or "unknown",
            url=f"https://github.com/{full_name}/commit/{sha}",
        )
        record.branches = _local_branches_containing(path, sha, remotes)
        _apply_local_file_stats(path, record)
        records.append(record)
    return records, errors


def _remote_refs(path: Path, remotes: list[str]) -> list[str]:
    refs: list[str] = []
    for remote in remotes:
        result = subprocess.run(
            ["git", "-C", str(path), "for-each-ref", "--format=%(refname)", f"refs/remotes/{remote}"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if result.returncode != 0:
            continue
        for raw in result.stdout.splitlines():
            ref = raw.strip()
            if ref and not ref.endswith("/HEAD"):
                refs.append(ref)
    return refs


def _local_branches_containing(path: Path, sha: str, remotes: list[str]) -> set[str]:
    result = subprocess.run(
        ["git", "-C", str(path), "branch", "-r", "--contains", sha, "--format=%(refname:short)"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    branches: set[str] = set()
    if result.returncode != 0:
        return branches
    for raw in result.stdout.splitlines():
        branch = raw.strip()
        if not branch or branch.endswith("/HEAD"):
            continue
        if not any(branch == remote or branch.startswith(f"{remote}/") for remote in remotes):
            continue
        branches.add(branch)
    return branches


def _apply_local_file_stats(path: Path, record: CommitRecord) -> None:
    stats = subprocess.run(
        ["git", "-C", str(path), "show", "--numstat", "--format=", "--no-renames", record.sha],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if stats.returncode != 0:
        return
    files: list[dict[str, Any]] = []
    additions = 0
    deletions = 0
    for line in stats.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        add_text, del_text, filename = parts[:3]
        add_count = int(add_text) if add_text.isdigit() else 0
        del_count = int(del_text) if del_text.isdigit() else 0
        additions += add_count
        deletions += del_count
        files.append(
            {
                "filename": filename,
                "status": "modified",
                "additions": add_count,
                "deletions": del_count,
            }
        )
    record.additions = additions
    record.deletions = deletions
    record.files = files


def _commit_from_api(repo: str, branch: str, raw: dict[str, Any]) -> CommitRecord:
    commit = raw.get("commit") or {}
    message = str(commit.get("message") or "").strip()
    author_info = commit.get("author") or {}
    api_author = raw.get("author") or {}
    author = str(api_author.get("login") or author_info.get("name") or "unknown")
    subject = message.splitlines()[0] if message else "(no commit message)"
    record = CommitRecord(
        repo=repo,
        sha=str(raw.get("sha") or ""),
        subject=subject,
        message=message,
        authored_at=str(author_info.get("date") or ""),
        author=author,
        url=str(raw.get("html_url") or ""),
    )
    record.branches.add(branch)
    return record


def _apply_commit_detail(record: CommitRecord, detail: dict[str, Any]) -> None:
    stats = detail.get("stats") or {}
    record.additions = int(stats.get("additions") or 0)
    record.deletions = int(stats.get("deletions") or 0)
    files = detail.get("files") or []
    if isinstance(files, list):
        record.files = [
            {
                "filename": item.get("filename"),
                "status": item.get("status"),
                "additions": item.get("additions", 0),
                "deletions": item.get("deletions", 0),
            }
            for item in files
            if isinstance(item, dict)
        ]


def render_markdown(
    commits: list[CommitRecord],
    errors: list[str],
    *,
    phase_runs: list[OrchestratorPhaseRecord] | None = None,
    repos_scanned: int,
    since: datetime,
    until: datetime,
    local_tz: timezone,
    checklist_path: Path,
) -> str:
    since_local = since.astimezone(local_tz)
    until_local = until.astimezone(local_tz)
    grouped = _group_by_repo(commits)
    repo_names = sorted(grouped)
    phase_runs = phase_runs or []

    lines: list[str] = []
    lines.append(f"# Daily Development Teaching Report - {until_local:%Y-%m-%d}")
    lines.append("")
    lines.append(
        f"Window: {since_local:%Y-%m-%d %H:%M %Z} "
        f"to {until_local:%Y-%m-%d %H:%M %Z}"
    )
    lines.append(f"Repos scanned: {repos_scanned}")
    lines.append(f"Repos with commits: {len(repo_names)}")
    lines.append(f"Commits found: {len(commits)}")
    lines.append(f"Orchestrator phase histories: {len(phase_runs)}")
    lines.append(f"Checklist: {checklist_path}")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append("")
    if commits:
        lines.append(
            "Today changed the codebase in concrete, reviewable commits. "
            "The report teaches from the commit facts first: what changed, "
            "why the change likely existed, what edge cases deserve attention, "
            "and how this affects the broader development loop."
        )
        lines.append("")
        lines.append("Repositories with activity:")
        for repo in repo_names:
            repo_commits = grouped[repo]
            lines.append(f"- {repo}: {len(repo_commits)} commit(s)")
        if phase_runs:
            lines.append("")
            lines.append("Local orchestrator evidence:")
            for run in phase_runs[:8]:
                lines.append(
                    f"- {run.repo}/{run.run_id}: "
                    f"{run.latest_phase}:{run.latest_status} "
                    f"after {run.entry_count} phase event(s)"
                )
    else:
        lines.append(
            "No GitHub commits were found in this window. That is still useful: "
            "the development loop should distinguish quiet periods from hidden "
            "unreported work. The next check is whether any unpushed local work "
            "or active orchestrator run exists outside GitHub."
        )
    lines.append("")
    lines.append("## Mastery Gate")
    lines.append("")
    lines.append(
        "Do not treat this report as mastered until the human can explain "
        "the problem, the solution, and the impact for the active repositories "
        "without rereading the commit list. The running checklist records those "
        "understanding checks."
    )
    lines.append("")

    if phase_runs:
        lines.extend(_orchestrator_phase_section(phase_runs, local_tz=local_tz))

    for repo in repo_names:
        repo_commits = grouped[repo]
        lines.extend(_repo_teaching_section(repo, repo_commits))

    lines.append("## What Remains")
    lines.append("")
    if commits:
        untested = [commit for commit in commits if not commit.touches_tests()]
        if untested:
            lines.append(
                "Some commits do not visibly touch test files. This does not prove "
                "they are unsafe, but it is the first edge-case question for review."
            )
            for commit in untested[:12]:
                lines.append(
                    f"- {commit.repo}@{commit.short_sha()}: confirm validation for "
                    f"{commit.subject}"
                )
            if len(untested) > 12:
                lines.append(f"- plus {len(untested) - 12} more commit(s)")
        else:
            lines.append(
                "Every reported commit touched an obvious test or validation surface. "
                "The remaining work is to confirm CI/deploy state and product impact."
            )
    else:
        lines.append(
            "No commit-specific follow-up. Check active orchestrator runs and "
            "uncommitted local work if the human expected progress today."
        )
    lines.append("")
    if errors:
        lines.append("## Collection Errors")
        lines.append("")
        lines.append(
            "These errors mean the report may be incomplete. Fix them before using "
            "the report as a full development audit."
        )
        for error in errors[:30]:
            lines.append(f"- {error}")
        if len(errors) > 30:
            lines.append(f"- plus {len(errors) - 30} more error(s)")
        lines.append("")

    lines.append("## Teaching Checklist Added For This Run")
    lines.append("")
    lines.append("- [ ] Problem: I can explain what changed and why the problem existed.")
    lines.append("- [ ] Branches: I can name which branches carried the work.")
    lines.append("- [ ] Solution: I can explain why this resolution fits the codebase.")
    lines.append("- [ ] Edge cases: I can name the tests or checks that should protect it.")
    lines.append("- [ ] Orchestrator: I can connect run phases to commits, artifacts, or blockers.")
    lines.append("- [ ] Context: I can explain what this will impact next.")
    lines.append("")
    lines.append("## Source Of Truth")
    lines.append("")
    lines.append(
        "Commit inventory came from GitHub branch refs, local clones that match "
        "GitHub remotes, and commit APIs. Orchestrator phase evidence came from "
        "local `.auto/orchestrator/*/phase-history.jsonl` or `phase-heartbeat.json` "
        "artifacts. Together these sources let the report include both landed "
        "commits and in-flight supervised work."
    )
    lines.append("")
    return "\n".join(lines)


def _group_by_repo(commits: list[CommitRecord]) -> dict[str, list[CommitRecord]]:
    grouped: dict[str, list[CommitRecord]] = {}
    for commit in commits:
        grouped.setdefault(commit.repo, []).append(commit)
    return grouped


def _orchestrator_phase_section(
    phase_runs: list[OrchestratorPhaseRecord],
    *,
    local_tz: timezone,
) -> list[str]:
    lines: list[str] = []
    lines.append("## Orchestrator Phase Evidence")
    lines.append("")
    lines.append(
        "These entries come from local run-root heartbeat artifacts. They explain "
        "what Hermes/autodev/Codex were doing between commits, so the human can "
        "distinguish landed work from active planning, execution, closeout, or blockers."
    )
    lines.append("")
    for run in phase_runs[:12]:
        updated = (
            run.updated_at.astimezone(local_tz).strftime("%Y-%m-%d %H:%M %Z")
            if run.updated_at
            else "unknown"
        )
        lines.append(f"### {run.repo} / {run.run_id}")
        lines.append("")
        lines.append(f"- Latest phase: `{run.latest_phase}: {run.latest_status}`")
        lines.append(f"- Phase path: {run.phase_path()}")
        lines.append(f"- Last heartbeat: {updated}")
        lines.append(f"- Run root: `{run.run_root}`")
        if run.latest_detail:
            lines.append(f"- Detail: {run.latest_detail}")
        if run.latest_artifact:
            lines.append(f"- Artifact: `{run.latest_artifact}`")
        lines.append(
            "- Teaching prompt: explain what this phase was supposed to prove, "
            "which artifact confirms progress, and what the next operator action should be."
        )
        lines.append("")
    if len(phase_runs) > 12:
        lines.append(f"- plus {len(phase_runs) - 12} more orchestrator run(s)")
        lines.append("")
    return lines


def _repo_teaching_section(repo: str, commits: list[CommitRecord]) -> list[str]:
    lines: list[str] = []
    lines.append(f"## {repo}")
    lines.append("")
    lines.append(f"Commit count: {len(commits)}")
    branches = sorted({branch for commit in commits for branch in commit.branches})
    lines.append(f"Branches: {_format_branches(branches, limit=12)}")
    lines.append("")
    kinds = sorted({commit.kind() for commit in commits})
    lines.append("### Problem")
    lines.append("")
    lines.append(_problem_explanation(kinds))
    lines.append("")
    lines.append("### Solution")
    lines.append("")
    for commit in commits:
        commit_branch_text = _format_branches(sorted(commit.branches), limit=4)
        lines.append(
            f"- `{commit.short_sha()}` {commit.subject} "
            f"({commit.author}, {commit_branch_text})"
        )
        if commit.url:
            lines.append(f"  Link: {commit.url}")
        if commit.files:
            file_bits = []
            for item in commit.files[:8]:
                filename = item.get("filename") or "unknown"
                status = item.get("status") or "modified"
                additions = item.get("additions") or 0
                deletions = item.get("deletions") or 0
                file_bits.append(f"{filename} ({status}, +{additions}/-{deletions})")
            lines.append("  Files: " + "; ".join(file_bits))
            if len(commit.files) > 8:
                lines.append(f"  Files: plus {len(commit.files) - 8} more")
        lines.append(
            "  Validation signal: "
            + (
                "test/validation files are visible in the diff"
                if commit.touches_tests()
                else "no obvious test file in the changed-file list"
            )
        )
    lines.append("")
    lines.append("### Edge Cases")
    lines.append("")
    lines.append(
        "Review whether the changed files cover the failure mode, not just the "
        "happy path. For commits without visible tests, ask what command or CI "
        "check proves the behavior. For commits touching orchestration, also "
        "check idempotency, credentials, and whether artifacts remain discoverable."
    )
    lines.append("")
    lines.append("### Broader Context")
    lines.append("")
    lines.append(_impact_explanation(kinds))
    lines.append("")
    return lines


def _format_branches(branches: Iterable[str], *, limit: int) -> str:
    ordered = [branch for branch in branches if branch]
    if not ordered:
        return "unknown"
    if len(ordered) <= limit:
        return ", ".join(ordered)
    shown = ", ".join(ordered[:limit])
    return f"{shown}, plus {len(ordered) - limit} more"


def _problem_explanation(kinds: list[str]) -> str:
    if "fix" in kinds:
        return (
            "At least one commit is framed as a fix, which means the prior "
            "system had a behavioral gap or reliability defect. The human "
            "should be able to say what made the previous behavior wrong and "
            "which branch now contains the correction."
        )
    if "feat" in kinds or "add" in kinds:
        return (
            "At least one commit adds capability. The important question is "
            "not just what was added, but what workflow was blocked before this "
            "capability existed."
        )
    if "docs" in kinds or "document" in kinds:
        return (
            "The work is mostly documentation or process clarification. The "
            "problem was likely ambiguity: people or agents could not reliably "
            "choose the right path without a clearer source of truth."
        )
    if "test" in kinds:
        return (
            "The work focuses on verification. That usually means the system "
            "needed a stronger proof boundary, a regression guard, or a clearer "
            "signal that a behavior is actually safe."
        )
    return (
        "The commit messages do not declare a narrow kind. The human should "
        "inspect the file list and explain the real motivation before treating "
        "this work as understood."
    )


def _impact_explanation(kinds: list[str]) -> str:
    if "fix" in kinds:
        return (
            "Fixes increase trust only if the failure mode is now impossible or "
            "caught early. The next impact question is whether future agents will "
            "see the guardrail before repeating the same mistake."
        )
    if "feat" in kinds or "add" in kinds:
        return (
            "New capability changes what the development loop can delegate. The "
            "next impact question is whether the capability has a clear operator "
            "surface, durable artifacts, and focused validation."
        )
    if "docs" in kinds or "document" in kinds:
        return (
            "Documentation matters when it changes future behavior. The next "
            "impact question is whether Hermes, Codex, and gbrain can discover "
            "this guidance without manual copy/paste."
        )
    return (
        "The broader impact is not obvious from the commit kind. Treat that as "
        "a teaching opportunity: connect the low-level file changes to the next "
        "operator decision."
    )


def update_checklist(
    checklist_path: Path,
    commits: list[CommitRecord],
    *,
    phase_runs: list[OrchestratorPhaseRecord] | None = None,
    report_markdown: Path,
    report_pdf: Path,
    since: datetime,
    until: datetime,
    local_tz: timezone,
) -> None:
    checklist_path.parent.mkdir(parents=True, exist_ok=True)
    if checklist_path.exists():
        existing = checklist_path.read_text(encoding="utf-8")
    else:
        existing = (
            "# Human Understanding Checklist\n\n"
            "This running document tracks what the human should understand "
            "from daily development reports. Check an item only after the human "
            "can explain it in her own words.\n\n"
        )
    since_local = since.astimezone(local_tz)
    until_local = until.astimezone(local_tz)
    repos = sorted({commit.repo for commit in commits})
    repo_text = ", ".join(repos) if repos else "none"
    phase_runs = phase_runs or []
    section = [
        f"## {until_local:%Y-%m-%d %H:%M %Z} Report",
        "",
        f"Window: {since_local:%Y-%m-%d %H:%M %Z} to {until_local:%Y-%m-%d %H:%M %Z}",
        f"Markdown: {report_markdown}",
        f"PDF: {report_pdf}",
        f"Repos: {repo_text}",
        f"Commits: {len(commits)}",
        f"Orchestrator phases: {len(phase_runs)}",
        "",
        "- [ ] Problem: she can explain what changed and why the prior state was insufficient.",
        "- [ ] Branches: she can name the branches and whether work came from Hermes or elsewhere.",
        "- [ ] Solution: she can explain the design decisions and why this approach fits.",
        "- [ ] Edge cases: she can name validation, missing tests, and risk boundaries.",
        "- [ ] Orchestrator: she can connect phase history to artifacts, commits, blockers, or next action.",
        "- [ ] Context: she can explain what downstream work this enables or blocks.",
        "",
    ]
    checklist_path.write_text(existing.rstrip() + "\n\n" + "\n".join(section), encoding="utf-8")


def write_simple_pdf(markdown_text: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    page_width = 612
    page_height = 792
    margin_x = 54
    margin_y = 54
    y_start = page_height - margin_y
    y_min = margin_y
    pages: list[list[tuple[str, int, str, int]]] = [[]]
    y = y_start

    def add_line(font: str, size: int, text: str, indent: int = 0) -> None:
        nonlocal y
        line_height = size + 5
        if y - line_height < y_min:
            pages.append([])
            y = y_start
        pages[-1].append((font, size, text, indent))
        y -= line_height

    for raw_line in markdown_text.splitlines():
        font, size, indent, text = _line_style(raw_line)
        width = max(40, int((page_width - margin_x * 2 - indent) / (size * 0.52)))
        wrapped = textwrap.wrap(
            text,
            width=width,
            replace_whitespace=False,
            drop_whitespace=True,
            break_long_words=True,
        ) or [""]
        for index, part in enumerate(wrapped):
            add_line(font, size, part, indent if index == 0 else indent + 14)

    objects: list[bytes] = []

    def add_object(body: str | bytes) -> int:
        if isinstance(body, str):
            raw = body.encode("latin-1", errors="replace")
        else:
            raw = body
        objects.append(raw)
        return len(objects)

    catalog_id = add_object("<< /Type /Catalog /Pages 2 0 R >>")
    pages_id = add_object(b"")
    regular_font_id = add_object("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    bold_font_id = add_object("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    page_ids: list[int] = []
    content_ids: list[int] = []

    for page in pages:
        commands: list[str] = []
        cursor_y = y_start
        for font, size, text, indent in page:
            escaped = _pdf_escape(_strip_markdown(text))
            font_name = "F2" if font == "bold" else "F1"
            commands.append(
                f"BT /{font_name} {size} Tf {margin_x + indent} {cursor_y:.2f} Td "
                f"({escaped}) Tj ET"
            )
            cursor_y -= size + 5
        content = "\n".join(commands).encode("latin-1", errors="replace")
        content_id = add_object(
            b"<< /Length "
            + str(len(content)).encode("ascii")
            + b" >>\nstream\n"
            + content
            + b"\nendstream"
        )
        page_id = add_object(
            "<< /Type /Page /Parent 2 0 R "
            f"/MediaBox [0 0 {page_width} {page_height}] "
            f"/Contents {content_id} 0 R "
            f"/Resources << /Font << /F1 {regular_font_id} 0 R "
            f"/F2 {bold_font_id} 0 R >> >> >>"
        )
        page_ids.append(page_id)
        content_ids.append(content_id)

    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[pages_id - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode(
        "latin-1"
    )
    assert catalog_id == 1
    assert content_ids

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")
    xref_start = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_start}\n%%EOF\n".encode("ascii")
    )
    output_path.write_bytes(bytes(pdf))


def _line_style(line: str) -> tuple[str, int, int, str]:
    stripped = line.strip()
    if stripped.startswith("# "):
        return "bold", 18, 0, stripped[2:].strip()
    if stripped.startswith("## "):
        return "bold", 15, 0, stripped[3:].strip()
    if stripped.startswith("### "):
        return "bold", 12, 0, stripped[4:].strip()
    if stripped.startswith("- "):
        return "regular", 10, 14, "- " + stripped[2:].strip()
    if not stripped:
        return "regular", 10, 0, ""
    return "regular", 10, 0, stripped


def _strip_markdown(text: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = text.replace("**", "")
    text = text.replace("__", "")
    return text


def _pdf_escape(text: str) -> str:
    text = text.encode("latin-1", errors="replace").decode("latin-1")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def sync_to_gbrain(markdown_path: Path, pdf_path: Path, checklist_path: Path, slug: str) -> str | None:
    if not shutil.which("gbrain"):
        return "gbrain command not found"
    markdown = markdown_path.read_text(encoding="utf-8")
    put = subprocess.run(
        ["gbrain", "put", slug],
        input=markdown,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if put.returncode != 0:
        return put.stderr.strip() or put.stdout.strip() or "gbrain put failed"
    upload = subprocess.run(
        ["gbrain", "files", "upload", str(pdf_path), "--page", slug],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if upload.returncode != 0:
        return upload.stderr.strip() or upload.stdout.strip() or "gbrain PDF upload failed"
    checklist = subprocess.run(
        ["gbrain", "put", "development-human-understanding-checklist"],
        input=checklist_path.read_text(encoding="utf-8"),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if checklist.returncode != 0:
        return checklist.stderr.strip() or checklist.stdout.strip() or "gbrain checklist put failed"
    return None


def generate_report(
    *,
    client: GitHubClient,
    owner: str | None,
    explicit_repos: list[str],
    include_archived: bool,
    since: datetime,
    until: datetime,
    report_dir: Path,
    local_tz: timezone,
    write_pdf: bool = True,
    write_gbrain: bool = True,
) -> ReportResult:
    repos = discover_repos(
        client,
        owner=owner,
        explicit_repos=explicit_repos,
        include_archived=include_archived,
    )
    repos_to_scan = repos if explicit_repos else [repo for repo in repos if _repo_updated_in_window(repo, since)]
    commits, errors = collect_commits(client, repos_to_scan, since=since, until=until)
    phase_runs = collect_orchestrator_phase_runs(repos_to_scan, since=since, until=until)
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = until.astimezone(local_tz).strftime("%Y%m%d-%H%M%S")
    markdown_path = report_dir / f"daily-dev-report-{stamp}.md"
    pdf_path = report_dir / f"daily-dev-report-{stamp}.pdf"
    checklist_path = report_dir / CHECKLIST_NAME
    markdown = render_markdown(
        commits,
        errors,
        phase_runs=phase_runs,
        repos_scanned=len(repos_to_scan),
        since=since,
        until=until,
        local_tz=local_tz,
        checklist_path=checklist_path,
    )
    markdown_path.write_text(markdown, encoding="utf-8")
    if write_pdf:
        write_simple_pdf(markdown, pdf_path)
    else:
        pdf_path.write_bytes(b"")
    update_checklist(
        checklist_path,
        commits,
        phase_runs=phase_runs,
        report_markdown=markdown_path,
        report_pdf=pdf_path,
        since=since,
        until=until,
        local_tz=local_tz,
    )

    slug = f"daily-development-teaching-report-{until.astimezone(local_tz):%Y-%m-%d}"
    gbrain_error = None
    if write_gbrain:
        gbrain_error = sync_to_gbrain(markdown_path, pdf_path, checklist_path, slug)

    return ReportResult(
        markdown_path=markdown_path,
        pdf_path=pdf_path,
        checklist_path=checklist_path,
        gbrain_slug=slug if write_gbrain and not gbrain_error else None,
        gbrain_error=gbrain_error,
        commit_count=len(commits),
        repo_count=len(repos_to_scan),
        repo_names=sorted({commit.repo for commit in commits}),
        orchestrator_run_count=len(phase_runs),
        errors=errors,
    )


def telegram_summary(result: ReportResult) -> str:
    repo_text = ", ".join(result.repo_names[:8]) if result.repo_names else "none"
    if len(result.repo_names) > 8:
        repo_text += f", plus {len(result.repo_names) - 8} more"
    lines = [
        "Daily development teaching report ready.",
        f"Repos scanned: {result.repo_count}",
        f"Repos with commits: {repo_text}",
        f"Commits: {result.commit_count}",
        f"Orchestrator runs: {result.orchestrator_run_count}",
        f"Markdown: {result.markdown_path}",
        f"PDF: {result.pdf_path}",
        f"Checklist: {result.checklist_path}",
    ]
    if result.gbrain_slug:
        lines.append(f"gbrain: {result.gbrain_slug}")
    elif result.gbrain_error:
        lines.append(f"gbrain: not synced ({result.gbrain_error})")
    if result.errors:
        lines.append(f"Collection warnings: {len(result.errors)}")
    lines.append(
        "Mastery gate: problem -> solution -> edge cases -> broader impact. "
        "Do not advance the lesson until the checklist is understood."
    )
    if result.pdf_path.stat().st_size > 0:
        lines.append(f"MEDIA:{result.pdf_path}")
    return "\n".join(lines)


def _timezone(name: str) -> timezone:
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)  # type: ignore[return-value]
        except Exception:
            pass
    return timezone.utc


def _parse_datetime(value: str | None, *, default: datetime) -> datetime:
    if not value:
        return default
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a daily GitHub teaching report")
    parser.add_argument("--owner", help="GitHub user/org to scan")
    parser.add_argument(
        "--repo",
        action="append",
        help="Specific owner/repo to scan. Repeatable or comma-separated",
    )
    parser.add_argument("--include-archived", action="store_true")
    parser.add_argument(
        "--lookback-hours",
        type=float,
        default=float(os.getenv("HERMES_DAILY_REPORT_LOOKBACK_HOURS", DEFAULT_LOOKBACK_HOURS)),
    )
    parser.add_argument("--since", help="ISO timestamp override for window start")
    parser.add_argument("--until", help="ISO timestamp override for window end")
    parser.add_argument(
        "--timezone",
        default=os.getenv("HERMES_DAILY_REPORT_TIMEZONE", DEFAULT_TIMEZONE),
    )
    parser.add_argument(
        "--out-dir",
        default=os.getenv("HERMES_DAILY_REPORT_DIR", str(DEFAULT_REPORT_DIR)),
    )
    parser.add_argument("--no-pdf", action="store_true", help="Skip PDF generation")
    parser.add_argument(
        "--no-gbrain",
        action="store_true",
        help="Do not write report/checklist to gbrain",
    )
    return parser


def _load_hermes_env() -> None:
    try:
        from hermes_cli.env_loader import load_hermes_dotenv

        load_hermes_dotenv()
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    _load_hermes_env()
    args = build_arg_parser().parse_args(argv)
    local_tz = _timezone(args.timezone)
    until = _parse_datetime(args.until, default=datetime.now(timezone.utc))
    since_default = until - timedelta(hours=args.lookback_hours)
    since = _parse_datetime(args.since, default=since_default)
    token = resolve_github_token()
    client = GitHubClient(token, base_url=os.getenv("GITHUB_API_URL", GITHUB_API_BASE))
    result = generate_report(
        client=client,
        owner=args.owner,
        explicit_repos=_parse_repo_list(args.repo),
        include_archived=args.include_archived,
        since=since,
        until=until,
        report_dir=Path(args.out_dir).expanduser(),
        local_tz=local_tz,
        write_pdf=not args.no_pdf,
        write_gbrain=not args.no_gbrain,
    )
    print(telegram_summary(result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
