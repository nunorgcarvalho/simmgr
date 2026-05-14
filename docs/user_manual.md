# SimMgr User Manual

SimMgr manages simulation manifests, a SQLite run registry, Slurm plans, attempts, JSONL logs, status collection, and resource learning. Your simulator remains project-specific and is called through a standard Python CLI interface.

## Quick Start

```bash
/n/groups/price/nuno/.venv_py13/bin/python -m simmgr.cli init --project-root /path/to/project
/n/groups/price/nuno/.venv_py13/bin/python -m simmgr.cli build-manifest --project-config /path/to/project/project_config.yaml
/n/groups/price/nuno/.venv_py13/bin/python -m simmgr.cli ingest-manifest --project-config /path/to/project/project_config.yaml --manifest latest
/n/groups/price/nuno/.venv_py13/bin/python -m simmgr.cli plan-jobs --project-config /path/to/project/project_config.yaml --where 'status == "pending"'
```

Inspect `plans/plan_XXX/plan_summary.txt` and `sbatch_commands.sh`, then submit:

```bash
/n/groups/price/nuno/.venv_py13/bin/python -m simmgr.cli submit-jobs --project-config /path/to/project/project_config.yaml --plan plan_001
```

After jobs finish:

```bash
/n/groups/price/nuno/.venv_py13/bin/python -m simmgr.cli collect-status --project-config /path/to/project/project_config.yaml --plan plan_001
/n/groups/price/nuno/.venv_py13/bin/python -m simmgr.cli export-registry --project-config /path/to/project/project_config.yaml
```

## Simulator Contract

The simulator is called with `--params-json`, `--run-id`, `--param-set-id`, `--replicate`, `--attempt-id`, `--attempt`, `--seed`, `--log-path`, and `--output-dir`.

The simulator should append a terminal event:

```json
{"event":"simulator_finished","status":"succeeded"}
```

For controlled errors, use:

```json
{"event":"simulator_finished","status":"failed_simulator_error","error_message":"..."}
```

Large scientific outputs should be written as files and referenced with JSONL `result_file` events.

## Local Demo

The repo includes `demos/popstat_demo_simulator.py`, which uses `popstatgensim` in the same style as the notebook example. The automated smoke workflow initializes `demos/popstat_demo_project/`, builds and ingests a manifest, plans jobs, manually creates local attempts for testing, runs a group, collects status, and exports the registry.

