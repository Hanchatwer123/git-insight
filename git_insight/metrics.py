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
