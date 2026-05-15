from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
from shiny import App, reactive, render, ui

from . import api
from .dashboard_data import (
    PLAN_FILES,
    attempt_rows,
    dashboard_command_rows,
    list_log_files,
    load_config,
    manifest_preview,
    manifest_rows,
    overview,
    plan_file_text,
    plan_rows,
    pretty_jsonl,
    read_text_file,
    resource_assessment_rows,
    resource_model_detail,
    resource_model_rows,
    run_dashboard_command,
    run_rows,
)


def create_app(project_config: str | Path | None = None, global_config: str | Path | None = None) -> App:
    config = load_config(project_config, global_config)
    title = f"SimMgr Dashboard: {config.get('project_name', 'project')}"
    app_ui = ui.page_navbar(
        _overview_page(),
        _runs_page(),
        _attempts_page(),
        _manifests_page(),
        _plans_page(),
        _resources_page(),
        _logs_page(),
        _commands_page(),
        title=title,
        id="page",
        selected="Overview",
        header=ui.tags.style(_CSS),
        fillable=True,
    )

    def server(input, output, session):
        refresh_tick = reactive.Value(0)
        last_command = reactive.Value("No dashboard commands have run yet.")

        def cfg() -> dict[str, Any]:
            return config

        def bump_refresh() -> None:
            refresh_tick.set(refresh_tick.get() + 1)

        def touch() -> int:
            return refresh_tick.get()

        @reactive.effect
        @reactive.event(input.refresh)
        def _refresh() -> None:
            bump_refresh()

        @render.ui
        def project_header():
            touch()
            info = overview(cfg())
            return ui.div(
                {"class": "hero"},
                ui.h2(info["project_name"]),
                ui.p(info["project_root"]),
                ui.p(ui.tags.code(info["project_config"])),
                ui.input_action_button("refresh", "Refresh dashboard", class_="btn-primary"),
            )

        @render.ui
        def overview_cards():
            touch()
            info = overview(cfg())
            return ui.div(
                {"class": "value-grid"},
                _value("Runs", info["total_runs"], "Total logical runs"),
                _value("Attempts", info["total_attempts"], "Execution attempts"),
                _value("Succeeded", info["succeeded_runs"], "Completed logical runs"),
                _value("Active", info["active_runs"], "Planned, submitted, or running"),
                _value("Failed OOM", info["failed_oom_runs"], "Memory failures"),
                _value("Simulator Errors", info["failed_simulator_error_runs"], "Simulator-level failures"),
                _value("Latest Plan", info["latest_plan_id"] or "none", "Newest plan directory"),
                _value("Latest Model", info["latest_resource_model_id"] or "none", "Newest resource model"),
            )

        @render.plot
        def run_status_plot():
            touch()
            return _bar_plot(overview(cfg())["run_status_counts"], "Logical runs by status", "Runs")

        @render.plot
        def attempt_status_plot():
            touch()
            return _bar_plot(overview(cfg())["attempt_status_counts"], "Attempts by status", "Attempts")

        @render.data_frame
        def runs_table():
            touch()
            data = _filter_runs(
                run_rows(cfg()),
                status=input.run_status(),
                search=input.run_search(),
                manifest=input.run_manifest(),
                replicate_min=input.run_replicate_min(),
                replicate_max=input.run_replicate_max(),
            )
            return render.DataGrid(_df(data), filters=True, selection_mode="rows", height="620px")

        @render.ui
        def run_manifest_filter():
            touch()
            choices = ["any"] + sorted({str(row.get("first_manifest_id", "")) for row in run_rows(cfg()) if row.get("first_manifest_id")})
            return ui.input_select("run_manifest", "Manifest", choices, selected="any")

        @render.text
        def run_detail():
            touch()
            run_id = input.run_detail_id().strip()
            if not run_id:
                return "Enter a run_id to inspect full params, attempts, and log paths."
            runs = [row for row in run_rows(cfg()) if row["run_id"] == run_id]
            attempts = [row for row in attempt_rows(cfg()) if row["run_id"] == run_id]
            return json.dumps({"run": runs[0] if runs else None, "attempts": attempts}, indent=2, sort_keys=True, default=str)

        @render.data_frame
        def attempts_table():
            touch()
            data = _filter_attempts(
                attempt_rows(cfg()),
                status=input.attempt_status(),
                search=input.attempt_search(),
                plan=input.attempt_plan(),
            )
            return render.DataGrid(_df(data), filters=True, selection_mode="rows", height="620px")

        @render.ui
        def attempt_plan_filter():
            touch()
            choices = ["any"] + sorted({str(row.get("plan_id", "")) for row in attempt_rows(cfg()) if row.get("plan_id")})
            return ui.input_select("attempt_plan", "Plan", choices, selected="any")

        @render.text
        def attempt_detail():
            touch()
            attempt_id = input.attempt_detail_id().strip()
            if not attempt_id:
                return "Enter an attempt_id to inspect metadata and logs."
            attempts = [row for row in attempt_rows(cfg()) if row["attempt_id"] == attempt_id]
            attempt = attempts[0] if attempts else None
            log_text = ""
            if attempt and attempt.get("attempt_log_path"):
                log_text = pretty_jsonl(attempt["attempt_log_path"], max_lines=120, tail=False)
            return json.dumps({"attempt": attempt}, indent=2, sort_keys=True, default=str) + ("\n\nLOG\n" + log_text if log_text else "")

        @render.data_frame
        def manifests_table():
            touch()
            return render.DataGrid(_df(manifest_rows(cfg())), filters=True, selection_mode="rows", height="360px")

        @render.ui
        def manifest_select():
            touch()
            choices = [row["manifest_id"] for row in manifest_rows(cfg())]
            return ui.input_select("manifest_id", "Manifest preview", choices or [""], selected=choices[-1] if choices else "")

        @render.data_frame
        def manifest_preview_table():
            touch()
            manifest_id = input.manifest_id()
            return render.DataGrid(_df(manifest_preview(cfg(), manifest_id)), filters=True, height="520px")

        @render.data_frame
        def plans_table():
            touch()
            return render.DataGrid(_df(plan_rows(cfg())), filters=True, selection_mode="rows", height="420px")

        @render.ui
        def plan_select():
            touch()
            choices = [row["plan_id"] for row in plan_rows(cfg())]
            selected = choices[-1] if choices else ""
            return ui.div(
                ui.input_select("plan_id", "Plan", choices or [""], selected=selected),
                ui.input_select("plan_file", "Plan file", PLAN_FILES, selected="plan_summary.txt"),
            )

        @render.ui
        def plan_select_for_submit():
            touch()
            choices = [row["plan_id"] for row in plan_rows(cfg())]
            return ui.input_select("cmd_submit_plan_id", "Plan", choices or [""], selected=choices[-1] if choices else "")

        @render.text
        def plan_file_preview():
            touch()
            plan_id = input.plan_id()
            if not plan_id:
                return "No plan selected."
            return plan_file_text(cfg(), plan_id, input.plan_file(), max_lines=250)

        @render.data_frame
        def resource_models_table():
            touch()
            return render.DataGrid(_df(resource_model_rows(cfg())), filters=True, selection_mode="rows", height="300px")

        @render.ui
        def resource_model_select():
            touch()
            choices = [row["resource_model_id"] for row in resource_model_rows(cfg())]
            return ui.input_select("resource_model_id", "Resource model", choices or [""], selected=choices[-1] if choices else "")

        @render.text
        def resource_model_json():
            touch()
            model = resource_model_detail(cfg(), input.resource_model_id())
            return json.dumps(model, indent=2, sort_keys=True, default=str) if model else "No resource models found."

        @render.data_frame
        def resource_assessment_table():
            touch()
            return render.DataGrid(_df(resource_assessment_rows(cfg())), filters=True, height="420px")

        @render.plot
        def runtime_assessment_plot():
            touch()
            data = resource_assessment_rows(cfg())
            return _scatter_plot(
                data,
                "predicted_time_minutes",
                "observed_time_minutes",
                "Predicted vs observed runtime",
                "Predicted minutes",
                "Observed minutes",
            )

        @render.plot
        def ram_assessment_plot():
            touch()
            data = resource_assessment_rows(cfg())
            return _scatter_plot(
                data,
                "predicted_ram_gb",
                "observed_max_rss_gb",
                "Predicted RAM vs Slurm MaxRSS",
                "Predicted GB",
                "Observed GB",
            )

        @render.data_frame
        def logs_table():
            touch()
            return render.DataGrid(_df(list_log_files(cfg(), input.log_search())), filters=True, height="420px")

        @render.ui
        def log_select():
            touch()
            logs = list_log_files(cfg(), input.log_search())
            choices = {row["path"]: f"{row['kind']}: {row['name']}" for row in logs[:500]}
            return ui.input_select("log_path", "Log file", choices or {"": "No logs matched"}, selected=next(iter(choices), ""))

        @render.text
        def log_text():
            touch()
            path = input.log_path()
            if not path:
                return "No log selected."
            if input.log_pretty():
                return pretty_jsonl(path, max_lines=int(input.log_lines()), tail=bool(input.log_tail()))
            return read_text_file(path, max_lines=int(input.log_lines()), tail=bool(input.log_tail()))

        @render.text
        def command_status():
            touch()
            return last_command.get()

        @render.data_frame
        def command_history_table():
            touch()
            return render.DataGrid(_df(dashboard_command_rows(cfg())), filters=True, height="360px")

        def run_action(action: str, callback, arguments: dict[str, Any] | None = None) -> None:
            event = run_dashboard_command(cfg(), action, callback, arguments)
            last_command.set(json.dumps(event, indent=2, sort_keys=True, default=str))
            bump_refresh()

        @reactive.effect
        @reactive.event(input.cmd_build_manifest)
        def _cmd_build_manifest() -> None:
            run_action("build_manifest", lambda: api.build_manifest(cfg()["_project_config_path"]))

        @reactive.effect
        @reactive.event(input.cmd_ingest_latest)
        def _cmd_ingest_latest() -> None:
            run_action("ingest_manifest", lambda: api.ingest_manifest(cfg()["_project_config_path"], "latest"), {"manifest": "latest"})

        @reactive.effect
        @reactive.event(input.cmd_collect_status)
        def _cmd_collect_status() -> None:
            plan = input.cmd_collect_plan().strip() or None
            run_action("collect_status", lambda: api.collect_status(cfg()["_project_config_path"], plan=plan), {"plan": plan})

        @reactive.effect
        @reactive.event(input.cmd_learn_resources)
        def _cmd_learn_resources() -> None:
            run_action("learn_resources", lambda: api.learn_resources(cfg()["_project_config_path"]))

        @reactive.effect
        @reactive.event(input.cmd_export_registry)
        def _cmd_export_registry() -> None:
            run_action("export_registry", lambda: api.export_registry(cfg()["_project_config_path"]))

        @reactive.effect
        @reactive.event(input.cmd_plan_runs)
        def _cmd_plan_runs() -> None:
            status = input.cmd_plan_status()
            status_arg = None if status == "any-default" else status
            pilot = input.cmd_plan_pilot().strip() or None
            where = input.cmd_plan_where().strip() or None
            retry = input.cmd_plan_retry()
            retry_arg = None if retry == "none" else retry
            args = {
                "where": where,
                "status": status_arg,
                "pilot_set": pilot,
                "retry_policy": retry_arg,
                "generous_resources": bool(input.cmd_plan_generous()),
                "one_run_per_group": bool(input.cmd_plan_one_per_group()),
            }
            run_action(
                "plan_jobs",
                lambda: api.plan_jobs(
                    cfg()["_project_config_path"],
                    where=where,
                    status=status_arg,
                    pilot_set=pilot,
                    retry_policy=retry_arg,
                    generous_resources=bool(input.cmd_plan_generous()),
                    one_run_per_group=bool(input.cmd_plan_one_per_group()),
                ),
                args,
            )

        @reactive.effect
        @reactive.event(input.cmd_submit_plan)
        def _cmd_submit_plan() -> None:
            plan = input.cmd_submit_plan_id()
            dry_run = bool(input.cmd_submit_dry_run())
            if not dry_run and not bool(input.cmd_submit_confirm()):
                last_command.set("Submission not run. Check the confirmation box or use dry-run.")
                return
            run_action(
                "submit_jobs",
                lambda: api.submit_jobs(cfg()["_project_config_path"], plan=plan, dry_run=dry_run),
                {"plan": plan, "dry_run": dry_run},
            )

    return App(app_ui, server)


