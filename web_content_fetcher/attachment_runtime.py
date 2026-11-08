"""Narrow local TXT/HTML adoption using the existing private process bridge."""
import os
from pathlib import Path
from .extraction_runtime import (
    ExtractionRuntimeError,
    ExtractionTimeoutError,
    RuntimeRefused,
    RuntimeValidationError,
    _read_config,
    _validated_runtime,
    run_worker,
)


def convert_in_private_runtime(source: Path) -> dict | None:
    if os.environ.get('WCF_USE_SYSTEM_PYTHON') == '1' or os.environ.get('WCF_ATTACHMENT_WORKER') == '1':
        return None
    if source.suffix.lower() not in {'.txt', '.html', '.htm'}:
        return None
    root = Path(__file__).resolve().parent.parent
    config_path = root / 'config/local-runtime.json'
    if not config_path.is_file():
        return None
    config = _read_config(config_path)
    if not config.get('enabled') or config.get('attachments_enabled') is not True:
        return None
    python, _receipt = _validated_runtime(config)
    try:
        model = Path(config['attachment_model_directory'])
    except (KeyError, TypeError) as exc:
        raise RuntimeValidationError('Attachment model directory is not configured') from exc
    # Missing local model is a hard stop, never a download or concealed fallback.
    for name in ['model.onnx', 'config.min.json']:
        if not (model / name).is_file():
            raise FileNotFoundError('Required installed local Magika model/config is unavailable')
    if source.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('Attachment exceeds 8 MiB private conversion bound')
    try:
        return run_worker(python, {**config, 'timeout_seconds': config.get('attachment_timeout_seconds', 15)},
            {'input_path': str(source.resolve()), 'model_directory': str(model.resolve())}, root,
            worker_module='web_content_fetcher.attachment_worker')
    except RuntimeRefused:
        raise
    except (ExtractionRuntimeError, ExtractionTimeoutError) as exc:
        reason = getattr(exc, 'reason', type(exc).__name__)
        if source.suffix.lower() == '.txt':
            text = source.read_text(encoding='utf-8')
        else:
            from .extractors import html_fragment_to_markdown
            text = html_fragment_to_markdown(source.read_text(encoding='utf-8'), base_url=source.resolve().as_uri())
        return {'text': text, 'conversion_runtime': {'route': 'builtin_fallback', 'reason': reason, 'model_executed_in_fallback': False}}
