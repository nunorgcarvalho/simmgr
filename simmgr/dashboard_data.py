from __future__ import annotations

import contextlib
import io
import json
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from .canonicalize import parse_params_json
from .config import configured_path, load_project_config, registry_path
from .logging_utils import append_jsonl, read_jsonl
from .registry import connect, rows
from .time_utils import utc_now
from .tsv import read_tsv

PLAN_FILES = [
    "selected_runs.tsv",
    "resource_predictions.tsv",
    "resource_assessment.tsv",
    "groups.tsv",
    "arrays.tsv",
    "submission.tsv",
    "plan_summary.txt",
    "sbatch_commands.sh",
]


def load_config(project_config: str | Path | None = None, global_config: str | Path | None = None) -> dict[str, Any]:
    return load_project_config(project_config, global_config)


def registry_table(config: dict[str, Any], table: str, order_by: str | None = None) -> list[dict[str, Any]]:
    sql = f"SELECT * FROM {table}"
    if order_by:
        sql += f" ORDER BY {order_by}"
    with connect(registry_path(config)) as conn:
        return rows(conn, sql)


def overview(config: dict[str, Any]) -> dict[str, Any]:
    runs = registry_table(config, "runs")
    attempts = registry_table(config, "attempts")
    manifests = manifest_rows(config)
    plans = plan_rows(config)
    models = resource_model_rows(config)
    metadata = {row["key"]: row["value"] for row in registry_table(config, "registry_metadata")}
    run_counts = Counter(row["status"] for row in runs)
    attempt_counts = Counter(row["status"] for row in attempts)
    return {
        "project_name": config.get("project_name", ""),
        "project_root": config.get("project_root", ""),
        "project_config": config.get("_project_config_path", ""),
        "manifest_count": len(manifests),
        "plan_count": len(plans),
        "latest_manifest_id": manifests[-1]["manifest_id"] if manifests else "",
        "latest_plan_id": plans[-1]["plan_id"] if plans else "",
        "latest_resource_model_id": models[-1]["resource_model_id"] if models else "",
        "total_runs": len(runs),
        "total_attempts": len(attempts),
        "pending_runs": run_counts.get("pending", 0),
        "active_runs": sum(run_counts.get(status, 0) for status in ["planned", "submitted", "running"]),
        "succeeded_runs": run_counts.get("succeeded", 0),
        "failed_oom_runs": run_counts.get("failed_oom", 0),
        "failed_timeout_runs": run_counts.get("failed_timeout", 0),
        "failed_simulator_error_runs": run_counts.get("failed_simulator_error", 0),
        "failed_unknown_runs": run_counts.get("failed_unknown", 0),
        "registry_updated_at": metadata.get("updated_at", ""),
        "run_status_counts": dict(sorted(run_counts.items())),
        "attempt_status_counts": dict(sorted(attempt_counts.items())),
    }


def run_rows(config: dict[str, Any], include_params: bool = True) -> list[dict[str, Any]]:
    with connect(registry_path(config)) as conn:
        data = rows(
            conn,
            """
            SELECT r.run_id, r.param_set_id, r.replicate, r.status, r.attempt_count,
                   r.best_attempt_id, r.first_manifest_id, r.last_manifest_id,
                   r.first_seen_at, r.updated_at, p.params_json
            FROM runs r JOIN param_sets p USING(param_set_id)
            ORDER BY r.run_id
            """,
        )
    if not include_params:
        return data
    return [_with_param_columns(row) for row in data]


def attempt_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    return registry_table(config, "attempts", "created_at")


def manifest_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    data = registry_table(config, "manifest_files", "manifest_id")
    registered = {row["manifest_id"] for row in data}
    for path in sorted(configured_path(config, "manifests_dir").glob("manifest_*.tsv")):
        manifest_id = path.stem
        if manifest_id in registered:
            continue
        try:
            rows_ = read_tsv(path)
        except FileNotFoundError:
            rows_ = []
        data.append(
            {
                "manifest_id": manifest_id,
                "manifest_path": str(path),
                "created_at": rows_[0].get("created_at", "") if rows_ else "",
                "ingested_at": "",
                "spec_path": rows_[0].get("spec_path", "") if rows_ else "",
                "spec_hash": rows_[0].get("spec_hash", "") if rows_ else "",
                "simmgr_version": rows_[0].get("simmgr_version", "") if rows_ else "",
                "row_count": len(rows_),
                "new_param_set_count": "",
                "new_run_count": "",
                "notes": "not ingested",
            }
        )
    return sorted(data, key=lambda row: row["manifest_id"])


