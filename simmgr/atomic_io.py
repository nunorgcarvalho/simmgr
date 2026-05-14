from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def atomic_write_text(path: str | Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_json(path: str | Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")

