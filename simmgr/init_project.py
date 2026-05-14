from __future__ import annotations

from pathlib import Path

from .config import DEFAULT_SIMULATION_SPEC, load_global_config, make_default_project_config
from .mini_yaml import write_yaml
from .registry import initialize
from .state import DEFAULT_STATE, save_state
from .tsv import write_tsv


def init_project(project_root: str | Path, global_config: str | Path | None = None, force: bool = False) -> Path:
    root = Path(project_root).expanduser().resolve()
    config_path = root / "project_config.yaml"
    if config_path.exists() and not force:
        raise SystemExit(f"Refusing to overwrite initialized project at {root}. Use --force to reinitialize.")

    global_defaults = load_global_config(global_config)
    config = make_default_project_config(root, global_defaults)
    paths = config["paths"]
    for key in [
        "manifests_dir",
        "registry_dir",
        "plans_dir",
        "logs_dir",
        "outputs_dir",
        "pilot_sets_dir",
        "resource_models_dir",
        "state_dir",
    ]:
        (root / paths[key]).mkdir(parents=True, exist_ok=True)
    for sub in ["attempts", "groups", "slurm"]:
        (root / paths["logs_dir"] / sub).mkdir(parents=True, exist_ok=True)
    (root / paths["registry_dir"] / "exports").mkdir(parents=True, exist_ok=True)

    write_yaml(config_path, config)
    write_yaml(root / "simulation_spec.yaml", DEFAULT_SIMULATION_SPEC)
    save_state(root / paths["state_dir"] / "simmgr_state.json", DEFAULT_STATE)
    write_tsv(root / paths["pilot_sets_dir"] / "pilot_001.tsv", [], ["run_id"])
    initialize(root / paths["registry_dir"] / "simmgr.sqlite")
    return root

