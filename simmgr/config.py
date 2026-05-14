from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .mini_yaml import load_yaml


DEFAULT_GLOBAL_CONFIG: dict[str, Any] = {
    "slurm_defaults": {
        "partition": "short",
        "account": "",
        "cpus_per_task": 1,
        "max_array_size": 1000,
    },
    "resource_defaults": {
        "max_job_time_minutes": 720,
        "default_time_minutes": 60,
        "default_ram_gb": 16,
        "min_time_minutes": 5,
        "min_ram_gb": 1,
        "max_ram_gb": 128,
        "safety_time_multiplier": 1.25,
        "safety_ram_multiplier": 1.25,
        "oom_retry_multiplier": 2.0,
        "timeout_retry_multiplier": 2.0,
    },
    "path_defaults": {
        "manifests_dir": "manifests",
        "registry_dir": "registry",
        "plans_dir": "plans",
        "logs_dir": "logs",
        "outputs_dir": "outputs",
        "pilot_sets_dir": "pilot_sets",
        "resource_models_dir": "resource_models",
        "state_dir": "state",
    },
}


DEFAULT_SIMULATION_SPEC: dict[str, Any] = {
    "default_parameters": {
        "simulator_version": 1,
        "N": 500,
        "num_variants": 500,
        "h2": 0.5,
        "replicates": 1,
    },
    "simulation_sets": [
        {
            "name": "demo_small",
            "grid": {"N": [200, 500], "num_variants": [100], "h2": [0.2, 0.5]},
            "replicates": 2,
        }
    ],
    "resource_model": {
        "numeric_parameters": ["N", "num_variants"],
        "categorical_parameters": [],
        "include_log_terms": True,
        "include_square_terms": False,
        "include_pairwise_products": False,
    },
}


def load_global_config(path: str | Path | None = None) -> dict[str, Any]:
    config = _deepcopy(DEFAULT_GLOBAL_CONFIG)
    candidates = []
    if path:
        candidates.append(Path(path))
    candidates.append(Path.home() / ".simmgr" / "global_config.yaml")
    candidates.append(Path(__file__).resolve().parent.parent / "global_config.yaml")
    for candidate in candidates:
        if candidate.exists():
            _deep_update(config, load_yaml(candidate))
            break
    return config


def resolve_project_config(project_config: str | Path | None, global_config: str | Path | None = None) -> Path:
    if project_config:
        return Path(project_config).expanduser().resolve()
    config = load_global_config(global_config)
    default = config.get("default_project_config")
    if not default:
        raise SystemExit("Pass --project-config or set default_project_config in global_config.yaml")
    return Path(default).expanduser().resolve()


def load_project_config(project_config: str | Path | None = None, global_config: str | Path | None = None) -> dict[str, Any]:
    path = resolve_project_config(project_config, global_config)
    config = load_yaml(path)
    config["_project_config_path"] = str(path)
    config["project_root"] = str(Path(config["project_root"]).expanduser().resolve())
    return config


def project_path(config: dict[str, Any], *parts: str) -> Path:
    return Path(config["project_root"], *parts)


def configured_path(config: dict[str, Any], key: str) -> Path:
    return project_path(config, config["paths"][key])


def registry_path(config: dict[str, Any]) -> Path:
    return configured_path(config, "registry_dir") / "simmgr.sqlite"


def state_path(config: dict[str, Any]) -> Path:
    return configured_path(config, "state_dir") / "simmgr_state.json"


def _deepcopy(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _deepcopy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deepcopy(v) for v in value]
    return value


def _deep_update(base: dict[str, Any], updates: dict[str, Any]) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value


def make_default_project_config(project_root: Path, global_config: dict[str, Any]) -> dict[str, Any]:
    slurm = _deepcopy(global_config.get("slurm_defaults", DEFAULT_GLOBAL_CONFIG["slurm_defaults"]))
    resources = _deepcopy(global_config.get("resource_defaults", DEFAULT_GLOBAL_CONFIG["resource_defaults"]))
    paths = _deepcopy(global_config.get("path_defaults", DEFAULT_GLOBAL_CONFIG["path_defaults"]))
    paths["simulation_spec"] = "simulation_spec.yaml"
    return {
        "project_name": project_root.name,
        "project_root": str(project_root.resolve()),
        "simulator": {"script": str(project_root / "simulator.py"), "python_executable": os.environ.get("PYTHON", "python")},
        "randomness": {"project_seed": 123456},
        "slurm": slurm,
        "resources": resources,
        "paths": paths,
    }
