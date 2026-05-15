# SimMgr Specification

## 1. Purpose

**SimMgr** is a reusable Python-based simulation management system for running, tracking, retrying, and learning resource requirements for simulation studies on a Slurm computing cluster.

SimMgr is intended for projects where a researcher already has project-specific simulation code. SimMgr does **not** implement the scientific simulator. Instead, it manages:

- simulation manifests;
- stable simulation identifiers;
- a persistent SQLite registry of parameter sets, logical runs, and execution attempts;
- Slurm planning, grouping, arrays, and submission;
- per-attempt JSONL logging;
- failure classification;
- resource prediction using regression models;
- retry workflows for failed jobs;
- append-only project history.

The design goal is to support many independent projects over time while keeping SimMgr itself generic and reusable.

---

## 2. Design Principles

### 2.1 Separation of responsibilities

SimMgr manages orchestration. Project-specific code manages scientific simulation behavior.

SimMgr is responsible for:

- creating project directory structure;
- reading project configuration;
- building simulation manifests from a simulation specification;
- ingesting manifests into the persistent SQLite registry;
- assigning stable IDs;
- selecting runs;
- predicting resources;
- creating Slurm job plans;
- grouping short runs into longer Slurm jobs;
- using job arrays where appropriate;
- submitting jobs;
- wrapping simulator execution;
- collecting Slurm and simulator status;
- updating run and attempt registry tables;
- fitting resource prediction models.

The project-specific simulator is responsible for:

- accepting simulation parameters from SimMgr;
- running the actual simulation and analysis;
- appending standardized and project-specific JSONL events to the attempt log;
- writing any large project-specific output files if needed.

Project-specific result summarization is not part of core SimMgr. A project may have its own `summarize_results.py` or equivalent.

---

### 2.2 Immutable manifests

Every generated manifest is immutable. If the user changes the desired simulation space, SimMgr creates a new manifest rather than overwriting a previous one.

Manifests may overlap with previous manifests. Ingesting a manifest adds only new unique logical runs to the registry, while also recording that already-existing runs appeared in the new manifest.

---

### 2.3 Logical runs versus attempts

A **logical run** is a unique simulation unit defined by:

```text
parameter set + replicate number
```

An **attempt** is one execution attempt of a logical run under a particular Slurm allocation and execution context.

A logical run may have many attempts over time. For example:

```text
attempt 1: failed due to OOM
attempt 2: succeeded after memory allocation increased
```

Retries do not create new manifests and do not create new logical runs. They create new attempts for existing logical runs.

---

### 2.4 Text-first storage with a SQLite registry

SimMgr should use text-first storage for files that users inspect or edit, but the mutable registry should be SQLite.

Use:

| Purpose | Format |
|---|---|
| Human-edited configs | YAML |
| Simulation specification | YAML |
| Manifests | TSV |
| Registry of parameter sets, runs, attempts, and manifest membership | SQLite |
| Optional registry exports | TSV |
| Plans | TSV plus shell scripts |
| Logs | JSONL |
| Resource models | JSON |
| Mutable SimMgr state | JSON |
| Pilot sets | TSV |
| Large project-specific outputs | Project-defined; referenced from JSONL logs |

SQLite should be used only for the registry, not for manifests, plans, logs, resource models, or project-specific scientific outputs.

The registry database should live at:

```text
registry/simmgr.sqlite
```

SimMgr should provide a way to export registry tables to human-readable TSV snapshots, for example:

```text
registry/exports/param_sets.tsv
registry/exports/runs.tsv
registry/exports/attempts.tsv
registry/exports/manifest_runs.tsv
```

The only exception to text-first storage outside the registry is that project-specific scientific outputs may use formats appropriate to the project. SimMgr should not assume or parse those files unless directed by the project-specific simulator or summarizer.

---

### 2.5 No silent overwriting

SimMgr should never overwrite previous manifests, plans, logs, resource models, registry exports, or successful results without explicit user instruction.

The SQLite registry is mutable by design, but updates must happen through transactions. Operations that update multiple registry tables should be atomic: either all related changes are committed, or none are.

---

### 2.6 Explicit parameter hashing

Parameter hashes are computed from fully explicit canonical parameter JSON.

SimMgr should not infer that a missing parameter is equivalent to a newly added parameter with a default value. Existing parameter sets and run IDs are never recomputed.

If the user adds a new simulation parameter to the specification, new parameter hashes are expected.

If the user changes scientific behavior of the simulator, they should increment `simulator_version` in `simulation_spec.yaml`; since `simulator_version` is treated as an ordinary simulation parameter, this changes hashes.

---

### 2.7 Avoid registry writes from Slurm worker jobs

For v1, Slurm worker processes should not write to the SQLite registry. This avoids concurrent writes from many cluster jobs and avoids potential filesystem-locking problems on shared cluster storage.

Specifically:

- `submit-jobs` creates attempt rows and records Slurm job IDs.
- `run-group` and `run-one`, which execute inside Slurm jobs, write JSONL logs only.
- `collect-status`, run later by the user or driver process, reads logs and Slurm accounting, then updates the SQLite registry.

This design keeps SQLite as a robust single-writer registry while preserving scalable Slurm execution.

---

## 3. Repository and Project Structure

### 3.1 SimMgr repository

The SimMgr repository should be a standalone Python project.

Suggested structure:

```text
SimMgr/
  README.md
  global_config.yaml

  simmgr/
    __init__.py
    cli.py
    init_project.py
    build_manifest.py
    ingest_manifest.py
    query_runs.py
    learn_resources.py
    plan_jobs.py
    submit_jobs.py
    run_group.py
    run_one.py
    collect_status.py
    export_registry.py

    ids.py
    canonicalize.py
    registry.py
    registry_schema.py
    resources.py
    slurm.py
    logging_utils.py
    atomic_io.py
    validation.py
```

This is only a suggested internal layout. Codex may reorganize implementation details, but the user-facing commands and file formats should follow this specification.

---

### 3.2 Global config

SimMgr has a global config file, preferably at one of:

```text
~/.simmgr/global_config.yaml
```

or, for early development:

```text
SimMgr/global_config.yaml
```

The global config stores user-wide defaults and an optional active project config path.

Example:

```yaml
default_project_config: /path/to/current/project/project_config.yaml

slurm_defaults:
  partition: short
  account: my_account
  cpus_per_task: 1
  max_array_size: 1000

resource_defaults:
  max_job_time_minutes: 720
  default_time_minutes: 60
  default_ram_gb: 16
  min_time_minutes: 5
  min_ram_gb: 1
  max_ram_gb: 128
  safety_time_multiplier: 1.25
  safety_ram_multiplier: 1.25
  oom_retry_multiplier: 2.0
  timeout_retry_multiplier: 2.0

path_defaults:
  manifests_dir: manifests
  registry_dir: registry
  plans_dir: plans
  logs_dir: logs
  outputs_dir: outputs
  pilot_sets_dir: pilot_sets
  resource_models_dir: resource_models
  state_dir: state
```

