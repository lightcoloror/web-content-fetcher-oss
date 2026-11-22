# Isolated synthetic routing test

This change adds one isolated, offline test for the WCF
`web_content_fetcher.site_strategies` URL classifier: one test method and a
nine-row synthetic fixture. It is a source-only change; no production behavior,
network access or platform integration is added.

## Contents

| path | role |
| --- | --- |
| `tests/test_synthetic_route_links.py` | one `unittest` test method |
| `tests/fixtures/synthetic_route_links.json` | nine synthetic URL rows with expected strategy/route values |
| `docs/synthetic-routing-test.md` | this guide |

`README.md` gains only a short link to this guide.

## Provenance

- Base: public WCF `main` at `873666523e334f23d0d88e87713531729eb541f6`.
- The test file reuses a supplied one-method test. Its assertions and
  classifier import are preserved, and the only change to the method body is
  the fixture filename (`real_links.json` -> `synthetic_route_links.json`).
  The method is wrapped in a standalone `unittest.TestCase`.
- The fixture is synthetic test data only (nine rows). It contains no real
  links, accounts or extracted content.

## Evidence and limits

The supplied isolated evidence for this test is narrow:

- one test method passed over the nine synthetic rows;
- a wrong-expectation control changed one row's expected route, and the
  assertion rejected that row as expected.

That evidence covers offline URL-pattern classification for these nine rows
only. It is not a run of the original 211-test WCF suite, and it does not prove
live platform access, authentication, export/browser routes, real link content
or extraction completeness.

An earlier verification of this candidate ended with overall **FAIL** at the
checker's input-validation step: six input files were supplied while the
inherited checker expected four. That is an input-contract failure, not a
failure of the routing assertions. No overall checker pass is claimed.

## Dependencies and import scope

- The test module itself uses only the Python standard library (`json`,
  `unittest`, `pathlib`). In the isolated check, the classifier
  (`web_content_fetcher.site_strategies`) was loaded through standard-library
  facilities while the package initializer was deliberately bypassed; the
  classifier module's own imports are standard-library (`re`, `urllib.parse`,
  `dataclasses`, `typing`).
- A normal checkout/package invocation of
  `python -m unittest tests.test_synthetic_route_links` imports the real
  `web_content_fetcher` package initializer first, and that initializer imports
  other package modules. The supplied public parent `pyproject.toml` requires
  Python >=3.10 and declares the dependencies `trafilatura`, `readability-lxml`
  and `markdown-it-py`, so a normal checkout/package run may need the project's
  declared dependencies to be installed.
- Package import and the usage command were not executed for this candidate.
  This record does not establish that those third-party dependencies run during
  this test, and it does not establish that they are unnecessary.

## Usage

From a checkout of this branch, which is based on the full public WCF tree:

```text
python -m unittest tests.test_synthetic_route_links
```

No test execution is part of this local candidate record, so the command above
is usage guidance, not a claim that this branch was executed here. The test does
not prove full-package behavior; the rest of WCF is outside this change and was
not exercised.
