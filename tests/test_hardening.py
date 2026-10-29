#!/usr/bin/env python3
"""Security checks for publication, rendering, and upstream downloads."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import threading
import unittest
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def git(cwd, *args, check=True):
    proc = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True)
    if check and proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    return proc


def script_text(html: str) -> str:
    match = re.search(r"<script>(.*?)</script>", html, re.S)
    if not match:
        raise AssertionError("page has no application script")
    return match.group(1)


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.web = (ROOT / "web" / "index.html").read_text()
        self.docs = (ROOT / "docs" / "index.html").read_text()

    def test_pages_are_byte_identical(self):
        self.assertEqual((ROOT / "web" / "index.html").read_bytes(), (ROOT / "docs" / "index.html").read_bytes())

    def test_csp_hash_matches_the_script(self):
        script = script_text(self.web)
        digest = base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode()
        self.assertIn(f"script-src 'sha256-{digest}'", self.web)
        self.assertIn("connect-src 'self'", self.web)
        self.assertNotIn("unsafe-inline", self.web.split("script-src", 1)[1].split(";", 1)[0])
        self.assertNotIn("/api/latest", self.web)
        self.assertIn('fetch("latest.json"', self.web)

    def test_malicious_numbers_cannot_inject(self):
        node = r"""
const fs = require("fs");
const vm = require("vm");
const html = fs.readFileSync(process.argv[1], "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const context = { document: {}, console, Number, String, Math, RegExp, JSON, Array, Object, Date, Intl };
vm.createContext(context);
const start = script.indexOf("function finite");
const end = script.indexOf("function move");
vm.runInContext(script.slice(start, end), context);
const attack = '"><img src=x onerror=alert(1)>';
if (context.showNum(attack) !== "—") process.exit(2);
if (context.showNum(Infinity) !== "—") process.exit(3);
if (context.finite(attack) !== null) process.exit(4);
const tile = `<div data-score="${context.esc(context.showNum(attack))}">${context.esc(context.showNum(attack))}</div>`;
if (tile.includes("<img") || tile.includes("onerror")) process.exit(5);
const widthAttack = attack;
const rain = context.finite(widthAttack);
if (rain !== null) process.exit(6);
if (context.showNum(43) !== "43") process.exit(8);
console.log("ok");
"""
        proc = subprocess.run(["node", "-e", node, str(ROOT / "web" / "index.html")], text=True, capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)


class ServerTests(unittest.TestCase):
    def test_local_preview_serves_project_data(self):
        from server import DATA, Handler

        original = DATA.read_text() if DATA.exists() else None
        DATA.parent.mkdir(exist_ok=True)
        DATA.write_text('{"parts":[],"generated_at":"test"}\n')
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        port = httpd.server_address[1]
        try:
            with urlopen(f"http://127.0.0.1:{port}/latest.json", timeout=5) as resp:
                body = json.loads(resp.read().decode())
            self.assertEqual(body["generated_at"], "test")
            with urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
                page = resp.read().decode()
            self.assertIn('fetch("latest.json"', page)
            self.assertNotIn("/api/latest", page)
        finally:
            httpd.shutdown()
            if original is None:
                DATA.unlink(missing_ok=True)
            else:
                DATA.write_text(original)


class FetchTests(unittest.TestCase):
    def test_url_policy(self):
        from http_fetch import FetchError, check_url

        hosts = {"example.com"}
        with self.assertRaises(FetchError):
            check_url("http://example.com/a", hosts)
        with self.assertRaises(FetchError):
            check_url("https://user:secret@example.com/a", hosts)
        with self.assertRaises(FetchError):
            check_url("https://example.com:8443/a", hosts)
        with self.assertRaises(FetchError):
            check_url("https://evil.example/a", hosts)
        self.assertEqual(check_url("https://example.com/a?b=1", hosts), "https://example.com/a?b=1")

    def test_declared_and_actual_size_are_capped(self):
        from http_fetch import FetchError, fetch_bytes
        from unittest.mock import patch

        class Resp:
            headers = {"Content-Length": "50"}
            def geturl(self):
                return "https://example.com/a"
            def read(self, n):
                return b"x" * 40
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        class Opener:
            def open(self, req, timeout=None):
                return Resp()

        with patch("http_fetch.build_opener", return_value=Opener()):
            with patch("http_fetch.check_url", side_effect=lambda url, hosts: url):
                with self.assertRaises(FetchError):
                    fetch_bytes("https://example.com/a", allow_hosts={"example.com"}, max_bytes=10)


class ZipTests(unittest.TestCase):
    def test_valid_shared_string_workbook(self):
        from energy_security import _xlsx_rows

        shared = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<si><t>July 2026</t></si></sst>"
        )
        sheet = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><v>10.5</v></c></row>'
            "</sheetData></worksheet>"
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("xl/sharedStrings.xml", shared)
            archive.writestr("xl/worksheets/sheet1.xml", sheet)
        rows = _xlsx_rows(buf.getvalue(), "xl/worksheets/sheet1.xml")
        self.assertEqual(rows[0]["A"], "July 2026")
        self.assertEqual(rows[0]["B"], "10.5")

    def test_government_workbook_still_opens(self):
        from safe_zip import open_xlsx

        blob = (ROOT / "data" / "et311.xlsx").read_bytes()
        with open_xlsx(blob) as archive:
            self.assertIn("xl/sharedStrings.xml", archive.namelist())

    def test_hostile_metadata_is_rejected_before_zipfile(self):
        from unittest.mock import patch
        from safe_zip import ZipRejected, open_xlsx

        blob = self._tiny_zip()
        eocd = blob.rfind(b"PK\x05\x06")
        hostile = bytearray(blob)
        hostile[eocd + 12 : eocd + 16] = (2_000_000).to_bytes(4, "little")
        with patch("safe_zip.zipfile.ZipFile", side_effect=AssertionError("ZipFile constructed")):
            with self.assertRaises(ZipRejected):
                open_xlsx(bytes(hostile))
        with patch("safe_zip.zipfile.ZipFile", side_effect=AssertionError("ZipFile constructed")):
            with self.assertRaises(ZipRejected):
                open_xlsx(blob + b"PK\x06\x06")

    def _tiny_zip(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("ok.txt", "fine")
        return buf.getvalue()


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.environ.get("TMPDIR", "/tmp")) / "uk-supply-publish-test"
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir()
        self.repo = self.tmp / "repo"
        self.remote = self.tmp / "remote.git"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        (self.repo / ".gitignore").write_text("data/latest.json\n")
        (self.repo / "web").mkdir()
        (self.repo / "web" / "index.html").write_text("<html>board</html>\n")
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / ".nojekyll").write_text("")
        (self.repo / "docs" / "index.html").write_text("old\n")
        (self.repo / "docs" / "latest.json").write_text("{}\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "init")
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True, capture_output=True, text=True)
        git(self.repo, "remote", "add", "origin", str(self.remote))
        git(self.repo, "push", "-u", "origin", "main")
        self.stub = self.tmp / "refresh.py"
        self.stub.write_text(
            "from pathlib import Path\n"
            "p = Path('data') / 'latest.json'\n"
            "p.parent.mkdir(exist_ok=True)\n"
            "p.write_text('{\"generated_at\":\"fresh\"}\\n')\n"
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_publish_sends_only_public_artifacts(self):
        from publish import publish

        publish(self.repo, refresh_argv=["python3", str(self.stub)], push=True)
        shown = git(self.remote, "ls-tree", "-r", "--name-only", "main").stdout.splitlines()
        self.assertIn("docs/latest.json", shown)
        self.assertNotIn("data/latest.json", shown)
        names = git(self.repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").stdout.splitlines()
        self.assertTrue(set(names) <= {"docs/.nojekyll", "docs/index.html", "docs/latest.json"})

    def test_prestaged_private_file_is_refused(self):
        from publish import PublishError, publish

        (self.repo / "secret.txt").write_text("private\n")
        git(self.repo, "add", "secret.txt")
        with self.assertRaises(PublishError):
            publish(self.repo, refresh_argv=["python3", str(self.stub)], push=True)
        shown = git(self.remote, "ls-tree", "-r", "--name-only", "main").stdout
        self.assertNotIn("secret.txt", shown)


if __name__ == "__main__":
    unittest.main()