def plan_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    registry_plans = {row["plan_id"]: row for row in registry_table(config, "plans", "plan_id")}
    plan_dirs = sorted(configured_path(config, "plans_dir").glob("plan_*"))
    attempts = attempt_rows(config)
    attempts_by_plan = Counter(row.get("plan_id") or "" for row in attempts)
    statuses_by_plan: dict[str, Counter[str]] = {}
    for attempt in attempts:
        statuses_by_plan.setdefault(attempt.get("plan_id") or "", Counter())[attempt.get("status") or ""] += 1
    out = []
    for plan_dir in plan_dirs:
        plan_id = plan_dir.name
        summary = read_plan_summary(plan_dir)
        row = dict(registry_plans.get(plan_id, {}))
        row.update(
            {
                "plan_id": plan_id,
                "plan_path": str(plan_dir),
                "created_at": row.get("created_at") or summary.get("created_at", ""),
                "status": row.get("status") or "created",
                "selected_runs": summary.get("selected_runs", _tsv_count(plan_dir / "selected_runs.tsv")),
                "groups": summary.get("groups", _unique_tsv_count(plan_dir / "groups.tsv", "group_id")),
                "arrays": summary.get("arrays", _unique_tsv_count(plan_dir / "arrays.tsv", "array_id")),
                "submitted": "yes" if (plan_dir / "submission.tsv").exists() else "no",
                "attempt_count": attempts_by_plan.get(plan_id, 0),
                "attempt_statuses": _format_counter(statuses_by_plan.get(plan_id, Counter())),
                "resource_buckets": _resource_buckets(plan_dir / "arrays.tsv"),
            }
        )
        out.append(row)
    return sorted(out, key=lambda row: row["plan_id"])


def read_plan_summary(plan_dir: str | Path) -> dict[str, str]:
    path = Path(plan_dir) / "plan_summary.txt"
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            out[key.strip()] = value.strip()
    return out


def plan_file_text(config: dict[str, Any], plan_id: str, file_name: str, max_lines: int = 200) -> str:
    if file_name not in PLAN_FILES:
        raise ValueError(f"Unsupported plan file: {file_name}")
    path = configured_path(config, "plans_dir") / plan_id / file_name
    return read_text_file(path, max_lines=max_lines)


def manifest_preview(config: dict[str, Any], manifest_id: str, max_rows: int = 50) -> list[dict[str, Any]]:
    path = configured_path(config, "manifests_dir") / f"{manifest_id}.tsv"
    if not path.exists():
        return []
    return read_tsv(path)[:max_rows]


