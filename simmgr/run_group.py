from __future__ import annotations

from pathlib import Path

from .config import configured_path, load_project_config, registry_path
from .logging_utils import append_jsonl
from .registry import connect, row, rows
from .run_one import run_one
from .submit_jobs import _resolve_plan
from .tsv import read_tsv


def run_group(
    project_config: str | Path,
    plan_id: str,
    group_id: str | None = None,
    array_id: str | None = None,
    array_task_index: int | None = None,
    global_config: str | Path | None = None,
) -> int:
    config = load_project_config(project_config, global_config)
    plan_dir = _resolve_plan(config, plan_id)
    if group_id is None:
        if array_id is None or array_task_index is None:
            raise SystemExit("Pass either --group-id or both --array-id and --array-task-index")
        arrays = read_tsv(plan_dir / "arrays.tsv")
        match = [r for r in arrays if r["array_id"] == array_id and int(r["array_task_index"]) == int(array_task_index)]
        if not match:
            raise SystemExit(f"No group for {array_id} task {array_task_index}")
        group_id = match[0]["group_id"]
    group_log = configured_path(config, "logs_dir") / "groups" / f"{plan_id}_{group_id}.jsonl"
    append_jsonl(group_log, {"event": "group_started", "plan_id": plan_id, "group_id": group_id})
    exit_code = 0
    with connect(registry_path(config)) as conn:
        attempts = rows(
            conn,
            "SELECT * FROM attempts WHERE plan_id = ? AND group_id = ? ORDER BY slurm_array_task_id, attempt",
            (plan_id, group_id),
        )
    if not attempts:
        raise SystemExit(f"No submitted attempts found for {plan_id} {group_id}")
    for attempt in attempts:
        append_jsonl(group_log, {"event": "group_run_started", "attempt_id": attempt["attempt_id"], "run_id": attempt["run_id"]})
        code = run_one(config["_project_config_path"], attempt["attempt_id"])
        append_jsonl(group_log, {"event": "group_run_finished", "attempt_id": attempt["attempt_id"], "exit_code": code})
        if code != 0:
            exit_code = code
    append_jsonl(group_log, {"event": "group_finished", "plan_id": plan_id, "group_id": group_id, "exit_code": exit_code})
    return exit_code

