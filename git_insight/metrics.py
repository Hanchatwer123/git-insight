"""Module B: metrics & analytics engine.

Pure functions that turn a stream of :class:`~git_insight.parser.Commit` objects
into a :class:`Report`. Nothing here touches git, the terminal or the file
system, which keeps it easy to test.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Collection, Dict, Iterable, List, Mapping, Optional, Set

from .parser import Commit

__all__ = [
    "Activity",
    "AuthorStats",
    "BusFactor",
    "HotFile",
    "Owner",
    "Report",
    "Summary",
    "WEEKDAYS",
    "compute_bus_factor",
    "compute_report",
    "is_fix",
    "report_to_dict",
]

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# Commit subjects that usually mean "something was broken here" (Conventional Commits
# "fix:" included). Whole words only, so "prefix" or "debugger" do not count.
FIX_PATTERN = re.compile(r"\b(fix(e[sd]|ing)?|bug(fix)?s?|hotfix(es)?|regression|crash(es|ed)?)\b", re.IGNORECASE)


def is_fix(subject: str) -> bool:
    """Return True if a commit subject looks like a bug fix."""
    return bool(FIX_PATTERN.search(subject))


# --------------------------------------------------------------------- results


@dataclass
class Summary:
    """Headline numbers for the analysed range."""

    total_commits: int = 0
    total_authors: int = 0
    active_days: int = 0
    first_commit: Optional[datetime] = None
    last_commit: Optional[datetime] = None
    span_days: int = 0
    lines_added: int = 0
    lines_deleted: int = 0
    files_touched: int = 0


@dataclass
class AuthorStats:
    """Contribution of one author (identified by e-mail)."""

    name: str
    email: str
    commits: int = 0
    added: int = 0
    deleted: int = 0

    @property
    def churn(self) -> int:
        """Lines added plus lines deleted."""
        return self.added + self.deleted


@dataclass
class Activity:
    """When commits happen, in the *author's* local time."""

    hours: List[int] = field(default_factory=lambda: [0] * 24)
    weekdays: List[int] = field(default_factory=lambda: [0] * 7)  # Monday = 0
    heatmap: List[List[int]] = field(default_factory=lambda: [[0] * 24 for _ in range(7)])


@dataclass
class HotFile:
    """A file that changes often: a hint at fragile or over-central code."""

    path: str
    changes: int  # number of commits that touched it
    churn: int  # lines added + deleted over its lifetime
    authors: int  # distinct people who changed it
    fixes: int = 0  # commits touching it whose subject looks like a bug fix

    @property
    def fix_ratio(self) -> float:
        """Share of this file's changes that were bug fixes (0.0 to 1.0)."""
        return self.fixes / self.changes if self.changes else 0.0


@dataclass
class Owner:
    """An author and the share of files they own (most added lines in the file)."""

    name: str
    files_owned: int
    share: float


@dataclass
class BusFactor:
    """Result of the bus-factor estimate.

    Attributes:
        value: Smallest number of authors whose departure would leave more than
            ``coverage_threshold`` of the files without any knowledgeable author.
        risk: ``"critical"`` (1), ``"warning"`` (2) or ``"healthy"`` (3+).
        files_total: Files taken into account.
        owners: Authors ranked by number of files they own.
    """

    value: int
    risk: str
    files_total: int
    knowledge_share: float
    coverage_threshold: float
    owners: List[Owner] = field(default_factory=list)


@dataclass
class Report:
    """Everything the formatters need."""

    summary: Summary
    authors: List[AuthorStats]  # sorted by commits, then lines
    activity: Activity
    hot_files: List[HotFile]
    bus_factor: Optional[BusFactor] = None


# ---------------------------------------------------------------------- engine


@dataclass
class _FileAcc:
    changes: int = 0
    added: int = 0
    deleted: int = 0
    fixes: int = 0
    # author key -> [lines added, commits]
    by_author: Dict[str, List[int]] = field(default_factory=dict)


