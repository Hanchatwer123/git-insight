from __future__ import annotations

import pytest

from git_insight.parser import (
    EmptyRepositoryError,
    GitInsightError,
    LogFilter,
    NotAGitRepositoryError,
    count_commits,
    ensure_has_commits,
    iter_commits,
    list_tracked_files,
    parse_log,
    resolve_repo,
)


def test_reads_all_commits_newest_first(sample_repo):
    commits = list(iter_commits(sample_repo.path))
    assert [c.subject for c in commits] == ["trim", "bob edits app", "bob adds lib", "grow", "first"]
    assert commits[-1].author_name == "Alice"
    assert commits[-1].author_email == "alice@example.com"
    assert commits[-1].date.isoformat() == "2024-01-01T10:00:00+00:00"


def test_numstat_values_binary_and_unicode_paths(sample_repo):
    by_subject = {c.subject: c for c in iter_commits(sample_repo.path)}
    first = {f.path: (f.added, f.deleted) for f in by_subject["first"].files}
    assert first == {"app.py": (3, 0), "README.md": (1, 0)}
    assert {f.path: f.added for f in by_subject["bob adds lib"].files} == {"lib.py": 2, "файл.txt": 1}
    bob = {f.path: (f.added, f.deleted) for f in by_subject["bob edits app"].files}
    assert bob == {"app.py": (1, 0), "logo.bin": (0, 0)}  # binary reports '-' and counts as 0
    assert by_subject["trim"].deleted == 2


def test_author_since_and_until_filters(sample_repo):
    path = sample_repo.path
    assert len(list(iter_commits(path, LogFilter(author="bob")))) == 2  # case-insensitive
    assert len(list(iter_commits(path, LogFilter(since="2024-01-04")))) == 2
    assert len(list(iter_commits(path, LogFilter(until="2024-01-02")))) == 2


def test_count_matches_iteration(sample_repo):
    for flt in (LogFilter(), LogFilter(author="alice"), LogFilter(since="2024-02-01")):
        assert count_commits(sample_repo.path, flt) == len(list(iter_commits(sample_repo.path, flt)))


def test_merge_commits_are_skipped_unless_requested(sample_repo):
    sample_repo.git("checkout", "-q", "-b", "feature")
    sample_repo.commit({"feat.txt": "f\n"}, "feature work", "2024-03-01T10:00:00+00:00")
    sample_repo.git("checkout", "-q", "main")
    sample_repo.git("merge", "--no-ff", "-q", "-m", "merge feature", "feature", when="2024-03-02T10:00:00+00:00")

    default = [c.subject for c in iter_commits(sample_repo.path)]
    assert "merge feature" not in default and "feature work" in default
    with_merges = [c.subject for c in iter_commits(sample_repo.path, LogFilter(include_merges=True))]
    assert "merge feature" in with_merges


def test_branch_selection(sample_repo):
    sample_repo.git("checkout", "-q", "-b", "feature")
    sample_repo.commit({"feat.txt": "f\n"}, "feature work", "2024-03-01T10:00:00+00:00")
    sample_repo.git("checkout", "-q", "main")
    assert len(list(iter_commits(sample_repo.path, LogFilter(rev="feature")))) == 6
    assert len(list(iter_commits(sample_repo.path))) == 5


def test_tracked_files(sample_repo):
    assert list_tracked_files(sample_repo.path) == {"app.py", "README.md", "lib.py", "файл.txt", "logo.bin"}


def test_resolve_repo_from_subdirectory(sample_repo):
    sub = sample_repo.path / "deep" / "er"
    sub.mkdir(parents=True)
    assert resolve_repo(sub).resolve() == sample_repo.path.resolve()


def test_missing_path_and_plain_folder(tmp_path):
    with pytest.raises(NotAGitRepositoryError, match="does not exist"):
        resolve_repo(tmp_path / "nope")
    with pytest.raises(NotAGitRepositoryError, match="not inside a Git repository"):
        resolve_repo(tmp_path)
    file = tmp_path / "f.txt"
    file.write_text("x")
    with pytest.raises(NotAGitRepositoryError, match="Not a directory"):
        resolve_repo(file)


def test_empty_repository(empty_repo):
    with pytest.raises(EmptyRepositoryError):
        ensure_has_commits(empty_repo.path)


def test_unknown_and_dangerous_revisions(sample_repo):
    with pytest.raises(GitInsightError, match="Unknown branch"):
        ensure_has_commits(sample_repo.path, "no-such-branch")
    with pytest.raises(GitInsightError, match="must not start with"):
        LogFilter(rev="--output=/tmp/pwned")


def test_bare_repository(sample_repo, tmp_path):
    bare = tmp_path / "bare.git"
    sample_repo.git("clone", "-q", "--bare", str(sample_repo.path), str(bare))
    repo = resolve_repo(bare)
    assert len(list(iter_commits(repo))) == 5


def test_stopping_early_does_not_hang(sample_repo):
    stream = iter_commits(sample_repo.path)
    assert next(stream).subject == "trim"
    stream.close()


def test_parse_log_skips_malformed_headers_and_handles_crlf():
    text = [
        "\x1e" + "\x1f".join(["a" * 40, "Ann", "ann@x.io", "2024-05-06T07:08:09+02:00", "ok"]) + "\r\n",
        "\r\n",
        "5\t1\tsrc/a.py\r\n",
        "\x1ebroken header without separators\n",
        "9\t9\tignored/because/header/is/bad.py\n",
    ]
    commits = list(parse_log(text))
    assert len(commits) == 1
    assert commits[0].files[0].path == "src/a.py" and commits[0].added == 5 and commits[0].deleted == 1
    assert commits[0].date.hour == 7  # author-local time is preserved
