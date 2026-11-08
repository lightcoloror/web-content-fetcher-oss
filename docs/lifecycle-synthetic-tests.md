# Lifecycle synthetic tests for the public WCF adapter

Status: **static review and synthetic execution complete.**
Date: 2026-11-09 (Asia/Shanghai).

## Purpose

This document describes the portable public version of the original WCF
lifecycle test suite. The check binds two immutable public snapshots to each
other:

- `web-content-fetcher` (public WCF), public parent commit
  `95b6df66b55726503675b779ab4d8a93cf043902`, Apache-2.0, 111 files,
  1,354,338 bytes;
- `artifact-lifecycle-core` (public Artifact Core), public parent commit
  `fab239085724458c2a6b9452d0a1ba4f4012593e`, Apache-2.0, 36 files,
  352,756 bytes.

No production logic is rewritten. No replacement Core, adapter, schema or
profile is created. The check keeps all ten original test method names and
their semantic assertions, and replaces the original private workstation roots
with an explicit operator-supplied root contract. It never performs automatic
cleanup, purge, quarantine of production archives or network access.

## Public source pins

| Item | Public identity |
| --- | --- |
| WCF parent commit | `95b6df66b55726503675b779ab4d8a93cf043902` |
| WCF adapter blob | `web_content_fetcher/artifact_lifecycle.py` = `ea292c81f1d0939d593e938e0431310e74f1d5cd` (SHA-256 `8228ab6b9321580a892928b410f252d816dc9680509f5e16d848b32e4ccc5a81`) |
| WCF CLI blob | `web_content_fetcher/cli.py` = `5c87e63e4036a2bbfb94ebff4b7ab66f2939d623` (SHA-256 `092eba72cfba9c4348ab02e0f93565f3403ea0d503de97e6da1bf282f5ddcd67`) |
| Core parent commit | `fab239085724458c2a6b9452d0a1ba4f4012593e` |
| Core module blob | `artifact_lifecycle_core.py` = `16381abf9e3480eee78dc89d117d001636f39b4a` (SHA-256 `e0b6ad47beef86a1245389049f6264831e07a42b9cc656fd8264879a7377b297`) |
| Core profile blob | `profiles/artifact-lifecycle-profiles.v1.json` = `61f7659c44704b7c1586c6ce050b8b8a7c33c50c` (SHA-256 `a3067ad998929a2287b48dc9debe7bb4791d394d73535dcdac09915bf92da44b`) |
| Original suite source | git commit `323203398310ac85b2df73536a57285f64723466`, blob `a70bdae5d601993c42664d0805db3879a9a2d457`, SHA-256 `b9343b27c13c596f39c8fbfe013608c5631e297fd767b893fc54e14d5235d4bb` |

## Public scope (exactly four text files)

| Path | Role |
| --- | --- |
| `checks/lifecycle_synthetic.py` | adapted ten-case lifecycle check |
| `docs/lifecycle-synthetic-tests.md` | this document |
| `LIFECYCLE_TESTS_PROVENANCE.json` | machine-readable public provenance |
| `README.md` | baseline README bytes (exact prefix) plus a small link/invocation appendix |

The suite lives under `checks/`, not under the default test directory. The
baseline README promises `python -m pip install ".[test]"` plus
`python -m pytest -q`, while the public Artifact Core is an optional external
dependency. A required-Core check under the default test tree would break that
promise, so this check is explicitly invoked and is not collected by default.

The baseline README bytes are preserved exactly as the prefix of the new
`README.md`; only a small appendix is appended. No other baseline file is
edited. The 147 supplied public dependency files (111 WCF + 36 Core) remain
otherwise immutable.

## Required environment contract

The check has **no private default root and no system-temp fallback**. Missing,
relative or unusable configuration raises a clear error in `setUpClass`; the
tests fail as errors and are never turned into a whole-class pass.

