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


def render_terminal(
    report: Report,
    *,
    title: str = "git-insight",
    subtitle: str = "",
    top: int = 10,
    console: Optional["Console"] = None,
) -> None:
    """Print the report to the terminal with rich panels, tables and charts."""
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    console = console or Console()
    s = report.summary

    console.print(
        Panel(
            Text(subtitle or "full history", style="dim"),
            title=Text(title, style="bold"),
            border_style="cyan",
            expand=False,
        )
    )

    grid = Table.grid(padding=(0, 3))
    for style in ("dim", "bold", "dim", "bold"):
        grid.add_column(style=style)
    grid.add_row("Commits", f"{s.total_commits:,}", "Authors", f"{s.total_authors:,}")
    grid.add_row("Active days", f"{s.active_days:,}", "Time span", f"{s.span_days:,} days")
    grid.add_row("First commit", _day(s.first_commit), "Last commit", _day(s.last_commit))
    grid.add_row(
        "Lines added",
        Text(f"+{s.lines_added:,}", style="green"),
        "Lines deleted",
        Text(f"-{s.lines_deleted:,}", style="red"),
    )
    grid.add_row("Files touched", f"{s.files_touched:,}", "", "")
    console.print(Panel(grid, title="Overview", border_style="cyan", expand=False))

    console.print(_contributors(report.authors, by="commits", top=top, title="Top contributors by commits"))
    console.print(_contributors(report.authors, by="lines", top=top, title="Top contributors by lines changed"))

    act = report.activity
    console.print(
        Panel(
            _chart(_HOUR_LABELS, act.hours, "Commits by hour of day", console, cell=3),
            title="Activity by hour (author local time)",
            border_style="blue",
        )
    )
    console.print(
        Panel(
            _chart(list(WEEKDAYS), act.weekdays, "Commits by weekday", console, cell=6),
            title="Activity by weekday",
            border_style="blue",
        )
    )
    console.print(Panel(_heatmap(report), title="Activity heatmap (weekday x hour)", border_style="blue", expand=False))

    if report.hot_files:
        peak = report.hot_files[0].changes
        table = Table(title="Hot files (most frequently changed)", box=box.SIMPLE_HEAD, title_justify="left", title_style="bold")
        table.add_column("#", justify="right", style="dim")
        table.add_column("File", overflow="fold")
        table.add_column("Changes", justify="right")
        table.add_column("Churn", justify="right")
        table.add_column("Authors", justify="right")
        table.add_column("Bug fixes", justify="right")
        table.add_column("", no_wrap=True)
        for i, hot in enumerate(report.hot_files[:top], 1):
            fixes = f"{hot.fixes:,} ({hot.fix_ratio:.0%})" if hot.fixes else "-"
            table.add_row(
                str(i), Text(hot.path), f"{hot.changes:,}", f"{hot.churn:,}", str(hot.authors),
                Text(fixes, style="red" if hot.fix_ratio >= 0.3 else ""),
                _bar(hot.changes, peak, style="yellow"),
            )
        console.print(table)

    bf = report.bus_factor
    if bf is None:
        console.print(Text("Bus factor: not available (filtered by author, or no files to analyse).", style="dim"))
        return
    body = Text()
    body.append(f"Bus factor: {bf.value}", style=_RISK_STYLE[bf.risk])
    body.append(f"  ({_RISK_TEXT[bf.risk]})\n", style="dim")
    body.append(f"Based on {bf.files_total:,} files; an author 'knows' a file if they wrote "
                f">= {bf.knowledge_share:.0%} of it.\n\n", style="dim")
    for owner in bf.owners[: min(top, 5)]:
        body.append(f"{owner.name:<28.28} ", style="bold")
        body.append("█" * max(1, round(owner.share * 30)), style=_RISK_STYLE[bf.risk])
        body.append(f" {owner.share:.0%} of files ({owner.files_owned:,})\n")
    console.print(Panel(body, title="Bus factor", border_style=_RISK_STYLE[bf.risk].split()[-1], expand=False))
