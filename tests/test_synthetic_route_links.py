"""Isolated synthetic-fixture routing assertion for the WCF site strategies.

Standalone copy of one supplied test method. The method, assertions and
classifier import are kept verbatim; only the fixture filename was changed from
``real_links.json`` to ``synthetic_route_links.json`` and the method was wrapped
in its own ordinary ``unittest.TestCase`` module.
"""

import json
import unittest
from pathlib import Path


class SyntheticRouteLinksTest(unittest.TestCase):
    def test_real_link_fixture_matches_site_strategies_without_network(self):
        from web_content_fetcher.site_strategies import classify_site_strategy

        fixture_path = Path(__file__).parent / "fixtures" / "synthetic_route_links.json"
        corpus = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(corpus["links"]), 5)
        self.assertTrue(all(not item.get("login_required") for item in corpus["links"]))
        for item in corpus["links"]:
            with self.subTest(link=item["id"]):
                strategy = classify_site_strategy(item["url"])
                self.assertEqual(strategy.site_key, item["expected_strategy"])
                self.assertEqual(strategy.kind, item["expected_route"])
                self.assertIn("minimum", item)


if __name__ == "__main__":
    unittest.main()
