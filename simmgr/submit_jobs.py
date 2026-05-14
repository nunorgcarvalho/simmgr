from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from .config import configured_path, load_project_config, registry_path
from .ids import attempt_id as make_attempt_id
from .registry import connect, row, transaction, update_metadata
from .runner import simmgr_shell_command
from .time_utils import utc_now
from .tsv import read_tsv, write_tsv


SUBMISSION_COLUMNS = [
    "plan_id",
    "array_id",
    "slurm_job_id",
    "array_task_index",
    "group_id",
    "run_id",
    "attempt_id",
    "status",
]


def submit_jobs(
    project_config: str | Path | None = None,
    plan: str | Path | None = None,
    dry_run: bool = False,
    global_config: str | Path | None = None,
) -> Path | None:
    config = load_project_config(project_config, global_config)
    plan_dir = _resolve_plan(config, plan)
    arrays = read_tsv(plan_dir / "arrays.tsv")
    groups = read_tsv(plan_dir / "groups.tsv")
    submission_rows = []
    for array_id in sorted({r["array_id"] for r in arrays}):
        command = _build_sbatch_command(config, plan_dir.name, array_id, [r for r in arrays if r["array_id"] == array_id])
        if dry_run:
            print(" ".join(command))
            continue
        result = subprocess.run(command, check=False, text=True, capture_output=True)
        if result.returncode != 0:
            raise SystemExit(f"sbatch failed for {array_id}:\n{result.stderr.strip()}")
        slurm_job_id = _parse_job_id(result.stdout)
        submission_rows.extend(_create_attempts(config, plan_dir.name, array_id, slurm_job_id, arrays, groups))
    if dry_run:
        return None
    out = plan_dir / "submission.tsv"
    write_tsv(out, submission_rows, SUBMISSION_COLUMNS)
    return out


def _resolve_plan(config: dict[str, Any], plan: str | Path | None) -> Path:
    plans_dir = configured_path(config, "plans_dir")
    if plan in (None, "latest"):
        plans = sorted(plans_dir.glob("plan_*"))
        if not plans:
            raise SystemExit("No plans found")
        return plans[-1]
    path = Path(plan)
    if not path.is_absolute():
        candidate = plans_dir / path
        if candidate.exists():
            return candidate
    return path.expanduser().resolve()


def _build_sbatch_command(config: dict[str, Any], plan_id: str, array_id: str, array_rows: list[dict[str, str]]) -> list[str]:
    first = array_rows[0]
    task_count = len(array_rows)
    run_group_command = simmgr_shell_command(
        config,
        "run-group",
        "--project-config",
        config["_project_config_path"],
        "--plan-id",
        plan_id,
        "--array-id",
        array_id,
        "--array-task-index",
        "__SIMMGR_ARRAY_TASK_INDEX__",
    ).replace("__SIMMGR_ARRAY_TASK_INDEX__", "$SLURM_ARRAY_TASK_ID")
    command = [
        "sbatch",
        f"--partition={config['slurm']['partition']}",
        f"--cpus-per-task={first['allocated_cpus']}",
        f"--mem={int(float(first['allocated_ram_gb']))}G",
        f"--time={_slurm_time(int(first['allocated_time_minutes']))}",
        f"--array=1-{task_count}",
        f"--job-name=simmgr_{plan_id}_{array_id}",
        f"--output={configured_path(config, 'logs_dir')}/slurm/{plan_id}_{array_id}.%A_%a.out",
        f"--error={configured_path(config, 'logs_dir')}/slurm/{plan_id}_{array_id}.%A_%a.err",
        "--wrap",
        run_group_command,
    ]
    if config["slurm"].get("account"):
        command.insert(2, f"--account={config['slurm']['account']}")
    return command


def _create_attempts(
    config: dict[str, Any],
    plan_id: str,
    array_id: str,
    slurm_job_id: str,
    arrays: list[dict[str, str]],
    groups: list[dict[str, str]],
) -> list[dict[str, str]]:
    now = utc_now()
    array_rows = [r for r in arrays if r["array_id"] == array_id]
    group_by_id: dict[str, list[dict[str, str]]] = {}
    for group in groups:
        group_by_id.setdefault(group["group_id"], []).append(group)
    out = []
    with connect(registry_path(config)) as conn, transaction(conn):
        for array_row in array_rows:
            group_id = array_row["group_id"]
            for group_row in sorted(group_by_id[group_id], key=lambda r: int(r["group_order"])):
                run = row(conn, "SELECT * FROM runs WHERE run_id = ?", (group_row["run_id"],))
                if run is None:
                    raise ValueError(f"Run not found in registry: {group_row['run_id']}")
                latest = row(conn, "SELECT MAX(attempt) AS latest FROM attempts WHERE run_id = ?", (run["run_id"],))
                attempt_number = int(latest["latest"] or 0) + 1
                attempt_id = make_attempt_id(run["run_id"], attempt_number)
                log_path = configured_path(config, "logs_dir") / "attempts" / f"{attempt_id}.jsonl"
                conn.execute(
                    """
                    INSERT INTO attempts(
                      attempt_id, run_id, param_set_id, replicate, attempt, status, plan_id, group_id,
                      array_id, slurm_job_id, slurm_array_task_id, allocated_time_minutes,
                      allocated_ram_gb, allocated_cpus, attempt_log_path, created_at, submitted_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'submitted', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        attempt_id,
                        run["run_id"],
                        run["param_set_id"],
                        run["replicate"],
                        attempt_number,
                        plan_id,
                        group_id,
                        array_id,
                        slurm_job_id,
                        array_row["array_task_index"],
                        int(group_row["allocated_time_minutes"]),
                        float(group_row["allocated_ram_gb"]),
                        int(group_row["allocated_cpus"]),
                        str(log_path),
                        now,
                        now,
                        now,
                    ),
                )
                conn.execute(
                    "UPDATE runs SET status = 'submitted', attempt_count = attempt_count + 1, best_attempt_id = ?, updated_at = ? WHERE run_id = ?",
                    (attempt_id, now, run["run_id"]),
                )
                out.append(
                    {
                        "plan_id": plan_id,
                        "array_id": array_id,
                        "slurm_job_id": slurm_job_id,
                        "array_task_index": array_row["array_task_index"],
                        "group_id": group_id,
                        "run_id": run["run_id"],
                        "attempt_id": attempt_id,
                        "status": "submitted",
                    }
                )
        conn.execute("UPDATE plans SET submitted_at = ?, status = 'submitted' WHERE plan_id = ?", (now, plan_id))
        update_metadata(conn)
    return out


def _parse_job_id(stdout: str) -> str:
    match = re.search(r"Submitted batch job\s+(\S+)", stdout)
    if not match:
        raise ValueError(f"Could not parse sbatch job id from: {stdout!r}")
    return match.group(1)


def _slurm_time(minutes: int) -> str:
    hours, mins = divmod(minutes, 60)
    return f"{hours:02d}:{mins:02d}:00"
