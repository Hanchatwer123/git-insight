"""Module A: Git parsing core.

Talks to the ``git`` executable through :mod:`subprocess` and turns the output of
``git log --numstat`` into typed Python objects. The log is *streamed*, so even
very large repositories are processed with constant memory.

Why ``subprocess`` instead of GitPython? ``git log --numstat`` already returns
exactly what we need in one pass; GitPython would spawn one process per commit
(or parse objects in Python) and add a dependency.

All problems are reported as subclasses of :class:`GitInsightError`, so callers
only need a single ``except`` clause.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple, Union

__all__ = [
    "Commit",
    "EmptyRepositoryError",
    "FileChange",
    "GitCommandError",
    "GitInsightError",
    "GitNotInstalledError",
    "LogFilter",
    "NotAGitRepositoryError",
    "count_commits",
    "ensure_has_commits",
    "iter_commits",
    "list_tracked_files",
    "parse_log",
    "resolve_repo",
]

_RS = "\x1e"  # record separator: starts every commit header line
_US = "\x1f"  # unit separator: splits the header fields
# %aN/%aE honour .mailmap; %aI is the author date in strict ISO 8601 with offset.
_PRETTY = "--pretty=format:%x1e%H%x1f%aN%x1f%aE%x1f%aI%x1f%s"
# Keep git's error messages in English so we can recognise them; never prompt.
_GIT_ENV_OVERRIDES = {"LC_MESSAGES": "C", "LANGUAGE": "C", "GIT_TERMINAL_PROMPT": "0"}


# --------------------------------------------------------------------------- errors


class GitInsightError(Exception):
    """Base class for every error raised by git-insight."""


class GitNotInstalledError(GitInsightError):
    """The ``git`` executable could not be found."""


class NotAGitRepositoryError(GitInsightError):
    """The given path does not exist or is not inside a Git repository."""


class EmptyRepositoryError(GitInsightError):
    """The repository has no commits yet."""


class GitCommandError(GitInsightError):
    """A git command failed or produced output we could not understand."""


# ------------------------------------------------------------------------ data model


@dataclass(frozen=True)
class FileChange:
    """Lines added and deleted in one file by one commit (binary files count as 0/0)."""

    path: str
    added: int
    deleted: int


@dataclass(frozen=True)
class Commit:
    """A single commit with its per-file statistics.

    ``date`` is the *author* date and keeps the author's own UTC offset, so
    ``date.hour`` is the author's local time of day.
    """

    sha: str
    author_name: str
    author_email: str
    date: datetime
    subject: str
    files: Tuple[FileChange, ...] = ()

    @property
    def added(self) -> int:
        """Total lines added by this commit."""
        return sum(f.added for f in self.files)

    @property
    def deleted(self) -> int:
        """Total lines deleted by this commit."""
        return sum(f.deleted for f in self.files)


@dataclass(frozen=True)
class LogFilter:
    """Selects which commits to read.

    Attributes:
        author: Case-insensitive regular expression matched against name and e-mail.
        since: Only commits after this date; any format git understands
            (``2024-01-31``, ``"2 weeks ago"``).
        until: Only commits before this date.
        rev: A single branch, tag or commit to start from (default: ``HEAD``).
        include_merges: Merge commits are skipped by default because they carry no
            line statistics of their own.
    """

    author: Optional[str] = None
    since: Optional[str] = None
    until: Optional[str] = None
    rev: Optional[str] = None
    include_merges: bool = False

    def __post_init__(self) -> None:
        if self.rev is not None and self.rev.startswith("-"):
            raise GitInsightError(f"Invalid revision {self.rev!r}: it must not start with '-'.")