| Variable | Required | Meaning |
| --- | --- | --- |
| `WCF_ARTIFACT_LIFECYCLE_CORE_TEST_ROOT` | yes | absolute path of the public Artifact Core root; must contain `artifact_lifecycle_core.py` and `profiles/artifact-lifecycle-profiles.v1.json` |
| `WCF_TEST_TMPDIR` | yes | absolute path of an existing, operator-owned temporary root; the check creates and removes only `artifact-lifecycle-<uuid>` beneath it |
| `WCF_USE_SYSTEM_PYTHON` | recommended `1` | bypasses the optional private-runtime re-exec bridge in the public snapshot; the re-exec path also cannot activate because the public snapshot contains no `config/local-runtime.json` |
| `PYTHONDONTWRITEBYTECODE` | recommended `1` | keep the public tree free of `__pycache__` |
| `WEB_CONTENT_FETCHER_CONFIG` | must stay unset, or point at an explicit public config | the CLI test builds the real parser, which loads the packaged `default_config.json` when this is unset |

No automatic search outside the operator-specified roots is performed. The
test temp root must not be the public snapshot, an evidence/receipt directory
or any protected input; a disposable operator-owned directory is expected.

### Example invocation (external user)

Run the optional check explicitly. This does not change pytest defaults,
dependency declarations or the default offline command:

```sh
export PYTHONDONTWRITEBYTECODE=1
export WCF_USE_SYSTEM_PYTHON=1
export WCF_ARTIFACT_LIFECYCLE_CORE_TEST_ROOT=<path-to-public-artifact-lifecycle-core>
export WCF_TEST_TMPDIR=<existing-disposable-test-root>
python -m unittest discover -s checks -p lifecycle_synthetic.py -v
```

On Windows, set the same variables with `set`, then run the same
`python -m unittest discover -s checks -p lifecycle_synthetic.py -v` command
from the source checkout. The test temp root must exist before the run. Use an
explicitly selected Python interpreter; the check performs no interpreter
search.

## Isolation and mutation scope

- `setUp` creates one fresh `artifact-lifecycle-<uuid>` directory under
  `WCF_TEST_TMPDIR`, then registers `addCleanup` immediately. Cleanup runs even
  if later setup steps fail.
- Before deletion, cleanup verifies that the target is a real directory (not a
  symlink), that its parent is exactly the configured resolved temp root, and
  that its name has the expected fresh-fixture prefix. It then calls
  `shutil.rmtree` **without** `ignore_errors=True`, so a genuine cleanup
  denial surfaces as a test error instead of being hidden.
- Cleanup removes only that owned child. It never deletes inputs, public
  snapshots, evidence, receipts, originals or a computed parent, and it never
  changes ACLs.
- The hardlink test uses a real `os.link` inside the fresh fixture root. A
  genuine link denial (`OSError`/`PermissionError`) is a test failure and a stop
  condition — it is not caught, skipped or worked around.
- `apply_quarantine` / `restore_from_receipt` run only on synthetic files
  created by the check run inside the marked synthetic root; the production
  WCF CLI has no quarantine or purge command and the check asserts that.

## Explicitly synthetic boundaries (not real integrations)

| Boundary | Representation | What it does **not** prove |
| --- | --- | --- |
| Core `free_space_status` in `setUp` | mocked healthy report (`ok`) for scan/plan gates | does not prove real host free space |
| Core `git_status` | mocked `clean` by default; the dirty subcase overrides it to `dirty` | **not real Git integration**; no `git` subprocess is launched |
| Core `volume_identity` (cross-volume case) | synthetic device ids `1` and `2` | does not prove a real second volume exists |
| Core `assert_no_reparse` (reparse case) | targeted side effect that raises only for `blocked.bin`; every other path calls the real helper | does not create a real reparse point |
| `shutil.disk_usage` (capture tests) | synthetic totals/free bytes | does not measure the real disk |
| `cli.capture_disk_preflight` (CLI forwarding case) | patched to assert the CLI forwards the exact operation/target/estimate | the real preflight arithmetic is covered by direct capture tests |