def launch_dashboard(
    project_config: str | Path | None = None,
    global_config: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
    launch_browser: bool = False,
) -> None:
    from shiny import run_app

    run_app(create_app(project_config, global_config), host=host, port=port, launch_browser=launch_browser)


def _overview_page():
    return ui.nav_panel(
        "Overview",
        ui.output_ui("project_header"),
        ui.output_ui("overview_cards"),
        ui.layout_columns(
            ui.card(ui.card_header("Runs by status"), ui.output_plot("run_status_plot")),
            ui.card(ui.card_header("Attempts by status"), ui.output_plot("attempt_status_plot")),
        ),
        value="Overview",
    )


def _runs_page():
    return ui.nav_panel(
        "Runs",
        ui.layout_sidebar(
            ui.sidebar(
                ui.input_select("run_status", "Status", ["any", "pending", "planned", "submitted", "running", "succeeded", "failed_oom", "failed_timeout", "failed_simulator_error", "failed_unknown", "not_started_due_to_group_failure"], selected="any"),
                ui.output_ui("run_manifest_filter"),
                ui.input_numeric("run_replicate_min", "Min replicate", 1, min=1),
                ui.input_numeric("run_replicate_max", "Max replicate", 999999, min=1),
                ui.input_text("run_search", "Text search", placeholder="run_id, param_set_id, params"),
                ui.input_text("run_detail_id", "Run detail run_id", placeholder="paste run_id"),
            ),
            ui.card(ui.card_header("Logical runs"), ui.output_data_frame("runs_table")),
            ui.card(ui.card_header("Run detail"), ui.output_text_verbatim("run_detail")),
        ),
        value="Runs",
    )


