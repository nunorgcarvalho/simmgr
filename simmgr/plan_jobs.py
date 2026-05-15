from __future__ import annotations

import shlex
from collections import defaultdict
from pathlib import Path
from typing import Any

from .config import configured_path, load_project_config, state_path
from .query_runs import query_runs
from .registry import connect
from .resources import predict_for_runs, round_time_minutes
from .runner import simmgr_shell_command
from .state import next_number
from .time_utils import utc_now
from .tsv import read_tsv, write_tsv


SELECTED_COLUMNS = ["run_id", "param_set_id", "replicate", "params_json", "selection_reason"]
PREDICTION_COLUMNS = [
    "run_id",
    "param_set_id",
    "predicted_time_minutes",
    "predicted_ram_gb",
    "allocated_time_minutes",
    "allocated_ram_gb",
    "allocated_cpus",
    "resource_model_id",
    "prediction_reason",
    "resource_limit_status",
]
GROUP_COLUMNS = [
    "group_id",
    "run_id",
    "group_order",
    "allocated_time_minutes",
    "allocated_ram_gb",
    "allocated_cpus",
    "predicted_run_time_minutes",
    "attempt_id",
]
ARRAY_COLUMNS = [
    "array_id",
    "allocated_time_minutes",
    "allocated_ram_gb",
    "allocated_cpus",
    "group_id",
    "array_task_index",
    "slurm_job_id",
]


def plan_jobs(
    project_config: str | Path | None = None,
    where: str | None = None,
    status: str | None = None,
    pilot_set: str | Path | None = None,
    resource_model: str | Path | None = None,
    retry_policy: str | None = None,
    generous_resources: bool = False,
    one_run_per_group: bool = False,
    global_config: str | Path | None = None,
) -> Path:
    config = load_project_config(project_config, global_config)
    selected = _select_runs(config, where, status, pilot_set)
    if not selected:
        raise SystemExit("No runs matched selection")
    plan_number = next_number(state_path(config), "last_plan_number")
    plan_id = f"plan_{plan_number:03d}"
    plan_dir = configured_path(config, "plans_dir") / plan_id
    plan_dir.mkdir(parents=False, exist_ok=False)
    selected_rows = [
        {
            "run_id": r["run_id"],
            "param_set_id": r["param_set_id"],
            "replicate": r["replicate"],
            "params_json": r["params_json"],
            "selection_reason": _selection_reason(where, status, pilot_set),
        }
        for r in selected
    ]
    write_tsv(plan_dir / "selected_runs.tsv", selected_rows, SELECTED_COLUMNS)
    if generous_resources:
        resource_model = "none"
    predictions = predict_for_runs(config, selected, resource_model, retry_policy)
    if generous_resources:
        for pred in predictions:
            pred["prediction_reason"] = "generous fallback"
    write_tsv(plan_dir / "resource_predictions.tsv", predictions, PREDICTION_COLUMNS)
    groups = _make_groups(config, predictions, one_run_per_group=one_run_per_group)
    write_tsv(plan_dir / "groups.tsv", groups, GROUP_COLUMNS)
    arrays = _make_arrays(config, groups)
    write_tsv(plan_dir / "arrays.tsv", arrays, ARRAY_COLUMNS)
    (plan_dir / "sbatch_commands.sh").write_text(_sbatch_commands(config, plan_id, arrays), encoding="utf-8")
    capped_count = sum(1 for p in predictions if p.get("resource_limit_status") != "ok")
    (plan_dir / "plan_summary.txt").write_text(
        f"plan_id: {plan_id}\ncreated_at: {utc_now()}\nselected_runs: {len(selected)}\ngroups: {len({g['group_id'] for g in groups})}\narrays: {len({a['array_id'] for a in arrays})}\none_run_per_group: {one_run_per_group}\nresource_capped_runs: {capped_count}\n",
        encoding="utf-8",
    )
    with connect(configured_path(config, "registry_dir") / "simmgr.sqlite") as conn:
        conn.execute(
            "INSERT OR IGNORE INTO plans(plan_id, plan_path, created_at, status, selection_summary, resource_model_id, notes) VALUES (?, ?, ?, 'created', ?, ?, '')",
            (plan_id, str(plan_dir), utc_now(), _selection_reason(where, status, pilot_set), predictions[0]["resource_model_id"]),
        )
        conn.commit()
    return plan_dir


def _select_runs(config: dict[str, Any], where: str | None, status: str | None, pilot_set: str | Path | None) -> list[dict[str, Any]]:
    if pilot_set:
        path = Path(pilot_set)
        if not path.is_absolute():
            path = configured_path(config, "pilot_sets_dir") / path
        wanted = {r["run_id"] for r in read_tsv(path) if r.get("run_id")}
        return [r for r in query_runs(config["_project_config_path"], where=where, status=status) if r["run_id"] in wanted]
    return query_runs(config["_project_config_path"], where=where, status=status)


def _selection_reason(where: str | None, status: str | None, pilot_set: str | Path | None) -> str:
    parts = []
    if status:
        parts.append(f"status={status}")
    if where:
        parts.append(f"where={where}")
    if pilot_set:
        parts.append(f"pilot_set={pilot_set}")
    return "; ".join(parts) if parts else "status=pending"