The check does **not** mock away lifecycle validation, manifest validation,
scan classification, plan construction, verify, quarantine, restore or the CLI
scan; those run through the real public adapter and Core code paths.

Disk-gate arithmetic is preserved as stated by the public adapter: required
free space is the absolute minimum reserve plus rounded estimated output
headroom (`minimum_free_bytes + round-up(estimated_output_bytes × 1.25)`), not
a ratio of the volume. The check asserts that policy shape and the 512 MiB
minimum/estimate values; no claim is made that Core call counts or
homogeneous-entry ordering are defects.

## Ten-case inventory

| # | Original method name (kept) | Preserved semantics |
| --- | --- | --- |
| 1 | `test_successful_referenced_archive_is_protected_and_only_known_ephemera_become_candidates` | authoritative/raw/archive reference pinning; known ephemera (cache) and duplicate conversion candidates; unknown path protected; plan gate `authoritative_reference_present` |
| 2 | `test_plan_fails_closed_for_active_owner_dispatch_dirty_git_and_hardlink_drift` | owner/process/dispatch gates; dirty-Git gate (synthetic); hardlink-coverage drift gate; eligible counts |
| 3 | `test_verify_detects_candidate_drift_and_cross_volume_is_blocked` | digest drift detection on candidate; `CROSS_VOLUME_BLOCKED` fail-closed |
| 4 | `test_reparse_low_disk_and_unknown_paths_fail_closed_or_protect` | `blocked_low_disk`; reparse-point denial surfaces as adapter error |
| 5 | `test_large_capture_disk_gate_blocks_before_browser_or_image_work` | large-capture preflight blocks before browser/image work; CLI forwards disk gate |
| 6 | `test_small_known_capture_warns_but_runs_with_two_gib_free` | 2 GiB free is warning-only for a small known capture; 512 MiB minimum reserve; 512 MiB estimate; no legacy ratio field |
| 7 | `test_capture_policy_matches_shared_profile` | canonical `research_evidence` profile constants match adapter constants; no `minimum_free_ratio` |
| 8 | `test_batch_capture_requires_estimate_and_blocks_when_it_will_not_fit` | batch estimate required; insufficient estimated space blocks |
| 9 | `test_synthetic_restore_requires_marker_and_production_cli_has_no_quarantine_or_purge` | marked synthetic quarantine + positive restore round-trip; real rejection cases for `execute=False`, missing marker and invalid marker each fail and leave the quarantined file unmoved with no restore output written; production CLI exposes no quarantine/purge subcommands |
| 10 | `test_cli_scan_returns_machine_readable_manifest` | CLI `archive lifecycle scan` emits machine-readable JSON, exit 0, `research_evidence` profile; the emitted manifest is re-read and `cache/item.bin` is asserted indexed as `cache_build_test_temp` |

The adapted check keeps all original assertion call sites (the original suite
has 50 assert-prefixed call sites, including one mock `assert_called_once_with`)
and adds only strengthening checks: 60 call sites total (50 original + 10
added). No assertion was removed, relaxed or made unconditional.

## Execution results

The check was executed explicitly with the fixed Python, the supplied public WCF
and Core copies, and both required environment roots. A scoped guard rejected
subprocess `Popen` construction, socket `connect`/`connect_ex` and DNS name
resolution by patching methods on the existing classes; class identities were
preserved, the original attributes were restored, and the guard recorded zero
attempts. No real Git process, socket connection or DNS lookup occurred.

Positive run:

- command: `python -m unittest discover -s checks -p lifecycle_synthetic.py -v`
  (inside the guarded runner);
- suite SHA-256 `312a979679460c6c7beb2fc0003f78823c3c250cb3c2dfd617c3a0711606b4b1`;
- 10 tests run, 10 passed, 0 failures, 0 errors, 0 skips;
- guard attempts 0; class identities preserved; guard attributes restored;
- no fixture directory left beneath the configured test temp root.

