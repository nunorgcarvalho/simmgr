from __future__ import annotations

from pathlib import Path

from .config import configured_path, load_project_config, registry_path
from .registry import connect, rows
from .time_utils import safe_timestamp
from .tsv import write_tsv

DEFAULT_TABLES = ["param_sets", "runs", "attempts", "manifest_files", "manifest_runs", "plans", "registry_metadata"]


def export_registry(
    project_config: str | Path | None = None,
    output_dir: str | Path | None = None,
    tables: list[str] | None = None,
    global_config: str | Path | None = None,
) -> Path:
    config = load_project_config(project_config, global_config)
    out = Path(output_dir) if output_dir else configured_path(config, "registry_dir") / "exports" / safe_timestamp()
    out.mkdir(parents=True, exist_ok=False)
    with connect(registry_path(config)) as conn:
        for table in tables or DEFAULT_TABLES:
            data = rows(conn, f"SELECT * FROM {table}")
            columns = list(data[0].keys()) if data else _columns(conn, table)
            write_tsv(out / f"{table}.tsv", data, columns)
    return out


def _columns(conn, table: str) -> list[str]:
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]