def _make_groups(config: dict[str, Any], predictions: list[dict[str, Any]], one_run_per_group: bool = False) -> list[dict[str, Any]]:
    if one_run_per_group:
        rows: list[dict[str, Any]] = []
        for group_number, pred in enumerate(predictions, start=1):
            rows.extend(_group_rows(config, group_number, [pred]))
        return rows
    max_minutes = int(config["resources"]["max_job_time_minutes"])
    buckets: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for pred in predictions:
        buckets[(int(pred["allocated_ram_gb"]), int(pred["allocated_cpus"]))].append(pred)
    rows: list[dict[str, Any]] = []
    group_number = 0
    for (_ram, _cpus), preds in sorted(buckets.items()):
        current: list[dict[str, Any]] = []
        current_time = 0.0
        for pred in preds:
            run_time = float(pred["predicted_time_minutes"])
            if current and current_time + run_time > max_minutes:
                group_number += 1
                rows.extend(_group_rows(config, group_number, current))
                current = []
                current_time = 0.0
            current.append(pred)
            current_time += run_time
        if current:
            group_number += 1
            rows.extend(_group_rows(config, group_number, current))
    return rows


def _group_rows(config: dict[str, Any], group_number: int, preds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    group_id = f"group_{group_number:05d}"
    group_time = round_time_minutes(sum(float(p["predicted_time_minutes"]) for p in preds), int(config["resources"]["min_time_minutes"]))
    group_time = min(group_time, int(config["resources"]["max_job_time_minutes"]))
    return [
        {
            "group_id": group_id,
            "run_id": pred["run_id"],
            "group_order": i,
            "allocated_time_minutes": group_time,
            "allocated_ram_gb": pred["allocated_ram_gb"],
            "allocated_cpus": pred["allocated_cpus"],
            "predicted_run_time_minutes": pred["predicted_time_minutes"],
            "attempt_id": "",
        }
        for i, pred in enumerate(preds, start=1)
    ]


def _make_arrays(config: dict[str, Any], groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_group = {}
    for row in groups:
        by_group.setdefault(row["group_id"], row)
    buckets: dict[tuple[int, int, int], list[str]] = defaultdict(list)
    for group_id, row in by_group.items():
        buckets[(int(row["allocated_time_minutes"]), int(row["allocated_ram_gb"]), int(row["allocated_cpus"]))].append(group_id)
    rows: list[dict[str, Any]] = []
    array_number = 0
    max_array = int(config["slurm"].get("max_array_size", 1000))
    for (time, ram, cpus), group_ids in sorted(buckets.items()):
        for start in range(0, len(group_ids), max_array):
            array_number += 1
            array_id = f"array_{array_number:03d}"
            for index, group_id in enumerate(group_ids[start : start + max_array], start=1):
                rows.append(
                    {
                        "array_id": array_id,
                        "allocated_time_minutes": time,
                        "allocated_ram_gb": ram,
                        "allocated_cpus": cpus,
                        "group_id": group_id,
                        "array_task_index": index,
                        "slurm_job_id": "",
                    }
                )
    return rows


def _sbatch_commands(config: dict[str, Any], plan_id: str, arrays: list[dict[str, Any]]) -> str:
    commands = ["#!/usr/bin/env bash", "set -euo pipefail", ""]
    seen = {}
    for row in arrays:
        seen.setdefault(row["array_id"], row)
    project_config = config["_project_config_path"]
    for array_id, row in seen.items():
        task_count = sum(1 for r in arrays if r["array_id"] == array_id)
        account = f" --account={config['slurm']['account']}" if config["slurm"].get("account") else ""
        run_group_command = simmgr_shell_command(
            config,
            "run-group",
            "--project-config",
            project_config,
            "--plan-id",
            plan_id,
            "--array-id",
            array_id,
            "--array-task-index",
            "__SIMMGR_ARRAY_TASK_INDEX__",
        ).replace("__SIMMGR_ARRAY_TASK_INDEX__", "$SLURM_ARRAY_TASK_ID")
        commands.append(
            "sbatch"
            f" --partition={config['slurm']['partition']}{account}"
            f" --cpus-per-task={row['allocated_cpus']}"
            f" --mem={int(row['allocated_ram_gb'])}G"
            f" --time={_slurm_time(int(row['allocated_time_minutes']))}"
            f" --array=1-{task_count}"
            f" --job-name=simmgr_{plan_id}_{array_id}"
            f" --output={configured_path(config, 'logs_dir')}/slurm/{plan_id}_{array_id}.%A_%a.out"
            f" --error={configured_path(config, 'logs_dir')}/slurm/{plan_id}_{array_id}.%A_%a.err"
            f" --wrap={shlex.quote(run_group_command)}"
        )
    return "\n".join(commands) + "\n"


def _slurm_time(minutes: int) -> str:
    hours, mins = divmod(minutes, 60)
    return f"{hours:02d}:{mins:02d}:00"
