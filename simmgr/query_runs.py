from __future__ import annotations

import operator
import re
from pathlib import Path
from typing import Any

from .canonicalize import parse_params_json
from .config import load_project_config, registry_path
from .registry import connect, rows
from .tsv import write_tsv

OPS = {
    "==": operator.eq,
    "!=": operator.ne,
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
}

QUERY_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)\s*(==|!=|>=|<=|>|<)\s*(.+?)\s*$")
NON_COMPLETED_STATUSES = {
    "pending",
    "planned",
    "submitted",
    "running",
    "failed_oom",
    "failed_timeout",
    "failed_node",
    "failed_simulator_error",
    "failed_validation",
    "failed_unknown",
    "not_started_due_to_group_failure",
    "submission_failed",
}


def query_runs(
    project_config: str | Path | None = None,
    where: str | None = None,
    status: str | None = None,
    output: str | Path | None = None,
    global_config: str | Path | None = None,
) -> list[dict[str, Any]]:
    config = load_project_config(project_config, global_config)
    with connect(registry_path(config)) as conn:
        data = rows(
            conn,
            """
            SELECT r.run_id, r.param_set_id, r.replicate, r.status, r.attempt_count,
                   r.best_attempt_id, r.first_manifest_id, r.last_manifest_id, p.params_json
            FROM runs r JOIN param_sets p USING(param_set_id)
            ORDER BY r.run_id
            """,
        )
    if status is None and where is None:
        status = "pending"
    if status:
        data = [r for r in data if status_matches(r["status"], status)]
    if where:
        data = [r for r in data if _matches(r, where)]
    if output:
        write_tsv(output, data, list(data[0].keys()) if data else ["run_id"])
    return data


def status_matches(actual: str, requested: str) -> bool:
    if requested == "any":
        return True
    if requested == "not_succeeded":
        return actual in NON_COMPLETED_STATUSES and actual != "succeeded"
    return actual == requested


def _matches(row: dict[str, Any], expr: str) -> bool:
    match = QUERY_RE.match(expr)
    if not match:
        raise ValueError(f"Unsupported query expression: {expr}")
    key, op, raw_value = match.groups()
    actual = _lookup(row, key)
    expected = _coerce(raw_value, actual)
    if key == "status" and op in {"==", "!="} and expected in {"any", "not_succeeded"}:
        matched = status_matches(str(actual), str(expected))
        return matched if op == "==" else not matched
    return bool(OPS[op](actual, expected))


def _lookup(row: dict[str, Any], key: str) -> Any:
    if key.startswith("params."):
        params = parse_params_json(row["params_json"])
        return params.get(key.split(".", 1)[1])
    return row.get(key)


def _coerce(text: str, actual: Any) -> Any:
    text = text.strip()
    if text[:1] in {"'", '"'} and text[-1:] == text[0]:
        return text[1:-1]
    if isinstance(actual, int):
        return int(text)
    if isinstance(actual, float):
        return float(text)
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        return text
