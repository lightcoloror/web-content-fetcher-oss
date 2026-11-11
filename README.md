# Web Content Fetcher

Web Content Fetcher extracts structured text and evidence metadata from web pages. It provides a Python library, a CLI and optional adapters for separately configured local tools.

## Optional private native runtime

The optional private native runtime is disabled by default and is not an
installed-runtime readiness claim. Source-checkout operators supply their own
actual validation material; see [docs/native-runtime.md](docs/native-runtime.md)
before enabling it.

## Install

```sh
python -m pip install .
wcf --help
wcf fetch https://example.com/ --format json
```

The fetch example makes a network request. Use pages you have permission to access. Browser, document conversion and other adapters require their own dependencies and configuration. The sample configuration is in [config](config/web_content_fetcher.example.json).

For offline checks, install `python -m pip install ".[test]"` and run `python -m pytest -q` in the source checkout. The included suite exercises synthetic text, mocked transports and archive behavior. Keep captures and browser profiles outside version control.

The first public source edition carries the library and supported offline fixtures. Local corpus fixtures, workspace wiring and historical operational records are maintained separately.

Apache-2.0. See [LICENSE](LICENSE), [NOTICE](NOTICE), and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). The public edition adapts the original packaging and documentation and selects portable tests.

## Windows source launcher

See [the source-checkout launcher and synthetic contract checks](docs/windows-ui-launcher.md) for explicit Python selection, native exit-code propagation and caller environment restoration. Running the launcher starts the UI; the synthetic check uses a fake module and starts no service.

The Windows source-checkout cmd launcher also forwards UI arguments, restores
the caller directory/environment and preserves the native Python exit code.
See [Windows launcher contracts](docs/windows-ui-launcher.md); checks use
synthetic modules and do not start the real UI.

## Discovery metadata

The [tool manifest](agent-tool-manifest.v1.json) describes existing entry points without granting execution authority. See [scope and metadata-only tests](docs/agent-tool-manifest.md). Platform labels and pinned public contract references do not prove live readiness or runtime interoperability.

## Real-page smoke helper

`scripts/check_real_pages.py` is an operator-run source-checkout helper for the
existing `fetch` CLI. It is not an installed-wheel entry point and not
automated test infrastructure. An operator supplies an explicit `--config`
file with `http(s)` cases such as `https://example.invalid/page`; there is no
built-in default corpus. The helper validates the whole config before running
any case, records a JSON report plus a Markdown summary, and exits `0`, `1` or
`2` for pass, contract failure, or invalid input.

The report is diagnostic and operator-local: it may contain the checked URLs,
the child command line, stderr excerpts and archive paths. It is not published
automatically and is not claimed to be scrubbed; ship no real reports or
configs. Running the helper is an operator action and implies no network
permission. See [docs/real-page-smoke.md](docs/real-page-smoke.md) for the
config shape, exit codes and report-sensitivity notes, and
[REAL_PAGE_SMOKE_PROVENANCE.json](REAL_PAGE_SMOKE_PROVENANCE.json) for the
attribution record.

`tests/test_real_pages_synthetic.py` describes the helper contract with
in-memory fakes only: no temporary files, child process, socket or HTTP. It is
a source-level check, not a live-page acceptance run.

## Optional lifecycle synthetic check

The optional lifecycle check against a separately supplied public Artifact Core
is explicit: it is not part of the default `python -m pip install ".[test]"` +
`python -m pytest -q` offline run above. It requires both environment roots and
adds no pytest configuration, pytest plugin or package dependency:

```sh
export WCF_ARTIFACT_LIFECYCLE_CORE_TEST_ROOT=<path-to-public-artifact-lifecycle-core>
export WCF_TEST_TMPDIR=<existing-disposable-test-root>
python -m unittest discover -s checks -p lifecycle_synthetic.py -v
```

See [docs/lifecycle-synthetic-tests.md](docs/lifecycle-synthetic-tests.md).

## Optional upstream regressions

See [Selected upstream regressions](docs/selected-upstream-regressions.md) for the opt-in synthetic Trafilatura and MarkItDown tests and version requirements.
