# Tool discovery metadata

`agent-tool-manifest.v1.json` describes existing WCF entry points. It grants no execution authority: loading it must not run its command strings, start services, fetch references, or opt into authenticated browser access.

The canonical CLI and optional MCP/UI commands are declared interfaces. Windows, Linux and macOS are target labels, not evidence of tested parity. Optional UI wrappers require an operator's explicit start; they were not executed for this increment. The doctor is intended to inspect local package/bridge readiness and can expose local paths. Its transitive imports were not exhaustively audited or executed here; metadata does not prove current online availability or absence of all side effects.

The material manifest reference is pinned to the public [portable contract bundle](https://github.com/lightcoloror/codex-local-governance-oss/blob/ba0bca6632979a35881583d531f4637593e2e66c/contracts/portable/v1/portable-contract-bundle.v1.json). It is a public metadata reference snapshot and proposal-only reference envelope, not the complete private canonical validator, a download instruction, or proof of interoperability. The discovery schema is separately available in [artifact-lifecycle-core](https://github.com/lightcoloror/artifact-lifecycle-core-oss/blob/fab239085724458c2a6b9452d0a1ba4f4012593e/schemas/agent-tool-manifest.v1.schema.json).

The source provenance records fixed first-party bytes and historical upstream revisions. Those revisions alone do not establish new licensing or runtime acceptance. Existing repository LICENSE, NOTICE and third-party notices remain applicable; no external code, binary, browser profile or private contract is added here.

Run the three metadata-only regression cases from a source checkout:

```console
python -B tests/test_agent_tool_manifest.py -v
```

They load JSON and test selected metadata constraints; they do not run any command declared in the manifest. Schema validation and independent semantic negative controls were also performed for the release candidate. These checks do not prove live fetching, doctor behavior, installed-package inclusion, CMS/browser startup, authorization or publishing capability. No dependencies, runtime routing, defaults or version number changed.
