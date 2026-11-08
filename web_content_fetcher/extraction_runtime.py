"""Extraction-only subprocess bridge; browser orchestration stays in the caller."""
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from .runtime_entry import validate_runtime


class RuntimeRefused(RuntimeError):
    """Fail-closed refusal: never fall back to another module or interpreter."""


class RuntimeValidationError(RuntimeRefused):
    """The configured runtime is missing, malformed, mutated or unvalidated."""


class ExtractionRuntimeError(RuntimeError):
    """Known operational child failure (nonzero exit or invalid result).

    Fallback is permitted only for this explicit signal. ``reason`` preserves the
    old public metadata/error reason string.
    """

    reason = 'RuntimeError'


class ExtractionTimeoutError(TimeoutError):
    """Confirmed-cleanup extraction timeout; fallback is permitted only here."""

    reason = 'TimeoutError'


def _read_config(path: Path) -> dict:
    try:
        config = json.loads(path.read_text(encoding='utf-8'))
    except PermissionError as exc:
        raise RuntimeRefused('Runtime config permission refused') from exc
    except OSError as exc:
        raise RuntimeValidationError('Runtime config unreadable') from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeValidationError('Runtime config malformed') from exc
    if not isinstance(config, dict):
        raise RuntimeValidationError('Runtime config invalid')
    return config


def _validated_runtime(config: dict) -> tuple[Path, dict]:
    if not isinstance(config, dict):
        raise RuntimeValidationError('Runtime config is not a mapping')
    try:
        python = validate_runtime(config)
        receipt = json.loads(Path(config['validation_receipt']).read_text(encoding='utf-8'))
    except PermissionError as exc:
        raise RuntimeRefused('Runtime validation permission refused') from exc
    except OSError as exc:
        raise RuntimeValidationError('Runtime validation material unreadable') from exc
    except RuntimeRefused:
        raise
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeValidationError('Runtime validation material malformed') from exc
    except Exception as exc:
        raise RuntimeValidationError(f'Runtime validation failed: {type(exc).__name__}') from exc
    if not isinstance(receipt, dict):
        raise RuntimeValidationError('Runtime validation receipt invalid')
    return python, receipt


def _timeout_seconds(config: dict) -> float:
    """Parse and clamp the extraction timeout once, before any import/spawn."""
    try:
        return max(0.05, min(60.0, float(config.get('timeout_seconds', 15))))
    except (TypeError, ValueError) as exc:
        raise RuntimeValidationError('Runtime timeout_seconds is invalid') from exc


def _cleanup(job, process) -> str | None:
    """Attempt every cleanup step; return a problem description if any step failed.

    Any problem must be treated as a hard refusal: a live or unknown child must
    never be left behind for a fallback path to continue.
    """
    problems: list[str] = []
    if job is not None:
        try:
            job.close()
        except Exception as exc:
            problems.append(f'close:{type(exc).__name__}')
    if process is not None:
        try:
            alive = process.poll() is None
        except Exception as exc:
            problems.append(f'poll:{type(exc).__name__}')
            alive = True
        if alive:
            try:
                process.kill()
            except Exception as exc:
                problems.append(f'kill:{type(exc).__name__}')
            try:
                process.wait(timeout=5)
            except Exception as exc:
                problems.append(f'wait:{type(exc).__name__}')
    return ','.join(problems) if problems else None


def route(html: str, url: str, title: str) -> dict | None:
    if os.environ.get('WCF_EXTRACTION_WORKER') == '1' or os.environ.get('WCF_USE_SYSTEM_PYTHON') == '1':
        return None
    root = Path(__file__).resolve().parent.parent
    path = root / 'config/local-runtime.json'
    try:
        if not path.is_file():
            return None
    except PermissionError as exc:
        raise RuntimeRefused('Runtime config permission refused') from exc
    config = _read_config(path)
    if not config.get('enabled') or config.get('mode') != 'extraction':
        return None
    python, _receipt = _validated_runtime(config)
    return run_worker(python, config, {'html': html, 'url': url, 'title': title}, root)


