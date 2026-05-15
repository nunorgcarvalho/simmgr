from __future__ import annotations

import argparse
import sys

from .build_manifest import build_manifest
from .collect_status import collect_status
from .export_registry import export_registry
from .ingest_manifest import ingest_manifest
from .init_project import init_project
from .plan_jobs import plan_jobs
from .query_runs import query_runs, should_print_status_summary, status_summary_text
from .resources import learn_resources
from .run_group import run_group
from .run_one import run_one
from .submit_jobs import submit_jobs
from .suggest_pilot import suggest_pilot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="simmgr")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.add_argument("--project-root")
    init.add_argument("--global-config")
    init.add_argument("--force", action="store_true")

    build = sub.add_parser("build-manifest")
    _project_args(build)

    ingest = sub.add_parser("ingest-manifest")
    _project_args(ingest)
    ingest.add_argument("--manifest", default="latest")

    query = sub.add_parser("query")
    _project_args(query)
    query.add_argument("--where")
    query.add_argument("--status")
    query.add_argument("--output")

    pilot = sub.add_parser("suggest-pilot")
    _project_args(pilot)
    pilot.add_argument("--n-runs", type=int, default=10)
    pilot.add_argument("--output")
    pilot.add_argument("--seed", type=int)

    learn = sub.add_parser("learn-resources")
    _project_args(learn)

    plan = sub.add_parser("plan-jobs")
    _project_args(plan)
    plan.add_argument("--where")
    plan.add_argument("--status")
    plan.add_argument("--pilot-set")
    plan.add_argument("--resource-model", default=None)
    plan.add_argument("--retry-policy", choices=["oom", "timeout"])
    plan.add_argument("--generous-resources", action="store_true")
    plan.add_argument("--one-run-per-group", action="store_true")

    submit = sub.add_parser("submit-jobs")
    _project_args(submit)
    submit.add_argument("--plan", default="latest")
    submit.add_argument("--dry-run", action="store_true")

    group = sub.add_parser("run-group")
    group.add_argument("--project-config", required=True)
    group.add_argument("--global-config")
    group.add_argument("--plan-id", required=True)
    group.add_argument("--group-id")
    group.add_argument("--array-id")
    group.add_argument("--array-task-index", type=int)

    one = sub.add_parser("run-one")
    one.add_argument("--project-config", required=True)
    one.add_argument("--global-config")
    one.add_argument("--attempt-id", required=True)

    collect = sub.add_parser("collect-status")
    _project_args(collect)
    collect.add_argument("--plan")
    collect.add_argument("--attempt")

    export = sub.add_parser("export-registry")
    _project_args(export)
    export.add_argument("--output-dir")
    export.add_argument("--tables", nargs="*")

    args = parser.parse_args(argv)
    if args.command == "init":
        root = init_project(args.project_root, args.global_config, args.force)
        print(root)
    elif args.command == "build-manifest":
        print(build_manifest(args.project_config, args.global_config))
    elif args.command == "ingest-manifest":
        print(ingest_manifest(args.project_config, args.manifest, args.global_config))
    elif args.command == "query":
        data = query_runs(args.project_config, args.where, args.status, args.output, args.global_config)
        if should_print_status_summary(args.status):
            print(status_summary_text(data, "query results"))
        _print_rows(data)
    elif args.command == "suggest-pilot":
        print(suggest_pilot(args.project_config, args.n_runs, args.output, args.seed, args.global_config))
    elif args.command == "learn-resources":
        print(learn_resources(args.project_config, args.global_config))
    elif args.command == "plan-jobs":
        print(
            plan_jobs(
                args.project_config,
                args.where,
                args.status,
                args.pilot_set,
                args.resource_model,
                args.retry_policy,
                args.generous_resources,
                args.one_run_per_group,
                args.global_config,
            )
        )
    elif args.command == "submit-jobs":
        out = submit_jobs(args.project_config, args.plan, args.dry_run, args.global_config)
        if out:
            print(out)
    elif args.command == "run-group":
        return run_group(args.project_config, args.plan_id, args.group_id, args.array_id, args.array_task_index, args.global_config)
    elif args.command == "run-one":
        return run_one(args.project_config, args.attempt_id, args.global_config)
    elif args.command == "collect-status":
        print(collect_status(args.project_config, args.plan, args.attempt, args.global_config))
    elif args.command == "export-registry":
        print(export_registry(args.project_config, args.output_dir, args.tables, args.global_config))
    return 0


def _project_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-config")
    parser.add_argument("--global-config")


def _print_rows(rows: list[dict]) -> None:
    if not rows:
        print("No rows")
        return
    columns = list(rows[0].keys())
    print("\t".join(columns))
    for row in rows:
        print("\t".join(str(row.get(c, "")) for c in columns))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
