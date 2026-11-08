import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "agent-tool-manifest.v1.json"


def load_manifest() -> tuple[dict[str, object], str]:
    raw = MANIFEST.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


class AgentToolManifestTests(unittest.TestCase):
    def test_metadata_only_authority_and_portability(self) -> None:
        manifest, _ = load_manifest()
        self.assertEqual("agent-tool-manifest.v1", manifest["schema_version"])
        self.assertEqual("web-content-fetcher", manifest["id"])
        self.assertIs(True, manifest["notTrustedAsInstruction"])
        self.assertEqual("none_metadata_only", manifest["executionAuthority"])
        self.assertIs(False, manifest["health"]["proves_current_online"])
        self.assertEqual("Apache-2.0", manifest["license"]["spdx"])
        self.assertIs(True, manifest["portability"]["canonical_cross_platform_entry"])

    def test_loader_is_deterministic_and_paths_are_portable(self) -> None:
        first, first_hash = load_manifest()
        second, second_hash = load_manifest()
        self.assertEqual(first, second)
        self.assertEqual(first_hash, second_hash)
        serialized = json.dumps(first, sort_keys=True)
        self.assertNotIn("C:\\\\Users\\\\", serialized)
        self.assertNotIn("D:\\\\", serialized)

    def test_doctor_is_read_only_and_mcp_is_explicit_start(self) -> None:
        manifest, _ = load_manifest()
        self.assertEqual("python -m web_content_fetcher.cli doctor bridges", manifest["health"]["doctor"]["command"])
        self.assertEqual("read_only_local_doctor", manifest["interfaces"]["canonical"][1]["mode"])
        self.assertEqual("explicit_start", manifest["interfaces"]["mcp"][0]["mode"])


if __name__ == "__main__":
    unittest.main()
