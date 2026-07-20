"""Customer-visible projection helpers for managed Oxaide sessions."""
from __future__ import annotations

import re
from typing import Any

COMPACTION_PREFIXES = (
    "[CONTEXT COMPACTION — REFERENCE ONLY]",
    "[CONTEXT COMPACTION - REFERENCE ONLY]",
    "[CONTEXT SUMMARY]:",
)
COMPACTION_END_MARKER = (
    "--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---"
)


def managed_customer_content_text(content: Any) -> str:
    """Render persisted content without exposing structured attachment payloads."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks = []
        for part in content:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict):
                kind = part.get("type")
                if kind in {"text", "input_text", "output_text"}:
                    chunks.append(str(part.get("text") or part.get("content") or ""))
                elif kind in {"image_url", "input_image", "image"}:
                    chunks.append("[image]")
                elif kind in {"input_audio", "audio"}:
                    chunks.append("[audio]")
                elif kind:
                    chunks.append("[attachment]")
        return "\n".join(chunk for chunk in chunks if chunk)
    if isinstance(content, dict):
        kind = content.get("type")
        if kind in {"text", "input_text", "output_text"}:
            return str(content.get("text") or content.get("content") or "")
        if kind in {"image_url", "input_image", "image"}:
            return "[image]"
        if kind in {"input_audio", "audio"}:
            return "[audio]"
        return "[attachment]"
    return "" if content is None else str(content)


def redact_managed_host_paths(text: str) -> str:
    """Replace Unix and Windows absolute host paths in customer-visible text."""
    text = re.sub(r"(?<![\w:/])/(?:[^\s/]+/)*[^\s/]+", "[research file]", text)
    text = re.sub(
        r"(?i)(?<![\\\w])\\\\(?:[^\s\\]+\\)+[^\s\\]+",
        "[research file]",
        text,
    )
    return re.sub(
        r"(?i)(?<![\w:])[A-Z]:\\(?:[^\s\\]+\\)*[^\s\\]+",
        "[research file]",
        text,
    )


def managed_customer_message_text(text: str) -> str:
    """Remove internal compaction context and redact host filesystem paths."""
    if text.lstrip().startswith(COMPACTION_PREFIXES):
        marker_index = text.find(COMPACTION_END_MARKER)
        if marker_index < 0:
            return ""
        text = text[marker_index + len(COMPACTION_END_MARKER) :].lstrip()
    return redact_managed_host_paths(text)
