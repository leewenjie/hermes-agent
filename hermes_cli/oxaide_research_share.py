"""Sanitized, immutable Oxaide research-share snapshots.

Only active user and assistant prose can cross this boundary. System prompts,
reasoning, tool calls/results, runtime identifiers, and filesystem paths are
structurally excluded before forced secret redaction runs as defense in depth.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from agent.redact import redact_sensitive_text
from agent.agent_runtime_helpers import strip_think_blocks
from hermes_constants import get_hermes_home

_SCHEMA_VERSION = "research-share.v1"
_DEFAULT_ENDPOINT = "https://oxaide.com/api/agents/research-shares"
_MAX_MESSAGES = 100
_MAX_MESSAGE_CHARS = 20_000
_MAX_ARTIFACT_BYTES = 1536 * 1024
_MAX_TOTAL_ARTIFACT_BYTES = 1536 * 1024
_MAX_ARTIFACTS = 8
_MAX_TOTAL_TEXT_CHARS = 300_000
_ALLOWED_ARTIFACTS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}
_MEDIA_RE = re.compile(r"^\s*MEDIA:\s*([^\s]+)\s*$", re.IGNORECASE)
_COMPACTION_PREFIXES = (
    "[CONTEXT COMPACTION — REFERENCE ONLY]",
    "[CONTEXT COMPACTION - REFERENCE ONLY]",
    "[CONTEXT SUMMARY]:",
)
_COMPACTION_END = "--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---"
_MERGED_PRIOR_CONTEXT_HEADER = "[PRIOR CONTEXT — for reference only; not a new message]"
_MERGED_SUMMARY_DELIMITER = "[END OF PRIOR CONTEXT — COMPACTION SUMMARY BELOW]"
_LOCAL_PATH_RE = re.compile(
    r"(?<![\w:])(?:file://[^\s`'\"<>]+|~/(?:\.?[\w.-]+/)*[^\s`'\"<>]*|/(?:opt|home|root|workspace|tmp|var|etc|mnt|srv|Users)/[^\s`'\"<>]+|\\\\[^\s`'\"<>]+|[A-Za-z]:\\[^\s`'\"<>]+)"
)
_URL_RE = re.compile(r"https?://[^\s<>()`'\"]+", re.IGNORECASE)
_SENSITIVE_QUERY_NAMES = frozenset({
    "access_token", "api_key", "apikey", "auth", "authorization", "code",
    "credential", "jwt", "key", "password", "secret", "session", "sig",
    "signature", "token", "x-amz-credential", "x-amz-signature",
})
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_WALLET_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")


class ResearchShareError(RuntimeError):
    """A research snapshot could not be prepared or published."""


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "text":
            parts.append(str(part.get("text") or ""))
    return "\n".join(parts)


def _strip_compaction(content: str) -> str:
    stripped = content.lstrip()
    if stripped.startswith(_MERGED_PRIOR_CONTEXT_HEADER) and _MERGED_SUMMARY_DELIMITER in stripped:
        prior = stripped[len(_MERGED_PRIOR_CONTEXT_HEADER):].split(_MERGED_SUMMARY_DELIMITER, 1)[0]
        return prior.strip()
    if not stripped.startswith(_COMPACTION_PREFIXES):
        return content
    if _COMPACTION_END not in content:
        return ""
    return content.split(_COMPACTION_END, 1)[1].lstrip()


def _sanitize_svg(data: bytes) -> bytes:
    import xml.etree.ElementTree as ET

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ResearchShareError("SVG artifact is not valid UTF-8") from exc
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ResearchShareError("SVG artifact is malformed") from exc
    blocked = {"script", "foreignobject", "iframe", "object", "embed", "audio", "video"}
    for element in root.iter():
        local_tag = element.tag.rsplit("}", 1)[-1].lower()
        if local_tag in blocked:
            raise ResearchShareError("SVG artifact contains active content")
        for raw_name, raw_value in element.attrib.items():
            name = raw_name.rsplit("}", 1)[-1].lower()
            value = str(raw_value).strip()
            lowered = value.lower()
            if name.startswith("on"):
                raise ResearchShareError("SVG artifact contains event handlers")
            if name in {"href", "src"} and value and not value.startswith("#"):
                raise ResearchShareError("SVG artifact contains an external reference")
            if "url(" in lowered or "@import" in lowered or "javascript:" in lowered or "data:" in lowered:
                raise ResearchShareError("SVG artifact contains an external reference")
    return text.encode("utf-8")


def _artifact_root() -> Path:
    raw = str(os.environ.get("HERMES_DASHBOARD_FILES_ROOT") or "/opt/data").strip()
    return Path(raw).expanduser().resolve()


def _load_artifact(raw_path: str, used_names: set[str]) -> dict[str, Any] | None:
    try:
        source = Path(raw_path).expanduser()
        if source.is_symlink():
            return None
        target = source.resolve(strict=True)
        root = _artifact_root()
    except (OSError, RuntimeError):
        return None
    if target != root and root not in target.parents:
        return None
    mime_type = _ALLOWED_ARTIFACTS.get(target.suffix.lower())
    if not mime_type:
        return None
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(source, flags)
    except OSError:
        return None
    try:
        stat_result = os.fstat(fd)
        if not __import__("stat").S_ISREG(stat_result.st_mode):
            return None
        if stat_result.st_size <= 0 or stat_result.st_size > _MAX_ARTIFACT_BYTES:
            return None
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(_MAX_ARTIFACT_BYTES + 1)
        if len(data) > _MAX_ARTIFACT_BYTES:
            return None
    finally:
        os.close(fd)
    if mime_type == "image/svg+xml":
        data = _sanitize_svg(data)
    name = target.name
    if name in used_names:
        name = f"{target.stem}-{hashlib.sha256(str(target).encode()).hexdigest()[:8]}{target.suffix.lower()}"
    used_names.add(name)
    return {
        "name": name,
        "mime_type": mime_type or mimetypes.guess_type(name)[0] or "application/octet-stream",
        "data_base64": base64.b64encode(data).decode("ascii"),
        "sha256": hashlib.sha256(data).hexdigest(),
        "_bytes": len(data),
    }


def _redact_public_urls(content: str) -> str:
    def sanitize(match: re.Match[str]) -> str:
        raw = match.group(0)
        trailing = ""
        while raw and raw[-1] in ".,;:!?":
            trailing = raw[-1] + trailing
            raw = raw[:-1]
        try:
            parsed = urllib.parse.urlsplit(raw)
            hostname = parsed.hostname or ""
            if not hostname:
                return "[redacted URL]" + trailing
            port = f":{parsed.port}" if parsed.port else ""
            query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            safe_query = [
                (name, "[redacted]" if name.lower() in _SENSITIVE_QUERY_NAMES else value)
                for name, value in query
            ]
            clean = urllib.parse.urlunsplit((parsed.scheme, hostname + port, parsed.path, urllib.parse.urlencode(safe_query), ""))
            return clean + trailing
        except (ValueError, UnicodeError):
            return "[redacted URL]" + trailing
    return _URL_RE.sub(sanitize, content)


def _sanitize_content(content: str) -> str:
    content = strip_think_blocks(None, content)
    content = redact_sensitive_text(content, force=True)
    content = _redact_public_urls(content)
    content = _LOCAL_PATH_RE.sub("[local file]", content)
    return content.strip()[:_MAX_MESSAGE_CHARS]


def build_research_snapshot(session: dict[str, Any], messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the exact public preview from a private Hermes session."""
    public_messages: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    used_names: set[str] = set()
    artifact_bytes = 0
    warnings: set[str] = set()
    total_text_chars = 0

    candidates = [message for message in messages if str(message.get("role") or "") in {"user", "assistant"}]
    if len(candidates) > _MAX_MESSAGES:
        candidates = candidates[-_MAX_MESSAGES:]
        warnings.add("Only the latest 100 public conversation messages are included.")

    for message in candidates:
        role = str(message.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        content = _strip_compaction(_text_content(message.get("content")))
        if not content.strip():
            continue
        if _EMAIL_RE.search(content):
            warnings.add("The selected conversation contains an email address.")
        if _WALLET_RE.search(content):
            warnings.add("The selected conversation contains a wallet-like address.")

        message_artifacts: list[str] = []
        kept_lines: list[str] = []
        for line in content.splitlines():
            media = _MEDIA_RE.match(line)
            if media and role == "assistant":
                artifact = _load_artifact(media.group(1).strip("`\"'"), used_names)
                if artifact and len(artifacts) < _MAX_ARTIFACTS and artifact_bytes + int(artifact["_bytes"]) <= _MAX_TOTAL_ARTIFACT_BYTES:
                    artifact_bytes += int(artifact.pop("_bytes"))
                    artifacts.append(artifact)
                    message_artifacts.append(str(artifact["name"]))
                else:
                    warnings.add("One referenced file was omitted because it was unavailable, unsafe, or too large.")
                continue
            kept_lines.append(line)
        sanitized = _sanitize_content("\n".join(kept_lines))
        if not sanitized:
            continue
        remaining = _MAX_TOTAL_TEXT_CHARS - total_text_chars
        if remaining <= 0:
            warnings.add("Older conversation detail was omitted to keep the public snapshot bounded.")
            continue
        sanitized = sanitized[:remaining]
        total_text_chars += len(sanitized)
        item: dict[str, Any] = {"role": role, "content": sanitized}
        timestamp = message.get("timestamp")
        if isinstance(timestamp, (int, float)) and timestamp >= 0:
            item["timestamp"] = float(timestamp)
        if message_artifacts:
            item["artifacts"] = message_artifacts
        public_messages.append(item)
    if not any(item["role"] == "user" for item in public_messages):
        raise ResearchShareError("The session has no public user question to share")
    if not any(item["role"] == "assistant" for item in public_messages):
        raise ResearchShareError("The session has no public assistant response to share")

    title = str(session.get("title") or session.get("preview") or "Shared Oxaide research").strip()
    title = _sanitize_content(title)[:200] or "Shared Oxaide research"
    return {
        "title": title,
        "description": "A read-only research conversation shared from Oxaide.",
        "snapshot": {
            "schema_version": _SCHEMA_VERSION,
            "messages": public_messages,
            "artifacts": artifacts,
        },
        "warnings": sorted(warnings),
    }


def session_fingerprint(runtime_key: str, session_id: str) -> str:
    return hashlib.sha256(f"research-share:v1:{runtime_key}:{session_id}".encode("utf-8")).hexdigest()


def snapshot_digest(preview: dict[str, Any]) -> str:
    canonical = json.dumps(preview["snapshot"], separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def publish_research_share(*, workspace_id: str, user_id: str, runtime_key: str,
                           session_id: str, preview: dict[str, Any], expires_in_days: int) -> dict[str, Any]:
    payload = {
        "action": "publish",
        "workspace_id": workspace_id,
        "user_id": user_id,
        "session_fingerprint": session_fingerprint(runtime_key, session_id),
        "client_nonce": uuid.uuid4().hex,
        "expires_in_days": expires_in_days,
        "title": preview["title"],
        "description": preview.get("description"),
        "snapshot": preview["snapshot"],
    }
    return _send(payload)


def _share_store_path() -> Path:
    return get_hermes_home() / "oxaide-research-shares.json"


def list_recorded_shares(session_id: str) -> list[dict[str, Any]]:
    path = _share_store_path()
    if not path.is_file():
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and row.get("session_id") == session_id]


def record_share(session_id: str, result: dict[str, Any]) -> None:
    path = _share_store_path()
    rows: list[dict[str, Any]] = []
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                rows = [row for row in loaded if isinstance(row, dict)]
        except (OSError, json.JSONDecodeError):
            rows = []
    rows.append({
        "session_id": session_id,
        "share_id": result.get("share_id"),
        "public_url": result.get("public_url"),
        "expires_at": result.get("expires_at"),
    })
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def remove_recorded_share(share_id: str) -> None:
    path = _share_store_path()
    if not path.is_file():
        return
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    kept = [row for row in rows if isinstance(row, dict) and row.get("share_id") != share_id]
    path.write_text(json.dumps(kept, separators=(",", ":")), encoding="utf-8")
    os.chmod(path, 0o600)


def revoke_research_share(*, workspace_id: str, user_id: str, share_id: str) -> dict[str, Any]:
    return _send({
        "action": "revoke",
        "workspace_id": workspace_id,
        "user_id": user_id,
        "share_id": share_id,
        "client_nonce": uuid.uuid4().hex,
    })


def sign_research_share_body(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        b"research-share:v1:" + timestamp.encode("ascii") + b"." + body,
        hashlib.sha256,
    ).hexdigest()


def _send(payload: dict[str, Any]) -> dict[str, Any]:
    endpoint = str(os.environ.get("OXAIDE_RESEARCH_SHARE_ENDPOINT") or _DEFAULT_ENDPOINT).strip()
    secret = str(os.environ.get("HERMES_OXAIDE_RESEARCH_SHARE_SIGNING_SECRET") or "").strip()
    if len(secret) < 32:
        raise ResearchShareError("Oxaide research sharing is not configured")
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    timestamp = str(int(time.time()))
    signature = sign_research_share_body(secret, timestamp, body)
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Oxaide-Hermes-Runtime/1",
            "X-Oxaide-Research-Timestamp": timestamp,
            "X-Oxaide-Research-Signature": signature,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10.0) as response:
            status = int(response.status)
            raw = response.read(32 * 1024)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        raw = exc.read(32 * 1024)
    except Exception as exc:
        raise ResearchShareError(f"Oxaide share endpoint unavailable ({type(exc).__name__})") from exc
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResearchShareError("Oxaide share endpoint returned invalid JSON") from exc
    if status < 200 or status >= 300 or not isinstance(result, dict) or result.get("ok") is not True:
        code = result.get("code") if isinstance(result, dict) else None
        raise ResearchShareError(str(code or f"Oxaide share endpoint returned HTTP {status}"))
    return result


def published_at_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