The global config is also the source of defaults used when initializing a project. However, it should not be copied literally into the project config. Fields such as `default_project_config` are global-only and should not be placed in a project config.

---

### 3.3 Project initialization

A user starts a new simulation project with a command conceptually like:

```bash
simmgr init --project-root /path/to/project_sims
```

This creates:

```text
/path/to/project_sims/
  project_config.yaml
  simulation_spec.yaml

  state/
    simmgr_state.json

  manifests/
  registry/
    simmgr.sqlite
    exports/

  pilot_sets/
    pilot_001.tsv

  plans/
  logs/
    attempts/
    groups/
    slurm/

  outputs/
  resource_models/
```

The initial `pilot_sets/pilot_001.tsv` should contain only:

```text
run_id
```

The user can later fill in run IDs after a manifest is built and ingested.

---

### 3.4 Project config

The project config is human-editable YAML. It defines project-level locations and Slurm defaults. It should not define scientific simulation parameters.

Example:

```yaml
project_name: my_statgen_project
project_root: /path/to/project_sims

simulator:
  script: /path/to/project/simulator.py
  python_executable: python

slurm:
  partition: short
  account: my_account
  cpus_per_task: 1
  max_array_size: 1000

resources:
  max_job_time_minutes: 720
  default_time_minutes: 60
  default_ram_gb: 16
  min_time_minutes: 5
  min_ram_gb: 1
  max_ram_gb: 128
  safety_time_multiplier: 1.25
  safety_ram_multiplier: 1.25
  oom_retry_multiplier: 2.0
  timeout_retry_multiplier: 2.0

paths:
  simulation_spec: simulation_spec.yaml
  manifests_dir: manifests
  registry_dir: registry
  plans_dir: plans
  logs_dir: logs
  outputs_dir: outputs
  pilot_sets_dir: pilot_sets
  resource_models_dir: resource_models
  state_dir: state
```

The registry path is derived from `project_root` and `paths.registry_dir`:

```text
<project_root>/<registry_dir>/simmgr.sqlite
```

If a SimMgr command is not passed `--project-config`, it may use `default_project_config` from the global config. The user is responsible for updating the global default when switching projects.

---

### 3.5 Mutable state file

Machine-generated counters and current IDs should not be stored in `project_config.yaml`.

Use:

```text
state/simmgr_state.json
```

Example:

```json
{
  "last_manifest_number": 3,
  "last_plan_number": 7,
  "last_resource_model_number": 2
}
```

This file is machine-managed and should be updated atomically.

Do not store attempt counters in `simmgr_state.json`. Attempt numbers should be derived transactionally from the SQLite registry for each `run_id`.

---

## 4. Simulation Specification

### 4.1 `simulation_spec.yaml`

The simulation specification is human-editable YAML. It defines simulation parameters and simulation sets.

Example:

```yaml
default_parameters:
  simulator_version: 1
  architecture: polygenic
  effect_distribution: normal
  ascertainment_model: none
  N: 500
  num_variants: 500
  h2: 0.5
  replicates: 1

simulation_sets:
  - grid:
      N: [500, 1000, 2000]
      num_variants: [500, 1000]
      h2: [0.1, 0.2, 0.5]
    replicates: 20

  - name: large_N_extra
    grid:
      N: [4000, 5000]
      num_variants: [5000]
      h2: [0.2]
    replicates: 5

resource_model:
  numeric_parameters:
    - N
    - num_variants
  categorical_parameters:
    - architecture
  include_log_terms: true
  include_square_terms: true
  include_pairwise_products: true
```

The `name` field of a simulation set is optional. If omitted, SimMgr assigns names such as:

```text
set_001
set_002
set_003
```

---

### 4.2 Defaults and overrides

For each simulation set:

1. Start with `default_parameters`.
2. Expand the grid inside that simulation set.
3. Override defaults with grid values.
4. Create one parameter set for each unique expanded combination.
5. Create one logical run per replicate.

The manifest builder should produce fully explicit parameter JSON for each parameter set based on the current spec.

A simulation set is simply a group of variable lists that are expanded grid-like. The combinations of parameters in each grid are then appended together across grids to produce a single manifest.

---

### 4.3 Project-specific advanced specification

For more complex parameter combinations that do not fit the YAML grid model, SimMgr may support a project-specific manifest builder later.

The only required contract is that the custom builder outputs a manifest TSV matching the standard manifest schema.

---

## 5. Identity Scheme

### 5.1 Canonical parameter JSON

Each parameter set is represented by canonical JSON.

Canonicalization requirements:

- sort keys deterministically;
- use consistent JSON formatting;
- use explicit values from `default_parameters` plus overrides;
- do not include replicate number;
- do include `simulator_version` if specified in the simulation spec;
- do not include transient execution information such as Slurm allocation, attempt number, timestamps, or paths.

Example canonical JSON:

```json
{"N":100000,"architecture":"polygenic","effect_distribution":"normal","h2":0.2,"num_variants":50000,"simulator_version":1}
```

---

### 5.2 `param_set_id`

The `param_set_id` is a hash of canonical `params_json`.

```text
param_set_id = hash(canonical params_json)
```

The exact hash algorithm can be chosen by Codex, but it should be stable, deterministic, and collision-resistant enough for practical use. SHA-256 truncated to 12 to 16 hex characters is reasonable.

Example:

```text
8f3a91c4e2b7
```

The `param_set_id` is a hash, not a counter.

---

### 5.3 `run_id`

The `run_id` is:

```text
<param_set_id>_r<replicate>
```

Example:

```text
8f3a91c4e2b7_r2
```

Replicate numbers should be positive integers unless explicitly extended later.

The replicate number is not part of `params_json`.

---

### 5.4 `attempt_id`

The `attempt_id` is:

```text
<run_id>_a<attempt>
```

Example:

```text
8f3a91c4e2b7_r2_a3
```

Attempt numbers are assigned by the SQLite registry inside a transaction:

```text
attempt = 1 + max(previous attempt for this run_id)
```

The first attempt for a run is attempt 1.

---

### 5.5 Effect of adding new parameters

Existing `param_set_id` values are never recomputed.

If a new parameter is added to `simulation_spec.yaml`, future manifests include it in canonical `params_json`, causing new hashes.

SimMgr should not assume that:

```json
{"N":100000,"h2":0.2}
```

is equivalent to:

```json
{"N":100000,"h2":0.2,"new_parameter":0}
```

unless a future explicit hash-equivalence feature is added. For v1, no such equivalence should be inferred.

---

## 6. Persistent Data Files and SQLite Registry

### 6.1 Manifests

Each manifest is immutable and stored as:

```text
manifests/manifest_001.tsv
manifests/manifest_002.tsv
...
```

Required columns:

| Column | Meaning |
|---|---|
| `manifest_id` | Manifest ID, for example `manifest_001` |
| `simulation_set_name` | Name or auto-generated set name |
| `param_set_id` | Hash of canonical params JSON |
| `run_id` | `<param_set_id>_r<replicate>` |
| `replicate` | Replicate number |
| `params_json` | Canonical JSON for parameter set |
| `created_at` | Timestamp of manifest creation |

