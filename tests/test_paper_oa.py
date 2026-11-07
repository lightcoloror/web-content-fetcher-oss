import contextlib
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from web_content_fetcher import paper_oa


class PaperOATests(unittest.TestCase):
    def test_title_rejected_without_execution(self):
        with patch.object(paper_oa.subprocess, "run") as run:
            self.assertEqual(paper_oa.discover_paper("A title")["status"], "blocked")
            run.assert_not_called()

    def invoke(self, status="found", url="https://example.org/paper.pdf", doi="10.1234/test", fail=None, drift=False):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "fixture.py"
            source.write_bytes(b"# synthetic; never executed\n")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()

            def fake_run(command, **kwargs):
                self.assertIn("--dry-run", command)
                self.assertEqual(command[command.index("--workers") + 1], "1")
                self.assertNotIn("CORE_API_KEY", kwargs["env"])
                self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
                if fail:
                    raise subprocess.TimeoutExpired(command, 1)
                target = Path(command[command.index("--manifest") + 1])
                with target.open("w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=["doi", "status", "url", "source"])
                    writer.writeheader()
                    writer.writerow(dict(doi=doi, status=status, url=url, source="fixture"))
                return subprocess.CompletedProcess(command, 0)

            with patch.object(paper_oa, "REVIEWED_SHA256", "bad" if drift else digest), patch.dict(os.environ, {"UNPAYWALL_EMAIL": "contact@research.test", "CORE_API_KEY": "synthetic"}), patch.object(paper_oa.subprocess, "run", side_effect=fake_run) as run:
                result = paper_oa.discover_paper("10.1234/test", script=str(source))
                if drift:
                    run.assert_not_called()
                return result

    def test_candidate_is_not_fulltext(self):
        result = self.invoke()
        self.assertEqual(result["status"], "candidate_found")
        self.assertFalse(result["fulltext_verified"])
        self.assertFalse(result["pdf_downloaded"])

    def test_no_oa_is_unknown(self):
        self.assertEqual(self.invoke(status="no_oa")["status"], "unknown")

    def test_timeout(self):
        self.assertEqual(self.invoke(fail=True)["reason"], "discovery_timeout")

    def test_source_drift(self):
        self.assertEqual(self.invoke(drift=True)["reason"], "source_changed_review_required")

    def test_wrong_doi(self):
        self.assertEqual(self.invoke(doi="10.1234/other")["reason"], "identity_mismatch")

    def test_signed_url_not_exposed(self):
        result = self.invoke(url="https://example.org/paper.pdf?token=synthetic")
        self.assertEqual(result["status"], "review_required")
        self.assertNotIn("synthetic", json.dumps(result))

    def test_cli_json(self):
        from web_content_fetcher.cli import main
        output = io.StringIO()
        with patch.object(paper_oa, "discover_paper", return_value={"status": "unknown"}), contextlib.redirect_stdout(output):
            self.assertEqual(main(["discover-paper", "10.1234/test"]), 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "unknown")
