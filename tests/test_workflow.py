from __future__ import annotations

import os
import json
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
from simmgr.logging_utils import append_jsonl
from simmgr.plan_jobs import plan_jobs
from simmgr.query_runs import query_runs
from simmgr.resources import learn_resources
from simmgr.registry import connect
from simmgr.run_group import run_group
from simmgr.suggest_pilot import suggest_pilot
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
                handle.write(json.dumps({"event": "simulator_finished", "status": "succeeded"}) + "\\n")
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
    assessment = read_tsv(plan_dir / "resource_assessment.tsv")
    assert assessment
    assert "observed_time_minutes" in assessment[0]
    assert "observed_max_rss_gb" in assessment[0]

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


def test_query_defaults_to_pending_and_supports_not_succeeded(tmp_path: Path) -> None:
    project = tmp_path / "project"
    init_project(project)
    config_path = project / "project_config.yaml"
    manifest = build_manifest(config_path)
    ingest_manifest(config_path, manifest)
    with connect(project / "registry" / "simmgr.sqlite") as conn:
        first_run = conn.execute("SELECT run_id FROM runs ORDER BY run_id LIMIT 1").fetchone()[0]
        conn.execute("UPDATE runs SET status = 'succeeded' WHERE run_id = ?", (first_run,))
        conn.commit()
    default_rows = query_runs(config_path)
    not_succeeded_rows = query_runs(config_path, status="not_succeeded")
    any_rows = query_runs(config_path, status="any")
    where_not_succeeded_rows = query_runs(config_path, where='status == "not_succeeded"', status="any")
    assert len(default_rows) == 7
    assert {row["status"] for row in default_rows} == {"pending"}
    assert len(not_succeeded_rows) == 7
    assert "succeeded" not in {row["status"] for row in not_succeeded_rows}
    assert len(any_rows) == 8
    assert len(where_not_succeeded_rows) == 7


def test_plan_jobs_status_any_and_not_succeeded(tmp_path: Path) -> None:
    project = tmp_path / "project"
    init_project(project)
    config_path = project / "project_config.yaml"
    manifest = build_manifest(config_path)
    ingest_manifest(config_path, manifest)
    with connect(project / "registry" / "simmgr.sqlite") as conn:
        first_run = conn.execute("SELECT run_id FROM runs ORDER BY run_id LIMIT 1").fetchone()[0]
        conn.execute("UPDATE runs SET status = 'succeeded' WHERE run_id = ?", (first_run,))
        conn.commit()
    any_plan = plan_jobs(config_path, status="any", generous_resources=True)
    not_succeeded_plan = plan_jobs(config_path, status="not_succeeded", generous_resources=True)
    assert len(read_tsv(any_plan / "selected_runs.tsv")) == 8
    assert len(read_tsv(not_succeeded_plan / "selected_runs.tsv")) == 7


def test_suggest_pilot_uses_lowest_unfinished_replicate_and_next_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    init_project(project)
    config_path = project / "project_config.yaml"
    manifest = build_manifest(config_path)
    ingest_manifest(config_path, manifest)
    with connect(project / "registry" / "simmgr.sqlite") as conn:
        conn.execute("UPDATE runs SET status = 'succeeded' WHERE replicate = 1")
        conn.commit()
    first = suggest_pilot(config_path, n_runs=3)
    first_rows = read_tsv(first)
    assert first.name == "pilot_001.tsv"
    assert len(first_rows) == 3
    assert {row["run_id"].rsplit("_r", 1)[1] for row in first_rows} == {"2"}
    second = suggest_pilot(config_path, n_runs=2)
    assert second.name == "pilot_002.tsv"
    assert len(read_tsv(second)) == 2


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


