# Explicit real-link smoke wrapper source

The existing optional wrapper now requires explicit `-CorpusPath` and `-OutputRoot`; it no longer discovers an undistributed real-link corpus or an output path embedded in that corpus. Each nonzero Python child exit forces that item's status to `failed` and records `process_exit_code:<value>`, even if JSON stdout claims `ok`. Other CLI arguments, parsed fields, minimum checks, Limit behavior, reporting and final exit policy are retained. Explicit local file operations use literal paths.

An intentionally authorized operator can invoke the source wrapper in a separate PowerShell process:

    pwsh -NoProfile -File scripts/run-real-link-smoke.ps1 -CorpusPath path/to/operator-corpus.json -OutputRoot path/to/new-output

No real corpus is included. Its `links` array contains objects with `id`, `category`, `url` and optional `minimum` text_chars/images/tables, expected_strategy and expected_route. Only use URLs and authenticated content already authorized for that operation. Expected strategy/route are report fields, not assertions; the existing script does not validate them against the observed route.

The wrapper intentionally calls `python -m web_content_fetcher.cli fetch-doc` and can write archives and summaries. Optional CompareFeishuExport/Headless flags may involve browser/export tooling. This source publication authorizes none of those live operations. No network, logged-in browser, account, export, real corpus, actual native Python process or complete wrapper was run for this increment.

## Synthetic regression

    pwsh -NoProfile -File tests/test_real_link_smoke_exit.ps1 -Candidate scripts/run-real-link-smoke.ps1 -Case All

The driver reads source as AST data and executes only extracted per-item logic and summary assignment using verified Function substitutes. Directory creation is a no-op; archive-file reads are forbidden; no real Python child is resolved. Seven behavior controls and three static/condition controls passed on PowerShell7.5.2: nonzero/JSON-ok, zero/JSON-ok, JSON-failed, malformed JSON, Function-throws, below-minimum, flag forwarding; mandatory parameters, Limit branch and exit-policy AST conditions. The final source process exit blocks are not executed by these controls. The same NonzeroJsonOk checker exits0 on the candidate and1 on a bad copy removing only the aggregation guard, failing for the expected status assertion.

## Existing limits

Summary top-level `status:ok` still means the summary was generated; consult failed/below_minimum and the exit policy, rather than treating it as successful extraction. The original policy is failed>0 =>2, otherwise below_minimum>0 =>1, otherwise0. Zero cases are not coverage. Merged stdout/stderr can make JSON parsing fail. The script overwrites its process PYTHONPATH, uses second-resolution run directories and may reuse a colliding directory; use a fresh output root and a separate process. This increment adds no rollback, transactional archive handling, unique-directory guarantee, service authentication, offline safety or content correctness proof.

Whole-wrapper execution, actual CLI option/runtime compatibility, real native exit propagation, archive extraction, authenticated pages, Windows PowerShell5.1 and business outcomes remain unverified. No dependencies, browser/renderer binaries, credentials or source lists are added. Apache-2.0 covers this first-party source, not external services or upstream assets.
