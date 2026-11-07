from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def convert_attachment(input_path: str | Path, output_path: str | Path = "") -> dict[str, Any]:
    source = Path(input_path)
    if not source.is_file():
        raise FileNotFoundError(f"attachment not found: {input_path}")
    target = Path(output_path) if output_path else source.with_suffix(source.suffix + ".md")
    try:
        from markitdown import MarkItDown
    except Exception as exc:
        raise RuntimeError("markitdown is not installed; install the attachments extra or markitdown package") from exc

    result = MarkItDown().convert(str(source))
    text = str(getattr(result, "text_content", "") or "")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    data = target.read_bytes()
    return {
        "input_path": str(source),
        "output_path": str(target),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "text_chars": len(text),
        "status": "ok",
    }
