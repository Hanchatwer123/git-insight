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


# -------------------------------------------------------------------------- html

_CSS = """
:root{--bg:#f6f8fa;--card:#fff;--fg:#1f2328;--muted:#656d76;--line:#d0d7de;--accent:#0969da;--add:#1a7f37;--del:#cf222e;--heat:46,160,67}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--card:#161b22;--fg:#e6edf3;--muted:#8d96a0;--line:#30363d;--accent:#58a6ff;--add:#3fb950;--del:#f85149}}
*{box-sizing:border-box}body{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:960px;margin:0 auto}h1{margin:0 0 4px;font-size:26px}h2{font-size:17px;margin:0 0 12px}
.sub{color:var(--muted);margin:0 0 20px}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px;overflow-x:auto}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin-bottom:16px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}.stat b{display:block;font-size:22px}.stat>span{color:var(--muted);font-size:13px}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);font-size:14px;vertical-align:middle}
th{color:var(--muted);font-weight:600}td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}.add{color:var(--add)}.del{color:var(--del)}
.bar{background:var(--line);border-radius:4px;height:8px;min-width:80px}.bar>span{display:block;height:100%;border-radius:4px;background:var(--accent)}
.cols{display:flex;align-items:flex-end;gap:3px;height:140px}.col{flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;height:100%;font-size:11px;color:var(--muted)}
.col i{display:block;width:100%;background:var(--accent);border-radius:3px 3px 0 0}.heat td{width:3.4%;height:22px;padding:0;border:2px solid var(--card);border-radius:4px;text-align:center;font-size:10px;color:var(--muted)}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;word-break:break-all}.risk{font-weight:700}.critical{color:var(--del)}.warning{color:#bf8700}.healthy{color:var(--add)}
"""


def _pct(value: int, peak: int) -> float:
    return 100 * value / peak if peak else 0.0


def _html_bar(value: int, peak: int) -> str:
    return f'<div class="bar"><span style="width:{_pct(value, peak):.1f}%"></span></div>'


def _html_columns(labels: Sequence[str], values: Sequence[int]) -> str:
    peak = max(values) or 1
    cols = "".join(
        f'<div class="col" title="{html.escape(label)}: {value}">'
        f'<i style="height:{_pct(value, peak):.1f}%"></i>{html.escape(label)}</div>'
        for label, value in zip(labels, values)
    )
    return f'<div class="cols">{cols}</div>'


