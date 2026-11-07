from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from web_content_fetcher.weixin_native_manifest import (
    CAPTURE_SCHEMA_VERSION,
    SCHEMA_VERSION,
    build_native_manifest,
    main,
    materialize_native_manifest,
    merge_accessibility_segments,
    read_json_object,
    validate_native_manifest,
)


WORKBUDDY_ROOT = Path(os.environ.get("WCF_WORKBUDDY_FIXTURE_ROOT") or "__missing_fixture__")
WORKBUDDY_MANIFEST = WORKBUDDY_ROOT / "native_document_manifest.json"


class WeixinNativeManifestTests(unittest.TestCase):
    def test_virtual_scroll_segments_merge_by_overlap_and_report_truncation(self):
        merged, report = merge_accessibility_segments(
            [
                {"anchor_index": 0, "text": "第一段\n共同内容12345"},
                {
                    "anchor_index": 1,
                    "text": "共同内容12345\n第二段",
                    "selection_truncated": True,
                },
            ]
        )
        self.assertEqual(merged, "第一段\n共同内容12345\n第二段")
        self.assertTrue(report["selection_truncation_detected"])
        self.assertEqual(report["truncated_anchor_indexes"], [1])

    def test_build_places_original_resources_in_source_order_without_gallery(self):
        capture = {
            "schema_version": CAPTURE_SCHEMA_VERSION,
            "source": {"platform": "tencent_document"},
            "segments": [
                {
                    "anchor_index": 0,
                    "text": "标题\n说明",
                    "nodes": [
                        {
                            "node_id": "n1",
                            "type": "heading",
                            "text": "标题",
                            "level": 1,
                            "source_order": 1,
                        },
                        {
                            "node_id": "n2",
                            "type": "paragraph",
                            "text": "说明",
                            "source_order": 3,
                        },
                    ],
                }
            ],
            "resources": [
                {
                    "id": "image-0001",
                    "source_order": 2,
                    "local_src": "original_images/one.png",
                    "sha256": "a" * 64,
                    "intrinsic_width": 100,
                    "intrinsic_height": 50,
                }
            ],
        }
        manifest = build_native_manifest(capture)
        self.assertEqual(
            [item["type"] for item in manifest["nodes"]],
            ["heading", "image", "paragraph"],
        )
        self.assertNotIn("gallery", [item["type"] for item in manifest["nodes"]])
        self.assertEqual(len({item["node_id"] for item in manifest["nodes"]}), 3)
        self.assertEqual(manifest["integrity"]["status"], "ok")

    def test_materialize_copies_original_resource_and_keeps_neutral_contract(self):
        temp_root = os.environ.get("WCF_TEST_TMPDIR") or None
        with tempfile.TemporaryDirectory(dir=temp_root) as tmp:
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            image = source / "original_images" / "one.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "nodes": [
                    {"type": "paragraph", "text": "前", "source_order": 1},
                    {
                        "type": "image",
                        "id": "image-0001",
                        "sequence": 1,
                        "local_src": "original_images/one.png",
                        "source_order": 2,
                    },
                    {"type": "paragraph", "text": "后", "source_order": 3},
                ],
                "resources": [
                    {
                        "id": "image-0001",
                        "sequence": 1,
                        "local_src": "original_images/one.png",
                        "sha256": "a" * 64,
                    }
                ],
                "degradations": [],
            }
            result = materialize_native_manifest(
                manifest,
                artifact_root=source,
                output_dir=target,
            )
            self.assertEqual(result["status"], "ok")
            self.assertTrue((target / "original_images" / "one.png").is_file())
            copied = read_json_object(target / "native_document_manifest.json")
            self.assertEqual(copied["schema_version"], SCHEMA_VERSION)

    @unittest.skipUnless(
        WORKBUDDY_MANIFEST.is_file(),
        "local WorkBuddy fixture is unavailable",
    )
    def test_workbuddy_static_fixture_locks_native_completeness(self):
        manifest = read_json_object(WORKBUDDY_MANIFEST)
        report = validate_native_manifest(
            manifest,
            artifact_root=WORKBUDDY_ROOT,
            expected_text_chars=37364,
            expected_images=353,
        )
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["direct_text_chars"], 37364)
        self.assertEqual(report["unique_images"], 353)
        self.assertEqual(report["image_nodes"], 353)
        self.assertEqual(report["local_resource_references"], 353)
        self.assertEqual(report["local_missing_count"], 0)
        self.assertTrue(report["checks"]["source_order_monotonic"])
        self.assertTrue(report["checks"]["gallery_forbidden"])
        self.assertTrue(report["checks"]["mixed_order_not_body_plus_gallery"])

    @unittest.skipUnless(
        WORKBUDDY_MANIFEST.is_file(),
        "local WorkBuddy fixture is unavailable",
    )
    def test_cli_inspect_returns_machine_readable_integrity(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(
                [
                    "inspect",
                    str(WORKBUDDY_MANIFEST),
                    "--artifact-root",
                    str(WORKBUDDY_ROOT),
                    "--expected-text-chars",
                    "37364",
                    "--expected-images",
                    "353",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "ok")


if __name__ == "__main__":
    unittest.main()
