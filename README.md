# SimMgr

SimMgr is a reusable Python simulation manager for Slurm-based projects. It builds immutable simulation manifests, ingests logical runs into a SQLite registry, plans Slurm groups/arrays, creates attempt records on successful submission, runs project-specific Python simulators through a standard wrapper, collects JSONL attempt logs, exports registry snapshots, and learns simple regression-based resource models.

The implementation follows `docs/SimMgr_specification.md`.

## Quick Start

Make sure your shell's `python` command points at the environment you want to use, then set `default_project_config` in `global_config.yaml`. After that, everyday commands can use the active project by default:

```bash
python -m simmgr.cli init
python -m simmgr.cli build-manifest
python -m simmgr.cli ingest-manifest --manifest latest
python -m simmgr.cli suggest-pilot --n-runs 10
python -m simmgr.cli plan-jobs --pilot-set pilot_001.tsv --generous-resources --one-run-per-group
python -m simmgr.cli plan-jobs --where 'status == "pending"'
```

If `default_project_config` is not set yet, initialize with `python -m simmgr.cli init --project-root /path/to/project` and then add that project's `project_config.yaml` to `global_config.yaml`.

Inspect the generated `plans/plan_XXX/` directory before submission:

```bash
python -m simmgr.cli submit-jobs --plan plan_001
```

After jobs finish:

```bash
python -m simmgr.cli collect-status --plan plan_001
python -m simmgr.cli export-registry
```

See `docs/user_manual.md` for the simulator contract and demo notes. The repo also includes `demos/popstat_demo_simulator.py` plus a small seeded demo project under `demos/popstat_demo_project/`.
