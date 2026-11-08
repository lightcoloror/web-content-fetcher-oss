"""Single local TXT/HTML conversion with installed Magika, without downloads."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import socket
import sys


def main():
    def deny(*args, **kwargs):
        raise RuntimeError('Network forbidden in attachment worker')
    socket.socket.connect = deny
    socket.create_connection = deny
    request = json.loads(sys.stdin.buffer.read(64 * 1024))
    source = Path(request['input_path']).resolve(strict=True)
    if str(source).startswith('\\\\') or source.suffix.lower() not in {'.txt', '.html', '.htm'} or not source.is_file() or source.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('Unsupported local attachment')
    model = Path(request['model_directory']).resolve(strict=True)
    for name in ['model.onnx', 'config.min.json']:
        if not (model / name).is_file():
            raise FileNotFoundError('Installed local Magika model unavailable')
    import onnxruntime as ort
    import magika
    ort.disable_telemetry_events()
    original_session = ort.InferenceSession
    def bounded_session(path, *args, **kwargs):
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        kwargs['sess_options'] = options
        return original_session(path, *args, **kwargs)
    ort.InferenceSession = bounded_session
    original_magika = magika.Magika
    def local_magika(*args, **kwargs):
        kwargs['model_dir'] = model
        return original_magika(*args, **kwargs)
    magika.Magika = local_magika
    from markitdown import MarkItDown
    converted = MarkItDown(enable_plugins=False).convert_local(str(source))
    result = {'text': converted.text_content, 'conversion_runtime': {'route': 'private',
        'python': sys.executable, 'markitdown': importlib.metadata.version('markitdown'),
        'magika': importlib.metadata.version('magika'), 'onnxruntime': importlib.metadata.version('onnxruntime'),
        'model_sha256': hashlib.sha256((model/'model.onnx').read_bytes()).hexdigest(),
        'detector': 'real_installed_local', 'session_threads': 1}}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode('utf-8'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