Recommended optional columns:

| Column | Meaning |
|---|---|
| `spec_path` | Path to simulation spec used |
| `spec_hash` | Hash of simulation spec file |
| `simmgr_version` | Version of SimMgr |
| `notes` | Optional notes |

A manifest may contain parameter sets and runs already present in previous manifests.

---

### 6.2 Registry database

The registry is a SQLite database:

```text
registry/simmgr.sqlite
```

The registry is the source of truth for:

- unique parameter sets ever ingested;
- unique logical runs ever ingested;
- execution attempts;
- manifest ingestion metadata;
- run membership in manifests;
- current summarized status of each logical run.

The database should be initialized by `simmgr init` and migrated by SimMgr if future schema versions are introduced.

Recommended SQLite settings:

- Enable foreign keys on every connection: `PRAGMA foreign_keys = ON`.
- Store a registry schema version using `PRAGMA user_version` and/or a `registry_metadata` table.
- Use transactions for every operation that updates more than one table.
- Avoid many concurrent writers; Slurm workers should not write to the registry.
- Use a reasonable SQLite busy timeout for command-line robustness.
- Do not assume WAL mode is safe on every cluster filesystem. Default SQLite journaling is acceptable for v1 unless the user explicitly enables WAL after verifying filesystem support.

---

### 6.3 `registry_metadata` table

Stores metadata about the registry itself.

Required fields, represented either as key-value rows or explicit columns:

| Field | Meaning |
|---|---|
| `schema_version` | Integer schema version |
| `created_at` | Timestamp database was created |
| `updated_at` | Timestamp database metadata was last updated |
| `simmgr_version` | SimMgr version that created or last migrated the registry, if available |

A simple key-value table is acceptable:

| Column | Meaning |
|---|---|
| `key` | Metadata key |
| `value` | Metadata value |

---

### 6.4 `manifest_files` table

One row per manifest file that has been ingested or registered.

Required columns:

| Column | Meaning |
|---|---|
| `manifest_id` | Primary key, for example `manifest_001` |
| `manifest_path` | Path to manifest TSV |
| `created_at` | Timestamp from manifest creation if available |
| `ingested_at` | Timestamp ingested into registry |
| `spec_path` | Path to simulation spec used, if known |
| `spec_hash` | Hash of simulation spec file, if known |
| `simmgr_version` | SimMgr version, if known |
| `row_count` | Number of rows in manifest |
| `new_param_set_count` | Number of newly inserted parameter sets |
| `new_run_count` | Number of newly inserted logical runs |
| `notes` | Optional notes |

---

### 6.5 `param_sets` table

One row per unique parameter set ever ingested.

Required columns:

| Column | Meaning |
|---|---|
| `param_set_id` | Primary key; hash of canonical params JSON |
| `params_json` | Canonical JSON |
| `first_manifest_id` | First manifest where this parameter set appeared |
| `last_manifest_id` | Most recent manifest where this parameter set appeared |
| `first_seen_at` | Timestamp first ingested |
| `updated_at` | Last registry update timestamp |
| `notes` | Optional notes |

Constraints and indexes:

- `param_set_id` is the primary key.
- `params_json` should be unique if practical.
- `first_manifest_id` and `last_manifest_id` may reference `manifest_files.manifest_id`.

Existing rows should not be modified except to update `last_manifest_id`, `updated_at`, or notes.

---

### 6.6 `runs` table

One row per unique logical run.

Required columns:

| Column | Meaning |
|---|---|
| `run_id` | Primary key; `<param_set_id>_r<replicate>` |
| `param_set_id` | Foreign key to `param_sets.param_set_id` |
| `replicate` | Replicate number |
| `status` | Current logical-run status |
| `attempt_count` | Number of attempts created for this run; can be cached or recomputed |
| `best_attempt_id` | Best/current attempt ID, if any |
| `first_manifest_id` | First manifest where this run appeared |
| `last_manifest_id` | Most recent manifest where this run appeared |
| `first_seen_at` | Timestamp first ingested |
| `updated_at` | Last registry update timestamp |
| `notes` | Optional notes |

Suggested status values:

```text
pending
planned
submitted
running
succeeded
failed_oom
failed_timeout
failed_node
failed_simulator_error
failed_validation
failed_unknown
excluded
```

The run status summarizes the logical run. Detailed execution history is in the `attempts` table.

Best attempt default logic:

- If one or more attempts succeeded, `best_attempt_id` is the most recent successful attempt.
- Otherwise, `best_attempt_id` is the most recent attempt.
- If no attempt has been created, leave blank or null.

Constraints and indexes:

- `run_id` is the primary key.
- `param_set_id` references `param_sets.param_set_id`.
- `(param_set_id, replicate)` should be unique.
- Index `status` for common queries.
- Index `first_manifest_id` and `last_manifest_id` if manifest filtering is common.

---

### 6.7 `attempts` table

One row per execution attempt.

Required columns:

| Column | Meaning |
|---|---|
| `attempt_id` | Primary key; `<run_id>_a<attempt>` |
| `run_id` | Logical run ID |
| `param_set_id` | Parameter set ID, denormalized for convenience |
| `replicate` | Replicate number, denormalized for convenience |
| `attempt` | Integer attempt number |
| `status` | Attempt status |
| `plan_id` | Plan that created this attempt |
| `group_id` | Group containing this attempt |
| `array_id` | Slurm array identifier within SimMgr plan, if applicable |
| `slurm_job_id` | Slurm job ID, once submitted |
| `slurm_array_task_id` | Slurm array task ID, if applicable |
| `allocated_time_minutes` | Time requested from Slurm |
| `allocated_ram_gb` | Memory requested from Slurm, in GB |
| `allocated_cpus` | CPUs requested |
| `attempt_log_path` | JSONL log path |
| `created_at` | Attempt creation timestamp |
| `submitted_at` | Submission timestamp, if known |
| `started_at` | Attempt start timestamp, if known |
| `ended_at` | Attempt end timestamp, if known |
| `elapsed_seconds` | Runtime if known |
| `max_rss_gb` | Maximum RSS if known, in GB |
| `exit_code` | Process exit code if known |
| `exit_reason` | Slurm or SimMgr failure reason |
| `updated_at` | Last update timestamp |

Attempt statuses may include:

```text
planned
submitted
running
succeeded
failed_oom
failed_timeout
failed_node
failed_simulator_error
failed_validation
failed_unknown
not_started_due_to_group_failure
submission_failed
```

Constraints and indexes:

- `attempt_id` is the primary key.
- `run_id` references `runs.run_id`.
- `param_set_id` references `param_sets.param_set_id`.
- `(run_id, attempt)` should be unique.
- Index `run_id`.
- Index `status`.
- Index `plan_id`.
- Index `slurm_job_id`.

Attempt-number assignment must be done in a transaction to avoid duplicate attempt numbers.

