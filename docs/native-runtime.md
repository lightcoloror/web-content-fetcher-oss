# Optional private native runtime

The native runtime is an **optional, disabled-by-default** path. Without
`config/local-runtime.json` (ignored by `config/local*.json`), every entrypoint,
extractor and attachment call behaves exactly like the public baseline. Nothing
is downloaded, installed, enabled or validated automatically. This document is
a source-checkout operator contract, **not** an installed-runtime readiness
claim.

Copy `config/native-runtime.example.json` to `config/local-runtime.json` only
after the operator has an already-validated private interpreter, receipt and
(optional) local Magika model. The example is deliberately `"enabled": false`
and its `validation_sha256` is a placeholder; enabling it without real
validation material is expected to fail closed.

## Activation contract

`config/local-runtime.json`:

| field | meaning |
| --- | --- |
| `enabled` | absent or falsey disables the optional path (legacy/default behaviour). Strict `true` is required for activation. Other truthy non-boolean values are invalid: `route`/attachment adopt may proceed to validation, then the `run_worker` policy refuses them; a direct `run_worker` refuses before validation |
| `mode` | `"extraction"` routes `_extract_trafilatura` through the supervisor and suppresses entrypoint re-exec; any other mode re-execs the three entrypoints |
| `python` | absolute path to the validated private interpreter |
| `validation_receipt` | absolute path to an operator-generated JSON receipt |
| `validation_sha256` | SHA-256 of the exact receipt bytes |
| `process_supervisor` | absolute path to a Python file defining the reused `WindowsJob` class (`web_content_fetcher/windows_job.py`) |
| `attachments_enabled` | strict `true` enables private TXT/HTML conversion and is also required by a direct attachment-worker `run_worker` call |
| `attachment_model_directory` | directory already containing `model.onnx` and `config.min.json` |
| `timeout_seconds` | optional, clamped to 0.05..60 for extraction (default 15); a nonnumeric value is invalid config and refuses before validation/supervisor import/spawn |
| `attachment_timeout_seconds` | optional attachment timeout (default 15) |

Receipt JSON:

- `python`: absolute path equal to the resolved configured interpreter;
- `pip_check_exit_code`: `0`;
- `entrypoint_tests_passed`: `true` (operator-attested; this code cannot prove
  it was actually earned);
- `fingerprints`: non-empty object mapping absolute resolved file paths to
  SHA-256. It must include the interpreter and the supervisor path; every listed
  file is re-hashed before use, and `run_worker` also requires the supervisor key.

This project adds no installer, receipt generator or runtime manager. The
operator's existing validation path must produce the receipt; no success is
fabricated and no model or binary is redistributed.

## Behaviour

- Entrypoints (`wcf`, `wcf-ui`, `wcf-mcp`) re-exec into the validated interpreter
  only when the config is enabled and `mode` is not `"extraction"`. Re-entry
  mismatch is an error, not a retry.
- Extraction spawns the allowlisted `web_content_fetcher.extraction_worker` in a
  suspended Windows child, assigns the reused job object, resumes it, bounds the
  request to 8 MiB and the timeout to 0.05..60 seconds, and enforces thread
  limits through environment variables.
- Attachments spawn `web_content_fetcher.attachment_worker` only for local
  `.txt`/`.html`/`.htm` files, require the local model files, and never download.
  The attachment path reuses the extraction config/receipt validation helpers, so
  malformed or non-object config/receipt data is refused rather than left as a
  raw error.
- **Policy permission:** a direct `run_worker` call requires `enabled is True`
  (and `attachments_enabled is True` for the attachment worker) before
  validation, supervisor import or spawn. A hash-valid disabled config is not
  permission. An unsupported worker module remains the existing `ValueError`
  API error.