def run_worker(python: Path, config: dict, payload: dict, root: Path, *, worker_module: str = 'web_content_fetcher.extraction_worker') -> dict:
    if worker_module not in {'web_content_fetcher.extraction_worker', 'web_content_fetcher.attachment_worker'}:
        raise ValueError('Unsupported private worker')
    # Policy permission must precede validation/import/spawn. A hash-valid but
    # disabled config is not permission for a direct run_worker call.
    if not isinstance(config, dict):
        raise RuntimeRefused('Runtime config is not a mapping')
    if worker_module.endswith('attachment_worker'):
        if config.get('enabled') is not True or config.get('attachments_enabled') is not True:
            raise RuntimeRefused('Attachment runtime is not enabled by policy')
    elif config.get('enabled') is not True:
        raise RuntimeRefused('Private runtime is not enabled by policy')
    if os.name != 'nt':
        raise RuntimeRefused('Configured extraction supervisor requires Windows')
    try:
        raw = json.dumps(payload).encode('utf-8')
    except Exception as exc:
        raise RuntimeRefused(f'Extraction request could not be serialized: {type(exc).__name__}') from exc
    if len(raw) > 8 * 1024 * 1024:
        raise RuntimeRefused('Extraction request exceeds 8 MiB')
    # Invalid/nonnumeric timeout is invalid config: refuse before validation/import/spawn.
    timeout_seconds = _timeout_seconds(config)
    # Direct callers must present the same validated contract as route(), not just a key.
    validated_python, receipt = _validated_runtime(config)
    try:
        python_path = Path(python)
        if python_path.resolve(strict=True) != validated_python:
            raise RuntimeValidationError('Worker interpreter is not the validated interpreter')
    except OSError as exc:
        raise RuntimeValidationError('Worker interpreter is unavailable') from exc
    except RuntimeRefused:
        raise
    except Exception as exc:
        raise RuntimeValidationError(f'Worker interpreter is invalid: {type(exc).__name__}') from exc
    try:
        supervisor = Path(config['process_supervisor']).resolve(strict=True)
    except PermissionError as exc:
        raise RuntimeRefused('Supervisor permission refused') from exc
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeValidationError('Supervisor is unavailable or unbound') from exc
    except Exception as exc:
        raise RuntimeValidationError(f'Supervisor is invalid: {type(exc).__name__}') from exc
    fingerprints = receipt.get('fingerprints')
    if not isinstance(fingerprints, dict) or str(supervisor) not in fingerprints:
        raise RuntimeValidationError('Supervisor is not bound by runtime validation')
    try:
        actual_fingerprint = hashlib.sha256(supervisor.read_bytes()).hexdigest()
    except PermissionError as exc:
        raise RuntimeRefused('Supervisor permission refused') from exc
    except OSError as exc:
        raise RuntimeValidationError('Supervisor is unreadable') from exc
    except Exception as exc:
        raise RuntimeValidationError(f'Supervisor is invalid: {type(exc).__name__}') from exc
    if actual_fingerprint != fingerprints[str(supervisor)]:
        raise RuntimeValidationError('Supervisor changed after validation')
    try:
        spec = importlib.util.spec_from_file_location('_wcf_extraction_supervisor', supervisor)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        job = module.WindowsJob()
    except RuntimeRefused:
        raise
    except Exception as exc:
        raise RuntimeRefused(f'Extraction supervisor load failed: {type(exc).__name__}') from exc
    env = os.environ.copy()
    env.update(WCF_EXTRACTION_WORKER='1', PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1',
               OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
    env['PYTHONPATH'] = str(root)
    process = None
    try:
        try:
            process = subprocess.Popen([str(python), '-B', '-m', worker_module],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=0x4 if os.name == 'nt' else 0, env=env, cwd=root)
        except Exception as exc:
            raise RuntimeRefused(f'Runtime spawn failed: {type(exc).__name__}') from exc
        try:
            job.assign(process)
            if os.name == 'nt':
                resume = ctypes.WinDLL('ntdll').NtResumeProcess
                resume.argtypes = [ctypes.c_void_p]
                resume.restype = ctypes.c_long
                if resume(int(process._handle)) != 0:
                    raise RuntimeRefused('Unable to resume owned extraction process')
        except RuntimeRefused:
            raise
        except Exception as exc:
            raise RuntimeRefused(f'Supervisor refused the owned process: {type(exc).__name__}') from exc
        try:
            out, err = process.communicate(raw, timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            try:
                job.terminate(124)
            except Exception as exc:
                raise RuntimeRefused(f'Supervisor terminate failed: {type(exc).__name__}') from exc
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeRefused('Extraction timeout was not confirmed by cleanup') from exc
            except Exception as exc:
                raise RuntimeRefused(f'Extraction timeout cleanup failed: {type(exc).__name__}') from exc
            try:
                exit_code = process.returncode
            except Exception as exc:
                raise RuntimeRefused('Extraction child state unknown after timeout') from exc
            if exit_code is None:
                raise RuntimeRefused('Extraction child state unknown after timeout')
            raise ExtractionTimeoutError('extraction_runtime_timeout') from None
        except Exception as exc:
            raise RuntimeRefused(f'Extraction child communication failed: {type(exc).__name__}') from exc
        try:
            exit_code = process.returncode
        except Exception as exc:
            raise RuntimeRefused('Extraction child state unknown') from exc
        if exit_code is None:
            raise RuntimeRefused('Extraction child state unknown')
        if exit_code:
            raise ExtractionRuntimeError(f'extraction_runtime_exit:{exit_code}')
        try:
            result = json.loads(out.decode('utf-8'))
        except Exception:
            result = None
        if not isinstance(result, dict) or not isinstance(result.get('text'), str) or (worker_module.endswith('extraction_worker') and result.get('engine') != 'trafilatura'):
            raise ExtractionRuntimeError('extraction_runtime_invalid_result')
        return result
    finally:
        cleanup_problem = _cleanup(job, process)
        if cleanup_problem is not None:
            raise RuntimeRefused(f'Extraction cleanup failed: {cleanup_problem}') from None
