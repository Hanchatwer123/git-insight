"""Module C (part 1): command-line interface.

Installed as the ``git-insight`` command. Because the executable is named
``git-insight`` and lives on ``PATH``, git also exposes it as ``git insight``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import click

from . import __version__, formatter
from .metrics import compute_report
from .parser import (
    GitInsightError,
    LogFilter,
    count_commits,
    ensure_has_commits,
    iter_commits,
    list_tracked_files,
    resolve_repo,
)

DEFAULT_HTML_NAME = "git-insight-report.html"


def _ensure_utf8_streams() -> None:
    """Switch stdout/stderr to UTF-8 when they use a legacy code page.

    On Windows, redirected output (pipes, files, some IDE consoles) defaults to a
    code page such as cp1252 that cannot encode the box and bar characters used
    by the terminal report, which would crash with ``UnicodeEncodeError``.
    """
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if encoding != "utf8" and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover - stream already in use / not reconfigurable
                pass


@click.command(
    name="git-insight",
    context_settings={"help_option_names": ["-h", "--help"]},
    help="Analyse the commit history of a local Git repository.\n\n"
    "PATH is the repository (or any folder inside it); the default is the current directory.",
)
@click.argument(
    "path",
    default=".",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option("-a", "--author", help="Only commits whose author name or e-mail matches this regex (case-insensitive).")
@click.option("-s", "--since", help="Only commits after this date, e.g. 2024-01-31 or '2 weeks ago'.")
@click.option("-u", "--until", help="Only commits before this date.")
@click.option("-b", "--branch", "rev", help="Branch, tag or commit to analyse (default: HEAD).")
@click.option(
    "-f", "--format", "fmt",
    type=click.Choice(["terminal", "html", "json"]),
    default="terminal", show_default=True,
    help="Output format.",
)
@click.option(
    "-o", "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    help=f"File for the html/json report (html default: ./{DEFAULT_HTML_NAME}; json default: stdout).",
)
@click.option("-n", "--top", type=click.IntRange(1, 100), default=10, show_default=True, help="Rows in each ranking.")
@click.option("--include-merges", is_flag=True, help="Also count merge commits (they have no line stats).")
@click.version_option(__version__, "-V", "--version", prog_name="git-insight")
def main(
    path: Path,
    author: Optional[str],
    since: Optional[str],
    until: Optional[str],
    rev: Optional[str],
    fmt: str,
    output: Optional[Path],
    top: int,
    include_merges: bool,
) -> None:
    """Entry point of the ``git-insight`` command."""
    _ensure_utf8_streams()
    try:
        flt = LogFilter(author=author, since=since, until=until, rev=rev, include_merges=include_merges)
        repo = resolve_repo(path)
        ensure_has_commits(repo, flt.rev)

        total = count_commits(repo, flt)
        if total == 0:
            click.echo("No commits match the given filters.", err=True)
            return

        commits = iter_commits(repo, flt)
        if fmt == "terminal":
            commits = formatter.track_commits(commits, total)

        report = compute_report(
            commits,
            list_tracked_files(repo, flt.rev),
            # With --author only one person's history is loaded, so a bus factor would be meaningless.
            with_bus_factor=author is None,
            top_files=top,
        )
    except GitInsightError as exc:
        raise click.ClickException(str(exc)) from exc

    parts = [f"author ~ {author}"] if author else []
    parts += [f"since {since}"] if since else []
    parts += [f"until {until}"] if until else []
    parts += [f"branch {rev}"] if rev else []
    subtitle = ", ".join(parts) or "full history"
    title = f"git-insight: {repo.name or repo}"

    if fmt == "terminal":
        formatter.render_terminal(report, title=title, subtitle=subtitle, top=top)
        return

    if fmt == "html":
        text = formatter.render_html(report, title=title, subtitle=subtitle, top=top)
        output = output or Path(DEFAULT_HTML_NAME)
    else:
        text = formatter.render_json(report, top=top)

    if output is None:
        click.echo(text)
        return
    try:
        output.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise click.ClickException(f"Cannot write {output}: {exc}") from exc
    click.echo(f"Report written to {output}", err=True)


if __name__ == "__main__":  # pragma: no cover
    main()
