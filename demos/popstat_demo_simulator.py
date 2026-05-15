#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import time
import traceback
from pathlib import Path


def append(path: Path, event: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--params-json", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--param-set-id", required=True)
    parser.add_argument("--replicate", required=True, type=int)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--attempt", required=True, type=int)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--log-path", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    params = json.loads(args.params_json)
    log_path = Path(args.log_path)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    start = time.time()
    try:
        import numpy as np
        import popstatgensim as psgs

        rng = np.random.default_rng(args.seed)
        n = int(params.get("N", 100))
        m = int(params.get("num_variants", 50))
        h2 = float(params.get("h2", 0.5))
        append(log_path, {"event": "simulator_progress", "stage": "population", "N": n, "M": m})
        pop = psgs.Population(n, m)
        pop.simulate_generations()
        effects = psgs.traits.generate_genetic_effects(
            var_A=h2,
            var_A_par=max(0.01, h2 / 4),
            r=0.1,
            M=m,
            M_causal=max(1, m // 2),
            force_var=True,
            G=pop.G,
            G_par=pop.get_Gpar(),
        )
        pop.add_trait(name="y1", effects={"A": effects["A"], "A_par": effects["A_par"]}, var_Eps=max(0.01, 1 - h2))
        y = pop.traits["y1"].y
        result = {
            "run_id": args.run_id,
            "attempt_id": args.attempt_id,
            "mean_y": float(np.mean(y)),
            "var_y": float(np.var(y)),
            "noise_check": float(rng.normal()),
        }
        result_path = output_dir / "summary.json"
        result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        append(log_path, {"event": "result", **result})
        append(log_path, {"event": "result_file", "kind": "summary", "path": str(result_path), "format": "json"})
        append(
            log_path,
            {
                "event": "simulator_finished",
                "status": "succeeded",
                "elapsed_seconds": time.time() - start,
            },
        )
        return 0
    except Exception as exc:
        append(
            log_path,
            {
                "event": "simulator_finished",
                "status": "failed_simulator_error",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "error_repr": repr(exc),
                "traceback": traceback.format_exc(),
            },
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
