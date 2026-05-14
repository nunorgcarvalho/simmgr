from __future__ import annotations

from pathlib import Path

from .config import configured_path, load_project_config, registry_path
from .registry import connect, transaction, update_metadata
from .time_utils import utc_now
from .tsv import read_tsv


def resolve_manifest(config: dict, manifest: str | Path | None) -> Path:
    manifests_dir = configured_path(config, "manifests_dir")
    if manifest in (None, "latest"):
        manifests = sorted(manifests_dir.glob("manifest_*.tsv"))
        if not manifests:
            raise SystemExit("No manifests found")
        return manifests[-1]
    path = Path(manifest)
    if not path.is_absolute():
        candidate = manifests_dir / path
        if candidate.exists():
            return candidate
        if not path.name.endswith(".tsv"):
            candidate = manifests_dir / f"{path.name}.tsv"
            if candidate.exists():
                return candidate
    return path.expanduser().resolve()


def ingest_manifest(
    project_config: str | Path | None = None,
    manifest: str | Path | None = None,
    global_config: str | Path | None = None,
) -> dict[str, int | str]:
    config = load_project_config(project_config, global_config)
    manifest_path = resolve_manifest(config, manifest)
    rows = read_tsv(manifest_path)
    if not rows:
        raise SystemExit(f"Manifest has no rows: {manifest_path}")
    manifest_id = rows[0]["manifest_id"]
    now = utc_now()
    new_params = 0
    new_runs = 0
    with connect(registry_path(config)) as conn, transaction(conn):
        first = rows[0]
        conn.execute(
            """
            INSERT INTO manifest_files(
              manifest_id, manifest_path, created_at, ingested_at, spec_path, spec_hash,
              simmgr_version, row_count, new_param_set_count, new_run_count, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
            ON CONFLICT(manifest_id) DO UPDATE SET
              manifest_path = excluded.manifest_path,
              ingested_at = excluded.ingested_at,
              row_count = excluded.row_count
            """,
            (
                manifest_id,
                str(manifest_path),
                first.get("created_at"),
                now,
                first.get("spec_path"),
                first.get("spec_hash"),
                first.get("simmgr_version"),
                len(rows),
                first.get("notes", ""),
            ),
        )
        for item in rows:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO param_sets(
                  param_set_id, params_json, first_manifest_id, last_manifest_id,
                  first_seen_at, updated_at, notes
                ) VALUES (?, ?, ?, ?, ?, ?, '')
                """,
                (item["param_set_id"], item["params_json"], manifest_id, manifest_id, now, now),
            )
            new_params += cur.rowcount
            conn.execute(
                "UPDATE param_sets SET last_manifest_id = ?, updated_at = ? WHERE param_set_id = ?",
                (manifest_id, now, item["param_set_id"]),
            )
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO runs(
                  run_id, param_set_id, replicate, status, attempt_count, best_attempt_id,
                  first_manifest_id, last_manifest_id, first_seen_at, updated_at, notes
                ) VALUES (?, ?, ?, 'pending', 0, NULL, ?, ?, ?, ?, '')
                """,
                (item["run_id"], item["param_set_id"], int(item["replicate"]), manifest_id, manifest_id, now, now),
            )
            new_runs += cur.rowcount
            conn.execute(
                "UPDATE runs SET last_manifest_id = ?, updated_at = ? WHERE run_id = ?",
                (manifest_id, now, item["run_id"]),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO manifest_runs(
                  manifest_id, run_id, param_set_id, replicate, simulation_set_name,
                  params_json, created_at, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    manifest_id,
                    item["run_id"],
                    item["param_set_id"],
                    int(item["replicate"]),
                    item["simulation_set_name"],
                    item["params_json"],
                    item.get("created_at"),
                    now,
                ),
            )
        conn.execute(
            "UPDATE manifest_files SET new_param_set_count = ?, new_run_count = ? WHERE manifest_id = ?",
            (new_params, new_runs, manifest_id),
        )
        update_metadata(conn)
    return {"manifest_id": manifest_id, "row_count": len(rows), "new_param_set_count": new_params, "new_run_count": new_runs}

