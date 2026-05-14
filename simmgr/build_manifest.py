from __future__ import annotations

import hashlib
import itertools
from pathlib import Path
from typing import Any

from . import __version__
from .canonicalize import canonical_json
from .config import configured_path, load_project_config, project_path, state_path
from .ids import param_set_id as make_param_set_id, run_id as make_run_id
from .mini_yaml import load_yaml
from .state import next_number
from .time_utils import utc_now
from .tsv import write_tsv

MANIFEST_COLUMNS = [
    "manifest_id",
    "simulation_set_name",
    "param_set_id",
    "run_id",
    "replicate",
    "params_json",
    "created_at",
    "spec_path",
    "spec_hash",
    "simmgr_version",
    "notes",
]


def build_manifest(project_config: str | Path | None = None, global_config: str | Path | None = None) -> Path:
    config = load_project_config(project_config, global_config)
    spec_path = project_path(config, config["paths"]["simulation_spec"])
    spec = load_yaml(spec_path)
    manifest_number = next_number(state_path(config), "last_manifest_number")
    manifest_id = f"manifest_{manifest_number:03d}"
    created_at = utc_now()
    spec_hash = hashlib.sha256(spec_path.read_bytes()).hexdigest()
    rows = []
    seen_runs: set[str] = set()
    defaults = dict(spec.get("default_parameters", {}))
    default_replicates = int(defaults.pop("replicates", 1))
    for set_index, sim_set in enumerate(spec.get("simulation_sets", []), start=1):
        name = sim_set.get("name") or f"set_{set_index:03d}"
        grid = sim_set.get("grid", {})
        keys = list(grid)
        values = [grid[key] if isinstance(grid[key], list) else [grid[key]] for key in keys]
        replicates = int(sim_set.get("replicates", default_replicates))
        for combo in itertools.product(*values):
            params: dict[str, Any] = dict(defaults)
            params.update(dict(zip(keys, combo)))
            params_json = canonical_json(params)
            param_set_id = make_param_set_id(params_json)
            for replicate in range(1, replicates + 1):
                run_id = make_run_id(param_set_id, replicate)
                if run_id in seen_runs:
                    continue
                seen_runs.add(run_id)
                rows.append(
                    {
                        "manifest_id": manifest_id,
                        "simulation_set_name": name,
                        "param_set_id": param_set_id,
                        "run_id": run_id,
                        "replicate": replicate,
                        "params_json": params_json,
                        "created_at": created_at,
                        "spec_path": str(spec_path),
                        "spec_hash": spec_hash,
                        "simmgr_version": __version__,
                        "notes": "",
                    }
                )
    out = configured_path(config, "manifests_dir") / f"{manifest_id}.tsv"
    write_tsv(out, rows, MANIFEST_COLUMNS)
    return out

