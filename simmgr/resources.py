from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from .canonicalize import parse_params_json
from .config import configured_path, load_project_config, project_path, registry_path, state_path
from .mini_yaml import load_yaml
from .registry import connect, rows
from .state import next_number
from .time_utils import utc_now


def round_ram_mb(value: float, min_ram_mb: int = 1000) -> int:
    gb = max(value, min_ram_mb) / 1024.0
    if gb <= 1:
        rounded = 1
    elif gb <= 16:
        rounded = 2 ** math.ceil(math.log2(gb))
    else:
        rounded = int(math.ceil(gb / 16.0) * 16)
    return int(rounded * 1024)


def round_time_minutes(value: float, min_time_minutes: int = 5) -> int:
    value = max(value, min_time_minutes)
    for candidate in [5, 10, 15, 30, 45, 60]:
        if value <= candidate:
            return candidate
    return int(math.ceil(value / 60.0) * 60)


def latest_resource_model(config: dict[str, Any]) -> Path | None:
    models = sorted(configured_path(config, "resource_models_dir").glob("resource_model_*.json"))
    return models[-1] if models else None


def learn_resources(project_config: str | Path | None = None, global_config: str | Path | None = None) -> Path:
    config = load_project_config(project_config, global_config)
    spec = load_yaml(project_path(config, config["paths"]["simulation_spec"]))
    feature_cfg = spec.get("resource_model", {})
    with connect(registry_path(config)) as conn:
        data = rows(
            conn,
            """
            SELECT a.elapsed_seconds, a.max_rss_mb, p.params_json
            FROM attempts a JOIN param_sets p USING(param_set_id)
            WHERE a.status = 'succeeded' AND a.elapsed_seconds IS NOT NULL AND a.max_rss_mb IS NOT NULL
            """,
        )
    if len(data) < 2:
        raise SystemExit("Need at least two successful attempts with elapsed_seconds and max_rss_mb to learn resources")
    feature_names, matrix = _feature_matrix([parse_params_json(r["params_json"]) for r in data], feature_cfg)
    x = np.column_stack([np.ones(len(matrix)), matrix])
    runtime_y = np.log(np.maximum([float(r["elapsed_seconds"]) for r in data], 1e-6))
    memory_y = np.log(np.maximum([float(r["max_rss_mb"]) for r in data], 1e-6))
    runtime_coef = np.linalg.lstsq(x, runtime_y, rcond=None)[0]
    memory_coef = np.linalg.lstsq(x, memory_y, rcond=None)[0]
    runtime_resid = runtime_y - x @ runtime_coef
    memory_resid = memory_y - x @ memory_coef
    number = next_number(state_path(config), "last_resource_model_number")
    model_id = f"resource_model_{number:03d}"
    coef_names = ["intercept"] + feature_names
    model = {
        "resource_model_id": model_id,
        "created_at": utc_now(),
        "model_type": "log_linear_regression",
        "training_attempt_count": len(data),
        "features": feature_cfg,
        "runtime_model": {
            "response": "log_runtime_seconds",
            "coefficients": dict(zip(coef_names, map(float, runtime_coef))),
            "residual_sd": float(np.std(runtime_resid, ddof=1)) if len(data) > 1 else 0.0,
        },
        "memory_model": {
            "response": "log_max_rss_mb",
            "coefficients": dict(zip(coef_names, map(float, memory_coef))),
            "residual_sd": float(np.std(memory_resid, ddof=1)) if len(data) > 1 else 0.0,
        },
    }
    out = configured_path(config, "resource_models_dir") / f"{model_id}.json"
    out.write_text(json.dumps(model, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def predict_for_runs(config: dict[str, Any], run_rows: list[dict[str, Any]], resource_model: str | Path | None, retry_policy: str | None) -> list[dict[str, Any]]:
    resources = config["resources"]
    model_path = None
    model = None
    if resource_model and resource_model != "none":
        model_path = latest_resource_model(config) if resource_model == "latest" else Path(resource_model)
    elif resource_model is None:
        model_path = latest_resource_model(config)
    if model_path and model_path.exists():
        model = json.loads(model_path.read_text(encoding="utf-8"))
    predictions = []
    latest_attempts = _latest_attempts(config, [r["run_id"] for r in run_rows])
    for run in run_rows:
        params = parse_params_json(run["params_json"])
        if model:
            predicted_seconds, predicted_ram = _predict_from_model(model, params)
            predicted_time = predicted_seconds / 60.0
            reason = "learned model"
            model_id = model["resource_model_id"]
        else:
            predicted_time = float(resources["default_time_minutes"])
            predicted_ram = float(resources["default_ram_mb"])
            reason = "fallback"
            model_id = "fallback"
        latest = latest_attempts.get(run["run_id"])
        if retry_policy == "oom" and latest and latest.get("allocated_ram_mb"):
            predicted_ram = max(predicted_ram, float(latest["allocated_ram_mb"]) * float(resources["oom_retry_multiplier"]))
            reason = "retry after OOM"
        if retry_policy == "timeout" and latest and latest.get("allocated_time_minutes"):
            predicted_time = max(predicted_time, float(latest["allocated_time_minutes"]) * float(resources["timeout_retry_multiplier"]))
            reason = "retry after timeout"
        allocated_time = round_time_minutes(predicted_time * float(resources["safety_time_multiplier"]), int(resources["min_time_minutes"]))
        allocated_ram = round_ram_mb(predicted_ram * float(resources["safety_ram_multiplier"]), int(resources["min_ram_mb"]))
        predictions.append(
            {
                "run_id": run["run_id"],
                "param_set_id": run["param_set_id"],
                "predicted_time_minutes": f"{predicted_time:.3f}",
                "predicted_ram_mb": f"{predicted_ram:.3f}",
                "allocated_time_minutes": min(allocated_time, int(resources["max_job_time_minutes"])),
                "allocated_ram_mb": allocated_ram,
                "allocated_cpus": int(config["slurm"]["cpus_per_task"]),
                "resource_model_id": model_id,
                "prediction_reason": reason,
            }
        )
    return predictions


def _feature_matrix(params_list: list[dict[str, Any]], feature_cfg: dict[str, Any]) -> tuple[list[str], np.ndarray]:
    numeric = feature_cfg.get("numeric_parameters") or sorted(k for p in params_list for k, v in p.items() if isinstance(v, (int, float)))
    categorical = feature_cfg.get("categorical_parameters") or []
    names: list[str] = []
    cols: list[list[float]] = []
    for key in numeric:
        values = [float(p.get(key, 0.0)) for p in params_list]
        names.append(key)
        cols.append(values)
        if feature_cfg.get("include_log_terms", False):
            names.append(f"log_{key}")
            cols.append([math.log(max(v, 1e-9)) for v in values])
        if feature_cfg.get("include_square_terms", False):
            names.append(f"{key}_squared")
            cols.append([v * v for v in values])
    if feature_cfg.get("include_pairwise_products", False):
        for i, left in enumerate(numeric):
            for right in numeric[i + 1 :]:
                names.append(f"{left}_x_{right}")
                cols.append([float(p.get(left, 0.0)) * float(p.get(right, 0.0)) for p in params_list])
    for key in categorical:
        levels = sorted({str(p.get(key, "")) for p in params_list})
        for level in levels[1:]:
            names.append(f"{key}={level}")
            cols.append([1.0 if str(p.get(key, "")) == level else 0.0 for p in params_list])
    matrix = np.array(cols, dtype=float).T if cols else np.zeros((len(params_list), 0))
    return names, matrix


def _predict_from_model(model: dict[str, Any], params: dict[str, Any]) -> tuple[float, float]:
    names, matrix = _feature_matrix([params], model.get("features", {}))
    values = {"intercept": 1.0, **dict(zip(names, matrix[0].tolist()))}
    runtime_log = sum(float(coef) * values.get(name, 0.0) for name, coef in model["runtime_model"]["coefficients"].items())
    memory_log = sum(float(coef) * values.get(name, 0.0) for name, coef in model["memory_model"]["coefficients"].items())
    return math.exp(runtime_log), math.exp(memory_log)


def _latest_attempts(config: dict[str, Any], run_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not run_ids:
        return {}
    placeholders = ",".join("?" for _ in run_ids)
    with connect(registry_path(config)) as conn:
        data = rows(
            conn,
            f"""
            SELECT a.* FROM attempts a
            JOIN (SELECT run_id, MAX(attempt) AS attempt FROM attempts WHERE run_id IN ({placeholders}) GROUP BY run_id) x
            ON a.run_id = x.run_id AND a.attempt = x.attempt
            """,
            tuple(run_ids),
        )
    return {r["run_id"]: r for r in data}