- **Fail-closed (never falls back):** malformed/mutated configs, receipts,
  interpreter or supervisor fingerprints; non-object receipts or configs;
  unexpected `KeyError`/`TypeError`/`AttributeError` from native route/config/
  receipt/validation code; unbound supervisors; unsupported operating systems
  and oversized requests; supervisor import/create failures; supervisor
  assign/terminate/resume refusals; unknown spawn or child-communication errors
  (`OSError` or any other exception); unconfirmed or secondary timeouts; unknown
  child state; and any cleanup failure (`job.close`, `poll`, `kill` or `wait`).
  Invalid or nonnumeric `timeout_seconds` is invalid config and refuses before
  validation/supervisor import/spawn.
  These raise `RuntimeRefused`/`RuntimeValidationError` and propagate through
  `_extract_trafilatura` and `extract_content` without a local, readability or
  builtin fallback. Unexpected route exceptions are wrapped as `RuntimeRefused`
  by `_extract_trafilatura`; they never become a local fallback.
- **Cleanup before any fallback:** every supervision path attempts `job.close()`
  and, for a live child, `kill()`/`wait()`. All attempts run even when an earlier
  cleanup step raises; if any step fails or the child state cannot be
  established, the operation is refused. A fallback is never taken while a
  child may still be live or of unknown state. The owner-authorized
  `WindowsJob.close` adaptation binds the Win32 `CloseHandle` signature, checks
  the returned BOOL, raises `OSError` on FALSE and retains the handle until
  closure is confirmed, so a CloseHandle failure is now detectable; the earlier
  supplied close discarded the API result, and old-run evidence retains that
  unobserved-status qualification.
- **Operational fallback (explicit, permitted):** only the explicit
  `ExtractionRuntimeError` signal (a known child exit or an invalid worker result
  after completed cleanup) and the explicit `ExtractionTimeoutError` signal (a
  timeout whose termination/cleanup is confirmed) keep the existing explicit
  local/builtin fallback. The metadata/error reason strings remain the old
  public values `RuntimeError` and `TimeoutError`. Unknown `RuntimeError` or
  `TimeoutError` instances are not fallback signals.
- **Explicit rollback:** `WCF_USE_SYSTEM_PYTHON=1` disables all hooks before any
  config read. Setting the config `enabled` to `false` or deleting it also
  restores the public default path.

## Limits and non-claims

- The supervisor is Windows-only. The reused class (with the owner-authorized
  `WindowsJob.close` adaptation; all other methods remain AST-identical to the
  supplied first-party excerpt) sets only
  `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`; the 8 MiB / 60 s bounds are request size
  and timeout, not a Job Object memory limit.
- The attachment worker's socket monkeypatches are code-level mitigations, not
  an OS network sandbox. No OS-level file/network isolation is claimed.
- Fingerprints reduce accidental drift; they do not stop a same-user process
  from rewriting config, receipt, interpreter or model between validation and
  use (TOCTOU), and they do not attest that entrypoint validation actually ran.
- Installed distributions resolve the config under `site-packages/config`, while
  source checkouts resolve `<repo>/config`; this existing behaviour is preserved.
- No real extraction/conversion/model acceptance, installed-runtime activation
  or publication approval is implied by this candidate. The original opt-in
  tests remain opt-in and must not be converted to passing evidence.
- **Verification status in this task:** stage-2 mock/synthetic unit and selected
  public default-path tests were executed under the Owner-approved fixture root
  (99 unit incl. 5 original, 59 public, 12 opt-in skipped) — those are synthetic
  evidence, not installed-runtime acceptance. Native synthetic controls were
  also executed with the fixed Python: actual WindowsJob create/assign/resume/
  terminate/cleanup plus actual `run_worker` success and confirmed-timeout paths
  using a stdlib synthetic worker package; the in-process validation seam is
  explicitly labelled, and supervisor/spawn/resume/communicate/timeout/cleanup
  were real. Real installed runtime, real validation receipt, model, network,
  browser, CLI/UI/MCP service and full OS sandbox behaviour were **not run** and
  are not claimed.
