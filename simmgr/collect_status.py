from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import configured_path, load_project_config, registry_path
from .logging_utils import read_jsonl
from .registry import connect, refresh_run_summary, rows, transaction, update_metadata
from .slurm import classify_slurm_state, sacct_attempt_info
from .time_utils import utc_now
from .tsv import read_tsv, write_tsv


def collect_status(
    project_config: str | Path | None = None,
    plan: str | None = None,
    attempt: str | None = None,
    global_config: str | Path | None = None,
) -> dict[str, int]:
    config = load_project_config(project_config, global_config)
    clauses = []
    params: list[str] = []
    if plan:
        clauses.append("plan_id = ?")
        params.append(plan)
    if attempt:
        clauses.append("attempt_id = ?")
        params.append(attempt)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    with connect(registry_path(config)) as conn:
        attempts = rows(conn, f"SELECT * FROM attempts {where} ORDER BY created_at", tuple(params))
    group_states = _load_group_attempt_states(config, plan) if plan else {}
    group_sizes = _group_sizes(attempts)
    counts: dict[str, int] = {}
    with connect(registry_path(config)) as conn, transaction(conn):
        for att in attempts:
            status, fields = _classify(att, group_states.get(att["attempt_id"]), group_sizes.get(att["group_id"], 0))
            counts[status] = counts.get(status, 0) + 1
            now = utc_now()
            conn.execute(
                """
                UPDATE attempts SET status = ?, started_at = COALESCE(?, started_at),
                  ended_at = COALESCE(?, ended_at), elapsed_seconds = COALESCE(?, elapsed_seconds),
                  max_rss_gb = COALESCE(?, max_rss_gb), exit_code = COALESCE(?, exit_code),
                  max_rss_source = COALESCE(?, max_rss_source), exit_reason = COALESCE(?, exit_reason), updated_at = ?
                WHERE attempt_id = ?
                """,
                (
                    status,
                    fields.get("started_at"),
                    fields.get("ended_at"),
                    fields.get("elapsed_seconds"),
                    fields.get("max_rss_gb"),
                    fields.get("exit_code"),
                    fields.get("max_rss_source"),
                    fields.get("exit_reason"),
                    now,
                    att["attempt_id"],
                ),
            )
            refresh_run_summary(conn, att["run_id"])
        update_metadata(conn)
    if plan:
        _write_resource_assessment(config, plan)
    return counts


def _classify(attempt: dict[str, Any], group_state: str | None = None, group_size: int = 0) -> tuple[str, dict[str, Any]]:
    events = read_jsonl(attempt["attempt_log_path"])
    if not events:
        slurm = sacct_attempt_info(attempt.get("slurm_job_id"), attempt.get("slurm_array_task_id"))
        status = classify_slurm_state(slurm.get("slurm_state"))
        if group_state == "not_started" and status in {"failed_oom", "failed_timeout", "failed_node", "failed_unknown"}:
            return "not_started_due_to_group_failure", {"exit_reason": slurm.get("slurm_state") or "group_failed_before_attempt_started"}
        if status:
            return status, {"exit_reason": slurm.get("slurm_state"), **_known_slurm_fields(slurm, include_max_rss=(group_size == 1))}
        return ("failed_unknown" if attempt["status"] not in {"planned", "submitted"} else attempt["status"], {"exit_reason": "missing_attempt_log"})
    fields: dict[str, Any] = {}
    metadata = next((e for e in events if e.get("event") == "attempt_metadata"), None)
    if metadata:
        fields["started_at"] = metadata.get("timestamp")
    for event in events:
        if event.get("max_rss_gb") is not None and event.get("resource_usage_source") == "slurm":
            fields["max_rss_gb"] = event.get("max_rss_gb")
        elif event.get("max_rss_mb") is not None and event.get("resource_usage_source") == "slurm":
            fields["max_rss_gb"] = float(event["max_rss_mb"]) / 1024.0
    terminal = next((e for e in reversed(events) if e.get("event") == "attempt_finished"), None)
    simulator_terminal = next((e for e in reversed(events) if e.get("event") == "simulator_finished"), None)
    if terminal:
        fields["ended_at"] = terminal.get("timestamp")
        fields["elapsed_seconds"] = terminal.get("elapsed_seconds")
        fields["exit_code"] = terminal.get("exit_code")
        status = terminal.get("status") or ("succeeded" if terminal.get("exit_code") == 0 else "failed_simulator_error")
        if status == "succeeded" and simulator_terminal and simulator_terminal.get("status") != "succeeded":
            status = simulator_terminal.get("status", "failed_simulator_error")
        if group_size == 1:
            slurm = sacct_attempt_info(attempt.get("slurm_job_id"), attempt.get("slurm_array_task_id"))
            if slurm.get("max_rss_gb") is not None:
                fields["max_rss_gb"] = slurm["max_rss_gb"]
                fields["max_rss_source"] = "slurm"
        return str(status), fields
    if simulator_terminal:
        return str(simulator_terminal.get("status", "failed_simulator_error")), fields
    slurm = sacct_attempt_info(attempt.get("slurm_job_id"), attempt.get("slurm_array_task_id"))
    status = classify_slurm_state(slurm.get("slurm_state"))
    if status:
        return status, {"exit_reason": slurm.get("slurm_state"), **fields, **_known_slurm_fields(slurm, include_max_rss=(group_size == 1))}
    return "failed_unknown", {"exit_reason": "missing_terminal_event", **fields}