---

### 6.8 `manifest_runs` table

Records which logical runs appeared in which manifest. This preserves manifest overlap history even when runs already existed before a manifest was ingested.

Required columns:

| Column | Meaning |
|---|---|
| `manifest_id` | Manifest ID |
| `run_id` | Logical run ID |
| `param_set_id` | Parameter set ID |
| `replicate` | Replicate number |
| `simulation_set_name` | Name or auto-generated set name from manifest |
| `params_json` | Canonical JSON as it appeared in the manifest |
| `created_at` | Manifest row creation timestamp if available |
| `ingested_at` | Timestamp row was ingested |

Constraints and indexes:

- Primary key should be `(manifest_id, run_id)`.
- `manifest_id` references `manifest_files.manifest_id`.
- `run_id` references `runs.run_id`.
- Index `run_id` for tracing where a run appeared.
- Index `param_set_id` for parameter-set tracing.

---

### 6.9 Optional `plans` table

Plan details are primarily stored as immutable files in `plans/plan_XXX/`. A lightweight registry table for plan metadata is optional but useful.

Optional columns:

| Column | Meaning |
|---|---|
| `plan_id` | Primary key, for example `plan_007` |
| `plan_path` | Path to plan directory |
| `created_at` | Timestamp created |
| `submitted_at` | Timestamp submitted, if submitted |
| `status` | created, submitted, partially_submitted, cancelled, etc. |
| `selection_summary` | Short text or JSON summary |
| `resource_model_id` | Resource model used, if any |
| `notes` | Optional notes |

This table is not required for v1 if attempts and plan files contain sufficient information, but Codex may implement it if convenient.

---

### 6.10 Registry exports

The SQLite registry should be inspectable. SimMgr should include an export command that writes TSV snapshots, for example:

```text
registry/exports/param_sets.tsv
registry/exports/runs.tsv
registry/exports/attempts.tsv
registry/exports/manifest_files.tsv
registry/exports/manifest_runs.tsv
```

Exports are not the source of truth. They are snapshots for inspection, sharing, or debugging.

Export files should include a timestamp or be overwritten only by explicit command. Either of these patterns is acceptable:

```text
registry/exports/runs.tsv
```

or:

```text
registry/exports/2026-05-13_120000/runs.tsv
```

The timestamped-directory approach is more consistent with the no-silent-overwriting principle.

---

## 7. Commands and Script Responsibilities

The following command names are conceptual. Codex may implement them as subcommands of a single `simmgr` CLI or as separate Python scripts.

---

### 7.1 `simmgr init`

Purpose:

- create a new project simulation directory;
- create default project config from global defaults;
- create default `simulation_spec.yaml`;
- create directory structure;
- create and initialize `registry/simmgr.sqlite`;
- create `state/simmgr_state.json`;
- create `pilot_sets/pilot_001.tsv` with only a `run_id` header.

Inputs:

```text
--project-root
--global-config optional
```

Outputs:

```text
project_config.yaml
simulation_spec.yaml
state/simmgr_state.json
registry/simmgr.sqlite
registry/exports/
pilot_sets/pilot_001.tsv
```

Behavior:

- Create all registry tables and indexes.
- Set the registry schema version.
- Refuse to overwrite an existing initialized project unless explicitly requested.

---

### 7.2 `simmgr build-manifest`

Purpose:

- read `project_config.yaml`;
- read `simulation_spec.yaml`;
- expand simulation sets;
- apply defaults;
- canonicalize `params_json`;
- compute `param_set_id`;
- create `run_id` for each replicate;
- write a new immutable manifest.

Inputs:

```text
--project-config optional if global default exists
```

Outputs:

```text
manifests/manifest_XXX.tsv
```

Behavior:

- Increment manifest counter in `state/simmgr_state.json`.
- Do not modify the SQLite registry.
- Do not execute simulations.
- Do not overwrite prior manifests.

---

### 7.3 `simmgr ingest-manifest`

Purpose:

- read a manifest;
- record the manifest file in `manifest_files`;
- add new parameter sets to `param_sets`;
- add new logical runs to `runs`;
- record all manifest-run memberships in `manifest_runs`, including overlaps with existing runs;
- ignore existing parameter sets and runs for insertion purposes;
- update `last_manifest_id` and timestamps for existing parameter sets/runs that reappear.

Inputs:

```text
--project-config optional
--manifest manifest_XXX optional; default latest
```

Outputs:

```text
updated registry/simmgr.sqlite
```

Behavior:

- Use a single transaction for the whole manifest ingestion.
- Do not create attempts.
- Do not update resources.
- Do not alter old run IDs.
- Do not overwrite results.

---

### 7.4 `simmgr query`

Purpose:

- select logical runs based on run status, attempt status, manifest ID, replicate number, or parameters inside `params_json`.
- default to `status == "pending"` when neither a status nor a where expression is supplied.
- support the virtual status shorthand `not_succeeded` for all non-completed runs; this is query syntax, not a stored run status.
- support the virtual status shorthand `any` to disable status filtering; this is query syntax, not a stored run status.

Example selectors:

```text
status == "pending"
status == "not_succeeded"
status == "any"
status != "succeeded"
status == "failed_oom"
status == "failed_timeout"
replicate == 1
replicate <= 10
params.N >= 100000
params.h2 == 0.2
params.architecture == "polygenic"
first_manifest_id == "manifest_003"
attempt_count == 0
```

Outputs may be:

```text
stdout table
or
plans/selection_*.tsv
or
user-specified TSV
```

Implementation details:

- Query `runs`, `param_sets`, and optionally `attempts` and `manifest_runs` from SQLite.
- Parameter predicates may be implemented by parsing `params_json` in Python after an initial SQL query, or by using SQLite JSON functions if available.
- The query language can be simple in v1. It does not need to support arbitrary Python evaluation if that creates safety or parsing concerns.
- Results should be exportable as TSV for inspection and as input to planning.

---

### 7.5 `simmgr learn-resources`

Purpose:

- read completed attempt data from SQLite;
- read associated parameter sets from SQLite;
- train or update resource prediction models;
- write a new JSON resource model file.

Inputs:

```text
--project-config optional
```

Outputs:

```text
resource_models/resource_model_XXX.json
```

Behavior:

- Use successful attempts to fit regression models.
- Use OOM and timeout failures to inform retry lower bounds, not as ordinary successful observations.
- Store model coefficients and metadata in JSON.
- Increment resource model counter in `state/simmgr_state.json`.
- The SQLite registry may optionally record the latest resource model ID in metadata, but the model file itself remains JSON.

---

### 7.6 `simmgr plan-jobs`

Purpose:

- select runs;
- predict resources;
- round predicted resources into dynamic Slurm buckets;
- group runs sequentially where appropriate;
- organize groups into Slurm arrays when resource allocations match;
- write a complete immutable plan directory;
- print or write sbatch commands.

Inputs may include:

```text
--project-config optional
--where optional query expression
--status optional; supports virtual values any and not_succeeded
--pilot-set pilot_001.tsv optional
--resource-model latest optional
--retry-policy optional
--one-run-per-group optional, recommended for RAM-learning pilots
```

Outputs:

```text
plans/plan_XXX/
  selected_runs.tsv
  resource_predictions.tsv
  groups.tsv
  arrays.tsv
  sbatch_commands.sh
  plan_summary.txt
```

Behavior:

- Do not submit jobs unless a separate explicit option is provided; preferred design is separate submission.
- Do not modify manifests.
- Do not create attempts during planning.
- May optionally register lightweight plan metadata in a `plans` registry table if that table is implemented.
- Do not change run status to `planned` by default. If planned status is used, it must be clear that this does not mean an attempt exists.

---

### 7.7 `simmgr submit-jobs`

Purpose:

- submit a previously created plan to Slurm;
- create attempt records in SQLite;
- record Slurm job IDs;
- update run and attempt registry records.

Inputs:

```text
--project-config optional
--plan plan_XXX
```

Outputs:

```text
updated registry/simmgr.sqlite
updated plans/plan_XXX/submission.tsv optional
```

Behavior:

- Use `sbatch` commands generated by the plan.
- Record Slurm IDs returned by `sbatch`.
- Create attempt IDs at submission time using current attempt counts from SQLite.
- Copy allocated resource fields from the plan into the `attempts` table.
- Update logical runs to `submitted` or similar.
- Do not alter manifest files.
- Use transactions around attempt creation and run-status updates.
- If Slurm submission fails, either do not create attempt rows for that failed submission or create attempts marked `submission_failed`. The chosen behavior must be consistent and documented.

Recommended robust behavior:

1. Submit one Slurm array or job.
2. If `sbatch` succeeds, create/finalize attempts for groups in that submitted array and record the Slurm job ID.
3. If `sbatch` fails, do not create attempts for that array, or mark any pre-created attempts as `submission_failed` in the same transaction.

---

### 7.8 `simmgr run-group`

Purpose:

- executed inside a Slurm job or array task;
- read the group definition for this job or array task;
- sequentially execute logical runs in the group;
- call `run-one` for each attempt;
- write group-level JSONL logs.

Inputs:

```text
--project-config
--plan-id
--group-id or array task index
```

Behavior:

- Continue to the next run if a simulator-level error occurs.
- If Slurm kills the whole job due to OOM or timeout, later cleanup is handled by `collect-status`.
- Checkpoint progress after each run.
- Write group log events to `logs/groups/`.
- Do not write to the SQLite registry from inside the Slurm job in v1.

---

### 7.9 `simmgr run-one`

Purpose:

- wrapper around the project-specific Python simulator;
- prepare metadata;
- call simulator script with standardized arguments;
- append SimMgr-level final attempt event;
- return simulator exit code/status to caller.

The simulator should be called conceptually as:

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

The exact argument names may be implemented by Codex, but the interface should contain these fields.

`run-one` should not write to the SQLite registry from inside a Slurm job in v1. It should write structured JSONL events that `collect-status` later ingests.

---

### 7.10 `simmgr collect-status`

Purpose:

- inspect attempt logs;
- inspect group logs;
- inspect Slurm accounting via `sacct` where available;
- classify attempt outcomes;
- update the `attempts` table;
- update summarized logical-run status in the `runs` table.

Inputs:

```text
--project-config optional
--plan optional
--attempt optional
```

Behavior:

- Prefer explicit SimMgr `attempt_finished` event when present.
- Use simulator terminal event for simulator-level status.
- Use Slurm accounting to detect OOM, timeout, preemption, node failure, or missing terminal events.
- If group died before some runs started, mark those attempts appropriately.
- If a grouped Slurm allocation times out after earlier runs completed, completed runs should remain completed and retain their per-run elapsed time for resource-model assessment.
- If an attempt succeeded but expected log/results are invalid, classify as failed validation if validation is implemented.
- Use transactions when updating attempts and the summarized run status.

---

### 7.11 `simmgr export-registry`

Purpose:

- export selected or all SQLite registry tables to TSV for inspection, debugging, or sharing.

Inputs:

```text
--project-config optional
--output-dir optional
--tables optional
```

Outputs:

```text
registry/exports/<timestamp>/param_sets.tsv
registry/exports/<timestamp>/runs.tsv
registry/exports/<timestamp>/attempts.tsv
registry/exports/<timestamp>/manifest_files.tsv
registry/exports/<timestamp>/manifest_runs.tsv
```

Behavior:

- Do not treat exports as source of truth.
- Do not update the registry except possibly to record export metadata if desired.
- Prefer timestamped export directories to avoid silent overwriting.

---

## 8. Simulator Interface and Attempt Logs

### 8.1 Attempt log path

Each simulator invocation writes to a specific attempt log:

```text
logs/attempts/<attempt_id>.jsonl
```

Example:

```text
logs/attempts/8f3a91c4e2b7_r2_a3.jsonl
```

A flat attempt log directory is acceptable for v1. At very large scale, optional sharding may be added later.

Separate directories should be used for:

```text
logs/attempts/
logs/groups/
logs/slurm/
```

The registry stores the exact `attempt_log_path`, so the log layout can be changed later without changing the identity scheme.

---

### 8.2 First event

SimMgr should write the first event before calling the simulator.

Example:

```json
{"event":"attempt_metadata","attempt_id":"8f3a91c4e2b7_r2_a3","attempt":3,"run_id":"8f3a91c4e2b7_r2","param_set_id":"8f3a91c4e2b7","replicate":2,"params":{"N":100000,"h2":0.2,"num_variants":50000,"simulator_version":1},"seed":812381,"allocated_time_minutes":60,"allocated_ram_gb":16}
```

This event should include:

- `event`;
- `attempt_id`;
- `attempt`;
- `run_id`;
- `param_set_id`;
- `replicate`;
- `params`;
- `seed`;
- allocated resources if known;
- SimMgr version if available;
- timestamp.

---

### 8.3 Simulator events

The simulator may append arbitrary project-specific JSONL events.

Examples:

```json
{"event":"simulator_progress","stage":"population_simulation","elapsed_seconds":44.2}
{"event":"simulator_progress","stage":"phenotype_generation","elapsed_seconds":51.7}
{"event":"result","heritability_hat":0.214,"se":0.031}
{"event":"result_file","kind":"variant_effects","path":"outputs/8f3a91c4e2b7_r2/effects.tsv.gz","format":"tsv.gz"}
```

For large scientific outputs, the simulator should write external files and log pointers to them rather than embedding large data in JSONL.

---

### 8.4 Standard simulator terminal event

If the simulator reaches controlled completion, it must append a standardized terminal event.

Success:

```json
{"event":"simulator_finished","status":"succeeded"}
```

Controlled simulator failure:

```json
{"event":"simulator_finished","status":"failed_simulator_error","error_message":"..."}
```

This is separate from the SimMgr attempt terminal event.

---

### 8.5 Final SimMgr attempt event