def resource_model_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for path in sorted(configured_path(config, "resource_models_dir").glob("resource_model_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        memory = data.get("memory_model") or {}
        out.append(
            {
                "resource_model_id": data.get("resource_model_id", path.stem),
                "path": str(path),
                "created_at": data.get("created_at", ""),
                "model_type": data.get("model_type", ""),
                "training_attempt_count": data.get("training_attempt_count", ""),
                "runtime_training_attempt_count": data.get("runtime_training_attempt_count", ""),
                "memory_training_attempt_count": data.get("memory_training_attempt_count", ""),
                "runtime_residual_sd": (data.get("runtime_model") or {}).get("residual_sd", ""),
                "memory_residual_sd": memory.get("residual_sd", ""),
                "fit_method": memory.get("fit_method", ""),
                "features": json.dumps(data.get("features", {}), sort_keys=True),
            }
        )
    return out


def resource_model_detail(config: dict[str, Any], model_id: str | None = None) -> dict[str, Any]:
    models = resource_model_rows(config)
    if not models:
        return {}
    chosen = next((row for row in models if row["resource_model_id"] == model_id), models[-1])
    return json.loads(Path(chosen["path"]).read_text(encoding="utf-8"))


def resource_assessment_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for path in sorted(configured_path(config, "plans_dir").glob("plan_*/resource_assessment.tsv")):
        for row in read_tsv(path):
            row = dict(row)
            row["plan_id"] = path.parent.name
            out.append(row)
    return out


def list_log_files(config: dict[str, Any], query: str = "") -> list[dict[str, Any]]:
    logs_dir = configured_path(config, "logs_dir")
    patterns = [
        ("attempt", "attempts", "*.jsonl"),
        ("group", "groups", "*.jsonl"),
        ("slurm", "slurm", "*"),
        ("dashboard", "dashboard_commands", "*.jsonl"),
    ]
    out = []
    query_lower = query.lower()
    for kind, subdir, pattern in patterns:
        directory = logs_dir / subdir
        if not directory.exists():
            continue
        for path in sorted(directory.glob(pattern)):
            if path.is_dir():
                continue
            text = path.name.lower()
            if query_lower and query_lower not in text:
                continue
            out.append(
                {
                    "kind": kind,
                    "name": path.name,
                    "path": str(path),
                    "size_bytes": path.stat().st_size,
                    "modified_at": path.stat().st_mtime,
                }
            )
    return out


def read_text_file(path: str | Path, max_lines: int = 200, tail: bool = False) -> str:
    path = Path(path)
    if not path.exists():
        return f"Missing file: {path}"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if tail:
        selected = lines[-max_lines:]
    else:
        selected = lines[:max_lines]
    suffix = ""
    if len(lines) > len(selected):
        suffix = f"\n... truncated {len(lines) - len(selected)} lines ..."
    return "\n".join(selected) + suffix


def pretty_jsonl(path: str | Path, max_lines: int = 200, tail: bool = False) -> str:
    raw = read_text_file(path, max_lines=max_lines, tail=tail)
    pretty_lines = []
    for line in raw.splitlines():
        try:
            pretty_lines.append(json.dumps(json.loads(line), indent=2, sort_keys=True))
        except json.JSONDecodeError:
            pretty_lines.append(line)
    return "\n".join(pretty_lines)


def dashboard_command_log_path(config: dict[str, Any]) -> Path:
    path = configured_path(config, "logs_dir") / "dashboard_commands" / "commands.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def run_dashboard_command(
    config: dict[str, Any],
    action: str,
    callback: Callable[[], Any],
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started_at = utc_now()
    stdout = io.StringIO()
    stderr = io.StringIO()
    event: dict[str, Any] = {
        "event": "dashboard_command",
        "action": action,
        "arguments": arguments or {},
        "project_config": config.get("_project_config_path", ""),
        "project_root": config.get("project_root", ""),
        "started_at": started_at,
    }
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = callback()
        event.update({"status": "succeeded", "result": _json_safe(result)})
    except SystemExit as exc:
        event.update({"status": "failed", "error_type": type(exc).__name__, "error_message": str(exc)})
    except Exception as exc:
        event.update({"status": "failed", "error_type": type(exc).__name__, "error_message": str(exc)})
    event.update({"finished_at": utc_now(), "stdout": stdout.getvalue(), "stderr": stderr.getvalue()})
    append_jsonl(dashboard_command_log_path(config), event)
    return event


def dashboard_command_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    path = dashboard_command_log_path(config)
    return read_jsonl(path) if path.exists() else []


def _with_param_columns(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    try:
        params = parse_params_json(row.get("params_json") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        params = {}
    for key, value in params.items():
        out[f"params.{key}"] = value
    return out


def _format_counter(counter: Counter[str]) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(counter.items())) if counter else ""


def _tsv_count(path: Path) -> int:
    return len(read_tsv(path)) if path.exists() else 0


def _unique_tsv_count(path: Path, key: str) -> int:
    if not path.exists():
        return 0
    return len({row.get(key, "") for row in read_tsv(path)})


def _resource_buckets(path: Path) -> str:
    if not path.exists():
        return ""
    buckets = Counter(
        (
            row.get("allocated_time_minutes", ""),
            row.get("allocated_ram_gb", ""),
            row.get("allocated_cpus", ""),
        )
        for row in read_tsv(path)
    )
    return "; ".join(f"{time}m/{ram}GB/{cpus}cpu x{count}" for (time, ram, cpus), count in sorted(buckets.items()))


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value
