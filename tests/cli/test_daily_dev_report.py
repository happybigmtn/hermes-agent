from __future__ import annotations

from datetime import datetime, timezone
import subprocess

from hermes_cli.daily_dev_report import (
    CommitRecord,
    RepoRef,
    _collect_local_repo_commits,
    collect_commits,
    generate_report,
    telegram_summary,
)


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict | None]] = []

    def get(self, path, params=None):
        self.calls.append((path, params))
        if path == "/user":
            return {"login": "octo"}
        if path == "/repos/octo/demo/commits/abc1234":
            return {
                "stats": {"additions": 12, "deletions": 3},
                "files": [
                    {
                        "filename": "src/report.py",
                        "status": "modified",
                        "additions": 8,
                        "deletions": 2,
                    },
                    {
                        "filename": "tests/test_report.py",
                        "status": "added",
                        "additions": 4,
                        "deletions": 1,
                    },
                ],
            }
        return None

    def paginate(self, path, params=None, stop_after_first=False):
        self.calls.append((path, params))
        if path == "/user/repos":
            return [
                {
                    "full_name": "octo/demo",
                    "archived": False,
                    "private": True,
                    "owner": {"login": "octo"},
                }
            ]
        if path == "/repos/octo/demo/branches":
            return [{"name": "main"}, {"name": "feature/report"}]
        if path == "/repos/octo/demo/commits":
            assert params is not None
            if params["sha"] in {"main", "feature/report"}:
                return [
                    {
                        "sha": "abc1234",
                        "html_url": "https://github.com/octo/demo/commit/abc1234",
                        "author": {"login": "r"},
                        "commit": {
                            "message": "feat(report): add daily teaching report\n\nLong body",
                            "author": {"name": "R", "date": "2026-06-01T12:00:00Z"},
                        },
                    }
                ]
        return []


def test_collect_commits_dedupes_across_branches_and_loads_file_stats():
    client = FakeClient()
    since = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    until = datetime(2026, 6, 2, 0, 0, tzinfo=timezone.utc)

    commits, errors = collect_commits(
        client,
        [RepoRef("octo/demo")],
        since=since,
        until=until,
    )

    assert errors == []
    assert len(commits) == 1
    commit = commits[0]
    assert commit.repo == "octo/demo"
    assert commit.branches == {"main", "feature/report"}
    assert commit.additions == 12
    assert commit.deletions == 3
    assert commit.touches_tests() is True


def test_generate_report_writes_markdown_pdf_and_checklist(tmp_path):
    client = FakeClient()
    since = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    until = datetime(2026, 6, 2, 0, 0, tzinfo=timezone.utc)

    result = generate_report(
        client=client,
        owner="octo",
        explicit_repos=[],
        include_archived=False,
        since=since,
        until=until,
        report_dir=tmp_path,
        local_tz=timezone.utc,
        write_pdf=True,
        write_gbrain=False,
    )

    assert result.commit_count == 1
    assert result.repo_count == 1
    assert result.markdown_path.exists()
    assert result.pdf_path.read_bytes().startswith(b"%PDF-1.4")
    checklist = result.checklist_path.read_text(encoding="utf-8")
    assert "Human Understanding Checklist" in checklist
    assert "Problem:" in checklist

    summary = telegram_summary(result)
    assert "Daily development teaching report ready." in summary
    assert f"MEDIA:{result.pdf_path}" in summary


def test_commit_kind_and_test_detection():
    commit = CommitRecord(
        repo="octo/demo",
        sha="abcdef123456",
        subject="fix(api): handle missing branch",
        message="fix(api): handle missing branch",
        authored_at="2026-06-01T12:00:00Z",
        author="r",
        url="",
        files=[{"filename": "src/api.py"}],
    )

    assert commit.kind() == "fix"
    assert commit.short_sha() == "abcdef1"
    assert commit.touches_tests() is False

    commit.files.append({"filename": "tests/test_api.py"})
    assert commit.touches_tests() is True


def test_collect_local_repo_commits_reads_all_refs(tmp_path):
    repo = tmp_path / "demo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "r@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "R"], cwd=repo, check=True)
    subprocess.run(["git", "remote", "add", "origin", "git@github.com:octo/demo.git"], cwd=repo, check=True)
    (repo / "src.py").write_text("print(\"hi\")\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_src.py").write_text("def test_hi():\n    assert True\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "feat(report): prove local collection"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    subprocess.run(["git", "update-ref", "refs/remotes/origin/main", head], cwd=repo, check=True)

    since = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    until = datetime(2030, 1, 1, 0, 0, tzinfo=timezone.utc)
    commits, errors = _collect_local_repo_commits("octo/demo", repo, since=since, until=until)

    assert any("git fetch origin failed" in error for error in errors)
    assert len(commits) == 1
    assert commits[0].branches == {"origin/main"}
    assert commits[0].touches_tests() is True