After the simulator process exits, `run-one` should append:

```json
{"event":"attempt_finished","attempt_id":"8f3a91c4e2b7_r2_a3","attempt":3,"status":"succeeded","exit_code":0,"elapsed_seconds":83.5}
```

The `attempt_finished` event is the main event that `collect-status` should look for.

If Slurm kills the process due to OOM or timeout, this event may be absent. In that case `collect-status` must use Slurm accounting and group logs.

`run-one` should record per-run elapsed time in `attempt_finished.elapsed_seconds`. Per-run RAM usage should be recorded only when SimMgr can attribute it through Slurm accounting. Project simulators should not guess RAM usage for SimMgr's resource model. In grouped sequential jobs, ordinary Slurm batch MaxRSS is group-level unless each run is represented by an attributable Slurm step, so SimMgr should leave per-run RAM blank rather than pretending group-level RSS belongs to every run.

---

### 8.6 Seeds

The project config contains a project-level seed or the simulation spec may include one if desired. Recommended source:

```yaml
randomness:
  project_seed: 123456
```

If stored in `project_config.yaml`, it is project-management-level configuration. If stored in `simulation_spec.yaml`, it becomes part of simulation identity if included in `params_json`. The preferred behavior is:

- Project seed is stored in `project_config.yaml`.
- The actual per-run seed is deterministically derived from `project_seed + run_id`.
- The seed is logged in the attempt metadata.
- The seed is not part of `params_json` unless the user intentionally wants it to define a different simulation condition.

---

## 9. Resource Modeling

### 9.1 Resource model inputs

Resource modeling uses:

- `params_json` from the SQLite `param_sets` table;
- selected numeric parameters;
- selected categorical parameters;
- observed elapsed time from successful attempts in the `attempts` table;
- observed max RSS from successful attempts in the `attempts` table when the value is Slurm-attributed to the individual run;
- failure information from OOM and timeout attempts in the `attempts` table.

The resource-relevant parameters are specified in `simulation_spec.yaml`.

Example:

```yaml
resource_model:
  numeric_parameters:
    - N
    - num_variants
  categorical_parameters:
    - architecture
  include_log_terms: true
  include_square_terms: true
  include_pairwise_products: true
```

If no resource-relevant parameters are specified, SimMgr may treat all parameters as candidate predictors, but it should warn that this may be inefficient or unstable.

---

### 9.2 Regression framework

Use regression, not random forests or other heavier models.

Fit separate models for:

```text
runtime_seconds
max_rss_gb
```

Prefer log-scale models:

```text
log(runtime_seconds)
log(max_rss_gb)
```

The runtime model can be trained from SimMgr wrapper elapsed times. The memory model should use exact Slurm-attributed per-run RSS when available. It may also use censored memory observations: successful runs without exact RSS provide upper bounds based on allocated RAM, and runs active during Slurm OOM provide lower bounds based on allocated RAM. If grouped sequential jobs only expose group-level MaxRSS, SimMgr should not use that value as exact per-run memory training data.

Candidate features may include:

- numeric parameters;
- log-transformed numeric parameters;
- squared numeric parameters;
- pairwise products;
- categorical indicators.

For v1, failed jobs do not need formal censored regression. Simple behavior is sufficient:

- successful attempts are used as ordinary training observations;
- OOM attempts indicate memory required more than allocated memory;
- timeout attempts indicate runtime required more than allocated time;
- OOM and timeout attempts define retry lower bounds.

---

### 9.3 Resource model storage

Resource models are stored as JSON:

```text
resource_models/resource_model_001.json
resource_models/resource_model_002.json
...
```

Example structure:

```json
{
  "resource_model_id": "resource_model_003",
  "created_at": "2026-05-13T12:00:00",
  "model_type": "log_linear_regression",
  "training_attempt_count": 152,
  "features": {
    "numeric_parameters": ["N", "num_variants"],
    "categorical_parameters": ["architecture"],
    "include_log_terms": true,
    "include_square_terms": true,
    "include_pairwise_products": true
  },
  "runtime_model": {
    "response": "log_runtime_seconds",
    "coefficients": {
      "intercept": 1.92,
      "log_N": 0.87,
      "log_num_variants": 0.31
    },
    "residual_sd": 0.42
  },
  "memory_model": {
    "response": "log_max_rss_gb",
    "coefficients": {
      "intercept": 4.10,
      "log_N": 0.52,
      "log_num_variants": 0.76
    },
    "residual_sd": 0.35
  }
}
```

Do not use pickle for v1.

---

### 9.4 Resource predictions

Resource predictions are not stored in manifests.

Predictions are stored in plan directories:

```text
plans/plan_007/resource_predictions.tsv
```

Required columns:

| Column | Meaning |
|---|---|
| `run_id` | Logical run ID |
| `param_set_id` | Parameter set ID |
| `predicted_time_minutes` | Continuous prediction |
| `predicted_ram_gb` | Continuous prediction, in GB |
| `allocated_time_minutes` | Rounded Slurm time |
| `allocated_ram_gb` | Rounded Slurm memory, in GB |
| `allocated_cpus` | CPUs |
| `resource_model_id` | Model used |
| `prediction_reason` | learned model, fallback, retry after OOM, retry after timeout, etc. |
| `resource_limit_status` | `ok`, `ram_capped`, `time_capped`, or both if planning ceilings were applied |

When attempts are created, allocated resource fields are copied into the SQLite `attempts` table.

After collection, SimMgr may write:

```text
plans/plan_007/resource_assessment.tsv
```

This compares predicted time/RAM to observed per-attempt elapsed time and Slurm-attributed per-run RSS when available.

---

### 9.5 Retry resource logic

For OOM:

```text
new_ram = max(model_prediction, previous_allocated_ram * oom_retry_multiplier)
```

For timeout:

```text
new_time = max(model_prediction, previous_allocated_time * timeout_retry_multiplier)
```

For successful attempts:

```text
allocated_time = observed_elapsed_time * safety_time_multiplier
allocated_ram = observed_max_rss * safety_ram_multiplier
```

Minimums should be enforced:

```text
allocated_time >= min_time_minutes
allocated_ram >= min_ram_gb
```

Maximums should be enforced or flagged:

```text
allocated_time <= max_job_time_minutes
allocated_ram <= cluster/project maximum if specified
```

If a prediction exceeds project maximums, the plan should mark the run as unplannable or require explicit override.

---

## 10. Resource Buckets

### 10.1 Dynamic buckets

SimMgr should dynamically create buckets by rounding predicted resources independently.

Memory ladder:

```text
1G, 2G, 4G, 8G, 16G, then 32G, 48G, 64G, 80G, 96G, 112G, 128G, ...
```

Rule:

- up to 16G: powers of two;
- after 16G: 16G increments.

Time ladder:

```text
5m, 10m, 15m, 30m, 45m, 1h, then 2h, 3h, 4h, ...
```

Rule:

- up to 1 hour: fixed short-job ladder including 45 minutes;
- after 1 hour: 1-hour increments.

