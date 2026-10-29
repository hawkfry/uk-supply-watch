#!/usr/bin/env python3
"""Refresh the board and push only the public page files.

Refuses to run when the working tree already has changes, and never stages
anything except the three files GitHub Pages serves.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
LATEST = ROOT / "data" / "latest.json"
PUBLIC_PATHS = (
    "docs/.nojekyll",
    "docs/index.html",
    "docs/latest.json",
)


class PublishError(RuntimeError):
    """Publication stopped before a commit or a push."""


def git(root: Path, args: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "git failed").strip()
        raise PublishError(detail)
    return proc


def dirty_entries(root: Path) -> list[str]:
    proc = git(root, ["status", "--porcelain=v1", "--untracked-files=all"])
    return [line for line in proc.stdout.splitlines() if line.strip()]


def staged_paths(root: Path) -> list[str]:
    proc = git(root, ["diff", "--cached", "--name-only"])
    return [line for line in proc.stdout.splitlines() if line.strip()]


def publish(root: Path | None = None, refresh_argv: list[str] | None = None, push: bool = True) -> int:
    root = root or ROOT
    if dirty_entries(root):
        raise PublishError("refusing to publish while the repository has other changes")

    refresh = subprocess.run(
        refresh_argv or [sys.executable, str(root / "refresh.py")],
        cwd=root,
    )
    if refresh.returncode:
        raise PublishError("refresh failed")
    changed = dirty_entries(root)
    if changed:
        raise PublishError("refresh changed repository files: " + "; ".join(changed))
    if not (root / "data" / "latest.json").exists():
        raise PublishError("refresh did not write data/latest.json")

    docs = root / "docs"
    docs.mkdir(exist_ok=True)
    shutil.copy(root / "web" / "index.html", docs / "index.html")
    shutil.copy(root / "data" / "latest.json", docs / "latest.json")
    (docs / ".nojekyll").write_text("")

    git(root, ["add", "--", *PUBLIC_PATHS])
    staged = staged_paths(root)
    if not set(staged) <= set(PUBLIC_PATHS):
        raise PublishError("refusing to commit an unexpected index")
    if not staged:
        print("Public page already matches this refresh.")
        return 0
    git(root, ["commit", "-m", "Publish the latest board"])
    if push:
        git(root, ["push"])
        print("Published. https://hawkfry.github.io/uk-supply-watch/")
    return 0


def main() -> int:
    try:
        return publish()
    except PublishError as exc:
        print(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
