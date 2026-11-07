from __future__ import annotations

import json
import re
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "wcf-zsxq-classification.v2"
DEFAULT_TAXONOMY: dict[str, Any] = {
    "name": "generic-content-v1",
    "source_basis": ["builtin:generic-content-v1"],
    "categories": {
        "access_and_compliance": [
            "permission",
            "consent",
            "terms",
            "privacy",
            "access control",
            "policy",
            "rate limit",
            "robots",
            "login",
            "权限",
            "授权",
            "隐私",
            "合规",
            "规则",
            "登录",
        ],
        "question_and_answer": [
            "question",
            "answer",
            "faq",
            "why",
            "how",
            "请问",
            "为什么",
            "怎么办",
            "答疑",
            "问题",
        ],
        "tutorial_and_workflow": [
            "tutorial",
            "steps",
            "workflow",
            "checklist",
            "process",
            "guide",
            "教程",
            "步骤",
            "流程",
            "清单",
            "指南",
        ],
        "tools_and_automation": [
            "tool",
            "api",
            "cli",
            "mcp",
            "automation",
            "script",
            "工具",
            "自动化",
            "脚本",
            "命令",
        ],
        "research_and_reference": [
            "research",
            "analysis",
            "comparison",
            "source",
            "evidence",
            "研究",
            "分析",
            "比较",
            "来源",
            "证据",
            "资料",
        ],
        "case_study": [
            "case study",
            "postmortem",
            "result",
            "experiment",
            "lesson",
            "案例",
            "复盘",
            "结果",
            "实验",
            "经验",
        ],
        "general_reference": ["reference", "note", "summary", "参考", "笔记", "总结"],
    },
    "evidence_types": {
        "tutorial": ["tutorial", "guide", "步骤", "教程", "指南"],
        "checklist": ["checklist", "清单", "检查项"],
        "example": ["example", "sample", "case", "示例", "样例", "案例"],
        "reference": ["reference", "source", "evidence", "参考", "来源", "证据", "资料"],
        "policy": ["policy", "terms", "permission", "规则", "条款", "权限", "合规"],
        "question": ["question", "faq", "请问", "问题", "答疑", "为什么"],
        "tool": ["tool", "api", "cli", "mcp", "script", "工具", "脚本", "命令"],
    },
    "stages_by_category": {
        "access_and_compliance": ["human_review"],
        "question_and_answer": ["triage", "answer"],
        "tutorial_and_workflow": ["draft", "validate"],
        "tools_and_automation": ["evaluate", "validate"],
        "research_and_reference": ["review", "cite"],
        "case_study": ["review", "adapt"],
        "general_reference": ["review"],
    },
    "actions_by_category": {
        "access_and_compliance": "review_permissions",
        "question_and_answer": "draft_answer",
        "tutorial_and_workflow": "validate_steps",
        "tools_and_automation": "test_tool",
        "research_and_reference": "verify_sources",
        "case_study": "extract_lessons",
        "general_reference": "review",
    },
    "maturity_by_category": {
        "access_and_compliance": "restricted",
        "question_and_answer": "draft",
        "tutorial_and_workflow": "actionable",
        "tools_and_automation": "actionable",
        "research_and_reference": "reference",
        "case_study": "reference",
        "general_reference": "reference",
    },
    "high_risk_keywords": [
        "password",
        "api key",
        "token",
        "cookie",
        "secret",
        "identity document",
        "phone number",
        "medical record",
        "payment",
        "bypass",
        "captcha",
        "密码",
        "密钥",
        "令牌",
        "身份证",
        "手机号",
        "病历",
        "支付",
        "绕过",
        "验证码",
    ],
    "medium_risk_keywords": [
        "private",
        "login",
        "account",
        "permission",
        "personal data",
        "confidential",
        "私有",
        "登录",
        "账号",
        "权限",
        "个人信息",
        "机密",
    ],
}


