"""Offline business regressions; the scoped runner forbids network and children."""
from __future__ import annotations

import gzip
import io
import json
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from web_content_fetcher import archive, batch, cli, core


class ArchiveOwnerFixTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(os.environ["TEST_TMP_ROOT"]) / self.id().split(".")[-1]
        self.root.mkdir(parents=True, exist_ok=False)
        self.url = "https://example.test/article?a=1&b=2"

    def result(self, url=None, text=None, archive_dir="", status=200, mime="text/plain", data=None):
        url = url or self.url
        raw = core.DirectPayload(data if data is not None else (text if text is not None else "Synthetic article evidence. " * 40).encode(), mime, status)
        return core.structure_content(core.FetchRequest(url=url, archive_dir=archive_dir, check_robots=False, backend="direct", extractor="builtin"), raw, raw.payload["detected_mime"], url, "direct", [])

    def test_empty_http_200_is_transport_success_not_content_success(self):
        result = self.result(text="  \n ")
        self.assertTrue(result.transport_ok)
        self.assertFalse(result.content_ok)
        self.assertNotEqual(result.status, "ok")
        self.assertEqual(result.to_dict()["http_status"], 200)

    def test_binary_pdf_mislabeled_html_retains_original_bytes_and_digest(self):
        data = b"%PDF-1.7\n\x00\xff synthetic bytes\n%%EOF"
        result = self.result(data=data, mime="text/html", archive_dir=str(self.root))
        self.assertFalse(result.content_ok)
        self.assertTrue(result.transport_ok)
        self.assertTrue(result.payload["mime_conflict"])
        self.assertEqual(result.extracted_text, "")
        path = Path(result.archive["raw_snapshot_path"])
        self.assertEqual(path.suffix, ".pdf")
        self.assertEqual(path.read_bytes(), data)
        self.assertEqual(archive.sha256_file(path), result.raw_sha256)
        saved = json.loads(Path(result.archive["structured_path"]).read_text(encoding="utf-8"))
        self.assertFalse(saved["content_ok"])
        self.assertEqual(next(f for f in saved["archive_index"]["files"] if f["role"] == "raw_snapshot")["media_type"], "application/pdf")

    def test_html_mislabeled_pdf_extracts_text_and_records_conflict(self):
        result = self.result(data=("<!doctype html><html><body><article>" + "Synthetic text. " * 60 + "</article></body></html>").encode(), mime="application/pdf")
        self.assertTrue(result.content_ok)
        self.assertFalse(result.payload["binary"])
        self.assertTrue(result.payload["mime_conflict"])

    def test_png_and_unknown_binary_are_not_decoded_as_body(self):
        for data in (b"\x89PNG\r\n\x1a\n\xff\x00", b"binary\0\xffdata"):
            with self.subTest(data=data[:4]):
                result = self.result(data=data, mime="text/plain")
                self.assertFalse(result.content_ok)
                self.assertEqual(result.extracted_text, "")

    def test_plain_text_no_longer_truncates_at_12000_characters(self):
        result = self.result(text="A structured source paragraph.\n" * 1000)
        self.assertEqual(len(result.extracted_text), len("A structured source paragraph.\n" * 1000))

    def test_http_error_response_never_becomes_success_or_browser_retry(self):
        for code in (429, 500, 503):
            with self.subTest(code=code):
                error = HTTPError(self.url, code, "Synthetic", {"content-type": "text/plain"}, io.BytesIO(b"temporarily unavailable " * 40))
                with patch.object(core.urllib.request, "urlopen", side_effect=error), patch.object(core, "browser_fetch") as browser:
                    result = core.fetch_content(core.FetchRequest(self.url, check_robots=False, backend="auto"))
                browser.assert_not_called()
                self.assertEqual(result.http_status, code)
                self.assertFalse(result.transport_ok)
                self.assertFalse(result.content_ok)
                self.assertEqual(result.status, "failed")

    def test_direct_byte_and_gzip_limits_are_explicit(self):
        class Response(io.BytesIO):
            headers = {"content-type": "text/plain"}
            def getcode(self): return 200
            def geturl(self): return "https://example.test/file"
        for compressed in (False, True):
            response = Response(gzip.compress(b"a" * 100000) if compressed else b"b" * 501)
            response.headers = {"content-type": "text/plain", "content-encoding": "gzip" if compressed else ""}
            with patch.object(core.urllib.request, "urlopen", return_value=response):
                raw, mime, url = core.direct_fetch(core.FetchRequest(self.url, max_bytes=500))
            self.assertEqual(len(raw.raw_bytes), 500)
            self.assertTrue(raw.payload["truncated"])
            result = core.structure_content(core.FetchRequest(url, check_robots=False), raw, mime, url, "direct", [])
            self.assertFalse(result.content_ok)

    def test_url_file_bom_blank_comment_and_exact_query_identity(self):
        path = self.root / "urls.txt"
        path.write_text("\ufeff# comment\n\n" + self.url + "\n", encoding="utf-8")
        self.assertEqual(batch.read_url_list(str(path)), [self.url])
        request = batch.BatchFetchRequest([self.url])
        self.assertNotEqual(batch._request_identity(request), batch._request_identity(replace(request, urls=[self.url + "0"])))
        self.assertNotEqual(batch._request_identity(request), batch._request_identity(replace(request, extractor="builtin")))

    def test_crash_resume_reuses_archived_success_and_is_idempotent(self):
        request = batch.BatchFetchRequest([self.url, self.url + "0"], archive_dir=str(self.root), as_documents=False, backend="direct", check_robots=False)
        first = self.result(archive_dir=str(self.root))
        with patch.object(batch, "fetch_content", side_effect=[first, KeyboardInterrupt()]):
            with self.assertRaises(KeyboardInterrupt):
                batch.fetch_batch(request)
        checkpoint = next((self.root / "batches").iterdir()) / "checkpoint.json"
        saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        self.assertEqual(saved["attempts"], 2)
        self.assertEqual(saved["items"][0]["requested_url"], self.url)
        second = self.result(url=self.url + "0", archive_dir=str(self.root))
        with patch.object(batch, "fetch_content", return_value=second) as fetch:
            result = batch.fetch_batch(replace(request, resume_from=str(checkpoint)))
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(fetch.call_args.args[0].url, self.url + "0")
        self.assertEqual(result["ok"], 2)
        with patch.object(batch, "fetch_content") as fetch:
            replay = batch.fetch_batch(replace(request, resume_from=str(checkpoint)))
        fetch.assert_not_called()
        self.assertTrue(replay["completed"])

    def test_budget_stop_and_resume_keeps_cumulative_attempts(self):
        request = batch.BatchFetchRequest([self.url, self.url + "0"], archive_dir=str(self.root), as_documents=False, max_attempts=1)
        with patch.object(batch, "fetch_content", return_value=self.result(archive_dir=str(self.root))) as fetch:
            result = batch.fetch_batch(request)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(result["stop_reason"], "attempt_budget_exhausted")
        self.assertEqual(result["pending"], 1)
        self.assertIn(self.url + "0", Path(result["batch_report"]["rerun_urls_path"]).read_text(encoding="utf-8").splitlines())
        with patch.object(batch, "fetch_content") as fetch:
            resumed = batch.fetch_batch(replace(request, resume_from=result["checkpoint_path"]))
        fetch.assert_not_called()
        self.assertFalse(resumed["completed"])

    def test_time_exhaustion_admits_no_work(self):
        with patch.object(batch, "fetch_content") as fetch:
            result = batch.fetch_batch(batch.BatchFetchRequest([self.url], max_batch_seconds=0.5, save_report=False))
        fetch.assert_not_called()
        self.assertEqual(result["stop_reason"], "time_budget_exhausted")

    def test_elapsed_budget_stops_next_item_and_caps_timeout(self):
        now = [0.0]
        def fetch(request):
            self.assertEqual(request.timeout_seconds, 2)
            now[0] = 3.0
            return self.result(url=request.url)
        with patch.object(batch.time, "monotonic", side_effect=lambda: now[0]), patch.object(batch, "fetch_content", side_effect=fetch) as mocked:
            result = batch.fetch_batch(batch.BatchFetchRequest([self.url, self.url + "0"], as_documents=False, save_report=False, max_batch_seconds=2))
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(result["stop_reason"], "time_budget_exhausted")
        self.assertEqual(result["pending_urls"], [self.url + "0"])

    def test_exact_duplicate_reuses_success_but_distinct_query_does_not(self):
        with patch.object(batch, "fetch_content", side_effect=lambda req: self.result(url=req.url)) as fetch:
            result = batch.fetch_batch(batch.BatchFetchRequest([self.url, self.url, self.url + "0"], as_documents=False, save_report=False))
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(result["items"][1]["reused_from_index"], 1)
        self.assertEqual(result["ok"], 3)

    def test_checkpoint_survives_report_failure(self):
        original = batch.write_batch_report
        def report(data, *args, **kwargs):
            if data.get("status") != "running":
                raise OSError("synthetic report write interruption")
            return original(data, *args, **kwargs)
        with patch.object(batch, "write_batch_report", side_effect=report), patch.object(batch, "fetch_content", return_value=self.result(archive_dir=str(self.root))):
            result = batch.fetch_batch(batch.BatchFetchRequest([self.url], archive_dir=str(self.root), as_documents=False))
        saved = json.loads(Path(result["checkpoint_path"]).read_text(encoding="utf-8"))
        self.assertEqual(len(saved["items"]), 1)
        self.assertTrue(saved["items"][0]["content_ok"])
        self.assertIn("report_error", result)

    def test_same_cause_circuit_stops_and_empty_body_not_counted_ok(self):
        for code, text in ((429, "service unavailable " * 40), (500, "service unavailable " * 40), (200, "")):
            with self.subTest(code=code), patch.object(batch, "fetch_content", side_effect=lambda req: self.result(url=req.url, status=code, text=text)) as fetch:
                result = batch.fetch_batch(batch.BatchFetchRequest([self.url + str(i) for i in range(6)], as_documents=False, save_report=False, same_error_limit=2))
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(result["stop_reason"], "same_cause_circuit_open")
            self.assertEqual(result["ok"], 0)

    def test_resume_rejects_identity_digest_and_archive_drift(self):
        request = batch.BatchFetchRequest([self.url], archive_dir=str(self.root), as_documents=False)
        with patch.object(batch, "fetch_content", return_value=self.result(archive_dir=str(self.root))):
            result = batch.fetch_batch(request)
        resume = replace(request, resume_from=result["checkpoint_path"])
        with patch.object(batch, "fetch_content") as fetch:
            with self.assertRaisesRegex(ValueError, "identity"):
                batch.fetch_batch(replace(resume, urls=[self.url + "0"]))
            raw = Path(result["items"][0]["archive_proof"][1]["path"])
            raw.write_bytes(b"drift")
            with self.assertRaisesRegex(ValueError, "drifted"):
                batch.fetch_batch(resume)
            checkpoint = Path(result["checkpoint_path"])
            checkpoint.write_text('{"checkpoint_sha256":"wrong"}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash_mismatch"):
                batch.fetch_batch(resume)
        fetch.assert_not_called()

    def test_atomic_failure_preserves_previous_checkpoint(self):
        path = self.root / "checkpoint.json"
        archive.atomic_write_json(path, {"version": "trusted"})
        with patch.object(archive.os, "replace", side_effect=OSError("synthetic interrupted replace")):
            with self.assertRaises(OSError):
                archive.atomic_write_json(path, {"version": "new"})
        self.assertEqual(json.loads(path.read_text())["version"], "trusted")

    def test_cli_passes_budget_and_resume_contract(self):
        with patch.object(batch, "fetch_batch", return_value={"status": "blocked"}) as fetch, patch.object(cli, "_large_artifact_disk_gate"), patch("sys.stdout", new_callable=io.StringIO):
            cli.main(["fetch-batch", "--url", self.url, "--resume-from", "checkpoint.json", "--max-attempts", "4", "--max-batch-seconds", "12", "--same-error-limit", "2"])
        request = fetch.call_args.args[0]
        self.assertEqual((request.resume_from, request.max_attempts, request.max_batch_seconds, request.same_error_limit), ("checkpoint.json", 4, 12, 2))
