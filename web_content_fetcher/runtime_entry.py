"""Optional, fail-closed handoff from stable entrypoints to a private runtime."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def maybe_reexec(module: str, argv: list[str] | None = None) -> int | None:
    if module not in {'web_content_fetcher.cli', 'web_content_fetcher.ui', 'web_content_fetcher.mcp_server'}:
        raise ValueError('Unsupported runtime entrypoint')
    if os.environ.get('WCF_USE_SYSTEM_PYTHON') == '1':
        return None
    root = Path(__file__).resolve().parent.parent
    config_path = root / 'config' / 'local-runtime.json'
    if not config_path.is_file():
        return None
    config = json.loads(config_path.read_text(encoding='utf-8'))
    if config.get('enabled') is not True:
        return None
    if config.get('mode') == 'extraction':
        return None
    python = validate_runtime(config)
    if Path(sys.executable).resolve() == python:
        return None
    if os.environ.get('WCF_RUNTIME_REENTRY') == '1':
        raise RuntimeError('Private runtime did not resolve to the validated interpreter')
    env = os.environ.copy()
    env['WCF_RUNTIME_REENTRY'] = '1'
    env['PYTHONPATH'] = str(root) + os.pathsep + env.get('PYTHONPATH', '')
    return subprocess.call([str(python), '-m', module, *(sys.argv[1:] if argv is None else argv)], env=env)


def validate_runtime(config: dict) -> Path:
    python = Path(config['python']).resolve(strict=True)
    receipt_path = Path(config['validation_receipt']).resolve(strict=True)
    raw = receipt_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != config.get('validation_sha256'):
        raise RuntimeError('Private runtime validation receipt changed')
    receipt = json.loads(raw)
    if receipt.get('pip_check_exit_code') != 0 or receipt.get('entrypoint_tests_passed') is not True:
        raise RuntimeError('Private runtime has not passed dependency and entrypoint validation')
    if Path(receipt['python']).resolve() != python:
        raise RuntimeError('Private runtime interpreter does not match validation')
    fingerprints = receipt.get('fingerprints')
    if not isinstance(fingerprints, dict) or str(python) not in fingerprints:
        raise RuntimeError('Private runtime validation lacks interpreter fingerprints')
    for name, expected in fingerprints.items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest() != expected:
            raise RuntimeError('Private runtime changed after validation')
    return python
