# SimMgr User Manual

SimMgr is a simulation orchestration layer. It does not own your scientific simulator; it owns the boring but important machinery around it: manifests, stable run IDs, a SQLite registry, Slurm plans, attempt logs, status collection, retries, resource learning, and registry exports.

This manual assumes your shell's `python` command already points at the environment you want SimMgr and your simulator to use.

## 1. Set An Active Project

SimMgr can be used with `--project-config /path/to/project_config.yaml`, but the intended everyday workflow is to set a default project once in `global_config.yaml`:

```yaml
default_project_config: /path/to/my_sim_project/project_config.yaml
```

After that, most commands can omit `--project-config`:

```bash
python -m simmgr.cli build-manifest
python -m simmgr.cli ingest-manifest
python -m simmgr.cli plan-jobs --where 'status == "pending"'
```

Use `--project-config` only when you intentionally want to operate on a different project.

## 2. Initialize A Project

Create the project directory once:

```bash
python -m simmgr.cli init --project-root /path/to/my_sim_project
```

This creates:

```text
project_config.yaml
simulation_spec.yaml
state/simmgr_state.json
manifests/
registry/simmgr.sqlite
registry/exports/
pilot_sets/pilot_001.tsv
plans/
logs/attempts/
logs/groups/
logs/slurm/
outputs/
resource_models/
```

Then edit `global_config.yaml` so `default_project_config` points at this new project. This is what makes later commands short.

## 3. Edit `project_config.yaml`

`project_config.yaml` describes project management details, not the scientific parameter grid.

The most important fields are:

```yaml
simulator:
  script: /path/to/my_simulator.py
  python_executable: python

resources:
  default_time_minutes: 60
  default_ram_gb: 16
  min_time_minutes: 5
  min_ram_gb: 1
  max_job_time_minutes: 720
  max_ram_gb: 128
```

`default_ram_gb`, `min_ram_gb`, and `max_ram_gb` are all in GB. `max_ram_gb` is a hard planning ceiling. If the learned model predicts more than this, SimMgr caps the allocation and marks the row as `ram_capped` in `resource_predictions.tsv`.

## 4. Edit `simulation_spec.yaml`

`simulation_spec.yaml` defines the simulation space.

Example:

```yaml
default_parameters:
  simulator_version: 1
  N: 500
  num_variants: 500
  h2: 0.5
  replicates: 2

simulation_sets:
  - name: small_pilot
    grid:
      N: [500, 1000, 2000]
      num_variants: [500, 1000]
      h2: [0.2, 0.5]
    replicates: 1

resource_model:
  numeric_parameters:
    - N
    - num_variants
  categorical_parameters: []
  include_log_terms: true
  include_square_terms: false
  include_pairwise_products: false
  ridge_lambda: 1.0
```

Important identity rule: `simulator_version` is part of `params_json`. If the simulator's scientific behavior changes, increment `simulator_version` so new runs get new stable IDs.

## 5. Build And Ingest A Manifest

Build an immutable manifest:

```bash
python -m simmgr.cli build-manifest
```

This writes `manifests/manifest_XXX.tsv`. It does not modify the registry and does not submit jobs.

Then ingest it:

```bash
python -m simmgr.cli ingest-manifest --manifest latest
```

Ingesting records unique parameter sets and logical runs in `registry/simmgr.sqlite`. If a later manifest overlaps an earlier one, SimMgr does not duplicate runs; it records the new manifest membership and updates `last_manifest_id`.

## 6. Query Runs

Use `query` to inspect the registry:

```bash
python -m simmgr.cli query --status pending
python -m simmgr.cli query --where 'replicate == 1'
python -m simmgr.cli query --where 'params.N >= 2000'
```

The query language is intentionally small and safe. It supports comparisons on run columns and `params.<name>` values from `params_json`.

## 7. Choose Pilot Runs

Pilot jobs teach SimMgr the approximate resource curve. The pilot set should span the parameters that drive runtime and memory.

A weak pilot might only include tiny runs:

```text
N = 500
num_variants = 500
```

That is useful for checking that the simulator works, but it is not enough for confident extrapolation to much larger `N`. For production planning across larger populations, include pilot points across the range, for example:

```text
small:   N = 500,   num_variants = 500
medium:  N = 2000,  num_variants = 1000
large:   N = 5000,  num_variants = 5000
```

You do not need many replicates for the first pilot. One or two representative runs per resource regime is often more informative than many replicates of the smallest condition.

Put selected run IDs in `pilot_sets/pilot_001.tsv`:

```text
run_id
22b62373dba2bc96_r1
9e2e7a92b4c8feb1_r1
```

## 8. Plan Pilot Jobs

Plan pilot jobs with conservative fallback resources:

```bash
python -m simmgr.cli plan-jobs --pilot-set pilot_001.tsv --generous-resources
```

This creates `plans/plan_XXX/`:

