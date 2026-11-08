"""Single HTML-to-text request. No transport, browser, attachment or model work."""
import dataclasses
import importlib.metadata
import json
import sys
from .extractors import _extract_trafilatura_local


def main():
    request = json.loads(sys.stdin.buffer.read(8 * 1024 * 1024 + 1))
    result = _extract_trafilatura_local(request['html'], url=request['url'], title=request['title'])
    result.metadata['extraction_runtime'] = {'python': sys.executable,
        'trafilatura': importlib.metadata.version('trafilatura'), 'route': 'private'}
    sys.stdout.buffer.write(json.dumps(dataclasses.asdict(result), ensure_ascii=False).encode('utf-8'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