CPU should come from project config for v1:

```yaml
slurm:
  cpus_per_task: 1
```

A resource bucket is the tuple:

```text
allocated_time_minutes + allocated_ram_gb + allocated_cpus
```

---

## 11. Job Planning, Groups, and Arrays

### 11.1 Logical hierarchy

```text
logical run
  -> attempt
    -> group
      -> Slurm job or Slurm array task
        -> Slurm array
```

A **group** is a set of attempts/runs that will execute sequentially inside one Slurm allocation.

A **Slurm array** is a set of groups with identical resource allocation.

---

### 11.2 Grouping policy

Runs can be grouped when they have compatible resource needs.

For v1:

- group only runs with the same `allocated_ram_gb`;
- group only runs with the same `allocated_cpus`;
- group runs so that total predicted time plus safety does not exceed `max_job_time_minutes`;
- assign group time based on summed predicted/allocated run times, rounded to the time ladder;
- ensure group memory is the memory bucket required by all runs in the group.

A group corresponds to one Slurm job allocation. If submitted as an array, each array task executes one group.

---

### 11.3 Plan directory

Each plan is immutable:

```text
plans/plan_001/
plans/plan_002/
...
```

Each plan should include:

```text
selected_runs.tsv
resource_predictions.tsv
groups.tsv
arrays.tsv
sbatch_commands.sh
plan_summary.txt
```

---

### 11.4 `selected_runs.tsv`

Required columns:

| Column | Meaning |
|---|---|
| `run_id` | Logical run ID |
| `param_set_id` | Parameter set ID |
| `replicate` | Replicate number |
| `params_json` | Canonical parameters |
| `selection_reason` | Why selected |

---

### 11.5 `groups.tsv`

Required columns:

| Column | Meaning |
|---|---|
| `group_id` | Group ID |
| `run_id` | Run assigned to group |
| `group_order` | Order within group |
| `allocated_time_minutes` | Group time allocation |
| `allocated_ram_gb` | Group memory allocation, in GB |
| `allocated_cpus` | Group CPUs |
| `predicted_run_time_minutes` | Predicted time for individual run |
| `attempt_id` | Blank until submission if attempts are created then |

There may be multiple rows per group, one per run.

After submission, SimMgr may either update `groups.tsv` with assigned `attempt_id`s or write a separate `submission.tsv`. The SQLite `attempts` table is the source of truth for attempt IDs.

---

### 11.6 `arrays.tsv`

Required columns:

| Column | Meaning |
|---|---|
| `array_id` | SimMgr array ID within plan |
| `allocated_time_minutes` | Array time |
| `allocated_ram_gb` | Array memory, in GB |
| `allocated_cpus` | Array CPUs |
| `group_id` | Group assigned to this array |
| `array_task_index` | Task index within array |
| `slurm_job_id` | Filled after submission if known |

If the number of groups with identical allocation exceeds the cluster array limit, split into multiple arrays.

Example with max array size 1000:

```text
2400 matching groups -> 3 arrays:
array 1: task 1-1000
array 2: task 1-1000
array 3: task 1-400
```

---

### 11.7 `sbatch_commands.sh`

Contains commands for each array/resource bucket.

Example conceptual command:

```bash
sbatch --partition=short --account=my_account --cpus-per-task=1 --mem=16G --time=01:00:00 --array=1-500 simmgr_run_group.sh --project-config /path/project_config.yaml --plan-id plan_007 --array-id array_001
```

Codex can decide exact wrapper structure, but `sbatch_commands.sh` should be human-inspectable and runnable.

---

### 11.8 Planning versus submission

Planning and submission must be separate.

`plan-jobs` writes the plan and sbatch commands.

`submit-jobs` submits the plan.

The user should be able to inspect:

```text
plan_summary.txt
sbatch_commands.sh
```

before submitting.

---

## 12. Pilot Learning

### 12.1 Pilot set files

Pilot sets are user-specified lists of run IDs.

Example:

```text
pilot_sets/pilot_001.tsv
```

Contents:

```text
run_id
8f3a91c4e2b7_r1
9a2210d4bc18_r1
ad420bb91e55_r1
```

The initial project contains a blank pilot file with only the header.

---

### 12.2 Pilot workflow

Suggested workflow:

```text
1. build manifest
2. ingest manifest
3. user fills pilot_sets/pilot_001.tsv with selected run IDs
4. plan jobs using the pilot set with generous resources
5. submit jobs
6. collect status
7. learn resources
8. plan main production runs
```

Pilot resource allocations may use conservative defaults or user-specified generous settings.

---

## 13. Failure Classification

### 13.1 Attempt failure statuses

Core failure categories:

```text
succeeded
failed_oom
failed_timeout
failed_node
failed_simulator_error
failed_validation
failed_unknown
not_started_due_to_group_failure
submission_failed
```

---

### 13.2 Classification logic

Suggested precedence:

1. If Slurm reports OOM, classify as `failed_oom`.
2. If Slurm reports timeout, classify as `failed_timeout`.
3. If Slurm reports node failure/preemption, classify as `failed_node`.
4. If `attempt_finished` exists and reports success, classify as `succeeded`.
5. If simulator terminal event reports controlled simulator error, classify as `failed_simulator_error`.
6. If process exit code is nonzero, classify as `failed_simulator_error` or `failed_unknown` depending on available evidence.
7. If expected terminal event is missing and Slurm state is ambiguous, classify as `failed_unknown`.
8. If validation exists and fails, classify as `failed_validation`.

This precedence can be adjusted, but OOM and timeout should be detected reliably because they directly inform resource learning.

---

### 13.3 Logical-run status update

After attempts are updated, the `runs` table should summarize logical-run status:

- If any attempt succeeded, run status is `succeeded`.
- Else if latest attempt failed OOM, run status is `failed_oom`.
- Else if latest attempt failed timeout, run status is `failed_timeout`.
- Else if latest attempt failed simulator error, run status is `failed_simulator_error`.
- Else if no attempts exist, run status is `pending`.

Update:

```text
attempt_count
best_attempt_id
status
updated_at
```

These updates should occur in the same transaction as related attempt updates when possible.

---

## 14. Query System

SimMgr should support selecting runs by both simulation parameters and job-related metadata.

Example query concepts:

```text
status == "pending"
status != "succeeded"
status == "failed_oom"
status == "failed_timeout"
replicate == 1
replicate <= 10
params.N >= 100000
params.h2 == 0.2
params.architecture == "polygenic"
first_manifest_id == "manifest_003"
attempt_count == 0
```

Implementation details:

- Query the SQLite `runs` table.
- Join with `param_sets` to access `params_json`.
- Optionally join with `attempts` to access latest or best attempt fields.
- Optionally join with `manifest_runs` to filter on manifest membership.
- Parse `params_json` in Python if SQLite JSON functions are unavailable or if a safer custom query evaluator is desired.
- Evaluate filters safely.
- Return results to stdout or write a TSV selection file.

