"""Module C (part 2): output formatting.

Three renderers share one :class:`~git_insight.metrics.Report`:

* :func:`render_terminal`: tables, panels and charts built with ``rich`` and ``plotext``;
* :func:`render_html`: one self-contained HTML file (no external assets);
* :func:`render_json`: machine-readable output.

``rich`` and ``plotext`` are imported lazily, so the HTML and JSON paths work
even where those libraries are missing.
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from typing import TYPE_CHECKING, Iterable, Iterator, List, Optional, Sequence

from .metrics import WEEKDAYS, AuthorStats, Report, report_to_dict

if TYPE_CHECKING:  # pragma: no cover
    from rich.console import Console
    from rich.text import Text

__all__ = ["render_html", "render_json", "render_terminal", "track_commits"]

_HOUR_LABELS = [f"{h:02d}" for h in range(24)]
_HEAT_STYLES = ["grey23", "dark_green", "green3", "green", "bright_green"]
_RISK_STYLE = {"critical": "bold red", "warning": "bold yellow", "healthy": "bold green"}
_RISK_TEXT = {
    "critical": "critical: one person's departure would stall most of the project",
    "warning": "moderate: two people hold most of the knowledge",
    "healthy": "healthy: knowledge is spread across three or more people",
}


def _day(value: Optional[datetime]) -> str:
    return value.strftime("%Y-%m-%d") if value else "-"


# ---------------------------------------------------------------------- terminal


def track_commits(
    commits: Iterable, total: int, console: Optional["Console"] = None
) -> Iterator:
    """Yield ``commits`` while showing a progress bar (on stderr, so pipes stay clean)."""
    from rich.console import Console
    from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

    console = console or Console(stderr=True)
    with Progress(
        TextColumn("[bold cyan]Reading history"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("", total=total or None)
        for commit in commits:
            yield commit
            progress.advance(task)


def _bar(value: int, peak: int, width: int = 16, style: str = "cyan") -> "Text":
    from rich.text import Text

    filled = round(width * value / peak) if peak else 0
    if value and not filled:
        filled = 1
    return Text("█" * filled, style=style)


def _column_chart(
    labels: Sequence[str], values: Sequence[int], *, height: int = 8, cell: int = 3
) -> "Text":
    """A plain-``rich`` vertical bar chart (fallback when plotext is unavailable)."""
    from rich.text import Text

    peak = max(values) or 1
    heights = [max(1, round(v / peak * height)) if v else 0 for v in values]
    chart = Text()
    for row in range(height, 0, -1):
        for h in heights:
            chart.append(("█" * (cell - 1) if h >= row else " " * (cell - 1)) + " ", style="cyan")
        chart.append("\n")
    for label in labels:
        chart.append(label.ljust(cell), style="dim")
    return chart


def _chart(labels: Sequence[str], values: Sequence[int], title: str, console: "Console", cell: int):
    """Draw a bar chart with plotext, falling back to a built-in one on any problem."""
    from rich.text import Text

    try:
        import plotext as plt

        plt.clear_figure()
        plt.plot_size(min(console.width - 4, 100), 14)
        plt.title(title)
        plt.bar(list(labels), list(values))
        return Text.from_ansi(plt.build())
    except Exception:  # noqa: BLE001 - purely cosmetic; never let a chart break the report
        return _column_chart(labels, values, cell=cell)


def _heatmap(report: Report) -> "Text":
    from rich.text import Text

    grid = report.activity.heatmap
    peak = max((v for row in grid for v in row), default=0)
    text = Text("     ")
    for hour in range(24):
        text.append(f"{hour:02d}" if hour % 3 == 0 else "  ", style="dim")
        text.append(" ")
    text.append("\n")
    for day, row in zip(WEEKDAYS, grid):
        text.append(f"{day}  ", style="dim")
        text.append(" ")
        for value in row:
            if value == 0:
                text.append("· ", style="grey30")
            else:
                level = max(1, min(4, -(-value * 4 // peak)))  # ceil(value / peak * 4)
                text.append("██", style=_HEAT_STYLES[level])
            text.append(" ")
        text.append("\n")
    return text


def _contributors(authors: List[AuthorStats], *, by: str, top: int, title: str):
    from rich import box
    from rich.table import Table
    from rich.text import Text

    key = (lambda a: a.commits) if by == "commits" else (lambda a: a.churn)
    ranked = sorted(authors, key=lambda a: (-key(a), a.name.lower()))[:top]
    peak = key(ranked[0]) if ranked else 0
    table = Table(title=title, box=box.SIMPLE_HEAD, title_justify="left", title_style="bold")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Author", overflow="fold")
    table.add_column("Commits", justify="right")
    table.add_column("+Lines", justify="right", style="green")
    table.add_column("-Lines", justify="right", style="red")
    table.add_column("", no_wrap=True)
    for i, author in enumerate(ranked, 1):
        table.add_row(
            str(i),
            Text(author.name),  # Text() so names containing [brackets] are never parsed as markup
            f"{author.commits:,}",
            f"{author.added:,}",
            f"{author.deleted:,}",
            _bar(key(author), peak),
        )
    return table
