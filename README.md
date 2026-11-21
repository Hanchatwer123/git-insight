# git-insight

Statistics for a local Git repository, right in your terminal or Jupyter notebook: who works on it,
when, which files keep changing **and keep needing bug fixes**, and how dependent the project is on a
few people (**bus factor**).

## Motivation

Team projects live in Git, but `git log` is a wall of text. Questions a team
actually asks are hard to answer from it:

- Is the work shared fairly, or is one person doing everything?
- If our main contributor gets sick before the deadline, can the rest of us continue?
- Which files break again and again, so they deserve tests or a refactor first?

`git-insight` answers these in one command, offline, without uploading your code to any service.

## What is new

Existing tools cover parts of this. For example, `git-fame` ranks authors by lines and `gitstats`
draws activity charts. `git-insight` adds two signals that we did not find in a simple
`pip install`-able tool:

1. **Bug-fix hotspots.** For every frequently changed file it counts how many of those changes were
   bug fixes (commit subjects like `fix ...`, `fix: ...`, `bugfix`, `hotfix`, `regression`, `crash`)
   and shows the share. A file that changes often *and* is mostly being fixed is a strong candidate
   for tests or refactoring.
2. **Bus factor.** A simplified truck-factor estimate (Avelino et al., 2016) with a clear verdict:
   critical, warning or healthy.

Plus: author activity in each author's *local* time, a weekday x hour heatmap, and three output
formats (terminal, self-contained HTML, JSON) from the same analysis.

## Install

```bash
pip install git-insight-tool
```

The PyPI package is `git-insight-tool`; the command it installs is `git-insight`.

Requires Python 3.9+ and the `git` executable. Dependencies: `click`, `rich`, `plotext`.

For development:

```bash
git clone https://github.com/Hanchatwer123/git-insight && cd git-insight
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Usage

```bash
git-insight                       # analyse the repo in the current directory
git-insight ~/code/project        # or any folder inside a repo
git insight                       # git finds the `git-insight` executable too
python -m git_insight             # same, if the script folder is not on PATH

git-insight --author alice        # one person (regex on name or e-mail, case-insensitive)
git-insight --since "3 months ago" --until 2025-01-01
git-insight --branch develop -n 20
git-insight --format html -o report.html     # self-contained page, opens in any browser
git-insight --format json > stats.json       # machine-readable
```

| Option | Meaning |
|---|---|
| `-a, --author TEXT` | Only commits whose author name/e-mail matches (regex). |
| `-s, --since TEXT`, `-u, --until TEXT` | Date range, any format git understands. |
| `-b, --branch TEXT` | Branch, tag or commit to analyse (default `HEAD`). |
| `-f, --format [terminal\|html\|json]` | Output format (default `terminal`). |
| `-o, --output FILE` | Where to write the html/json report (html default: `./git-insight-report.html`). |
| `-n, --top INT` | Rows per ranking (default 10). |
| `--include-merges` | Also count merge commits. |

### In Jupyter / JupyterHub

```python
%pip install git-insight-tool
!git-insight /path/to/repo -n 5
```

Or use the Python API and show the HTML report inside the notebook:

```python
from IPython.display import HTML
from git_insight import formatter
from git_insight.metrics import compute_report
from git_insight.parser import iter_commits, list_tracked_files, resolve_repo

repo = resolve_repo("/path/to/repo")
report = compute_report(iter_commits(repo), list_tracked_files(repo))
print(report.bus_factor.value, report.bus_factor.risk)
for f in report.hot_files[:5]:
    print(f.path, f.changes, f.fixes, f"{f.fix_ratio:.0%}")
HTML(formatter.render_html(report, title=repo.name, subtitle="full history", top=10))
```

## Example use cases

- **Course or hackathon team**: before the deadline, check that every member has regular commits and
  that the bus factor is not 1.
- **Joining an open-source project**: find the files that change most and need the most fixes, and
  the people who know them best.
- **Code review planning**: give extra attention to files with a high bug-fix share.
- **Reports and dashboards**: export JSON and build your own charts, or attach the HTML report.

## What it measures

- **Overview**: commits, authors, active days, time span, lines added/deleted, files touched.
- **Top contributors** by commits and by lines changed. Authors are matched by e-mail, and
  `.mailmap` is honoured.
- **Activity** by hour, by weekday and as a weekday x hour heatmap, in each author's *local* time.
- **Hot files**: files that exist today and changed in the most commits, with the number and share
  of bug-fix commits among those changes.
- **Bus factor**: for every file, the author who wrote most of it is the owner, and anyone with at
  least 20% of it also "knows" it. Authors are removed greedily, always the one who knows the most
  files, until more than half of the files have nobody left who knows them. The number of authors
  removed is the bus factor: 1 is critical, 2 is a warning, 3+ is healthy.

### Limitations

- Bug fixes are detected from commit subjects only, so they depend on how a team writes messages.
- Renames are not followed (a renamed file counts as a delete plus an add).
- Merge commits are skipped by default because git reports no line stats for them. Squash merges
  and rebases are attributed to whoever committed the result.
- The bus factor uses lines *added*, not `git blame`, so mass reformatting or vendored code can
  skew it. It is a signal, not a verdict.
- With `--author`, the bus factor is omitted: one person's history says nothing about the team.

## Development

```bash
pytest            # tests build real throw-away git repositories
ruff check .
```

| Module | Role |
|---|---|
| `git_insight/parser.py` | Streams `git log --numstat` into typed objects; all git errors become `GitInsightError`. |
| `git_insight/metrics.py` | Pure analytics: summary, rankings, activity, hot files with bug-fix counts, bus factor. |
| `git_insight/formatter.py` | `rich` + `plotext` terminal output, HTML and JSON renderers. |
| `git_insight/cli.py` | The `click` command (`git-insight`). |

## License

MIT