```text
selected_runs.tsv
resource_predictions.tsv
groups.tsv
arrays.tsv
sbatch_commands.sh
plan_summary.txt
```

Always inspect `resource_predictions.tsv` and `sbatch_commands.sh` before submitting. Memory columns are in GB:

```text
predicted_ram_gb
allocated_ram_gb
resource_limit_status
```

If `resource_limit_status` is `ram_capped`, the model wanted more than `resources.max_ram_gb`. Treat that as a warning: either the model is extrapolating badly, the job truly exceeds the configured cluster/project maximum, or the pilot set did not cover that region well.

## 9. Submit Jobs

After inspecting the plan:

```bash
python -m simmgr.cli submit-jobs --plan plan_001
```

Submission is intentionally separate from planning. SimMgr creates attempt records only after `sbatch` succeeds for an array. Slurm workers do not write to the SQLite registry; they write JSONL logs only.

## 10. Collect Status

After Slurm jobs finish:

```bash
python -m simmgr.cli collect-status --plan plan_001
```

`collect-status` reads attempt logs and, when available, Slurm accounting. It updates attempt statuses and summarized logical-run statuses in the registry.

Common statuses include:

```text
succeeded
failed_oom
failed_timeout
failed_node
failed_simulator_error
failed_unknown
```

## 11. Learn Resources

Once pilot attempts have succeeded:

```bash
python -m simmgr.cli learn-resources
```

This writes `resource_models/resource_model_XXX.json`. SimMgr fits simple log-linear ridge regressions for runtime and, when trustworthy Slurm-attributed per-run RSS is available, memory. The model is deliberately lightweight and inspectable.

A key caveat: a learned model can only extrapolate responsibly if the pilot data covers the resource-relevant range. If pilots only include very small `N`, predictions for large `N` may be unstable. The `max_ram_gb` ceiling protects the cluster plan from impossible memory requests, but it does not magically make the extrapolation scientifically trustworthy.

## 12. Plan Production Runs

After resource learning:

```bash
python -m simmgr.cli plan-jobs --where 'status == "pending"'
```

Then inspect:

```text
plans/plan_XXX/resource_predictions.tsv
plans/plan_XXX/plan_summary.txt
plans/plan_XXX/sbatch_commands.sh
```

If many rows are capped, run additional pilots in the capped region before submitting production.

After `collect-status --plan plan_XXX`, SimMgr also writes:

```text
plans/plan_XXX/resource_assessment.tsv
```

This compares predicted and observed resources for attempts in that plan. Runtime is assessed per run from the SimMgr wrapper's `attempt_finished.elapsed_seconds` event. RAM is only assessed when SimMgr can attribute memory usage from Slurm accounting; simulator-reported RAM is not treated as authoritative.

## 13. Retry Failures

Retries create new attempts for existing logical runs. They do not create new manifests.

OOM retry:

```bash
python -m simmgr.cli plan-jobs --where 'status == "failed_oom"' --retry-policy oom
python -m simmgr.cli submit-jobs --plan plan_XXX
```

Timeout retry:

```bash
python -m simmgr.cli plan-jobs --where 'status == "failed_timeout"' --retry-policy timeout
python -m simmgr.cli submit-jobs --plan plan_XXX
```

Retry policies use the learned model plus the previous allocation multiplied by the configured retry multiplier, while still respecting `max_job_time_minutes` and `max_ram_gb`.

## 14. Export The Registry

For inspection or sharing:

```bash
python -m simmgr.cli export-registry
```

Exports go to timestamped directories under `registry/exports/`. They are snapshots, not the source of truth.

## 15. Simulator Contract

SimMgr calls your simulator like this:

```bash
python simulator.py \
  --params-json '<canonical params json>' \
  --run-id '<run_id>' \
  --param-set-id '<param_set_id>' \
  --replicate '<replicate>' \
  --attempt-id '<attempt_id>' \
  --attempt '<attempt>' \
  --seed '<seed>' \
  --log-path '<attempt_log_path>' \
  --output-dir '<output_dir>'
```

Your simulator should append a terminal event to the provided log path:

```json
{"event":"simulator_finished","status":"succeeded"}
```

Do not have the simulator guess or self-report RAM usage for SimMgr resource learning. SimMgr only records RAM when it can be attributed through Slurm accounting. For grouped sequential runs, ordinary batch-level MaxRSS is usually group-level rather than run-level, so SimMgr leaves per-run RAM blank unless it can attribute the value safely.

For controlled simulator errors:

```json
{"event":"simulator_finished","status":"failed_simulator_error","error_message":"..."}
```

Large scientific outputs should be written as files and referenced with JSONL `result_file` events.

## 16. Demo Project

The repo includes:

```text
demos/popstat_demo_simulator.py
demos/popstat_demo_project/
```

The demo simulator uses `popstatgensim` and follows the same simulator interface. The demo project is intentionally small; it is good for exercising the workflow, not for proving resource extrapolation at high population sizes.
