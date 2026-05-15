from __future__ import annotations

SCHEMA_VERSION = 2


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS registry_metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS manifest_files (
  manifest_id TEXT PRIMARY KEY,
  manifest_path TEXT NOT NULL,
  created_at TEXT,
  ingested_at TEXT NOT NULL,
  spec_path TEXT,
  spec_hash TEXT,
  simmgr_version TEXT,
  row_count INTEGER NOT NULL,
  new_param_set_count INTEGER NOT NULL,
  new_run_count INTEGER NOT NULL,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS param_sets (
  param_set_id TEXT PRIMARY KEY,
  params_json TEXT NOT NULL UNIQUE,
  first_manifest_id TEXT,
  last_manifest_id TEXT,
  first_seen_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  notes TEXT,
  FOREIGN KEY(first_manifest_id) REFERENCES manifest_files(manifest_id),
  FOREIGN KEY(last_manifest_id) REFERENCES manifest_files(manifest_id)
);

CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  param_set_id TEXT NOT NULL,
  replicate INTEGER NOT NULL,
  status TEXT NOT NULL,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  best_attempt_id TEXT,
  first_manifest_id TEXT,
  last_manifest_id TEXT,
  first_seen_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  notes TEXT,
  UNIQUE(param_set_id, replicate),
  FOREIGN KEY(param_set_id) REFERENCES param_sets(param_set_id),
  FOREIGN KEY(first_manifest_id) REFERENCES manifest_files(manifest_id),
  FOREIGN KEY(last_manifest_id) REFERENCES manifest_files(manifest_id)
);

CREATE TABLE IF NOT EXISTS attempts (
  attempt_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  param_set_id TEXT NOT NULL,
  replicate INTEGER NOT NULL,
  attempt INTEGER NOT NULL,
  status TEXT NOT NULL,
  plan_id TEXT,
  group_id TEXT,
  array_id TEXT,
  slurm_job_id TEXT,
  slurm_array_task_id TEXT,
  allocated_time_minutes INTEGER,
  allocated_ram_gb REAL,
  allocated_cpus INTEGER,
  attempt_log_path TEXT,
  created_at TEXT NOT NULL,
  submitted_at TEXT,
  started_at TEXT,
  ended_at TEXT,
  elapsed_seconds REAL,
  max_rss_gb REAL,
  max_rss_source TEXT,
  exit_code INTEGER,
  exit_reason TEXT,
  updated_at TEXT NOT NULL,
  UNIQUE(run_id, attempt),
  FOREIGN KEY(run_id) REFERENCES runs(run_id),
  FOREIGN KEY(param_set_id) REFERENCES param_sets(param_set_id)
);

CREATE TABLE IF NOT EXISTS manifest_runs (
  manifest_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  param_set_id TEXT NOT NULL,
  replicate INTEGER NOT NULL,
  simulation_set_name TEXT NOT NULL,
  params_json TEXT NOT NULL,
  created_at TEXT,
  ingested_at TEXT NOT NULL,
  PRIMARY KEY(manifest_id, run_id),
  FOREIGN KEY(manifest_id) REFERENCES manifest_files(manifest_id),
  FOREIGN KEY(run_id) REFERENCES runs(run_id),
  FOREIGN KEY(param_set_id) REFERENCES param_sets(param_set_id)
);

CREATE TABLE IF NOT EXISTS plans (
  plan_id TEXT PRIMARY KEY,
  plan_path TEXT NOT NULL,
  created_at TEXT NOT NULL,
  submitted_at TEXT,
  status TEXT NOT NULL,
  selection_summary TEXT,
  resource_model_id TEXT,
  notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_first_manifest ON runs(first_manifest_id);
CREATE INDEX IF NOT EXISTS idx_runs_last_manifest ON runs(last_manifest_id);
CREATE INDEX IF NOT EXISTS idx_attempts_run_id ON attempts(run_id);
CREATE INDEX IF NOT EXISTS idx_attempts_status ON attempts(status);
CREATE INDEX IF NOT EXISTS idx_attempts_plan_id ON attempts(plan_id);
CREATE INDEX IF NOT EXISTS idx_attempts_slurm_job_id ON attempts(slurm_job_id);
CREATE INDEX IF NOT EXISTS idx_manifest_runs_run_id ON manifest_runs(run_id);
CREATE INDEX IF NOT EXISTS idx_manifest_runs_param_set_id ON manifest_runs(param_set_id);
"""
