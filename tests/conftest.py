"""Helpers that build real throw-away Git repositories with fixed authors and dates."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Dict, Union

import pytest


class Repo:
    def __init__(self, path: Path) -> None:
        self.path = path

    def git(self, *args: str, name: str = "Alice", email: str = "alice@example.com", when: str = "") -> str:
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
        }
        if when:
            env["GIT_AUTHOR_DATE"] = when
            env["GIT_COMMITTER_DATE"] = when
        done = subprocess.run(
            ["git", "-c", "commit.gpgsign=false", *args],
            cwd=self.path, env=env, capture_output=True, text=True, check=True,
        )
        return done.stdout

    def commit(
        self, files: Dict[str, Union[str, bytes]], message: str, when: str,
        name: str = "Alice", email: str = "alice@example.com",
    ) -> None:
        for rel, content in files.items():
            target = self.path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_text(content, encoding="utf-8")
        self.git("add", "-A", name=name, email=email, when=when)
        self.git("commit", "-q", "-m", message, name=name, email=email, when=when)


def init_repo(path: Path) -> Repo:
    path.mkdir(parents=True, exist_ok=True)
    repo = Repo(path)
    repo.git("init", "-q", "-b", "main")
    return repo


@pytest.fixture
def empty_repo(tmp_path: Path) -> Repo:
    return init_repo(tmp_path / "empty")


@pytest.fixture
def sample_repo(tmp_path: Path) -> Repo:
    """Five commits, two authors. 2024-01-01 was a Monday.

    #  author  when (Mon 10h, Mon 15h, Wed 9h, Fri 23h, Sat 12h)  change
    1  Alice   app.py +3, README.md +1
    2  Alice   app.py +2
    3  Bob     lib.py +2, файл.txt +1
    4  Bob     app.py +1, logo.bin (binary)
    5  Alice   app.py -2
    """
    repo = init_repo(tmp_path / "sample")
    repo.commit({"app.py": "1\n2\n3\n", "README.md": "hello\n"}, "first", "2024-01-01T10:00:00+00:00")
    repo.commit({"app.py": "1\n2\n3\n4\n5\n"}, "grow", "2024-01-01T15:30:00+00:00")
    repo.commit({"lib.py": "x\ny\n", "файл.txt": "привет\n"}, "bob adds lib", "2024-01-03T09:00:00+00:00",
                name="Bob", email="bob@example.com")
    repo.commit({"app.py": "1\n2\n3\n4\n5\n6\n", "logo.bin": b"\x00\x01\x02\xff"}, "bob edits app",
                "2024-01-05T23:00:00+00:00", name="Bob", email="bob@example.com")
    repo.commit({"app.py": "1\n2\n3\n4\n"}, "trim", "2024-02-10T12:00:00+00:00")
    return repo
