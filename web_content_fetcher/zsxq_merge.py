from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .zsxq_cli import _render_group_index


def merge_exports(export_paths: list[str], *, output_dir: str = "") -> dict[str, Any]:
    if not export_paths:
        return {"status": "failed", "errors": ["at least one export path is required"]}

    loaded = [_load_summary(Path(path)) for path in export_paths]
    generated_at = datetime.now(timezone.utc).isoformat()
    root = Path(output_dir) if output_dir else _default_output_dir(loaded[0]["root"], loaded[0]["group_id"], generated_at)
    root.mkdir(parents=True, exist_ok=True)
    topics_root = root / "topics"
    topics_root.mkdir(parents=True, exist_ok=True)

    by_id: dict[str, dict[str, Any]] = {}
    raw_by_id: dict[str, dict[str, Any]] = {}
    duplicate_topic_ids: list[str] = []
    skipped_without_id = 0
    for export in loaded:
        raw_topics = _read_jsonl(export["root"] / "topics.jsonl")
        raw_lookup = {_topic_id(item): item for item in raw_topics if _topic_id(item)}
        for topic in export["topics"]:
            topic_id = _topic_id(topic)
            if not topic_id:
                skipped_without_id += 1
                continue
            if topic_id in by_id:
                duplicate_topic_ids.append(topic_id)
                continue
            copied = _copy_topic_archive(topic, topics_root)
            by_id[topic_id] = {**topic, **copied}
            raw_by_id[topic_id] = raw_lookup.get(topic_id) or topic

    topics = sorted(by_id.values(), key=lambda item: str(item.get("create_time") or ""), reverse=True)
    raw_topics = [raw_by_id.get(_topic_id(topic), topic) for topic in topics]
    newest = str(topics[0].get("create_time") or "") if topics else ""
    oldest = str(topics[-1].get("create_time") or "") if topics else ""
    completion = _completion_status(loaded)
    with_comments = any(bool(export["with_comments"]) for export in loaded)
    group_id = next((export["group_id"] for export in loaded if export["group_id"]), "")
    group_name = next((export["group_name"] for export in loaded if export["group_name"]), "")

    summary = {
        "action": "merge_exports",
        "status": "ok" if completion["completed"] else "partial",
        "transport": "zsxq-cli",
        "fetched_at": generated_at,
        "group_id": group_id,
        "group_name": group_name,
        "topic_count": len(topics),
        "page_count": sum(int(export["page_count"] or 0) for export in loaded),
        "completed": completion["completed"],
        "next_end_time": completion["next_end_time"],
        "with_comments": with_comments,
        "oldest_topic_time": oldest,
        "newest_topic_time": newest,
        "collection_path": str(root),
        "source_exports": [
            {
                "path": str(export["root"]),
                "status": export["status"],
                "topic_count": len(export["topics"]),
                "completed": export["completed"],
                "next_end_time": export["next_end_time"],
                "errors": export["errors"],
            }
            for export in loaded
        ],
        "completion_proof": completion["proof"],
        "duplicate_topic_ids": sorted(set(duplicate_topic_ids)),
        "duplicate_count": len(duplicate_topic_ids),
        "skipped_without_id": skipped_without_id,
        "topics": topics,
        "errors": completion["errors"],
    }

    summary_path = root / "summary.json"
    topics_jsonl_path = root / "topics.jsonl"
    index_path = root / "index.md"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    topics_jsonl_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in raw_topics) + ("\n" if raw_topics else ""), encoding="utf-8", newline="\n")
    index_path.write_text(_render_group_index(summary, topics), encoding="utf-8", newline="\n")

    return {
        "status": summary["status"],
        "archive_path": str(root),
        "summary_path": str(summary_path),
        "topics_jsonl_path": str(topics_jsonl_path),
        "index_markdown_path": str(index_path),
        "topic_count": len(topics),
        "completed": completion["completed"],
        "next_end_time": completion["next_end_time"],
        "oldest_topic_time": oldest,
        "newest_topic_time": newest,
        "with_comments": with_comments,
        "duplicate_count": len(duplicate_topic_ids),
        "skipped_without_id": skipped_without_id,
        "source_export_count": len(loaded),
        "completion_proof": completion["proof"],
    }


def _default_output_dir(first_root: Path, group_id: str, generated_at: str) -> Path:
    stamp = generated_at.replace("-", "").replace(":", "").split(".")[0]
    return first_root.parent / f"zsxq_group_{group_id}_full_complete_{stamp}"


def _load_summary(path: Path) -> dict[str, Any]:
    summary_path = path / "summary.json" if path.is_dir() else path
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    return {
        "root": summary_path.parent,
        "status": str(data.get("status") or ""),
        "group_id": str(data.get("group_id") or ""),
        "group_name": str(data.get("group_name") or ""),
        "topics": [item for item in data.get("topics") or [] if isinstance(item, dict)],
        "completed": bool(data.get("completed")),
        "next_end_time": str(data.get("next_end_time") or ""),
        "page_count": data.get("page_count") or 0,
        "with_comments": bool(data.get("with_comments")),
        "errors": data.get("errors") if isinstance(data.get("errors"), list) else [],
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    items = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            items.append(item)
    return items


def _topic_id(topic: dict[str, Any]) -> str:
    return str(topic.get("topic_id") or topic.get("topicId") or topic.get("id") or "")


def _copy_topic_archive(topic: dict[str, Any], topics_root: Path) -> dict[str, str]:
    source = Path(str(topic.get("archive_path") or ""))
    if not source.exists():
        return {}
    target = topics_root / source.name
    if target.exists():
        suffix = _topic_id(topic) or "topic"
        target = topics_root / f"{source.name}_{suffix}"
    shutil.copytree(source, target)
    copied = {
        "archive_path": str(target),
        "extracted_text_path": str(target / "extracted_text.md"),
        "structured_path": str(target / "structured.json"),
    }
    _rewrite_copied_structured(target, copied)
    return copied


def _rewrite_copied_structured(target: Path, copied: dict[str, str]) -> None:
    structured_path = target / "structured.json"
    if not structured_path.exists():
        return
    try:
        data = json.loads(structured_path.read_text(encoding="utf-8"))
    except Exception:
        return
    archive_index = data.get("archive_index") if isinstance(data.get("archive_index"), dict) else {}
    archive_index["archive_path"] = str(target)
    archive_index["extracted_text_path"] = copied["extracted_text_path"]
    data["archive_index"] = archive_index
    structured_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def _completion_status(exports: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    for export in exports:
        errors.extend(str(item) for item in export["errors"])

    completed_exports = [export for export in exports if export["completed"]]
    if completed_exports:
        proof = [
            "At least one resume/checkpoint export returned completed=true from the official zsxq-cli pagination path.",
            "Merged output keeps all unique topic_id values from earlier partial exports and later checkpoints.",
        ]
        if any(len(export["topics"]) == 0 for export in completed_exports):
            proof.append("The final completed checkpoint returned zero topics after the previous next_end_time, indicating no older visible page for this account/API cursor.")
        return {"completed": True, "next_end_time": "", "proof": proof, "errors": errors}

    next_end_time = next((export["next_end_time"] for export in reversed(exports) if export["next_end_time"]), "")
    return {
        "completed": False,
        "next_end_time": next_end_time,
        "proof": ["No source export reported completed=true; keep merged output partial."],
        "errors": errors,
    }
