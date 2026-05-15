from __future__ import annotations

from pathlib import Path

from simmgr.build_manifest import build_manifest
from simmgr.dashboard_data import (
    dashboard_command_rows,
    list_log_files,
    manifest_preview,
    overview,
    plan_file_text,
    plan_rows,
    run_dashboard_command,
    run_rows,
)
from simmgr.ingest_manifest import ingest_manifest
from simmgr.init_project import init_project
from simmgr.logging_utils import append_jsonl
from simmgr.plan_jobs import plan_jobs
from simmgr.config import load_project_config


def test_dashboard_data_summarizes_project_files_and_registry(tmp_path: Path) -> None:
    project = tmp_path / "project"
    init_project(project)
    config_path = project / "project_config.yaml"
    manifest = build_manifest(config_path)
    ingest_manifest(config_path, manifest)
    plan = plan_jobs(config_path, status="pending", generous_resources=True, one_run_per_group=True)
    config = load_project_config(config_path)

    summary = overview(config)
    assert summary["project_name"] == "project"
    assert summary["total_runs"] == 8
    assert summary["manifest_count"] == 1
    assert summary["plan_count"] == 1

    runs = run_rows(config)
    assert len(runs) == 8
    assert "params.N" in runs[0]

    manifests = manifest_preview(config, "manifest_001")
    assert len(manifests) == 8

    plans = plan_rows(config)
    assert plans[0]["plan_id"] == plan.name
    assert plans[0]["selected_runs"] == "8"
    assert "selected_runs.tsv" in plan_file_text(config, plan.name, "plan_summary.txt") or "plan_id" in plan_file_text(config, plan.name, "plan_summary.txt")


def test_dashboard_command_logging_and_log_listing(tmp_path: Path) -> None:
    project = tmp_path / "project"
    init_project(project)
    config = load_project_config(project / "project_config.yaml")
    attempt_log = project / "logs" / "attempts" / "attempt_001.jsonl"
    append_jsonl(attempt_log, {"event": "hello", "run_id": "run_1"})

    event = run_dashboard_command(config, "demo", lambda: Path("made.txt"), {"flag": True})
    assert event["status"] == "succeeded"
    assert event["result"] == "made.txt"
    history = dashboard_command_rows(config)
    assert history[-1]["action"] == "demo"

    logs = list_log_files(config, "attempt_001")
    assert logs and logs[0]["kind"] == "attempt"
