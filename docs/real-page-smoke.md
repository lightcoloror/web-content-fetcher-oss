# Real-page smoke helper

`scripts/check_real_pages.py` is an **operator-run source-checkout helper**, not
an installed-wheel entry point and not an automated unit test. It runs the
project's existing `web_content_fetcher` CLI for an explicit operator-supplied
configuration and records what happened. Running the helper is an operator
action against URLs the operator has permission to access; the helper itself
does not grant network permission, and this page makes no live-readiness claim.

## Provenance

- Upstream project: `web-content-fetcher` (Apache-2.0, Copyright 2026
  lightcoloror).
- Adapted from the first-party source blob: commit
  `323203398310ac85b2df73536a57285f64723466`, blob
  `87195e212f3a1174172b6909ffcad063fd168ea7`, path
  `scripts/check_real_pages.py`.
- Baseline public tree parent: `d1c9811cfb76d2d4f6384037d37acad890c51b24`.
- Original blob SHA-256:
  `6076f9501d1ff0647db6e212e61aa49494aad6057a93295964dcd4347266600f`
  (8,320 bytes).
- The adaptation keeps the first-party `run_case`, `evaluate_case`,
  `render_markdown` and `main` contracts and makes a minimal behaviour
  adaptation for explicit input validation and controlled failures. The full
  provenance record is `REAL_PAGE_SMOKE_PROVENANCE.json`.

## No supplied live material

This page and the helper ship no real configuration, no live URLs and no
output reports. Examples below use `example.invalid`, which must not resolve
and must never be replaced by a real page in a public file. Operators supply
their own config outside version control.

## Operator configuration

`--config` is required. There is no built-in default corpus. A minimal config:

```json
{
  "cases": [
    {
      "id": "example-page",
      "url": "https://example.invalid/page",
      "required": true,
      "timeout_seconds": 20,
      "min_extracted_chars": 200,
      "selectors": ["h1", {"selector": "main p"}]
    }
  ]
}
```

Supported case fields, with the validated shape:

- `id` — required; non-empty portable archive directory name. Slash, backslash,
  colon, `<`, `>`, double quote, pipe, question mark, star, control characters,
  dot segments, leading/trailing whitespace, a trailing dot and Windows reserved
  basenames are rejected. Safe Unicode is retained, but ids that collide after
  NFC normalisation plus case folding are rejected before any case runs.
- `url` — required; absolute `http` or `https` URL with an actual hostname.
  Malformed authorities (including malformed IPv6) and host-less authorities
  (for example a port with no hostname) are rejected as invalid config.
- `required` — optional boolean; defaults to `false`.
- `allow_status_fail` — optional boolean; permits a non-`ok` response status.
- `use_web_access`, `prefer_browser` — optional booleans forwarded to the CLI.
- `timeout_seconds` — optional positive integer; falls back to `--timeout`.
- `cache_max_age_seconds` — optional non-negative integer; defaults to `900`.
- `min_extracted_chars`, `min_selector_results` — optional non-negative
  integers; `0` disables the threshold.
- `selectors` — optional list of selector strings or of objects with a
  non-empty string `selector`.
- `expect_site_profile` — optional expected `site_data.site_profile` value.
- `fetch_profile` — optional CLI fetch profile; defaults to `generic`.
- `notes` — optional free-form operator note included in the report.

The whole config is validated before any child process runs. Malformed shapes,
empty case lists, malformed or host-less URLs, invalid portable ids, invalid
numeric/boolean field types, invalid selectors, non-positive timeouts and
negative limits exit `2` before any fetch or report write.

## Usage

```sh
python scripts/check_real_pages.py --config /path/to/operator-config.json
```

Options keep the ordinary operator-local semantics:

- `--config` (required) — explicit operator config file.
- `--output` — where to write the JSON report; the Markdown summary is written
  next to it with an `.md` suffix.
- `--timeout` — default per-case CLI timeout in seconds, positive integer.
- `--limit` — run only the first N validated cases; `0` means all.
- `--strict` — treat optional case failures as a failure exit.
- `--no-archive` — do not pass an archive directory.

Exit codes: `0` when required cases pass (and optional cases pass in strict
mode), `1` when the run failed the selected contract, `2` for invalid CLI or
config input before any fetch.

## Report sensitivity

The JSON report and Markdown summary are **diagnostic, operator-local
artifacts**. They are sensitive by design: they may contain the checked URLs,
the generated child command line, stderr excerpts and archive paths. They are
intentionally not claimed to be path-free or URL-free, they are never published
automatically, and they must not be shipped as public fixtures. Treat them like
local logs.

A child timeout is reported as a bounded case failure. Child output is decoded
as strict UTF-8: invalid UTF-8 bytes are reported as a bounded case failure
instead of being silently replaced. A child response must be a JSON object with
sane `site_data`/`selector_results` shapes and, when present, a string
`extracted_text`; malformed responses are controlled case failures rather than
false-green results. A real operating-system or permission failure while
launching the child is preserved and stops the run instead of being converted
into a soft case failure, so an environment refusal cannot look green.

## Synthetic tests

`tests/test_real_pages_synthetic.py` is a stdlib-only suite written to exercise
the helper with explicit in-memory fakes for `CompletedProcess`, the monotonic
clock and `Path` reads/writes/`mkdir`. It is written to use no temporary
directories, no filesystem writes, no child process, no socket and no HTTP, and
to block accidental external attempts through class-preserving guards. It is a
source-level contract check, not live-network acceptance; actual document
contents, transports and public pages remain operator-supplied unknowns.

## License

Apache-2.0. See `LICENSE`, `NOTICE` and `THIRD_PARTY_NOTICES.md`.