def _attempts_page():
    return ui.nav_panel(
        "Attempts",
        ui.layout_sidebar(
            ui.sidebar(
                ui.input_select("attempt_status", "Status", ["any", "planned", "submitted", "running", "succeeded", "failed_oom", "failed_timeout", "failed_node", "failed_simulator_error", "failed_unknown", "not_started_due_to_group_failure"], selected="any"),
                ui.output_ui("attempt_plan_filter"),
                ui.input_text("attempt_search", "Text search", placeholder="attempt, run, group, Slurm id"),
                ui.input_text("attempt_detail_id", "Attempt detail attempt_id", placeholder="paste attempt_id"),
            ),
            ui.card(ui.card_header("Attempts"), ui.output_data_frame("attempts_table")),
            ui.card(ui.card_header("Attempt detail and log"), ui.output_text_verbatim("attempt_detail")),
        ),
        value="Attempts",
    )


def _manifests_page():
    return ui.nav_panel(
        "Manifests",
        ui.card(ui.card_header("Manifest registry"), ui.output_data_frame("manifests_table")),
        ui.card(ui.card_header("Manifest preview"), ui.output_ui("manifest_select"), ui.output_data_frame("manifest_preview_table")),
        value="Manifests",
    )


def _plans_page():
    return ui.nav_panel(
        "Plans",
        ui.card(ui.card_header("Plan registry and directories"), ui.output_data_frame("plans_table")),
        ui.card(ui.card_header("Plan file preview"), ui.output_ui("plan_select"), ui.output_text_verbatim("plan_file_preview")),
        value="Plans",
    )


