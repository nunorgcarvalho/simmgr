from __future__ import annotations

import os
import sqlite3
import stat
import textwrap
from pathlib import Path

from simmgr.build_manifest import build_manifest
from simmgr.collect_status import collect_status
from simmgr.config import load_project_config
from simmgr.export_registry import export_registry
from simmgr.ingest_manifest import ingest_manifest
from simmgr.init_project import init_project
from simmgr.plan_jobs import plan_jobs
from simmgr.registry import connect
from simmgr.run_group import run_group
from simmgr.submit_jobs import submit_jobs
from simmgr.tsv import read_tsv


def test_manifest_registry_plan_and_local_execution(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    init_project(project)
    simulator = tmp_path / "simulator.py"
    simulator.write_text(
        textwrap.dedent(
            """
            import argparse, json
            from pathlib import Path

            parser = argparse.ArgumentParser()
            for arg in ["params-json", "run-id", "param-set-id", "replicate", "attempt-id", "attempt", "seed", "log-path", "output-dir"]:
                parser.add_argument("--" + arg, required=True)
            args = parser.parse_args()
            Path(args.output_dir).mkdir(parents=True, exist_ok=True)
            with open(args.log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({"event": "simulator_finished", "status": "succeeded", "max_rss_gb": 1.25}) + "\\n")
            """
        ),
        encoding="utf-8",
    )
    config_path = project / "project_config.yaml"
    text = config_path.read_text(encoding="utf-8")
    text = text.replace(str(project / "simulator.py"), str(simulator))
    text = text.replace("python_executable: python", f"python_executable: {os.sys.executable}")
    config_path.write_text(text, encoding="utf-8")

    manifest = build_manifest(config_path)
    summary = ingest_manifest(config_path, manifest)
    assert summary["new_run_count"] == 8

    plan_dir = plan_jobs(config_path, where="replicate == 1", generous_resources=True)
    predictions = read_tsv(plan_dir / "resource_predictions.tsv")
    assert "allocated_ram_gb" in predictions[0]
    assert "allocated_ram_mb" not in predictions[0]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    sbatch = fake_bin / "sbatch"
    sbatch.write_text("#!/usr/bin/env bash\necho Submitted batch job 12345\n", encoding="utf-8")
    sbatch.chmod(sbatch.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    submission = submit_jobs(config_path, plan_dir.name)
    assert submission and submission.exists()

    assert run_group(config_path, plan_dir.name, array_id="array_001", array_task_index=1) == 0
    counts = collect_status(config_path, plan=plan_dir.name)
    assert counts == {"succeeded": 4}

    exports = export_registry(config_path)
    assert (exports / "runs.tsv").exists()
    with connect(project / "registry" / "simmgr.sqlite") as conn:
        succeeded = conn.execute("SELECT COUNT(*) FROM runs WHERE status = 'succeeded'").fetchone()[0]
    assert succeeded == 4


def test_overlapping_manifest_ingest_records_membership(tmp_path: Path) -> None:
    project = tmp_path / "project"
    init_project(project)
    config_path = project / "project_config.yaml"
    first = build_manifest(config_path)
    ingest_manifest(config_path, first)
    second = build_manifest(config_path)
    summary = ingest_manifest(config_path, second)
    assert summary["new_param_set_count"] == 0
    assert summary["new_run_count"] == 0
    with sqlite3.connect(project / "registry" / "simmgr.sqlite") as conn:
        memberships = conn.execute("SELECT COUNT(*) FROM manifest_runs").fetchone()[0]
    assert memberships == 16


def test_ram_predictions_are_capped_in_gb(tmp_path: Path) -> None:
    project = tmp_path / "project"
    init_project(project)
    config_path = project / "project_config.yaml"
    text = config_path.read_text(encoding="utf-8")
    text = text.replace("max_ram_gb: 128", "max_ram_gb: 2")
    config_path.write_text(text, encoding="utf-8")
    manifest = build_manifest(config_path)
    ingest_manifest(config_path, manifest)
    plan_dir = plan_jobs(config_path, where="replicate == 1", generous_resources=True)
    predictions = read_tsv(plan_dir / "resource_predictions.tsv")
    assert {row["allocated_ram_gb"] for row in predictions} == {"2"}
    assert {row["resource_limit_status"] for row in predictions} == {"ram_capped"}
