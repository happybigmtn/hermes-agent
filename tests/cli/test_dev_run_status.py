from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import subprocess

from hermes_cli.dev_run_status import (
    ProcessRecord,
    collect_status,
    generate_status_report,
    parse_process_table,
    render_markdown,
    telegram_summary,
    write_gbrain_page,
)


def _init_repo(path):
    path.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "r@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "R"], cwd=path, check=True)
    (path / "README.md").write_text("demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def test_parse_process_table_reads_ps_rows():
    rows = """\
      101       1 Sl       04:20  3.5  0.1 /usr/bin/auto gen --snapshot-only
      102     101 Sl       00:12 11.0  0.2 node /usr/local/bin/codex exec --json
    """

    records = parse_process_table(rows)

    assert [record.pid for record in records] == [101, 102]
    assert records[0].label() == "auto gen"
    assert records[1].label() == "codex exec"


def test_collect_status_uses_repo_level_gen_artifacts(tmp_path):
    repo_root = tmp_path / "repos"
    repo = repo_root / "autonomy-bitino"
    _init_repo(repo)
    run_root = repo / ".auto" / "orchestrator" / "task012"
    run_root.mkdir(parents=True)
    (run_root / "run.env").write_text("RUN_ID=task012\nREPO_SLUG=autonomy-bitino\n", encoding="utf-8")
    (run_root / "corpus.log").write_text("corpus complete\n", encoding="utf-8")
    gen = repo / "gen-20260601-223355"
    gen.mkdir()
    plan = gen / "IMPLEMENTATION_PLAN.md"
    plan.write_text("# IMPLEMENTATION_PLAN\n", encoding="utf-8")
    future_time = datetime(2030, 1, 1, 0, 0, tzinfo=timezone.utc).timestamp()
    os.utime(plan, (future_time, future_time))
    processes = [
        ProcessRecord(
            pid=1,
            ppid=0,
            stat="Sl",
            elapsed="00:10",
            cpu=2.0,
            mem=0.1,
            command=f"auto pilot autonomy-bitino --run-id task012 --run-root {run_root} --planning-only",
        ),
        ProcessRecord(
            pid=2,
            ppid=1,
            stat="Sl",
            elapsed="00:05",
            cpu=7.0,
            mem=0.1,
            command=f"/home/dev/.local/bin/auto gen --snapshot-only --cd {repo}",
        ),
        ProcessRecord(
            pid=3,
            ppid=2,
            stat="Sl",
            elapsed="00:04",
            cpu=12.0,
            mem=0.1,
            command=f"codex exec --json --cd {repo}",
        ),
    ]

    now = datetime.now(timezone.utc)
    runs = collect_status(
        repo_roots=[repo_root],
        now=now,
        stale_after=timedelta(minutes=30),
        recent_after=now - timedelta(hours=12),
        processes=processes,
    )

    assert len(runs) == 1
    run = runs[0]
    assert run.active is True
    assert run.attention is False
    assert run.phase() == "planning: auto gen snapshot + Codex review"
    assert run.latest_artifact().path == plan
    assert run.implementation_plan == plan


def test_collect_status_prefers_phase_heartbeat_over_process_guess(tmp_path):
    repo_root = tmp_path / "repos"
    repo = repo_root / "autonomy-bitino"
    _init_repo(repo)
    run_root = repo / ".auto" / "orchestrator" / "task012"
    run_root.mkdir(parents=True)
    (run_root / "run.env").write_text("RUN_ID=task012\nREPO_SLUG=autonomy-bitino\n", encoding="utf-8")
    (run_root / "phase-heartbeat.json").write_text(
        """\
{
  "artifact": "/tmp/corpus.log",
  "detail": "auto corpus planning spine",
  "phase": "corpus",
  "status": "running",
  "updated_at": "2026-06-01T23:10:22Z"
}
""",
        encoding="utf-8",
    )
    processes = [
        ProcessRecord(
            pid=3,
            ppid=2,
            stat="Sl",
            elapsed="00:04",
            cpu=12.0,
            mem=0.1,
            command=f"codex exec --json --cd {repo}",
        ),
    ]

    now = datetime(2026, 6, 1, 23, 12, tzinfo=timezone.utc)
    runs = collect_status(
        repo_roots=[repo_root],
        now=now,
        stale_after=timedelta(minutes=30),
        recent_after=now - timedelta(hours=12),
        processes=processes,
    )

    assert len(runs) == 1
    assert runs[0].phase() == "corpus: running"
    markdown = render_markdown(runs, generated_at=now, stale_after=timedelta(minutes=30))
    assert "Phase: corpus: running" in markdown
    assert "Phase heartbeat: updated=2026-06-01T23:10:22Z" in markdown


def test_collect_status_marks_stale_active_run(tmp_path):
    repo_root = tmp_path / "repos"
    repo = repo_root / "demo"
    _init_repo(repo)
    run_root = repo / ".auto" / "orchestrator" / "run1"
    run_root.mkdir(parents=True)
    (run_root / "run.env").write_text("RUN_ID=run1\nREPO_SLUG=demo\n", encoding="utf-8")
    old_file = run_root / "corpus.log"
    old_file.write_text("old\n", encoding="utf-8")
    old_time = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc).timestamp()
    os.utime(old_file, (old_time, old_time))
    os.utime(run_root / "run.env", (old_time, old_time))
    processes = [ProcessRecord(1, 0, "Sl", "45:00", 4.0, 0.1, f"auto gen --snapshot-only --run-id run1 --run-root {run_root}")]

    runs = collect_status(
        repo_roots=[repo_root],
        now=datetime(2026, 6, 1, 11, 0, tzinfo=timezone.utc),
        stale_after=timedelta(minutes=30),
        recent_after=datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
        processes=processes,
    )

    assert runs[0].attention is True
    assert "latest artifact" in runs[0].attention_reason