def _resources_page():
    return ui.nav_panel(
        "Resources",
        ui.layout_columns(
            ui.card(ui.card_header("Runtime assessment"), ui.output_plot("runtime_assessment_plot")),
            ui.card(ui.card_header("RAM assessment"), ui.output_plot("ram_assessment_plot")),
        ),
        ui.card(ui.card_header("Resource models"), ui.output_data_frame("resource_models_table")),
        ui.card(ui.card_header("Selected model JSON"), ui.output_ui("resource_model_select"), ui.output_text_verbatim("resource_model_json")),
        ui.card(ui.card_header("Resource assessment rows"), ui.output_data_frame("resource_assessment_table")),
        value="Resources",
    )


def _logs_page():
    return ui.nav_panel(
        "Logs",
        ui.layout_sidebar(
            ui.sidebar(
                ui.input_text("log_search", "Search log names", placeholder="run, attempt, group, Slurm id"),
                ui.output_ui("log_select"),
                ui.input_numeric("log_lines", "Lines", 120, min=1, max=5000),
                ui.input_checkbox("log_tail", "Tail selected lines", True),
                ui.input_checkbox("log_pretty", "Pretty-print JSONL", True),
            ),
            ui.card(ui.card_header("Matching logs"), ui.output_data_frame("logs_table")),
            ui.card(ui.card_header("Log text"), ui.output_text_verbatim("log_text")),
        ),
        value="Logs",
    )