def compute_report(
    commits: Iterable[Commit],
    tracked_files: Optional[Collection[str]] = None,
    *,
    with_bus_factor: bool = True,
    top_files: int = 10,
    knowledge_share: float = 0.2,
    coverage_threshold: float = 0.5,
) -> Report:
    """Compute all metrics in a single pass over ``commits``.

    Args:
        commits: Any iterable of commits (a generator is fine).
        tracked_files: Files that exist at the end of the range. When given, hot
            files and the bus factor ignore files that were later deleted.
        with_bus_factor: Skip the bus factor (e.g. when the history was filtered
            to a single author, which would make it meaningless).
        top_files: How many hot files to keep.
        knowledge_share: An author "knows" a file if they wrote at least this share of it.
        coverage_threshold: Fraction of files that must lose all knowledgeable
            authors for the project to count as stalled.
    """
    authors: Dict[str, AuthorStats] = {}
    files: Dict[str, _FileAcc] = {}
    days: Set[date] = set()
    activity = Activity()
    summary = Summary()

    for commit in commits:
        summary.total_commits += 1
        key = commit.author_email.lower() or commit.author_name.lower()
        stats = authors.get(key)
        if stats is None:
            # git log is newest-first, so the first name seen is the most recent one.
            stats = authors[key] = AuthorStats(commit.author_name, commit.author_email)
        stats.commits += 1

        when = commit.date
        days.add(when.date())
        activity.hours[when.hour] += 1
        activity.weekdays[when.weekday()] += 1
        activity.heatmap[when.weekday()][when.hour] += 1
        if summary.first_commit is None or when < summary.first_commit:
            summary.first_commit = when
        if summary.last_commit is None or when > summary.last_commit:
            summary.last_commit = when

        fix = is_fix(commit.subject)
        for change in commit.files:
            stats.added += change.added
            stats.deleted += change.deleted
            summary.lines_added += change.added
            summary.lines_deleted += change.deleted
            acc = files.get(change.path)
            if acc is None:
                acc = files[change.path] = _FileAcc()
            acc.changes += 1
            acc.fixes += fix
            acc.added += change.added
            acc.deleted += change.deleted
            mine = acc.by_author.setdefault(key, [0, 0])
            mine[0] += change.added
            mine[1] += 1

    summary.total_authors = len(authors)
    summary.active_days = len(days)
    summary.files_touched = len(files)
    if summary.first_commit and summary.last_commit:
        summary.span_days = (summary.last_commit.date() - summary.first_commit.date()).days + 1

    existing = {
        path: acc
        for path, acc in files.items()
        if tracked_files is None or path in tracked_files
    }

    hot = sorted(existing.items(), key=lambda kv: (-kv[1].changes, -(kv[1].added + kv[1].deleted), kv[0]))
    hot_files = [
        HotFile(path, acc.changes, acc.added + acc.deleted, len(acc.by_author), acc.fixes)
        for path, acc in hot[:top_files]
    ]

    bus_factor = None
    if with_bus_factor:
        weights: Dict[str, Dict[str, float]] = {}
        for path, acc in existing.items():
            # Prefer added lines; binary or delete-only files fall back to commit counts.
            use_lines = acc.added > 0
            weights[path] = {
                a: float(v[0] if use_lines else v[1]) for a, v in acc.by_author.items()
            }
        names = {k: a.name for k, a in authors.items()}
        bus_factor = compute_bus_factor(
            weights,
            names,
            knowledge_share=knowledge_share,
            coverage_threshold=coverage_threshold,
        )

    ranked = sorted(authors.values(), key=lambda a: (-a.commits, -a.churn, a.name.lower()))
    return Report(summary, ranked, activity, hot_files, bus_factor)


def compute_bus_factor(
    file_weights: Mapping[str, Mapping[str, float]],
    names: Mapping[str, str],
    *,
    knowledge_share: float = 0.2,
    coverage_threshold: float = 0.5,
) -> Optional[BusFactor]:
    """Estimate the bus (truck) factor from per-file authorship.

    A simplified version of the "truck factor" of Avelino et al. (2016):

    1. For every file, the *owner* is the author who wrote most of it, and every
       author with at least ``knowledge_share`` of it also *knows* the file.
    2. Greedily remove the author who knows the most still-covered files.
    3. A file is *lost* when none of the people who know it remain.
    4. Stop as soon as more than ``coverage_threshold`` of all files are lost;
       the number of removed authors is the bus factor.

    Args:
        file_weights: ``{path: {author_key: weight}}`` (weight = lines written).
        names: ``{author_key: display name}``.

    Returns:
        ``None`` when there are no files to analyse.
    """
    knowers: Dict[str, Set[str]] = {}
    owned: Counter = Counter()

    for path, weights in file_weights.items():
        total = sum(weights.values())
        if not weights or total <= 0:
            continue
        owner = min(weights, key=lambda a: (-weights[a], a))
        owned[owner] += 1
        knowers[path] = {a for a, w in weights.items() if w / total >= knowledge_share} | {owner}

    total_files = len(knowers)
    if total_files == 0:
        return None

    files_of: Dict[str, Set[str]] = {}
    for path, people in knowers.items():
        for person in people:
            files_of.setdefault(person, set()).add(path)

    lost = 0
    removed = 0
    while files_of:
        person = min(files_of, key=lambda a: (-len(files_of[a]), a))
        for path in files_of.pop(person):
            knowers[path].discard(person)
            if not knowers[path]:
                lost += 1
        removed += 1
        if lost / total_files > coverage_threshold:
            break

    risk = "critical" if removed <= 1 else "warning" if removed == 2 else "healthy"
    owners = [
        Owner(names.get(a, a), n, n / total_files)
        for a, n in sorted(owned.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return BusFactor(removed, risk, total_files, knowledge_share, coverage_threshold, owners)


def report_to_dict(report: Report, top: Optional[int] = None) -> Dict[str, Any]:
    """Convert a report to plain data (datetimes become ISO strings) for JSON output."""
    data = asdict(report)
    if top is not None:
        data["authors"] = data["authors"][:top]
        data["hot_files"] = data["hot_files"][:top]
        if data["bus_factor"]:
            data["bus_factor"]["owners"] = data["bus_factor"]["owners"][:top]
    for author in data["authors"]:
        author["churn"] = author["added"] + author["deleted"]
    for hot in data["hot_files"]:
        hot["fix_ratio"] = round(hot["fixes"] / hot["changes"], 3) if hot["changes"] else 0.0
    for key in ("first_commit", "last_commit"):
        value = data["summary"][key]
        data["summary"][key] = value.isoformat() if value else None
    return data
