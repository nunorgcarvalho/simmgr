from __future__ import annotations

from pathlib import Path

from shiny import App

from simmgr.cli import main
from simmgr.dashboard import create_app
from simmgr.init_project import init_project


def test_dashboard_app_can_be_constructed(tmp_path: Path) -> None:
    project = tmp_path / "project"
    init_project(project)
    app = create_app(project / "project_config.yaml")
    assert isinstance(app, App)


def test_dashboard_cli_help(capsys) -> None:
    try:
        main(["dashboard", "--help"])
    except SystemExit as exc:
        assert exc.code == 0
    captured = capsys.readouterr()
    assert "--port" in captured.out
    assert "--launch-browser" in captured.out