def test_repo_only_process_attaches_to_newest_run_only(tmp_path):
    repo_root = tmp_path / "repos"
    repo = repo_root / "demo"
    _init_repo(repo)
    old_run = repo / ".auto" / "orchestrator" / "old"
    new_run = repo / ".auto" / "orchestrator" / "new"
    old_run.mkdir(parents=True)
    new_run.mkdir(parents=True)
    (old_run / "run.env").write_text("RUN_ID=old\nREPO_SLUG=demo\n", encoding="utf-8")
    (new_run / "run.env").write_text("RUN_ID=new\nREPO_SLUG=demo\n", encoding="utf-8")
    old_time = datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc).timestamp()
    new_time = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc).timestamp()
    os.utime(old_run / "run.env", (old_time, old_time))
    os.utime(new_run / "run.env", (new_time, new_time))
    gen = repo / "gen-20260601-100000"
    gen.mkdir()
    plan = gen / "IMPLEMENTATION_PLAN.md"
    plan.write_text("# plan\n", encoding="utf-8")
    os.utime(plan, (new_time, new_time))
    processes = [ProcessRecord(1, 0, "Sl", "00:10", 5.0, 0.1, f"codex exec --json --cd {repo}")]

    runs = collect_status(
        repo_roots=[repo_root],
        now=datetime(2026, 6, 1, 10, 5, tzinfo=timezone.utc),
        stale_after=timedelta(minutes=30),
        recent_after=datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc),
        processes=processes,
    )

    by_id = {run.run_id: run for run in runs}
    assert by_id["new"].active is True
    assert by_id["new"].implementation_plan == plan
    assert by_id["old"].active is False
    assert by_id["old"].implementation_plan is None


def test_generate_status_report_writes_markdown_and_summary(tmp_path):
    repo_root = tmp_path / "repos"
    repo = repo_root / "demo"
    _init_repo(repo)
    run_root = repo / ".auto" / "orchestrator" / "run1"
    run_root.mkdir(parents=True)
    (run_root / "run.env").write_text("RUN_ID=run1\nREPO_SLUG=demo\n", encoding="utf-8")

    now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    result = generate_status_report(
        repo_roots=[repo_root],
        report_dir=tmp_path / "reports",
        now=now,
        stale_after=timedelta(minutes=30),
        recent_after=now - timedelta(hours=12),
        write_gbrain=False,
        processes=[],
    )

    assert result.markdown_path.exists()
    text = result.markdown_path.read_text(encoding="utf-8")
    assert "Dev Orchestrator Active Run Status" in text
    summary = telegram_summary(result)
    assert "Active runs: 0" in summary
    assert str(result.markdown_path) in summary


def test_write_gbrain_page_uses_content_arg_and_neutral_cwd(monkeypatch):
    monkeypatch.setattr("hermes_cli.dev_run_status.shutil.which", lambda command: f"/bin/{command}")
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(args, 0, "{}", "")

    monkeypatch.setattr("hermes_cli.dev_run_status.subprocess.run", fake_run)

    assert write_gbrain_page("status-slug", "# body") is None

    assert seen["args"][:4] == ["gbrain", "put", "status-slug", "--content"]
    assert seen["kwargs"]["cwd"] == "/tmp"
    assert "input" not in seen["kwargs"]
    assert "Dev Orchestrator Active Run Status" in seen["args"][4]
