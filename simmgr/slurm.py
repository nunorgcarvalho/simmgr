from __future__ import annotations

import re
import subprocess
from typing import Any


def sacct_attempt_info(slurm_job_id: str | None, array_task_id: str | None = None) -> dict[str, Any]:
    if not slurm_job_id:
        return {}
    job = str(slurm_job_id)
    if array_task_id not in (None, ""):
        job = f"{job}_{array_task_id}"
    command = [
        "sacct",
        "-j",
        job,
        "--parsable2",
        "--noheader",
        "--format=JobID,JobIDRaw,State,ExitCode,ElapsedRaw,MaxRSS",
    ]
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}
    records: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        fields = line.split("|")
        if len(fields) < 6:
            continue
        job_id, job_raw, state, exit_code, elapsed_raw, max_rss = fields[:6]
        if job_id != job and not job_id.startswith(f"{job}.") and job_raw != job:
            continue
        records.append({
            "job_id": job_id,
            "slurm_state": state,
            "exit_code": _parse_exit_code(exit_code),
            "elapsed_seconds": float(elapsed_raw) if elapsed_raw.isdigit() else None,
            "max_rss_gb": _parse_max_rss_gb(max_rss),
        })
    if not records:
        return {}
    primary = records[0]
    for record in records:
        if classify_slurm_state(record.get("slurm_state")):
            primary["slurm_state"] = record["slurm_state"]
            primary["exit_code"] = record.get("exit_code")
            primary["elapsed_seconds"] = record.get("elapsed_seconds")
            break
    for record in records:
        if record["job_id"].endswith(".batch") and record.get("max_rss_gb") is not None:
            primary["max_rss_gb"] = record["max_rss_gb"]
            break
    return primary


def classify_slurm_state(state: str | None) -> str | None:
    if not state:
        return None
    upper = state.upper()
    if "OUT_OF_MEMORY" in upper or "OOM" in upper:
        return "failed_oom"
    if "TIMEOUT" in upper:
        return "failed_timeout"
    if any(token in upper for token in ["NODE_FAIL", "PREEMPTED", "BOOT_FAIL"]):
        return "failed_node"
    if "COMPLETED" in upper:
        return None
    if "FAILED" in upper or "CANCELLED" in upper:
        return "failed_unknown"
    return None


def _parse_exit_code(value: str) -> int | None:
    if not value:
        return None
    head = value.split(":", 1)[0]
    return int(head) if head.isdigit() else None


def _parse_max_rss_gb(value: str) -> float | None:
    match = re.fullmatch(r"([0-9.]+)([KMGTP]?)", value.strip())
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2)
    factors = {"": 1 / (1024 * 1024 * 1024), "K": 1 / (1024 * 1024), "M": 1 / 1024, "G": 1, "T": 1024, "P": 1024 * 1024}
    return number * factors[unit]
