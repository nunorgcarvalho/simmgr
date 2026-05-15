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


def round_ram_gb(value: float, min_ram_gb: float = 1.0) -> int:
    gb = max(value, min_ram_gb)
    if gb <= 1:
        rounded = 1
    elif gb <= 16:
        rounded = 2 ** math.ceil(math.log2(gb))
    else:
        rounded = int(math.ceil(gb / 16.0) * 16)
    return int(rounded)


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
        runtime_data = rows(
            conn,
            """
            SELECT a.elapsed_seconds, p.params_json
            FROM attempts a JOIN param_sets p USING(param_set_id)
            WHERE a.status = 'succeeded' AND a.elapsed_seconds IS NOT NULL
            """,
        )
        memory_data = rows(
            conn,
            """
            SELECT a.max_rss_gb, p.params_json
            FROM attempts a JOIN param_sets p USING(param_set_id)
            WHERE a.status = 'succeeded' AND a.max_rss_gb IS NOT NULL AND a.max_rss_source = 'slurm'
            """,
        )
    if len(runtime_data) < 2:
        raise SystemExit("Need at least two successful attempts with elapsed_seconds to learn resources")
    feature_names, matrix = _feature_matrix([parse_params_json(r["params_json"]) for r in runtime_data], feature_cfg)
    x_scaled, scaling = _standardize_matrix(matrix, feature_names)
    x = np.column_stack([np.ones(len(x_scaled)), x_scaled])
    runtime_y = np.log(np.maximum([float(r["elapsed_seconds"]) for r in runtime_data], 1e-6))
    ridge_lambda = float(feature_cfg.get("ridge_lambda", 1.0))
    runtime_coef = _fit_ridge(x, runtime_y, ridge_lambda)
    runtime_resid = runtime_y - x @ runtime_coef
    number = next_number(state_path(config), "last_resource_model_number")
    model_id = f"resource_model_{number:03d}"
    coef_names = ["intercept"] + feature_names
    model = {
        "resource_model_id": model_id,
        "created_at": utc_now(),
        "model_type": "log_linear_regression",
        "training_attempt_count": len(runtime_data),
        "runtime_training_attempt_count": len(runtime_data),
        "memory_training_attempt_count": len(memory_data),
        "features": feature_cfg,
        "feature_names": feature_names,
        "feature_scaling": scaling,
        "ridge_lambda": ridge_lambda,
        "runtime_model": {
            "response": "log_runtime_seconds",
            "coefficients": dict(zip(coef_names, map(float, runtime_coef))),
            "residual_sd": float(np.std(runtime_resid, ddof=1)) if len(runtime_data) > 1 else 0.0,
        },
        "memory_model": None,
    }
    if len(memory_data) >= 2:
        memory_feature_names, memory_matrix = _feature_matrix([parse_params_json(r["params_json"]) for r in memory_data], feature_cfg)
        memory_x_scaled, memory_scaling = _standardize_matrix(memory_matrix, memory_feature_names)
        memory_x = np.column_stack([np.ones(len(memory_x_scaled)), memory_x_scaled])
        memory_y = np.log(np.maximum([float(r["max_rss_gb"]) for r in memory_data], 1e-6))
        memory_coef = _fit_ridge(memory_x, memory_y, ridge_lambda)
        memory_resid = memory_y - memory_x @ memory_coef
        memory_coef_names = ["intercept"] + memory_feature_names
        model["memory_feature_names"] = memory_feature_names
        model["memory_feature_scaling"] = memory_scaling
        model["memory_model"] = {
            "response": "log_max_rss_gb",
            "coefficients": dict(zip(memory_coef_names, map(float, memory_coef))),
            "residual_sd": float(np.std(memory_resid, ddof=1)) if len(memory_data) > 1 else 0.0,
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
            if predicted_ram is None:
                predicted_ram = float(resources["default_ram_gb"])
                reason = "learned runtime, fallback memory"
        else:
            predicted_time = float(resources["default_time_minutes"])
            predicted_ram = float(resources["default_ram_gb"])
            reason = "fallback"
            model_id = "fallback"
        latest = latest_attempts.get(run["run_id"])
        if retry_policy == "oom" and latest and latest.get("allocated_ram_gb"):
            predicted_ram = max(predicted_ram, float(latest["allocated_ram_gb"]) * float(resources["oom_retry_multiplier"]))
            reason = "retry after OOM"
        if retry_policy == "timeout" and latest and latest.get("allocated_time_minutes"):
            predicted_time = max(predicted_time, float(latest["allocated_time_minutes"]) * float(resources["timeout_retry_multiplier"]))
            reason = "retry after timeout"
        allocated_time_raw = round_time_minutes(predicted_time * float(resources["safety_time_multiplier"]), int(resources["min_time_minutes"]))
        allocated_ram_raw = round_ram_gb(predicted_ram * float(resources["safety_ram_multiplier"]), float(resources["min_ram_gb"]))
        max_time = int(resources["max_job_time_minutes"])
        max_ram = int(resources.get("max_ram_gb", allocated_ram_raw))
        limit_notes = []
        if allocated_time_raw > max_time:
            limit_notes.append("time_capped")
        if allocated_ram_raw > max_ram:
            limit_notes.append("ram_capped")
        allocated_time = min(allocated_time_raw, max_time)
        allocated_ram = min(allocated_ram_raw, max_ram)
        resource_limit_status = "ok" if not limit_notes else ",".join(limit_notes)
        if limit_notes:
            reason = f"{reason}; {resource_limit_status}"
        predictions.append(
            {
                "run_id": run["run_id"],
                "param_set_id": run["param_set_id"],
                "predicted_time_minutes": f"{predicted_time:.3f}",
                "predicted_ram_gb": f"{predicted_ram:.3f}",
                "allocated_time_minutes": allocated_time,
                "allocated_ram_gb": allocated_ram,
                "allocated_cpus": int(config["slurm"]["cpus_per_task"]),
                "resource_model_id": model_id,
                "prediction_reason": reason,
                "resource_limit_status": resource_limit_status,
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


def _predict_from_model(model: dict[str, Any], params: dict[str, Any]) -> tuple[float, float | None]:
    names, matrix = _feature_matrix([params], model.get("features", {}))
    feature_values = dict(zip(names, matrix[0].tolist()))
    scaling = model.get("feature_scaling")
    if scaling:
        feature_values = {
            name: (feature_values.get(name, 0.0) - float(scaling.get(name, {}).get("mean", 0.0)))
            / float(scaling.get(name, {}).get("scale", 1.0) or 1.0)
            for name in model.get("feature_names", names)
        }
    values = {"intercept": 1.0, **feature_values}
    runtime_log = sum(float(coef) * values.get(name, 0.0) for name, coef in model["runtime_model"]["coefficients"].items())
    if not model.get("memory_model"):
        return math.exp(runtime_log), None
    memory_values = values
    if model.get("memory_feature_scaling"):
        memory_names, memory_matrix = _feature_matrix([params], model.get("features", {}))
        raw_memory_values = dict(zip(memory_names, memory_matrix[0].tolist()))
        memory_values = {
            name: (raw_memory_values.get(name, 0.0) - float(model["memory_feature_scaling"].get(name, {}).get("mean", 0.0)))
            / float(model["memory_feature_scaling"].get(name, {}).get("scale", 1.0) or 1.0)
            for name in model.get("memory_feature_names", memory_names)
        }
        memory_values = {"intercept": 1.0, **memory_values}
    memory_log = sum(float(coef) * memory_values.get(name, 0.0) for name, coef in model["memory_model"]["coefficients"].items())
    memory = math.exp(memory_log)
    if model.get("memory_model", {}).get("response") == "log_max_rss_mb":
        memory /= 1024.0
    return math.exp(runtime_log), memory


def _standardize_matrix(matrix: np.ndarray, feature_names: list[str]) -> tuple[np.ndarray, dict[str, dict[str, float]]]:
    if matrix.shape[1] == 0:
        return matrix, {}
    means = matrix.mean(axis=0)
    scales = matrix.std(axis=0)
    scales = np.where(scales < 1e-12, 1.0, scales)
    scaled = (matrix - means) / scales
    return scaled, {
        name: {"mean": float(mean), "scale": float(scale)}
        for name, mean, scale in zip(feature_names, means.tolist(), scales.tolist())
    }


def _fit_ridge(x: np.ndarray, y: np.ndarray, ridge_lambda: float) -> np.ndarray:
    penalty = np.eye(x.shape[1]) * ridge_lambda
    penalty[0, 0] = 0.0
    return np.linalg.solve(x.T @ x + penalty, x.T @ y)


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
