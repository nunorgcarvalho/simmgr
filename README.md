# SimMgr

SimMgr is a reusable Python simulation manager for Slurm-based projects. It builds immutable simulation manifests, ingests logical runs into a SQLite registry, plans Slurm groups/arrays, creates attempt records on successful submission, runs project-specific Python simulators through a standard wrapper, collects JSONL attempt logs, exports registry snapshots, and learns simple regression-based resource models.

The implementation follows `docs/SimMgr_specification.md`.

## Quick Start

Use the Python 3.13 environment on the cluster:

```bash
/n/groups/price/nuno/.venv_py13/bin/python -m simmgr.cli init --project-root /path/to/project
/n/groups/price/nuno/.venv_py13/bin/python -m simmgr.cli build-manifest --project-config /path/to/project/project_config.yaml
/n/groups/price/nuno/.venv_py13/bin/python -m simmgr.cli ingest-manifest --project-config /path/to/project/project_config.yaml --manifest latest
/n/groups/price/nuno/.venv_py13/bin/python -m simmgr.cli plan-jobs --project-config /path/to/project/project_config.yaml --where 'status == "pending"'
```

Inspect the generated `plans/plan_XXX/` directory before submission:

```bash
/n/groups/price/nuno/.venv_py13/bin/python -m simmgr.cli submit-jobs --project-config /path/to/project/project_config.yaml --plan plan_001
```

After jobs finish:

```bash
/n/groups/price/nuno/.venv_py13/bin/python -m simmgr.cli collect-status --project-config /path/to/project/project_config.yaml --plan plan_001
/n/groups/price/nuno/.venv_py13/bin/python -m simmgr.cli export-registry --project-config /path/to/project/project_config.yaml
```

See `docs/user_manual.md` for the simulator contract and demo notes. The repo also includes `demos/popstat_demo_simulator.py` plus a small seeded demo project under `demos/popstat_demo_project/`.