def _commands_page():
    return ui.nav_panel(
        "Command Center",
        ui.layout_sidebar(
            ui.sidebar(
                ui.h5("Safe commands"),
                ui.input_action_button("cmd_build_manifest", "Build manifest"),
                ui.input_action_button("cmd_ingest_latest", "Ingest latest manifest"),
                ui.input_text("cmd_collect_plan", "Collect plan", value="", placeholder="blank for all attempts"),
                ui.input_action_button("cmd_collect_status", "Collect status"),
                ui.input_action_button("cmd_learn_resources", "Learn resources"),
                ui.input_action_button("cmd_export_registry", "Export registry"),
                ui.hr(),
                ui.h5("Plan jobs"),
                ui.input_select("cmd_plan_status", "Status", {"any-default": "any (default)", "pending": "pending", "not_succeeded": "not_succeeded", "failed_oom": "failed_oom", "failed_timeout": "failed_timeout", "failed_simulator_error": "failed_simulator_error"}, selected="any-default"),
                ui.input_text("cmd_plan_where", "Where expression", value="", placeholder='params.N >= 2000'),
                ui.input_text("cmd_plan_pilot", "Pilot set", value="", placeholder="pilot_001.tsv"),
                ui.input_select("cmd_plan_retry", "Retry policy", {"none": "none", "oom": "oom", "timeout": "timeout"}, selected="none"),
                ui.input_checkbox("cmd_plan_generous", "Generous resources", False),
                ui.input_checkbox("cmd_plan_one_per_group", "One run per group", False),
                ui.input_action_button("cmd_plan_runs", "Create plan"),
                ui.hr(),
                ui.h5("Submit plan"),
                ui.output_ui("plan_select_for_submit"),
                ui.input_checkbox("cmd_submit_dry_run", "Dry run", True),
                ui.input_checkbox("cmd_submit_confirm", "I confirm Slurm submission", False),
                ui.input_action_button("cmd_submit_plan", "Submit selected plan", class_="btn-warning"),
            ),
            ui.card(ui.card_header("Last command"), ui.output_text_verbatim("command_status")),
            ui.card(ui.card_header("Dashboard command history"), ui.output_data_frame("command_history_table")),
        ),
        value="Command Center",
    )


