"""Terminal rendering needs rich (and optionally plotext); skipped where they are not installed."""

from __future__ import annotations

import pytest

pytest.importorskip("rich")

from rich.console import Console  # noqa: E402

from git_insight import formatter  # noqa: E402
from git_insight.metrics import compute_report  # noqa: E402
from git_insight.parser import iter_commits, list_tracked_files  # noqa: E402


@pytest.fixture
def report(sample_repo):
    return compute_report(iter_commits(sample_repo.path), list_tracked_files(sample_repo.path))


def render(report, **kwargs) -> str:
    console = Console(record=True, width=110, force_terminal=False)
    formatter.render_terminal(report, console=console, **kwargs)
    return console.export_text()


def test_sections_are_present(report):
    out = render(report, title="demo", subtitle="full history")
    for text in ("demo", "Overview", "Top contributors by commits", "Top contributors by lines changed",
                 "Activity heatmap", "Hot files", "Bus factor: 1", "Alice", "Bob", "app.py"):
        assert text in out


def test_markup_in_names_is_not_interpreted(report):
    report.authors[0].name = "[red]Mallory[/red]"
    assert "[red]Mallory[/red]" in render(report)


def test_fallback_chart_works_without_plotext(report, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "plotext", None)  # makes `import plotext` fail
    out = render(report)
    assert "Activity by weekday" in out and "Mon" in out and "█" in out


def test_progress_wrapper_passes_items_through():
    console = Console(file=__import__("io").StringIO(), force_terminal=False)
    assert list(formatter.track_commits(iter(range(5)), 5, console)) == [0, 1, 2, 3, 4]
