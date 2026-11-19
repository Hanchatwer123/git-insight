from __future__ import annotations

import json
from datetime import datetime

import pytest
from click.testing import CliRunner

from git_insight import __version__
from git_insight.cli import main
from git_insight.formatter import render_html
from git_insight.metrics import compute_report
from git_insight.parser import Commit, FileChange


def run(*args):
    return CliRunner().invoke(main, [str(a) for a in args])


def test_json_output(sample_repo):
    result = run(sample_repo.path, "--format", "json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["summary"]["total_commits"] == 5
    assert [a["name"] for a in data["authors"]] == ["Alice", "Bob"]
    assert data["hot_files"][0]["path"] == "app.py"
    assert data["bus_factor"]["value"] == 1


def test_author_filter_skips_bus_factor(sample_repo):
    result = run(sample_repo.path, "-f", "json", "--author", "bob")
    data = json.loads(result.output)
    assert data["summary"]["total_commits"] == 2 and data["summary"]["total_authors"] == 1
    assert data["bus_factor"] is None


def test_since_and_branch_filters(sample_repo):
    assert json.loads(run(sample_repo.path, "-f", "json", "--since", "2024-02-01").output)["summary"]["total_commits"] == 1
    assert run(sample_repo.path, "-f", "json", "--branch", "nope").exit_code != 0


def test_html_report_is_written_and_escaped(sample_repo, tmp_path):
    out = tmp_path / "report.html"
    result = run(sample_repo.path, "--format", "html", "--output", out)
    assert result.exit_code == 0, result.output
    page = out.read_text(encoding="utf-8")
    assert page.startswith("<!doctype html>") and "Alice" in page and "app.py" in page
    assert "Bus factor" in page and "git-insight: sample" in page


def test_html_default_filename(sample_repo, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert run(sample_repo.path, "-f", "html").exit_code == 0
    assert (tmp_path / "git-insight-report.html").is_file()


def test_html_escapes_untrusted_names():
    evil = Commit("1", "<script>alert(1)</script>", "e@x.io", datetime.fromisoformat("2024-01-01T10:00:00+00:00"),
                  "m", (FileChange("<img src=x onerror=alert(1)>.py", 1, 0),))
    page = render_html(compute_report([evil]), title="<b>t</b>")
    assert "<script>alert(1)</script>" not in page and "<img src=x" not in page and "<b>t</b>" not in page
    assert "&lt;script&gt;" in page


def test_not_a_repository(tmp_path):
    result = run(tmp_path, "-f", "json")
    assert result.exit_code == 1 and "Error:" in result.output and "not inside a Git repository" in result.output


def test_missing_path(tmp_path):
    assert run(tmp_path / "missing").exit_code == 2  # rejected by click itself


def test_empty_repository(empty_repo):
    result = run(empty_repo.path, "-f", "json")
    assert result.exit_code == 1 and "no commits yet" in result.output


def test_filters_matching_nothing_is_not_an_error(sample_repo):
    result = run(sample_repo.path, "-f", "json", "--since", "2030-01-01")
    assert result.exit_code == 0 and "No commits match" in result.output


def test_unwritable_output(sample_repo, tmp_path):
    result = run(sample_repo.path, "-f", "html", "-o", tmp_path / "no" / "such" / "dir" / "r.html")
    assert result.exit_code == 1 and "Cannot write" in result.output


def test_version_and_help():
    assert __version__ in run("--version").output
    help_text = run("--help").output
    for flag in ("--author", "--since", "--format", "--branch", "--top"):
        assert flag in help_text


def test_terminal_format(sample_repo):
    pytest.importorskip("rich")
    pytest.importorskip("plotext")
    result = run(sample_repo.path)  # default format
    assert result.exit_code == 0, result.output
    for text in ("Overview", "Top contributors by commits", "Hot files", "Bus factor", "app.py"):
        assert text in result.output
