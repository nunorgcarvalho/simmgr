# SimMgr Dashboard Pseudo-Specification

## 1. Purpose

The **SimMgr Dashboard** is a local web-based GUI for inspecting, monitoring, and optionally controlling SimMgr simulation projects.

The dashboard should be treated as a companion interface to the SimMgr command-line tools. It should not replace the CLI or duplicate SimMgr's core logic. Instead, it should expose a convenient visual interface for:

- monitoring logical runs, attempts, manifests, plans, and resource models;
- inspecting logs and failure states;
- visualizing resource usage and resource-model performance;
- running common SimMgr commands through safe button-driven workflows;
- giving the user a quick understanding of the state of a simulation project.

The dashboard is intended to be **local-only**. It does not need authentication, multi-user deployment, cloud hosting, or external database services for v1.

---

## 2. Recommended Implementation

The dashboard should be implemented in Python, ideally using:

- **Shiny for Python**, preferred for a structured long-term dashboard

Codex may choose the specific framework, but the app should run locally with a command conceptually like:

```bash
simmgr dashboard --project-config /path/to/project_config.yaml
```

If no `--project-config` is provided, the dashboard may use the default project config specified in SimMgr's global config.

The dashboard should read project paths, Slurm defaults, and registry location from the project config, just as other SimMgr commands do.

---

## 3. Relationship to SimMgr Core

The dashboard should be a frontend over the existing SimMgr backend.

Important design principle:

> The dashboard should not reimplement SimMgr logic.

Instead, SimMgr should expose reusable Python functions that are used by both:

1. the command-line interface; and
2. the dashboard.

For example, both the CLI and dashboard should call the same internal functions for:

- building manifests;
- ingesting manifests;
- querying runs;
- planning jobs;
- submitting plans;
- collecting status;
- learning resource models;
- exporting registry tables.

The dashboard may call these functions directly through a Python API such as:

```text
simmgr.api.build_manifest(...)
simmgr.api.ingest_manifest(...)
simmgr.api.plan_jobs(...)
simmgr.api.submit_jobs(...)
simmgr.api.collect_status(...)
simmgr.api.learn_resources(...)
```

The exact API structure is up to Codex, but the dashboard should avoid shelling out to CLI commands when a direct Python function is available.

---

## 4. Data Sources

The dashboard should primarily read from the SQLite registry defined by SimMgr.

Expected registry tables include, at minimum:

- `param_sets`
- `runs`
- `attempts`
- manifest-related metadata tables
- plan-related metadata tables, if implemented

The dashboard may also read:

- immutable manifest TSVs;
- plan directories and plan TSVs;
- `plan_summary.txt` files;
- `sbatch_commands.sh` files;
- attempt JSONL logs;
- group JSONL logs;
- Slurm stdout/stderr logs;
- resource model JSON files;
- exported registry TSVs, if useful.

The SQLite registry should be the main source for current run and attempt state.

---

## 5. Write Behavior and Safety

The dashboard may eventually trigger SimMgr actions that modify the project. These should be handled carefully.

Read-only views are safe and should be implemented first.

Actions that modify project state should:

- call the same SimMgr backend functions used by the CLI;
- show clear confirmation before running;
- display stdout/stderr or structured status after completion;
- write a dashboard command log or command history entry;
- avoid arbitrary shell execution;
- not allow users to execute arbitrary commands through the web UI.

Cluster-submitting actions, especially `submit-jobs`, should require explicit confirmation.

Recommended safety policy:

| Action type | Confirmation needed? |
|---|---:|
| Refresh dashboard | No |
| View registry/logs/plans | No |
| Build manifest | Optional |
| Ingest manifest | Yes |
| Plan jobs | Optional or yes |
| Submit jobs | Yes, strongly |
| Collect status | Optional |
| Learn resources | Optional |
| Export registry | No |

---

## 6. Suggested App Pages

The dashboard should be organized into pages, tabs, or sidebar sections. The exact layout is flexible.

Recommended pages are described below.

---

## 7. Overview Page

The overview page should summarize the entire project.

Useful cards or summary boxes:

- project name;
- project root;
- active project config path;
- number of manifests;
- number of plans;
- latest manifest ID;
- latest plan ID;
- latest resource model ID;
- total logical runs;
- pending runs;
- planned/submitted/running runs;
- succeeded runs;
- failed OOM runs;
- failed timeout runs;
- failed simulator-error runs;
- failed unknown runs;
- total attempts;
- most recent registry update.