def render_html(report: Report, *, title: str = "git-insight report", subtitle: str = "", top: int = 10) -> str:
    """Return a self-contained HTML page (all text is escaped, no external requests)."""
    e = html.escape
    s = report.summary
    parts: List[str] = []

    stats = [
        ("Commits", f"{s.total_commits:,}"), ("Authors", f"{s.total_authors:,}"),
        ("Active days", f"{s.active_days:,}"), ("Lines added", f'<span class="add">+{s.lines_added:,}</span>'),
        ("Lines deleted", f'<span class="del">-{s.lines_deleted:,}</span>'), ("Files touched", f"{s.files_touched:,}"),
    ]
    parts.append('<div class="stats">' + "".join(
        f'<div class="stat"><b>{value}</b><span>{label}</span></div>' for label, value in stats) + "</div>")

    def contributors(heading: str, key) -> str:
        ranked = sorted(report.authors, key=lambda a: (-key(a), a.name.lower()))[:top]
        peak = key(ranked[0]) if ranked else 0
        rows = "".join(
            f'<tr><td class="n">{i}</td><td>{e(a.name)}</td><td class="n">{a.commits:,}</td>'
            f'<td class="n add">+{a.added:,}</td><td class="n del">-{a.deleted:,}</td><td>{_html_bar(key(a), peak)}</td></tr>'
            for i, a in enumerate(ranked, 1)
        )
        return (f'<section class="card"><h2>{heading}</h2><table><tr><th class="n">#</th><th>Author</th>'
                f'<th class="n">Commits</th><th class="n">+Lines</th><th class="n">-Lines</th><th></th></tr>{rows}</table></section>')

    parts.append(contributors("Top contributors by commits", lambda a: a.commits))
    parts.append(contributors("Top contributors by lines changed", lambda a: a.churn))

    act = report.activity
    parts.append(f'<section class="card"><h2>Activity by hour (author local time)</h2>{_html_columns(_HOUR_LABELS, act.hours)}</section>')
    parts.append(f'<section class="card"><h2>Activity by weekday</h2>{_html_columns(list(WEEKDAYS), act.weekdays)}</section>')

    peak = max((v for row in act.heatmap for v in row), default=0)
    head = "<tr><th></th>" + "".join(f"<th>{h if h % 3 == 0 else ''}</th>" for h in range(24)) + "</tr>"
    body = ""
    for day, row in zip(WEEKDAYS, act.heatmap):
        cells = "".join(
            f'<td title="{day} {h:02d}:00 - {v} commit(s)" style="background:rgba(var(--heat),{0.12 + 0.88 * v / peak:.2f})"></td>'
            if v else f'<td title="{day} {h:02d}:00 - 0" style="background:var(--line);opacity:.4"></td>'
            for h, v in enumerate(row)
        )
        body += f"<tr><th>{day}</th>{cells}</tr>"
    parts.append(f'<section class="card"><h2>Activity heatmap</h2><table class="heat">{head}{body}</table></section>')

    if report.hot_files:
        peak_changes = report.hot_files[0].changes
        rows = "".join(
            f'<tr><td class="n">{i}</td><td><code>{e(h.path)}</code></td><td class="n">{h.changes:,}</td>'
            f'<td class="n">{h.churn:,}</td><td class="n">{h.authors}</td>'
            f'<td class="n">{f"{h.fixes:,} ({h.fix_ratio:.0%})" if h.fixes else "-"}</td>'
            f'<td>{_html_bar(h.changes, peak_changes)}</td></tr>'
            for i, h in enumerate(report.hot_files[:top], 1)
        )
        parts.append('<section class="card"><h2>Hot files</h2><table><tr><th class="n">#</th><th>File</th>'
                     '<th class="n">Changes</th><th class="n">Churn</th><th class="n">Authors</th>'
                     f'<th class="n">Bug fixes</th><th></th></tr>{rows}</table></section>')

    bf = report.bus_factor
    if bf is None:
        parts.append('<section class="card"><h2>Bus factor</h2><p class="sub">Not available (filtered by author, or no files to analyse).</p></section>')
    else:
        owners = "".join(
            f"<tr><td>{e(o.name)}</td><td class=\"n\">{o.files_owned:,}</td><td class=\"n\">{o.share:.0%}</td>"
            f"<td>{_html_bar(o.files_owned, bf.owners[0].files_owned)}</td></tr>"
            for o in bf.owners[: min(top, 5)]
        )
        parts.append(
            f'<section class="card"><h2>Bus factor: <span class="risk {bf.risk}">{bf.value}</span></h2>'
            f'<p class="sub">{e(_RISK_TEXT[bf.risk])}. Based on {bf.files_total:,} files; an author "knows" a file '
            f"if they wrote at least {bf.knowledge_share:.0%} of it.</p>"
            f'<table><tr><th>Owner</th><th class="n">Files</th><th class="n">Share</th><th></th></tr>{owners}</table></section>'
        )

    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{e(title)}</title><style>{_CSS}</style></head><body><main>"
        f'<h1>{e(title)}</h1><p class="sub">{e(subtitle or "full history")} &middot; '
        f"{_day(s.first_commit)} to {_day(s.last_commit)}</p>{''.join(parts)}</main></body></html>\n"
    )


# -------------------------------------------------------------------------- json


def render_json(report: Report, *, top: Optional[int] = None) -> str:
    """Return the report as pretty-printed JSON."""
    return json.dumps(report_to_dict(report, top), indent=2, ensure_ascii=False)