def _value(title: str, value: Any, subtitle: str):
    return ui.div({"class": "metric"}, ui.div({"class": "metric-title"}, title), ui.div({"class": "metric-value"}, str(value)), ui.div({"class": "metric-subtitle"}, subtitle))


def _df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _filter_runs(rows: list[dict[str, Any]], status: str, search: str, manifest: str, replicate_min: float, replicate_max: float) -> list[dict[str, Any]]:
    search_lower = (search or "").lower()
    out = []
    for row in rows:
        if status != "any" and row.get("status") != status:
            continue
        if manifest and manifest != "any" and row.get("first_manifest_id") != manifest:
            continue
        replicate = int(row.get("replicate") or 0)
        if replicate < int(replicate_min or 1) or replicate > int(replicate_max or 999999):
            continue
        if search_lower and search_lower not in json.dumps(row, sort_keys=True, default=str).lower():
            continue
        out.append(row)
    return out


def _filter_attempts(rows: list[dict[str, Any]], status: str, search: str, plan: str) -> list[dict[str, Any]]:
    search_lower = (search or "").lower()
    out = []
    for row in rows:
        if status != "any" and row.get("status") != status:
            continue
        if plan and plan != "any" and row.get("plan_id") != plan:
            continue
        if search_lower and search_lower not in json.dumps(row, sort_keys=True, default=str).lower():
            continue
        out.append(row)
    return out


def _bar_plot(counts: dict[str, int], title: str, ylabel: str):
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 4))
    if counts:
        labels = list(counts)
        values = [counts[label] for label in labels]
        ax.bar(labels, values, color="#2563eb")
        ax.tick_params(axis="x", rotation=35)
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    fig.tight_layout()
    return fig


def _scatter_plot(rows: list[dict[str, Any]], x_key: str, y_key: str, title: str, xlabel: str, ylabel: str):
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7, 4))
    x_vals = []
    y_vals = []
    for row in rows:
        try:
            x = float(row.get(x_key) or "")
            y = float(row.get(y_key) or "")
        except ValueError:
            continue
        x_vals.append(x)
        y_vals.append(y)
    if x_vals and y_vals:
        ax.scatter(x_vals, y_vals, color="#0f766e", alpha=0.75)
        low = min(x_vals + y_vals)
        high = max(x_vals + y_vals)
        ax.plot([low, high], [low, high], color="#94a3b8", linestyle="--")
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    fig.tight_layout()
    return fig


_CSS = """
body { background: #f7f3ea; color: #172026; }
.navbar { background: linear-gradient(90deg, #132a35, #275163) !important; }
.hero {
  background: radial-gradient(circle at top left, #fff7d6, #d9edf2 55%, #f7f3ea);
  border: 1px solid #d6c8a8;
  border-radius: 22px;
  padding: 1.25rem 1.5rem;
  margin-bottom: 1rem;
}
.hero h2 { margin: 0 0 .35rem 0; font-weight: 800; letter-spacing: .02em; }
.hero p { margin: .1rem 0; }
.value-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: .85rem;
  margin-bottom: 1rem;
}
.metric {
  background: #fffdf7;
  border: 1px solid #d8c9a9;
  border-radius: 18px;
  padding: .95rem;
  box-shadow: 0 10px 25px rgba(36, 55, 65, .08);
}
.metric-title { font-size: .8rem; text-transform: uppercase; letter-spacing: .08em; color: #60717a; }
.metric-value { font-size: 1.75rem; font-weight: 800; color: #12313d; }
.metric-subtitle { font-size: .82rem; color: #60717a; }
pre { max-height: 680px; overflow: auto; background: #102027 !important; color: #e9f7ef !important; }
"""
