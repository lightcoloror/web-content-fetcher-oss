"""Optional, reviewed external OA discovery. Never download or certify a PDF."""
from __future__ import annotations

import csv
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit, urlunsplit

REVIEWED_SHA256 = "e1145feb3a9449e686b1722c25fe7314bce8f591b47365cff57b8a804d742427"


def discover_paper(doi: str, *, script: str = "", timeout: int = 120) -> dict:
    result = {"schema_version": "wcf-paper-discovery.v1", "status": "blocked",
              "candidates": [], "fulltext_verified": False, "pdf_downloaded": False,
              "adapter": "paper-fetch-oa", "source_sha256": REVIEWED_SHA256}

    def finish(status: str, reason: str) -> dict:
        return {**result, "status": status, "reason": reason}

    value = re.sub(r"^https://(?:dx\.)?doi\.org/", "", doi.strip(), flags=re.I)
    if len(value) > 512 or not re.fullmatch(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9<>\[\]+]+", value):
        return finish("blocked", "exact_doi_required_title_matching_not_trusted")
    if not 1 <= timeout <= 300:
        return finish("blocked", "timeout_out_of_range")
    source = script or os.environ.get("WCF_PAPER_OA_SCRIPT", "")
    if not source:
        return finish("blocked", "paper_oa_script_not_configured")
    path = Path(source).resolve()
    try:
        if path.stat().st_size > 1_000_000 or hashlib.sha256(path.read_bytes()).hexdigest() != REVIEWED_SHA256:
            return finish("blocked", "source_changed_review_required")
    except OSError:
        return finish("blocked", "source_unavailable")
    email = os.environ.get("UNPAYWALL_EMAIL", "").strip()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or email.lower().endswith(("@example.org", "@example.com")):
        return finish("blocked", "real_contact_email_required")

    # Upstream dry-run still uses the network and writes CSV. Isolate those writes;
    # omit optional account keys and never relay raw stdout/stderr or signed URLs.
    env = os.environ.copy()
    for key in ("CORE_API_KEY", "S2_API_KEY", "PYTHONPATH", "PYTHONHOME"):
        env.pop(key, None)
    try:
        with tempfile.TemporaryDirectory(prefix="wcf-paper-discovery-") as temp:
            manifest = Path(temp) / "manifest.csv"
            completed = subprocess.run(
                [sys.executable, "-I", "-B", str(path), value, "--dry-run", "--workers", "1",
                 "--out", temp, "--manifest", str(manifest)],
                cwd=temp, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=timeout, check=False,
            )
            if completed.returncode != 0:
                return finish("unknown", "upstream_execution_failed")
            if not manifest.is_file() or manifest.stat().st_size > 65536:
                return finish("unknown", "invalid_discovery_manifest")
            with manifest.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            if len(rows) != 1 or rows[0].get("doi", "").lower() != value.lower():
                return finish("unknown", "identity_mismatch")
            row = rows[0]
            if row.get("status") != "found":
                return finish("unknown", "no_candidate_or_provider_failure_not_distinguishable")
            url = urlsplit(row.get("url", ""))
            if url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password:
                return finish("unknown", "invalid_candidate_url")
            # Query-bearing links may contain credentials; do not turn a stripped
            # URL into a supposedly executable download target.
            if url.query or url.fragment:
                return finish("review_required", "candidate_url_contains_query_or_fragment")
            result["candidates"] = [{"doi": value, "url": urlunsplit(url),
                                     "source": row.get("source", ""),
                                     "identity_status": "not_verified", "license_status": "unknown"}]
            result["coverage"] = "first_candidate_only_upstream_dry_run"
            result["next_action"] = "verify_identity_version_license_then_use_existing_acquisition_route"
            return finish("candidate_found", "not_downloaded_not_fulltext_evidence")
    except subprocess.TimeoutExpired:
        return finish("unknown", "discovery_timeout")
    except (OSError, UnicodeError, csv.Error, ValueError):
        return finish("unknown", "discovery_failed")
