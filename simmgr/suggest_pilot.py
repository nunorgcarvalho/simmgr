from __future__ import annotations

import random
import re
from pathlib import Path
from typing import Any

import numpy as np

from .canonicalize import parse_params_json
from .config import configured_path, load_project_config, project_path, registry_path
from .mini_yaml import load_yaml
from .registry import connect, rows
from .resources import _feature_matrix, _standardize_matrix
from .tsv import read_tsv, write_tsv

PILOT_RE = re.compile(r"^pilot_(\d+)\.tsv$")


def suggest_pilot(
    project_config: str | Path | None = None,
    n_runs: int = 10,
    output: str | Path | None = None,
    seed: int | None = None,
    global_config: str | Path | None = None,
) -> Path:
    if n_runs < 1:
        raise ValueError("n_runs must be at least 1")
    config = load_project_config(project_config, global_config)
    spec = load_yaml(project_path(config, config["paths"]["simulation_spec"]))
    feature_cfg = spec.get("resource_model", {})
    rng = random.Random(seed if seed is not None else int(config.get("randomness", {}).get("project_seed", 0)))
    candidates = _eligible_param_sets(config)
    if not candidates:
        raise SystemExit("No parameter sets have an unfinished replicate available")
    selected = _maximin_sample(candidates, feature_cfg, min(n_runs, len(candidates)), rng)
    out = _resolve_output_path(config, output)
    write_tsv(out, [{"run_id": item["run_id"]} for item in selected], ["run_id"])
    return out


def _eligible_param_sets(config: dict[str, Any]) -> list[dict[str, Any]]:
    with connect(registry_path(config)) as conn:
        data = rows(
            conn,
            """
            SELECT r.run_id, r.param_set_id, r.replicate, r.status, p.params_json
            FROM runs r JOIN param_sets p USING(param_set_id)
            ORDER BY r.param_set_id, r.replicate
            """,
        )
    by_param_set: dict[str, list[dict[str, Any]]] = {}
    for row in data:
        by_param_set.setdefault(row["param_set_id"], []).append(row)
    candidates = []
    for param_set_id, run_rows in by_param_set.items():
        unfinished = [row for row in run_rows if row["status"] != "succeeded"]
        if not unfinished:
            continue
        chosen = min(unfinished, key=lambda row: int(row["replicate"]))
        candidates.append(
            {
                "run_id": chosen["run_id"],
                "param_set_id": param_set_id,
                "replicate": int(chosen["replicate"]),
                "params_json": chosen["params_json"],
                "params": parse_params_json(chosen["params_json"]),
            }
        )
    return sorted(candidates, key=lambda row: row["run_id"])


def _maximin_sample(
    candidates: list[dict[str, Any]],
    feature_cfg: dict[str, Any],
    n_runs: int,
    rng: random.Random,
) -> list[dict[str, Any]]:
    if n_runs >= len(candidates):
        return candidates
    _feature_names, matrix = _feature_matrix([item["params"] for item in candidates], feature_cfg)
    if matrix.shape[1] == 0:
        return sorted(rng.sample(candidates, n_runs), key=lambda row: row["run_id"])
    scaled, _scaling = _standardize_matrix(matrix, [str(i) for i in range(matrix.shape[1])])
    selected_indices: list[int] = []
    remaining = set(range(len(candidates)))
    centroid = scaled.mean(axis=0)
    first = _best_index(
        remaining,
        lambda idx: float(np.sum((scaled[idx] - centroid) ** 2)),
        rng,
    )
    selected_indices.append(first)
    remaining.remove(first)
    while len(selected_indices) < n_runs and remaining:
        selected_matrix = scaled[selected_indices]
        next_idx = _best_index(
            remaining,
            lambda idx: float(np.min(np.sum((selected_matrix - scaled[idx]) ** 2, axis=1))),
            rng,
        )
        selected_indices.append(next_idx)
        remaining.remove(next_idx)
    return [candidates[idx] for idx in selected_indices]


def _best_index(indices: set[int], score_fn: Any, rng: random.Random) -> int:
    jittered_scores = [(score_fn(idx), rng.random(), idx) for idx in indices]
    return max(jittered_scores)[2]


def _resolve_output_path(config: dict[str, Any], output: str | Path | None) -> Path:
    pilot_dir = configured_path(config, "pilot_sets_dir")
    if output:
        path = Path(output)
        if not path.is_absolute():
            path = pilot_dir / path
        if path.exists() and not _pilot_file_is_empty(path):
            raise SystemExit(f"Refusing to overwrite non-empty pilot set: {path}")
        return path
    pilot_dir.mkdir(parents=True, exist_ok=True)
    pilot_files = sorted(pilot_dir.glob("pilot_*.tsv"))
    pilot_001 = pilot_dir / "pilot_001.tsv"
    if not pilot_files:
        return pilot_001
    if pilot_files == [pilot_001] and _pilot_file_is_empty(pilot_001):
        return pilot_001
    next_number = max((_pilot_number(path) for path in pilot_files), default=0) + 1
    return pilot_dir / f"pilot_{next_number:03d}.tsv"


def _pilot_file_is_empty(path: Path) -> bool:
    if not path.exists():
        return True
    return all(not row.get("run_id") for row in read_tsv(path))


def _pilot_number(path: Path) -> int:
    match = PILOT_RE.match(path.name)
    return int(match.group(1)) if match else 0
