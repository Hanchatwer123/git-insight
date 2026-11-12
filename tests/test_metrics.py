from __future__ import annotations

from datetime import datetime

from git_insight.metrics import compute_bus_factor, compute_report, report_to_dict
from git_insight.parser import Commit, FileChange, iter_commits


def mk(sha, name, iso, files, email=None):
    return Commit(
        sha, name, email or f"{name.lower()}@x.io", datetime.fromisoformat(iso), "msg",
        tuple(FileChange(p, a, d) for p, a, d in files),
    )


def test_summary_and_activity():
    commits = [
        mk("3", "Bob", "2024-01-03T09:00:00+00:00", [("b.py", 4, 1)]),
        mk("2", "Alice", "2024-01-01T15:30:00+00:00", [("a.py", 2, 0)]),
        mk("1", "Alice", "2024-01-01T10:00:00+00:00", [("a.py", 3, 0), ("README.md", 1, 0)]),
    ]
    r = compute_report(commits)
    s = r.summary
    assert (s.total_commits, s.total_authors, s.active_days) == (3, 2, 2)
    assert (s.lines_added, s.lines_deleted, s.files_touched) == (10, 1, 3)
    assert s.span_days == 3
    assert s.first_commit.isoformat() == "2024-01-01T10:00:00+00:00"
    assert r.activity.hours[10] == r.activity.hours[15] == r.activity.hours[9] == 1
    assert r.activity.weekdays[0] == 2 and r.activity.weekdays[2] == 1  # Mon x2, Wed x1
    assert r.activity.heatmap[0][10] == 1 and sum(map(sum, r.activity.heatmap)) == 3


def test_authors_are_ranked_and_merged_by_email():
    commits = [
        mk("3", "Alice B.", "2024-01-03T09:00:00+00:00", [("a", 1, 0)], email="Alice@X.io"),
        mk("2", "alice", "2024-01-02T09:00:00+00:00", [("a", 5, 2)], email="alice@x.io"),
        mk("1", "Bob", "2024-01-01T09:00:00+00:00", [("b", 100, 0)]),
    ]
    authors = compute_report(commits).authors
    assert [a.name for a in authors] == ["Alice B.", "Bob"]  # newest name wins; ranked by commits
    assert (authors[0].commits, authors[0].added, authors[0].deleted, authors[0].churn) == (2, 6, 2, 8)


def test_hot_files_order_and_tracked_filter():
    commits = [
        mk(str(i), "A" if i % 2 else "B", f"2024-01-0{i}T09:00:00+00:00", [("hot.py", 1, 1)] + ([("old.py", 1, 0)] if i < 4 else []))
        for i in range(1, 6)
    ]
    report = compute_report(commits)
    assert [(h.path, h.changes, h.churn, h.authors) for h in report.hot_files] == [
        ("hot.py", 5, 10, 2), ("old.py", 3, 3, 2),
    ]
    only_existing = compute_report(commits, tracked_files={"hot.py"})
    assert [h.path for h in only_existing.hot_files] == ["hot.py"]
    assert len(compute_report(commits, top_files=1).hot_files) == 1


def test_is_fix_matches_whole_words_only():
    from git_insight.metrics import is_fix

    for subject in ["Fix crash on empty repo", "fix: off-by-one", "bugfix in parser", "Fixed typo",
                    "hotfix for release", "revert regression"]:
        assert is_fix(subject), subject
    for subject in ["add prefix option", "use debugger", "refactor parser", "Fixture cleanup"]:
        assert not is_fix(subject), subject


def test_hot_files_count_bug_fixes():
    def c(sha, subject, files):
        return Commit(sha, "A", "a@x.io", datetime.fromisoformat(f"2024-01-0{sha}T09:00:00+00:00"), subject,
                      tuple(FileChange(p, 1, 0) for p in files))

    commits = [
        c("4", "fix crash in parser", ["parser.py"]),
        c("3", "Fix: wrong total", ["parser.py", "cli.py"]),
        c("2", "add cli", ["cli.py"]),
        c("1", "initial parser", ["parser.py"]),
    ]
    report = compute_report(commits)
    by_path = {h.path: h for h in report.hot_files}
    assert (by_path["parser.py"].changes, by_path["parser.py"].fixes) == (3, 2)
    assert (by_path["cli.py"].changes, by_path["cli.py"].fixes) == (2, 1)
    assert abs(by_path["parser.py"].fix_ratio - 2 / 3) < 1e-9
    data = report_to_dict(report)
    assert data["hot_files"][0]["fixes"] == 2 and data["hot_files"][0]["fix_ratio"] == 0.667


def test_empty_input():
    r = compute_report([])
    assert r.summary.total_commits == 0 and r.authors == [] and r.hot_files == []
    assert r.summary.first_commit is None and r.bus_factor is None


def test_bus_factor_single_author_is_one():
    bf = compute_bus_factor({"a": {"x": 10}, "b": {"x": 5}}, {"x": "X"})
    assert bf.value == 1 and bf.risk == "critical" and bf.owners[0].share == 1.0


def test_bus_factor_two_balanced_authors_is_two():
    # Each owns half of the files alone: losing one leaves exactly 50% lost, which is not "more than half".
    files = {"f1": {"a": 9}, "f2": {"a": 9}, "f3": {"b": 9}, "f4": {"b": 9}}
    bf = compute_bus_factor(files, {"a": "A", "b": "B"})
    assert bf.value == 2 and bf.risk == "warning"


def test_bus_factor_dominant_author():
    files = {"f1": {"a": 9}, "f2": {"a": 9}, "f3": {"a": 9}, "f4": {"b": 9}}
    assert compute_bus_factor(files, {"a": "A", "b": "B"}).value == 1


def test_shared_knowledge_raises_bus_factor():
    # Everyone wrote a similar share of every file, so nobody is a single point of failure.
    files = {f"f{i}": {"a": 5, "b": 5, "c": 5} for i in range(6)}
    bf = compute_bus_factor(files, {"a": "A", "b": "B", "c": "C"})
    assert bf.value == 3 and bf.risk == "healthy"


def test_bus_factor_without_files():
    assert compute_bus_factor({}, {}) is None
    assert compute_bus_factor({"f": {"a": 0}}, {"a": "A"}) is None


def test_report_on_real_repository(sample_repo):
    from git_insight.parser import list_tracked_files

    report = compute_report(iter_commits(sample_repo.path), list_tracked_files(sample_repo.path))
    s = report.summary
    assert (s.total_commits, s.total_authors, s.active_days) == (5, 2, 4)
    assert (s.lines_added, s.lines_deleted, s.files_touched) == (10, 2, 5)
    alice, bob = report.authors
    assert (alice.name, alice.commits, alice.added, alice.deleted) == ("Alice", 3, 6, 2)
    assert (bob.name, bob.commits, bob.added, bob.deleted) == ("Bob", 2, 4, 0)
    assert report.activity.hours[23] == 1 and report.activity.weekdays[4] == 1  # Fri 23h
    assert report.hot_files[0].path == "app.py" and report.hot_files[0].changes == 4
    assert report.bus_factor.value == 1 and report.bus_factor.owners[0].name == "Bob"  # Bob owns 3 of 5 files


def test_report_to_dict_is_json_friendly(sample_repo):
    import json

    report = compute_report(iter_commits(sample_repo.path))
    data = json.loads(json.dumps(report_to_dict(report, top=1)))
    assert len(data["authors"]) == 1 and data["authors"][0]["churn"] == 8
    assert data["summary"]["first_commit"].startswith("2024-01-01T10:00:00")