def classify_exports(
    export_paths: list[str],
    *,
    output_dir: str = "",
    taxonomy_path: str = "",
) -> dict[str, Any]:
    taxonomy, taxonomy_source = _load_taxonomy(taxonomy_path)
    exports = [_load_export(Path(path)) for path in export_paths]
    topics: list[dict[str, Any]] = []
    for export in exports:
        topics.extend(export["topics"])

    items = [_classify_topic(topic, taxonomy) for topic in topics]
    category_counts = Counter(item["primary_category"] for item in items)
    evidence_counts = Counter(item["evidence_type"] for item in items)
    risk_counts = Counter(item["sensitivity_risk"] for item in items)
    review_items = [
        item
        for item in items
        if "human_review" in item["workflow_stage_candidates"]
        or item["sensitivity_risk"] == "high"
    ]

    base_output = Path(output_dir) if output_dir else Path(export_paths[0])
    base_output.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()
    result = {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "generated_at": generated_at,
        "taxonomy": {
            "name": str(taxonomy.get("name") or "custom"),
            "source": taxonomy_source,
            "source_basis": list(taxonomy.get("source_basis") or []),
        },
        "source_exports": [
            {
                "path": export["path"],
                "group_id": export["group_id"],
                "group_name": export["group_name"],
                "topic_count": export["topic_count"],
                "completed": export["completed"],
                "next_end_time": export["next_end_time"],
                "errors": export["errors"],
            }
            for export in exports
        ],
        "topic_count": len(items),
        "category_counts": dict(category_counts),
        "evidence_counts": dict(evidence_counts),
        "risk_counts": dict(risk_counts),
        "needs_human_review_count": len(review_items),
        "items": items,
    }
    json_path = base_output / "classification_index.json"
    md_path = base_output / "classification_index.md"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    md_path.write_text(_render_markdown(result), encoding="utf-8", newline="\n")
    result["classification_json_path"] = str(json_path)
    result["classification_markdown_path"] = str(md_path)
    return result


def _load_taxonomy(taxonomy_path: str) -> tuple[dict[str, Any], str]:
    if not taxonomy_path:
        return deepcopy(DEFAULT_TAXONOMY), "builtin:generic-content-v1"
    source = Path(taxonomy_path)
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("taxonomy_must_be_object")
    _validate_taxonomy(value)
    return value, f"user-provided:{source.name}"


def _validate_taxonomy(value: dict[str, Any]) -> None:
    for key in ("categories", "evidence_types"):
        mapping = value.get(key)
        if not isinstance(mapping, dict) or not mapping:
            raise ValueError(f"taxonomy_requires_nonempty_{key}")
        for label, keywords in mapping.items():
            if not str(label).strip() or not isinstance(keywords, list):
                raise ValueError(f"taxonomy_invalid_{key}")
    for key in (
        "stages_by_category",
        "actions_by_category",
        "maturity_by_category",
    ):
        if not isinstance(value.get(key), dict):
            raise ValueError(f"taxonomy_requires_{key}")


def _load_export(path: Path) -> dict[str, Any]:
    summary_path = path / "summary.json" if path.is_dir() else path
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    export_root = summary_path.parent
    topics = []
    for topic in summary.get("topics") or []:
        if not isinstance(topic, dict):
            continue
        item = dict(topic)
        item["export_root"] = str(export_root)
        item["source_summary_path"] = str(summary_path)
        item["text"] = _read_topic_text(item)
        topics.append(item)
    return {
        "path": str(export_root),
        "group_id": str(summary.get("group_id") or ""),
        "group_name": str(summary.get("group_name") or ""),
        "topic_count": len(topics),
        "completed": bool(summary.get("completed")),
        "next_end_time": str(summary.get("next_end_time") or ""),
        "errors": summary.get("errors")
        if isinstance(summary.get("errors"), list)
        else [],
        "topics": topics,
    }


def _read_topic_text(topic: dict[str, Any]) -> str:
    for key in ("extracted_text_path", "archive_path"):
        value = str(topic.get(key) or "")
        if not value:
            continue
        path = Path(value)
        if key == "archive_path":
            path = path / "extracted_text.md"
        try:
            if path.exists():
                return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return str(topic.get("title") or "")


def _classify_topic(
    topic: dict[str, Any],
    taxonomy: dict[str, Any],
) -> dict[str, Any]:
    text = _compact_text(f"{topic.get('title') or ''}\n{topic.get('text') or ''}")
    category, category_scores = _best_label(
        text,
        taxonomy["categories"],
        default="general_reference",
    )
    evidence_type, _scores = _best_label(
        text,
        taxonomy["evidence_types"],
        default="reference",
    )
    risk = _risk_level(text, taxonomy)
    score = category_scores.get(category, 0)
    if risk == "high":
        content_use_case = "human_review"
    elif category == "access_and_compliance":
        content_use_case = "needs_review"
    elif evidence_type in {"tutorial", "checklist", "tool"} and risk == "low":
        content_use_case = "ready_to_adapt"
    elif score <= 1:
        content_use_case = "background_reference"
    else:
        content_use_case = "needs_adaptation"

    topic_id = str(topic.get("topic_id") or "")
    stages = taxonomy["stages_by_category"].get(category, ["review"])
    if not isinstance(stages, list):
        stages = [str(stages)]
    return {
        "topic_id": topic_id,
        "title": _compact_text(str(topic.get("title") or topic_id)),
        "url": (
            f"https://wx.zsxq.com/dweb2/index/topic_detail/{topic_id}"
            if topic_id
            else ""
        ),
        "archive_path": str(topic.get("archive_path") or ""),
        "primary_category": category,
        "workflow_stage_candidates": [str(item) for item in stages],
        "next_action_candidate": str(
            taxonomy["actions_by_category"].get(category, "review")
        ),
        "content_maturity_stage": str(
            taxonomy["maturity_by_category"].get(category, "reference")
        ),
        "content_use_case": content_use_case,
        "evidence_type": evidence_type,
        "sensitivity_risk": risk,
        "why": (
            f"topic {topic_id} matched category '{category}'; "
            f"excerpt: {_excerpt(text)}"
        ),
        "missing_context_or_questions": _missing_context(
            category,
            risk,
            evidence_type,
        ),
    }