Useful plots:

- run count by status;
- attempt count by status;
- successes/failures over time, if timestamps are available;
- failures by type.

The overview page should be the first page users see after launching the dashboard.

---

## 8. Runs Browser

The runs browser should show one row per logical run.

Suggested columns:

- `run_id`
- `param_set_id`
- `replicate`
- `status`
- `attempt_count`
- `best_attempt_id`
- `first_manifest_id`
- `updated_at`
- selected parameter columns extracted from `params_json`

Users should be able to filter by:

- run status;
- manifest ID;
- replicate number or range;
- attempt count;
- parameter values from `params_json`;
- simulator version, if present in `params_json`;
- text search over run IDs or parameter set IDs.

Users should be able to select a run and see details:

- full `params_json`;
- all attempts for that run;
- best attempt;
- associated logs;
- associated plan/group information, if available.

---

## 9. Attempts Browser

The attempts browser should show one row per execution attempt.

Suggested columns:

- `attempt_id`
- `run_id`
- `param_set_id`
- `replicate`
- `attempt`
- `status`
- `plan_id`
- `group_id`
- `array_id`
- `slurm_job_id`
- `slurm_array_task_id`
- `allocated_time_minutes`
- `allocated_ram_mb`
- `allocated_cpus`
- `elapsed_seconds`
- `max_rss_mb`
- `exit_code`
- `exit_reason`
- `started_at`
- `ended_at`
- `attempt_log_path`

Users should be able to filter by:

- status;
- failure type;
- plan ID;
- group ID;
- Slurm job ID;
- allocated RAM/time;
- elapsed time;
- max RSS;
- attempt number.

Selecting an attempt should show:

- attempt metadata;
- full attempt JSONL log;
- final terminal events;
- relevant group log events;
- Slurm stdout/stderr paths if available.

---

## 10. Manifest Browser

The manifest browser should show immutable manifests generated by SimMgr.

For each manifest, show:

- manifest ID;
- created timestamp;
- number of rows/runs;
- number of unique parameter sets;
- number of new runs ingested, if available;
- simulation set names;
- simulation spec path;
- simulation spec hash, if available.

Users should be able to preview manifest rows, including:

- `simulation_set_name`
- `param_set_id`
- `run_id`
- `replicate`
- `params_json`

The dashboard should make clear that manifests are immutable historical records.

---

## 11. Plan Browser

The plan browser should show generated Slurm plans.

For each plan, show:

- plan ID;
- created timestamp;
- selected run count;
- number of groups;
- number of arrays;
- resource buckets used;
- whether the plan has been submitted;
- number of attempts associated with the plan;
- current success/failure state of those attempts.

The user should be able to open or preview:

- `selected_runs.tsv`;
- `resource_predictions.tsv`;
- `groups.tsv`;
- `arrays.tsv`;
- `plan_summary.txt`;
- `sbatch_commands.sh`.

For unsubmitted plans, the dashboard may expose a **Submit Plan** button, with confirmation.

---

## 12. Resource Dashboard

The resource dashboard should help the user understand runtime and memory behavior.

Suggested visualizations:

- observed runtime versus predicted runtime;
- observed RAM versus predicted RAM;
- observed runtime by key simulation parameters;
- observed max RSS by key simulation parameters;
- allocated time versus observed elapsed time;
- allocated RAM versus observed max RSS;
- OOM failures by allocated RAM;
- timeout failures by allocated time;
- residuals from the current resource model.

The dashboard should also show metadata for the current/latest resource model:

- resource model ID;
- created timestamp;
- number of training attempts;
- numeric parameters used;
- categorical parameters used;
- included feature transformations;
- model coefficients, if easy to display;
- residual standard deviation or equivalent fit summaries.

The user should be able to compare resource models if multiple exist, but this is optional for v1.

---

## 13. Log Viewer

The log viewer should allow users to inspect logs without manually navigating the filesystem.

Supported logs:

- attempt JSONL logs;
- group JSONL logs;
- Slurm stdout logs;
- Slurm stderr logs;
- dashboard command logs, if implemented.

Users should be able to:

- search by `run_id`, `attempt_id`, `group_id`, or Slurm job ID;
- view the whole log;
- view the first event;
- view the last event;
- tail the last N lines;
- optionally pretty-print JSONL events.

