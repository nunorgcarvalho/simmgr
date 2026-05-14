from __future__ import annotations

import json
from pathlib import Path

from .atomic_io import atomic_write_json


DEFAULT_STATE = {
    "last_manifest_number": 0,
    "last_plan_number": 0,
    "last_resource_model_number": 0,
}


def load_state(path: str | Path) -> dict[str, int]:
    path = Path(path)
    if not path.exists():
        return dict(DEFAULT_STATE)
    data = json.loads(path.read_text(encoding="utf-8"))
    return {**DEFAULT_STATE, **data}


def save_state(path: str | Path, state: dict[str, int]) -> None:
    atomic_write_json(path, state)


def next_number(path: str | Path, key: str) -> int:
    state = load_state(path)
    state[key] = int(state.get(key, 0)) + 1
    save_state(path, state)
    return state[key]

