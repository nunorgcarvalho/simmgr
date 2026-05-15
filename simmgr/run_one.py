from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from . import __version__
from .canonicalize import parse_params_json
from .config import DEFAULT_GLOBAL_CONFIG, configured_path, load_project_config, registry_path
from .ids import deterministic_seed
from .logging_utils import append_jsonl
from .registry import connect, row


def run_one(
    project_config: str | Path,
    attempt_id: str,
    global_config: str | Path | None = None,
) -> int:
    config = load_project_config(project_config, global_config)
    with connect(registry_path(config)) as conn:
        attempt = row(conn, "SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,))
        if attempt is None:
            raise SystemExit(f"Attempt not found: {attempt_id}")
        run = row(
            conn,
            "SELECT r.*, p.params_json FROM runs r JOIN param_sets p USING(param_set_id) WHERE r.run_id = ?",
            (attempt["run_id"],),
        )
    if run is None:
        raise SystemExit(f"Run not found for attempt: {attempt_id}")
    params = parse_params_json(run["params_json"])
    seed = deterministic_seed(int(config.get("randomness", {}).get("project_seed", 123456)), run["run_id"])
    log_path = Path(attempt["attempt_log_path"])
    output_dir = configured_path(config, "outputs_dir") / run["run_id"] / attempt_id
    output_dir.mkdir(parents=True, exist_ok=True)
    append_jsonl(
        log_path,
        {
            "event": "attempt_metadata",
            "attempt_id": attempt_id,
            "attempt": attempt["attempt"],
            "run_id": run["run_id"],
            "param_set_id": run["param_set_id"],
            "replicate": run["replicate"],
            "params": params,
            "seed": seed,
            "allocated_time_minutes": attempt["allocated_time_minutes"],
            "allocated_ram_gb": attempt["allocated_ram_gb"],
            "allocated_cpus": attempt["allocated_cpus"],
            "simmgr_version": __version__,
        },
    )
    command = _simulator_command(config, run, attempt, attempt_id, seed, log_path, output_dir)
    start = time.time()
    result = subprocess.run(command, check=False)
    elapsed = time.time() - start
    status = "succeeded" if result.returncode == 0 else "failed_simulator_error"
    terminal_event = {
        "event": "attempt_finished",
        "attempt_id": attempt_id,
        "attempt": attempt["attempt"],
        "status": status,
        "exit_code": result.returncode,
        "elapsed_seconds": elapsed,
    }
    append_jsonl(log_path, terminal_event)
    return int(result.returncode)


def run_one_from_fields(project_config: str | Path, attempt: dict[str, Any]) -> int:
    return run_one(project_config, attempt["attempt_id"])


def _simulator_command(
    config: dict[str, Any],
    run: dict[str, Any],
    attempt: dict[str, Any],
    attempt_id: str,
    seed: int,
    log_path: Path,
    output_dir: Path,
) -> list[str]:
    script = config["simulator"]["script"]
    python = config["simulator"].get("python_executable", "python")
    values = {
        "params-json": run["params_json"],
        "run-id": run["run_id"],
        "param-set-id": run["param_set_id"],
        "replicate": run["replicate"],
        "attempt-id": attempt_id,
        "attempt": attempt["attempt"],
        "seed": seed,
        "log-path": log_path,
        "output-dir": output_dir,
    }
    command = [python, script]
    for name, enabled in _simulator_arguments(config).items():
        if enabled:
            command.extend([f"--{name}", str(values[name])])
    return command


def _simulator_arguments(config: dict[str, Any]) -> dict[str, bool]:
    defaults = DEFAULT_GLOBAL_CONFIG["simulator_defaults"]["arguments"]
    configured = config.get("simulator", {}).get("arguments", {})
    return {name: bool(configured.get(name, enabled)) for name, enabled in defaults.items()}
