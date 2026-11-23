# Selected upstream regressions

This opt-in suite checks Trafilatura article extraction and MarkItDown
conversion using synthetic HTML, streams, a mocked HTTP response and a
disposable text fixture. It includes the WCF attachment UTF-8 and digest
checks. Each test temporarily selects the in-process WCF runtime using
`WCF_USE_SYSTEM_PYTHON=1` and restores the environment during cleanup.
Tests that construct the converter substitute the Magika constructor.

For reproduction, use a separate WCF test environment. The extraction case
was attempted with Trafilatura 2.0.0. MarkItDown 0.1.9 is the source reference
for the conversion assertions. From the checkout root, opt in and run this
file specifically:

```sh
WCF_SELECTED_UPSTREAM_TESTS=1 python -m unittest discover -s tests -p test_selected_upstream.py -v
```

In PowerShell, set `$env:WCF_SELECTED_UPSTREAM_TESTS = '1'` in a disposable
shell and run the same Python command without the inline assignment.
Ordinary discovery skips this suite unless you opt in.

The [MarkItDown v0.1.9 source](https://github.com/microsoft/markitdown/blob/21d9c1d01ecc12538dab19811fd247ef2f96ef60/packages/markitdown/src/markitdown/_markitdown.py)
defines the private UTF-8 sampling helper and uses 64 KiB response chunks.
[Version 0.1.5](https://github.com/microsoft/markitdown/blob/v0.1.5/packages/markitdown/src/markitdown/_markitdown.py)
lacks that helper and uses 512-byte response chunks. The assertions on these
behaviors are version-specific. See [Third-Party Notices](../THIRD_PARTY_NOTICES.md).
MarkItDown and Trafilatura remain separate dependencies. This source batch
does not vendor them or change the project's dependency declarations.

With Trafilatura 2.0.0, the first extraction case reported the expected
paragraph twice and failed the single-occurrence assertion. That run stopped
after the failure, leaving the inline-code case unrun. The five conversion
cases were unrun because the available environment has MarkItDown 0.1.5.
The complete suite has no verified passing dependency combination.
Four isolated checks verified setup and environment restoration.