Same-suite corrected byte-minimal mutant run. The Owner-confirmed mutant is in
the public WCF adapter's `_classification` referenced branch and was created by
byte-level replacement only in a private scratch copy: the unique span
`b'    if referenced:\n'` (ASCII `    if referenced:` plus LF) was replaced with
`b'    if False and referenced:\n'`.

- Byte evidence: positive adapter 26,885 bytes → corrected mutant 26,895 bytes;
  the old span occurred exactly once (offset 12,839); the mutant file equals
  `positive_bytes.replace(old, new, 1)`; LF count 636 in both, CRLF count 0 in
  both; positive adapter SHA-256
  `8228ab6b9321580a892928b410f252d816dc9680509f5e16d848b32e4ccc5a81`
  unchanged, corrected mutant SHA-256
  `d1ec69cb90d90cd61a0e2475b57ed1fa42370e57f99d73069909eaf9cdd51237`; all
  other files including the suite are byte-identical.
- A text-mode whole-file newline rewrite is not accepted as minimality
  evidence. An earlier private mutant draft used that approach and was rejected
  as non-minimal; it is preserved only as private review history and is not the
  evidence of record.
- Same unmodified suite bytes (SHA-256 identical);
- 10 tests run, 9 passed, exactly 1 failure, 0 errors, 0 skips;
- the expected assertion failed in case 1:
  `assertEqual(raw_snapshot["artifact_role"], "authoritative_input")` produced
  `'unknown' != 'authoritative_input'`;
- the plan gate `authoritative_reference_present` is separately driven by
  `referenced_by`, so it was not expected to fail from this one-line change;
  the referenced artifact's `pin` also remained true through the unknown-path
  fallback, so the pin assertion did not fail. The observed failure is the
  honest, minimal assertion failure for this mutant;
- guard attempts 0; class identities preserved; no fixture left behind.

Execution-count qualification: this task's synthetic executions total 30 test
executions (10 positive + 10 first-mutant + 10 corrected-mutant) across the same
10 distinct cases; the corrected mutant is the evidence of record. An
independent Owner replay reported 10/10 positive and 7 runtime controls passed.

A config-failure control is also proposed: running with
`WCF_ARTIFACT_LIFECYCLE_CORE_TEST_ROOT` unset must raise `RuntimeError` in
`setUpClass`, reporting errors rather than passes or skips. It was not executed
in this run.

## What was and was not verified

Verified in this execution and the preceding static review:

- the positive suite ran through the real public adapter/Core code paths and
  returned 10/10 passing with no skips;
- the same unmodified suite against the corrected byte-minimal private mutant
  failed the expected case-1 authoritative-input assertion;
- the guard observed no process/network attempts, preserved class identities
  and restored patched attributes; no fixture remained after the runs;
- all 111 WCF and 36 Core supplied public dependency files hash-match their
  public facts; the baseline README bytes are preserved exactly as the prefix
  of the appended README; the original suite SHA-256 matches the public blob
  fact `b9343b27c13c596f39c8fbfe013608c5631e297fd767b893fc54e14d5235d4bb`;
- the adapted check keeps all ten method names in the original order with 60
  assertion call sites, no skip marker, no private-root token and no
  system-temp fallback;
- `checks/lifecycle_synthetic.py` does not match the default pytest file
  patterns `test_*.py` or `*_test.py`, and the earlier candidate test module was
  removed from the dependency tree. This is a filename-pattern **static** proof
  only; no pytest collection was executed.

Not verified and not claimed:

- no real Git integration, real disk measurement, reparse point, second volume
  or production cleanup/purge was exercised;
- the process/network guard is a scoped in-process guard, not an OS sandbox or
  an all-access audit;
- missing private source bodies, if any, remain unknown and are not inferred;
- no customer/health data handling or production-readiness claim. This
  synthetic pass does not prove real cleanup safety, customer-data handling or
  OS confinement.

## License and third-party material

The public snapshots remain Apache-2.0 with their own `LICENSE`, `NOTICE` and
`THIRD_PARTY_NOTICES.md` files unchanged. The check adds no third-party assets,
fixtures, media or private history; all fixtures are generated text/bytes at
run time.
