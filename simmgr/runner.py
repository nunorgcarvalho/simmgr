from __future__ import annotations

import shlex
from pathlib import Path


def simmgr_shell_command(config: dict, *args: str) -> str:
    python = config.get("simmgr", {}).get("python_executable") or config.get("simulator", {}).get("python_executable") or "python"
    repo_root = Path(__file__).resolve().parents[1]
    quoted_args = " ".join(shlex.quote(str(arg)) for arg in args)
    return f"PYTHONPATH={shlex.quote(str(repo_root))}:$PYTHONPATH {shlex.quote(str(python))} -m simmgr.cli {quoted_args}"