def test_group_timeout_preserves_completed_runs_and_marks_unstarted(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    init_project(project)
    config_path = project / "project_config.yaml"
    manifest = build_manifest(config_path)
    ingest_manifest(config_path, manifest)
    plan_dir = plan_jobs(config_path, where="replicate == 1", generous_resources=True)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    sbatch = fake_bin / "sbatch"
    sbatch.write_text("#!/usr/bin/env bash\necho Submitted batch job 12345\n", encoding="utf-8")
    sbatch.chmod(sbatch.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    submit_jobs(config_path, plan_dir.name)
    submissions = read_tsv(plan_dir / "submission.tsv")
    first = submissions[0]
    first_log = project / "logs" / "attempts" / f"{first['attempt_id']}.jsonl"
    append_jsonl(first_log, {"event": "attempt_metadata", "attempt_id": first["attempt_id"]})
    append_jsonl(
        first_log,
        {
            "event": "attempt_finished",
            "attempt_id": first["attempt_id"],
            "status": "succeeded",
            "exit_code": 0,
            "elapsed_seconds": 12.0,
        },
    )
    group_log = project / "logs" / "groups" / f"{plan_dir.name}_{first['group_id']}.jsonl"
    append_jsonl(group_log, {"event": "group_started", "plan_id": plan_dir.name, "group_id": first["group_id"]})
    append_jsonl(group_log, {"event": "group_run_started", "attempt_id": first["attempt_id"], "run_id": first["run_id"]})
    append_jsonl(group_log, {"event": "group_run_finished", "attempt_id": first["attempt_id"], "exit_code": 0})

    import simmgr.collect_status as collect_module

    monkeypatch.setattr(
        collect_module,
        "sacct_attempt_info",
        lambda *_args, **_kwargs: {"slurm_state": "TIMEOUT", "exit_code": 0, "elapsed_seconds": 3600},
    )
    counts = collect_status(config_path, plan=plan_dir.name)
    assert counts == {"succeeded": 1, "not_started_due_to_group_failure": 3}
    with connect(project / "registry" / "simmgr.sqlite") as conn:
        statuses = {
            row[0]
            for row in conn.execute(
                "SELECT status FROM attempts WHERE plan_id = ? AND attempt_id != ?",
                (plan_dir.name, first["attempt_id"]),
            )
        }
    assert statuses == {"not_started_due_to_group_failure"}


def test_slurm_stderr_oom_overrides_sigkill_terminal_event(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    init_project(project)
    config_path = project / "project_config.yaml"
    manifest = build_manifest(config_path)
    ingest_manifest(config_path, manifest)
    plan_dir = plan_jobs(config_path, where="replicate == 1", generous_resources=True, one_run_per_group=True)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    sbatch = fake_bin / "sbatch"
    sbatch.write_text("#!/usr/bin/env bash\necho Submitted batch job 12345\n", encoding="utf-8")
    sbatch.chmod(sbatch.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    submit_jobs(config_path, plan_dir.name)
    submission = read_tsv(plan_dir / "submission.tsv")[0]
    attempt_log = project / "logs" / "attempts" / f"{submission['attempt_id']}.jsonl"
    append_jsonl(attempt_log, {"event": "attempt_metadata", "attempt_id": submission["attempt_id"]})
    append_jsonl(
        attempt_log,
        {
            "event": "attempt_finished",
            "attempt_id": submission["attempt_id"],
            "status": "failed_simulator_error",
            "exit_code": -9,
            "elapsed_seconds": 5.0,
        },
    )
    slurm_err = project / "logs" / "slurm" / f"{plan_dir.name}_{submission['array_id']}.{submission['slurm_job_id']}_{submission['array_task_index']}.err"
    slurm_err.write_text("slurmstepd: error: Detected 1 oom_kill event\n", encoding="utf-8")

    import simmgr.collect_status as collect_module

    monkeypatch.setattr(collect_module, "sacct_attempt_info", lambda *_args, **_kwargs: {})
    counts = collect_status(config_path, plan=plan_dir.name)
    assert counts["failed_oom"] == 1
    with connect(project / "registry" / "simmgr.sqlite") as conn:
        status, exit_reason = conn.execute(
            "SELECT status, exit_reason FROM attempts WHERE attempt_id = ?",
            (submission["attempt_id"],),
        ).fetchone()
    assert status == "failed_oom"
    assert exit_reason == "failed_oom"


def test_one_run_per_group_planning(tmp_path: Path) -> None:
    project = tmp_path / "project"
    init_project(project)
    config_path = project / "project_config.yaml"
    manifest = build_manifest(config_path)
    ingest_manifest(config_path, manifest)
    plan_dir = plan_jobs(config_path, where="replicate == 1", generous_resources=True, one_run_per_group=True)
    groups = read_tsv(plan_dir / "groups.tsv")
    group_ids = [row["group_id"] for row in groups]
    assert len(groups) == 4
    assert len(set(group_ids)) == 4
    summary = (plan_dir / "plan_summary.txt").read_text(encoding="utf-8")
    assert "one_run_per_group: True" in summary


def test_censored_memory_model_uses_bounds(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    init_project(project)
    config_path = project / "project_config.yaml"
    manifest = build_manifest(config_path)
    ingest_manifest(config_path, manifest)
    plan_dir = plan_jobs(config_path, where="replicate == 1", generous_resources=True, one_run_per_group=True)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    sbatch = fake_bin / "sbatch"
    sbatch.write_text("#!/usr/bin/env bash\necho Submitted batch job 12345\n", encoding="utf-8")
    sbatch.chmod(sbatch.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    submit_jobs(config_path, plan_dir.name)
    submissions = read_tsv(plan_dir / "submission.tsv")
    with connect(project / "registry" / "simmgr.sqlite") as conn:
        conn.execute(
            "UPDATE attempts SET status = 'succeeded', elapsed_seconds = 10, allocated_ram_gb = 16 WHERE attempt_id = ?",
            (submissions[0]["attempt_id"],),
        )
        conn.execute(
            "UPDATE attempts SET status = 'failed_oom', allocated_ram_gb = 16 WHERE attempt_id = ?",
            (submissions[1]["attempt_id"],),
        )
        conn.execute(
            "UPDATE attempts SET status = 'succeeded', elapsed_seconds = 20, allocated_ram_gb = 4, max_rss_gb = 2, max_rss_source = 'slurm' WHERE attempt_id = ?",
            (submissions[2]["attempt_id"],),
        )
        conn.commit()
    model_path = learn_resources(config_path)
    model = json.loads(model_path.read_text(encoding="utf-8"))
    assert model["memory_model"]["fit_method"] == "censored_log_linear_regression"
    assert model["memory_model"]["observation_counts"]["upper"] >= 1
    assert model["memory_model"]["observation_counts"]["lower"] >= 1
    assert model["memory_model"]["observation_counts"]["exact"] >= 1
