from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import load_project_config, registry_path
from .logging_utils import read_jsonl
from .registry import connect, refresh_run_summary, rows, transaction, update_metadata
from .slurm import classify_slurm_state, sacct_attempt_info
from .time_utils import utc_now


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
    counts: dict[str, int] = {}
    with connect(registry_path(config)) as conn, transaction(conn):
        for att in attempts:
            status, fields = _classify(att)
            counts[status] = counts.get(status, 0) + 1
            now = utc_now()
            conn.execute(
                """
                UPDATE attempts SET status = ?, started_at = COALESCE(?, started_at),
                  ended_at = COALESCE(?, ended_at), elapsed_seconds = COALESCE(?, elapsed_seconds),
                  max_rss_gb = COALESCE(?, max_rss_gb), exit_code = COALESCE(?, exit_code),
                  exit_reason = COALESCE(?, exit_reason), updated_at = ?
                WHERE attempt_id = ?
                """,
                (
                    status,
                    fields.get("started_at"),
                    fields.get("ended_at"),
                    fields.get("elapsed_seconds"),
                    fields.get("max_rss_gb"),
                    fields.get("exit_code"),
                    fields.get("exit_reason"),
                    now,
                    att["attempt_id"],
                ),
            )
            refresh_run_summary(conn, att["run_id"])
        update_metadata(conn)
    return counts


def _classify(attempt: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    events = read_jsonl(attempt["attempt_log_path"])
    if not events:
        slurm = sacct_attempt_info(attempt.get("slurm_job_id"), attempt.get("slurm_array_task_id"))
        status = classify_slurm_state(slurm.get("slurm_state"))
        if status:
            return status, {"exit_reason": slurm.get("slurm_state"), **_known_slurm_fields(slurm)}
        return ("failed_unknown" if attempt["status"] not in {"planned", "submitted"} else attempt["status"], {"exit_reason": "missing_attempt_log"})
    fields: dict[str, Any] = {}
    metadata = next((e for e in events if e.get("event") == "attempt_metadata"), None)
    if metadata:
        fields["started_at"] = metadata.get("timestamp")
    for event in events:
        if event.get("max_rss_gb") is not None:
            fields["max_rss_gb"] = event.get("max_rss_gb")
        elif event.get("max_rss_mb") is not None:
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
        return str(status), fields
    if simulator_terminal:
        return str(simulator_terminal.get("status", "failed_simulator_error")), fields
    slurm = sacct_attempt_info(attempt.get("slurm_job_id"), attempt.get("slurm_array_task_id"))
    status = classify_slurm_state(slurm.get("slurm_state"))
    if status:
        return status, {"exit_reason": slurm.get("slurm_state"), **fields, **_known_slurm_fields(slurm)}
    return "failed_unknown", {"exit_reason": "missing_terminal_event", **fields}


def _known_slurm_fields(slurm: dict[str, Any]) -> dict[str, Any]:
    return {key: slurm[key] for key in ["elapsed_seconds", "max_rss_gb", "exit_code"] if slurm.get(key) is not None}
