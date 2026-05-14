from __future__ import annotations

import hashlib


def stable_hash(text: str, length: int = 16) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def param_set_id(params_json: str) -> str:
    return stable_hash(params_json)


def run_id(param_set_id: str, replicate: int) -> str:
    return f"{param_set_id}_r{replicate}"


def attempt_id(run_id: str, attempt: int) -> str:
    return f"{run_id}_a{attempt}"


def deterministic_seed(project_seed: int, run_id_value: str) -> int:
    digest = hashlib.sha256(f"{project_seed}:{run_id_value}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16)