The log viewer should not require loading very large logs entirely into memory if avoidable.

---

## 14. Command Center

The dashboard may include a command page for running common SimMgr workflows.

Suggested command buttons:

- Build manifest;
- Ingest latest manifest;
- Collect status;
- Learn resources;
- Export registry;
- Plan pending runs;
- Plan failed OOM retries;
- Plan failed timeout retries;
- Plan runs from a pilot set;
- Submit selected plan.

For planning commands, users should be able to provide basic arguments through UI controls:

- status selector;
- query expression text box;
- pilot set selector;
- resource model selector;
- retry policy selector;
- generous resources toggle for pilot jobs.

For submission commands, users should choose an existing plan and confirm submission.

Command outputs should be visible in the dashboard after execution.

Recommended command-history fields:

- command/action name;
- arguments;
- started timestamp;
- finished timestamp;
- status;
- stdout summary;
- stderr summary;
- produced manifest/plan/resource model ID, if applicable.

---

## 15. Query Builder

The dashboard should support selecting runs using a simple GUI query builder.

Useful controls:

- status dropdown;
- manifest dropdown;
- replicate range;
- attempt count range;
- parameter filters from `params_json`;
- text search over IDs.

For advanced users, optionally expose a text query field matching the SimMgr CLI query syntax, for example:

```text
status == "pending" and params.N >= 100000 and replicate <= 10
```

The GUI query builder can initially support only common filters and leave complex queries to the CLI.

---

## 16. Dashboard Command Logging

Any dashboard-triggered command that changes project state should be logged.

A simple implementation can write JSONL command logs to:

```text
logs/dashboard_commands/
```

Recommended event information:

- command/action name;
- arguments;
- project config path;
- user working directory;
- start time;
- end time;
- success/failure;
- exception message if failed;
- produced file paths or IDs.

The exact storage mechanism is flexible.

---

## 17. Concurrency and SQLite

The dashboard will read from the SQLite registry frequently.

General guidance:

- read-only queries should be safe and frequent;
- writes should go through SimMgr backend functions;
- long-running worker jobs should not write directly to SQLite from compute nodes;
- registry updates should generally happen through foreground SimMgr commands such as `submit-jobs` and `collect-status`;
- the dashboard should refresh after commands complete.

If the app provides auto-refresh, it should not refresh so aggressively that it causes unnecessary filesystem or database load. A refresh button plus optional periodic refresh is sufficient.

---

## 18. Suggested Development Phases

### Phase 1: Read-only dashboard

Implement:

- project overview;
- runs browser;
- attempts browser;
- manifest browser;
- plan browser;
- log viewer;
- basic plots.

This phase should not modify project state.

### Phase 2: Safe command buttons

Add buttons for:

- build manifest;
- ingest manifest;
- collect status;
- learn resources;
- export registry.

Log command execution.

### Phase 3: Planning controls

Add:

- query builder;
- plan pending runs;
- plan failed OOM retries;
- plan failed timeout retries;
- plan pilot set runs;
- preview newly created plan.

### Phase 4: Submission controls

Add:

- submit selected plan;
- display Slurm job IDs;
- display submission output;
- show plan-associated run/attempt progress.

Submission should require explicit confirmation.

---

## 19. Out of Scope for v1

Do not require the following for the initial dashboard:

- remote deployment;
- authentication;
- multiple users;
- editing simulation specs through the GUI;
- editing project config through the GUI;
- live Slurm job cancellation;
- automatic background polling of Slurm beyond normal refresh/status collection;
- complex project-specific result summarization;
- interactive editing of resource models;
- support for non-Slurm schedulers.

These can be considered later.

---

## 20. Summary of Desired Dashboard Behavior

The dashboard should provide a local graphical interface for SimMgr projects.

Core behavior:

```text
Launch locally from a project config
Read project state from SQLite registry and project files
Show overview of runs, attempts, manifests, plans, logs, and resources
Allow filtering and inspecting logical runs and attempts
Visualize resource predictions and observed usage
Preview manifests and plans
Expose safe buttons for common SimMgr commands
Require confirmation for Slurm submission
Log dashboard-triggered commands
Keep core logic in SimMgr backend functions, not in the dashboard itself
```

The dashboard should make SimMgr easier to monitor and operate without compromising the CLI-first, reproducible, append-only design of the main system.