The query language can be simple in v1. It does not need to support arbitrary Python evaluation if that creates safety or parsing concerns.

---

## 15. Standard Workflows

### 15.1 Initialize a project

```text
simmgr init --project-root /path/to/project_sims
```

User edits:

```text
project_config.yaml
simulation_spec.yaml
```

---

### 15.2 Build and ingest simulations

```text
simmgr build-manifest --project-config project_config.yaml
simmgr ingest-manifest --project-config project_config.yaml --manifest latest
```

Result:

- immutable manifest written;
- new unique parameter sets added to SQLite;
- new unique logical runs added to SQLite;
- manifest membership recorded in SQLite;
- previous runs untouched.

---

### 15.3 Run pilot jobs

User fills:

```text
pilot_sets/pilot_001.tsv
```

Then:

```text
simmgr plan-jobs --project-config project_config.yaml --pilot-set pilot_001.tsv --generous-resources --one-run-per-group
simmgr submit-jobs --project-config project_config.yaml --plan plan_001
simmgr collect-status --project-config project_config.yaml --plan plan_001
simmgr learn-resources --project-config project_config.yaml
```

---

### 15.4 Run production jobs

```text
simmgr plan-jobs --project-config project_config.yaml --where 'status == "pending"'
simmgr submit-jobs --project-config project_config.yaml --plan plan_002
simmgr collect-status --project-config project_config.yaml --plan plan_002
simmgr learn-resources --project-config project_config.yaml
```

---

### 15.5 Retry failures

OOM retries:

```text
simmgr plan-jobs --project-config project_config.yaml --where 'status == "failed_oom"' --retry-policy oom
simmgr submit-jobs --project-config project_config.yaml --plan plan_003
```

Timeout retries:

```text
simmgr plan-jobs --project-config project_config.yaml --where 'status == "failed_timeout"' --retry-policy timeout
simmgr submit-jobs --project-config project_config.yaml --plan plan_004
```

Retrying creates new attempts, not new manifests.

---

### 15.6 Add more simulations

User edits `simulation_spec.yaml`.

Then:

```text
simmgr build-manifest
simmgr ingest-manifest
```

Only new unique logical runs are added to the registry. Existing runs remain unchanged except for updated manifest-membership metadata if they reappear.

---

### 15.7 Change simulator behavior

User increments:

```yaml
default_parameters:
  simulator_version: 2
```

inside `simulation_spec.yaml`.

Then:

```text
simmgr build-manifest
simmgr ingest-manifest
```

Because `simulator_version` is inside `params_json`, all affected `param_set_id`s and `run_id`s change.

Old runs remain untouched.

---

### 15.8 Export registry for inspection

```text
simmgr export-registry --project-config project_config.yaml
```

This writes human-readable TSV snapshots of the SQLite registry to `registry/exports/`.

---

## 16. Important Edge Cases

### 16.1 Manifest contains duplicate rows

`build-manifest` should either:

- deduplicate exact duplicate logical runs within the manifest; or
- write them once and warn.

Do not create duplicate registry rows.

---

### 16.2 Manifest overlaps previous manifest

Expected behavior.

`ingest-manifest` should skip inserting existing `param_set_id`s and `run_id`s, but should record manifest membership in `manifest_runs` and update `last_manifest_id` metadata.

---

### 16.3 Plan created but not submitted

No attempts should be created if the preferred design is used.

The plan remains inspectable and can be discarded.

---

### 16.4 Attempt created but Slurm submission fails

If `submit-jobs` creates attempts before `sbatch` succeeds, it must handle failed submission carefully.

Preferred behavior:

1. Submit array/job.
2. If `sbatch` succeeds, create or finalize attempt rows with Slurm job ID.
3. If `sbatch` fails, do not create attempts, or mark them as `submission_failed`.

Codex should choose a robust implementation and apply it consistently.

---

### 16.5 Group killed by Slurm before all runs start

Use group logs and attempt logs to infer:

- active run at failure time;
- runs completed before failure;
- runs not yet started.

Mark unstarted runs as:

```text
not_started_due_to_group_failure
```

or leave them pending if no attempt was actually created. The cleaner approach depends on whether attempts are created at submission time for all planned group members. Since attempts are likely created at submission time, use `not_started_due_to_group_failure`.

---

### 16.6 Simulator exits zero but does not write terminal event

Classify as suspicious.

Possible status:

```text
failed_validation
```

or:

```text
failed_unknown
```

SimMgr should expect a standardized `simulator_finished` event.

---

### 16.7 Slurm says completed but attempt log missing

Classify as `failed_unknown` or `failed_validation`.

This indicates a wrapper, path, or filesystem problem.

---

### 16.8 Very large flat log directory

For v1, all attempt logs can live in:

```text
logs/attempts/
```

If the number of attempts later becomes very large, optional sharding can be added without changing the registry because the registry stores explicit `attempt_log_path`.

---

### 16.9 SQLite on shared cluster filesystems

SQLite is reliable for the registry, but cluster filesystems vary.

For v1:

- Avoid writing to SQLite from Slurm array tasks or simulator jobs.
- Keep registry writes in user-invoked SimMgr commands such as `ingest-manifest`, `submit-jobs`, and `collect-status`.
- Avoid running multiple registry-writing SimMgr commands simultaneously.
- If a command detects that the registry is locked, it should fail clearly or respect a configured busy timeout.
- Consider warning users not to place the project registry on a filesystem where SQLite locking is known to be unreliable.

---

## 17. Out of Scope for v1

Do not implement the following in v1 unless explicitly requested later:

- Parquet output;
- pickle resource models;
- random forest or gradient boosting resource models;
- automatic hash-equivalence rules for new parameters;
- support for R simulators;
- support for non-Slurm schedulers;
- complex project-specific result summarization;
- automatic pilot set selection, except possibly as a helper later;
- direct registry writes from Slurm worker jobs.

SQLite registry support is **in scope** for v1.

---

## 18. Summary of Agreed Core Design

SimMgr v1 should implement:

```text
Standalone reusable Python repository
Text-first project storage outside the registry
SQLite registry at registry/simmgr.sqlite
YAML configs and simulation specs
TSV immutable manifests
TSV plan files and optional TSV registry exports
JSONL per-attempt logs
JSON resource models
Immutable manifests
Transactional mutable SQLite registry
Explicit parameter canonicalization
param_set_id = hash(params_json)
run_id = <param_set_id>_r<replicate>
attempt_id = <run_id>_a<attempt>
One logical run can have many attempts
Retries do not create new manifests
Resource predictions stored in plan directories
Actual allocations stored in attempts table
Regression-based resource learning
Dynamic time/RAM buckets
Groups for sequential short runs
Slurm arrays for groups with identical allocations
Pilot sets as one-column run_id TSV files
Python-only simulator interface for v1
Standard simulator_finished and attempt_finished JSONL events
Project-specific summarization outside core SimMgr
No SQLite writes from Slurm worker jobs in v1
```

This specification should be treated as the implementation guide for Codex when creating the initial SimMgr scripts.
