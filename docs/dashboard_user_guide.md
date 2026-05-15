# SimMgr Dashboard User Guide

The SimMgr dashboard is a local Shiny web app for inspecting and operating a SimMgr project.

Launch it from the active default project:

```bash
python -m simmgr.cli dashboard
```

Or point at a project explicitly:

```bash
python -m simmgr.cli dashboard --project-config /path/to/project_config.yaml --port 8000
```

By default the app binds to `127.0.0.1`, so it is local-only. Add `--launch-browser` if you want Shiny to open a browser automatically.

## Pages

The dashboard includes:

- `Overview`: project paths, run/attempt status counts, latest plan, latest manifest, latest resource model, and status plots.
- `Runs`: one row per logical run, with status/search/manifest/replicate filters and a run detail viewer.
- `Attempts`: one row per attempt, with status/search/plan filters and an attempt log viewer.
- `Manifests`: ingested and on-disk manifests, plus immutable manifest previews.
- `Plans`: plan directories, submission state, attempt summaries, and previews for plan TSVs, summaries, and sbatch commands.
- `Resources`: resource model metadata, model JSON, resource assessment rows, and predicted-vs-observed plots.
- `Logs`: attempt, group, Slurm, and dashboard command logs with search, tail, and JSONL pretty-printing.
- `Command Center`: safe buttons for common SimMgr workflows.

## Command Safety

Dashboard actions call the same Python functions used by the CLI. They do not shell out to arbitrary commands.

The following actions are available:

- Build manifest.
- Ingest latest manifest.
- Collect status.
- Learn resources.
- Export registry.
- Create plans with status, where-expression, pilot-set, retry-policy, generous-resource, and one-run-per-group controls.
- Submit a selected plan.

Submitting a plan defaults to dry-run mode. A real Slurm submission requires unchecking dry-run and checking the confirmation box.

Every dashboard-triggered command is logged to:

```text
logs/dashboard_commands/commands.jsonl
```

The log records the action name, arguments, project path, start/end timestamps, success/failure, stdout/stderr, and returned result.

## Refresh Model

The app reads from the SQLite registry and project files when outputs render. Use the `Refresh dashboard` button after external CLI commands or Slurm status collection. The dashboard does not continuously poll Slurm on its own.
