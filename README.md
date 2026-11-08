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
