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


# ----------------------------------------------------------------- running git


def _git_env() -> Dict[str, str]:
    return {**os.environ, **_GIT_ENV_OVERRIDES}


def _run_git(args: Sequence[str], cwd: Path) -> "subprocess.CompletedProcess[str]":
    """Run ``git <args>`` in ``cwd`` and capture its output."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=_git_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitNotInstalledError("The 'git' executable was not found in PATH.") from exc
    except PermissionError as exc:
        raise GitInsightError(f"Permission denied while running git in {cwd}: {exc}") from exc
    except OSError as exc:
        raise GitInsightError(f"Could not run git in {cwd}: {exc}") from exc


def resolve_repo(path: Union[str, Path] = ".") -> Path:
    """Return the root of the repository that contains ``path``.

    Raises:
        NotAGitRepositoryError: ``path`` is missing, not a directory, or not in a repository.
        GitInsightError: git refuses to read the repository (e.g. unsafe ownership).
        GitNotInstalledError: git is not installed.
    """
    start = Path(path).expanduser()
    try:
        if not start.exists():
            raise NotAGitRepositoryError(f"Path does not exist: {start}")
        if not start.is_dir():
            raise NotAGitRepositoryError(f"Not a directory: {start}")
    except PermissionError as exc:
        raise GitInsightError(f"Permission denied: cannot access {start}") from exc

    top = _run_git(["rev-parse", "--show-toplevel"], start)
    if top.returncode == 0 and top.stdout.strip():
        return Path(top.stdout.strip())
    if "dubious ownership" in top.stderr:
        raise GitInsightError(
            f"Git refuses to read {start.resolve()} (the directory is owned by another user). "
            f"If you trust it, run: git config --global --add safe.directory {start.resolve()}"
        )
    # A bare repository has no work tree, so --show-toplevel fails but --git-dir works.
    if _run_git(["rev-parse", "--git-dir"], start).returncode == 0:
        return start.resolve()
    raise NotAGitRepositoryError(f"{start.resolve()} is not inside a Git repository.")


def ensure_has_commits(repo: Path, rev: Optional[str] = None) -> None:
    """Fail with a clear message if ``rev`` (default ``HEAD``) does not point at a commit.

    Raises:
        EmptyRepositoryError: the repository has no commits yet.
        GitInsightError: ``rev`` is not a known branch, tag or commit.
    """
    target = rev or "HEAD"
    proc = _run_git(["rev-parse", "--verify", "--quiet", f"{target}^{{commit}}"], repo)
    if proc.returncode != 0:
        if rev is None:
            raise EmptyRepositoryError("The repository has no commits yet.")
        raise GitInsightError(f"Unknown branch, tag or revision: {rev!r}")


def _filter_args(flt: LogFilter) -> List[str]:
    # Values are attached with '=' so they can never be mistaken for options.
    args: List[str] = []
    if not flt.include_merges:
        args.append("--no-merges")
    if flt.author:
        args += ["--regexp-ignore-case", f"--author={flt.author}"]
    if flt.since:
        args.append(f"--since={flt.since}")
    if flt.until:
        args.append(f"--until={flt.until}")
    args += [flt.rev or "HEAD", "--"]
    return args


def count_commits(repo: Path, flt: Optional[LogFilter] = None) -> int:
    """Count the commits :func:`iter_commits` would yield (cheap; used for progress bars)."""
    flt = flt or LogFilter()
    proc = _run_git(["rev-list", "--count", *_filter_args(flt)], repo)
    if proc.returncode != 0:
        raise GitCommandError(f"git rev-list failed: {proc.stderr.strip() or 'unknown error'}")
    try:
        return int(proc.stdout.strip())
    except ValueError as exc:
        raise GitCommandError(f"Unexpected output from git rev-list: {proc.stdout!r}") from exc


def list_tracked_files(repo: Path, rev: Optional[str] = None) -> Optional[Set[str]]:
    """Return the files that exist at ``rev`` (default ``HEAD``), or ``None`` if unavailable."""
    proc = _run_git(["ls-tree", "-r", "--name-only", "-z", rev or "HEAD"], repo)
    if proc.returncode != 0:
        return None
    return {p for p in proc.stdout.split("\0") if p}