def _best_label(
    text: str,
    keyword_map: dict[str, list[str]],
    *,
    default: str,
) -> tuple[str, dict[str, int]]:
    normalized = text.lower()
    scores: dict[str, int] = {}
    for label, keywords in keyword_map.items():
        scores[str(label)] = sum(
            1
            for keyword in keywords
            if str(keyword).strip() and str(keyword).lower() in normalized
        )
    best_label, best_score = max(scores.items(), key=lambda item: item[1])
    return (best_label if best_score > 0 else default), scores


def _risk_level(text: str, taxonomy: dict[str, Any]) -> str:
    normalized = text.lower()
    high = [
        str(item).lower()
        for item in taxonomy.get("high_risk_keywords") or []
        if str(item).strip()
    ]
    medium = [
        str(item).lower()
        for item in taxonomy.get("medium_risk_keywords") or []
        if str(item).strip()
    ]
    if any(keyword in normalized for keyword in high):
        return "high"
    return "medium" if any(keyword in normalized for keyword in medium) else "low"


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _excerpt(text: str, *, limit: int = 90) -> str:
    compact = _compact_text(text)
    return compact[:limit] + ("..." if len(compact) > limit else "")


def _missing_context(category: str, risk: str, evidence_type: str) -> str:
    if risk == "high":
        return "Review privacy, credentials, permissions, and publication scope before reuse."
    if category == "access_and_compliance":
        return "Confirm current platform terms, authorization, and intended audience."
    if evidence_type in {"tutorial", "checklist", "tool"}:
        return "Validate commands, versions, prerequisites, and expected outputs."
    if evidence_type == "reference":
        return "Verify source dates and preserve citations before reuse."
    return "Confirm context, evidence quality, and intended use."


def _render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# ZSXQ Archive Classification Index",
        "",
        f"- Generated: {result['generated_at']}",
        f"- Schema: {result['schema_version']}",
        f"- Taxonomy: {result['taxonomy']['name']} ({result['taxonomy']['source']})",
        f"- Topics: {result['topic_count']}",
        f"- Needs human review: {result['needs_human_review_count']}",
        "",
        "## Source Exports",
        "",
    ]
    for export in result["source_exports"]:
        lines.append(f"- {export['path']}")
        lines.append(
            f"  - group: {export['group_name']} ({export['group_id']})"
        )
        lines.append(
            "  - topics: "
            f"{export['topic_count']} | completed: {export['completed']} | "
            f"next_end_time: {export['next_end_time']}"
        )
        if export["errors"]:
            lines.append(
                f"  - errors: {'; '.join(str(item) for item in export['errors'][:3])}"
            )
    lines.extend(["", "## Category Counts", ""])
    for label, count in sorted(
        result["category_counts"].items(),
        key=lambda item: item[1],
        reverse=True,
    ):
        lines.append(f"- {label}: {count}")
    lines.extend(["", "## Evidence Types", ""])
    for label, count in sorted(
        result["evidence_counts"].items(),
        key=lambda item: item[1],
        reverse=True,
    ):
        lines.append(f"- {label}: {count}")
    lines.extend(["", "## Topic Index", ""])
    for item in result["items"]:
        lines.append(f"### {item['title'] or item['topic_id']}")
        lines.append("")
        for key in (
            "topic_id",
            "url",
            "archive_path",
            "primary_category",
            "next_action_candidate",
            "content_maturity_stage",
            "content_use_case",
            "evidence_type",
            "sensitivity_risk",
            "why",
            "missing_context_or_questions",
        ):
            lines.append(f"- {key}: {item[key]}")
        lines.append(
            "- workflow_stage_candidates: "
            + ", ".join(item["workflow_stage_candidates"])
        )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