def _known_slurm_fields(slurm: dict[str, Any], include_max_rss: bool = False) -> dict[str, Any]:
    keys = ["elapsed_seconds", "exit_code"]
    if include_max_rss:
        keys.append("max_rss_gb")
    fields = {key: slurm[key] for key in keys if slurm.get(key) is not None}
    if include_max_rss and fields.get("max_rss_gb") is not None:
        fields["max_rss_source"] = "slurm"
    return fields


def _group_sizes(attempts: list[dict[str, Any]]) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for attempt in attempts:
        group_id = attempt.get("group_id")
        if group_id:
            sizes[group_id] = sizes.get(group_id, 0) + 1
    return sizes


def _load_group_attempt_states(config: dict[str, Any], plan_id: str | None) -> dict[str, str]:
    if not plan_id:
        return {}
    states: dict[str, str] = {}
    group_dir = configured_path(config, "logs_dir") / "groups"
    for group_log in group_dir.glob(f"{plan_id}_*.jsonl"):
        for event in read_jsonl(group_log):
            attempt_id = event.get("attempt_id")
            if not attempt_id:
                continue
            if event.get("event") == "group_run_started":
                states.setdefault(attempt_id, "started")
            elif event.get("event") == "group_run_finished":
                states[attempt_id] = "finished"
    with connect(registry_path(config)) as conn:
        attempts = rows(conn, "SELECT attempt_id, group_id FROM attempts WHERE plan_id = ?", (plan_id,))
    groups_with_logs = {path.stem[len(plan_id) + 1 :] for path in group_dir.glob(f"{plan_id}_*.jsonl")}
    for attempt in attempts:
        if attempt["attempt_id"] not in states and attempt["group_id"] in groups_with_logs:
            states[attempt["attempt_id"]] = "not_started"
    return states


def _write_resource_assessment(config: dict[str, Any], plan_id: str) -> None:
    plan_dir = configured_path(config, "plans_dir") / plan_id
    predictions_path = plan_dir / "resource_predictions.tsv"
    if not predictions_path.exists():
        return
    predictions = {row["run_id"]: row for row in read_tsv(predictions_path)}
    with connect(registry_path(config)) as conn:
        attempts = rows(conn, "SELECT * FROM attempts WHERE plan_id = ? ORDER BY run_id, attempt", (plan_id,))
    assessment = []
    for attempt in attempts:
        prediction = predictions.get(attempt["run_id"], {})
        predicted_time = _float_or_none(prediction.get("predicted_time_minutes"))
        predicted_ram = _float_or_none(prediction.get("predicted_ram_gb"))
        elapsed_seconds = _float_or_none(attempt.get("elapsed_seconds"))
        observed_ram = _float_or_none(attempt.get("max_rss_gb"))
        observed_minutes = elapsed_seconds / 60.0 if elapsed_seconds is not None else None
        assessment.append(
            {
                "run_id": attempt["run_id"],
                "attempt_id": attempt["attempt_id"],
                "status": attempt["status"],
                "resource_model_id": prediction.get("resource_model_id", ""),
                "predicted_time_minutes": prediction.get("predicted_time_minutes", ""),
                "observed_time_minutes": _format_optional(observed_minutes),
                "time_prediction_ratio": _ratio(observed_minutes, predicted_time),
                "predicted_ram_gb": prediction.get("predicted_ram_gb", ""),
                "observed_max_rss_gb": _format_optional(observed_ram),
                "max_rss_source": attempt.get("max_rss_source") or "",
                "ram_prediction_ratio": _ratio(observed_ram, predicted_ram),
                "allocated_time_minutes": attempt.get("allocated_time_minutes"),
                "allocated_ram_gb": attempt.get("allocated_ram_gb"),
                "resource_limit_status": prediction.get("resource_limit_status", ""),
            }
        )
    write_tsv(
        plan_dir / "resource_assessment.tsv",
        assessment,
        [
            "run_id",
            "attempt_id",
            "status",
            "resource_model_id",
            "predicted_time_minutes",
            "observed_time_minutes",
            "time_prediction_ratio",
            "predicted_ram_gb",
            "observed_max_rss_gb",
            "max_rss_source",
            "ram_prediction_ratio",
            "allocated_time_minutes",
            "allocated_ram_gb",
            "resource_limit_status",
        ],
    )


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _ratio(observed: float | None, predicted: float | None) -> str:
    if observed is None or predicted in (None, 0):
        return ""
    return f"{observed / predicted:.3f}"


def _format_optional(value: float | None) -> str:
    return "" if value is None else f"{value:.6f}"
