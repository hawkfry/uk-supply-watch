#!/usr/bin/env python3
"""Refresh the board and push the public GitHub Page.

Run from the Mac when you want the public page updated:

    python3 publish.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
LATEST = ROOT / "data" / "latest.json"


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, text=True)


def main() -> int:
    refresh = subprocess.run([sys.executable, str(ROOT / "refresh.py")], cwd=ROOT)
    if refresh.returncode:
        print("Refresh failed. Not publishing a half-written board.")
        return refresh.returncode
    if not LATEST.exists():
        print("No data/latest.json to publish.")
        return 1

    DOCS.mkdir(exist_ok=True)
    shutil.copy(ROOT / "web" / "index.html", DOCS / "index.html")
    shutil.copy(LATEST, DOCS / "latest.json")
    (DOCS / ".nojekyll").write_text("")

    run(["git", "add", "docs", "web/index.html", "publish.py", "README.md", ".gitignore"])
    dirty = run(["git", "diff", "--cached", "--quiet"])
    if dirty.returncode == 0:
        print("Public page already matches this refresh.")
        return 0

    commit = run(["git", "commit", "-m", "Publish the latest board"])
    if commit.returncode:
        return commit.returncode
    push = run(["git", "push"])
    if push.returncode:
        print("Commit is local. Push failed.")
        return push.returncode
    print("Published. https://hawkfry.github.io/uk-supply-watch/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
