import base64
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from hermes_cli import oxaide_research_share
from hermes_cli.oxaide_research_share import (
    build_research_snapshot,
    load_local_research_share,
    list_recorded_shares,
    publish_local_research_share,
    record_share,
    remove_recorded_share,
    ResearchShareError,
    revoke_local_research_share,
    session_fingerprint,
    sign_research_share_body,
    snapshot_digest,
)


def _session():
    return {"title": "Revenue quality review", "preview": "fallback"}


def test_snapshot_structurally_excludes_private_roles_and_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_DASHBOARD_FILES_ROOT", str(tmp_path))
    messages = [
        {"role": "system", "content": "SYSTEM SECRET", "reasoning": "hidden"},
        {"role": "user", "content": "What changed in revenue quality?", "tool_calls": [{"secret": True}]},
        {"role": "tool", "content": "API_KEY=should-never-publish"},
        {"role": "assistant", "content": "Revenue rose, but cash conversion weakened.", "reasoning": "private chain"},
    ]

    result = build_research_snapshot(_session(), messages)

    assert [message["role"] for message in result["snapshot"]["messages"]] == ["user", "assistant"]
    serialized = str(result)
    assert "SYSTEM SECRET" not in serialized
    assert "should-never-publish" not in serialized
    assert "private chain" not in serialized
    assert "tool_calls" not in serialized


def test_snapshot_removes_compaction_secrets_and_local_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_DASHBOARD_FILES_ROOT", str(tmp_path))
    messages = [
        {"role": "user", "content": "Question"},
        {
            "role": "assistant",
            "content": (
                "[CONTEXT COMPACTION — REFERENCE ONLY]\nprivate context\n"
                "--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---\n"
                "Result saved in /opt/data/private/result.csv"
            ),
        },
    ]

    result = build_research_snapshot(_session(), messages)
    content = result["snapshot"]["messages"][1]["content"]

    assert "private context" not in content
    assert "/opt/data" not in content
    assert "[local file]" in content


def test_snapshot_removes_merged_compaction_and_inline_private_blocks(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_DASHBOARD_FILES_ROOT", str(tmp_path))
    messages = [
        {"role": "user", "content": "Question"},
        {
            "role": "assistant",
            "content": (
                "[PRIOR CONTEXT — for reference only; not a new message]\n"
                "Visible earlier answer\n"
                "[END OF PRIOR CONTEXT — COMPACTION SUMMARY BELOW]\n"
                "private summary\n"
                "--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---"
            ),
        },
        {"role": "assistant", "content": "<think>private reasoning</think>Public result<tool_call>secret args</tool_call>"},
    ]
    result = build_research_snapshot(_session(), messages)
    serialized = str(result)
    assert "Visible earlier answer" in serialized
    assert "private summary" not in serialized
    assert "private reasoning" not in serialized
    assert "secret args" not in serialized
    assert "Public result" in serialized


def test_snapshot_redacts_url_credentials_and_sensitive_query_values(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_DASHBOARD_FILES_ROOT", str(tmp_path))
    result = build_research_snapshot(_session(), [
        {"role": "user", "content": "Question"},
        {"role": "assistant", "content": "Source https://user:pass@example.com/data?range=5y&token=secret-value&X-Amz-Signature=abc#private"},
    ])
    content = result["snapshot"]["messages"][1]["content"]
    assert "user:pass" not in content
    assert "secret-value" not in content
    assert "#private" not in content
    assert "range=5y" in content
    assert "%5Bredacted%5D" in content


def test_snapshot_captures_safe_svg_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_DASHBOARD_FILES_ROOT", str(tmp_path))
    chart = tmp_path / "quality-chart.svg"
    chart.write_text('<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>')
    messages = [
        {"role": "user", "content": "Show the chart"},
        {"role": "assistant", "content": f"Here is the generated evidence.\nMEDIA:{chart}"},
    ]

    result = build_research_snapshot(_session(), messages)
    assistant = result["snapshot"]["messages"][1]
    artifact = result["snapshot"]["artifacts"][0]

    assert assistant["artifacts"] == ["quality-chart.svg"]
    assert "MEDIA:" not in assistant["content"]
    assert artifact["mime_type"] == "image/svg+xml"
    assert base64.b64decode(artifact["data_base64"]).startswith(b"<svg")
    assert len(artifact["sha256"]) == 64


def test_snapshot_rejects_active_svg_and_omits_symlinks(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_DASHBOARD_FILES_ROOT", str(tmp_path))
    active = tmp_path / "active.svg"
    active.write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
    with pytest.raises(ResearchShareError, match="active content"):
        build_research_snapshot(_session(), [
            {"role": "user", "content": "Question"},
            {"role": "assistant", "content": f"Answer\nMEDIA:{active}"},
        ])

    safe = tmp_path / "safe.svg"
    safe.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    link = tmp_path / "linked.svg"
    link.symlink_to(safe)
    result = build_research_snapshot(_session(), [
        {"role": "user", "content": "Question"},
        {"role": "assistant", "content": f"Answer\nMEDIA:{link}"},
    ])
    assert result["snapshot"]["artifacts"] == []
    assert result["warnings"]


def test_snapshot_omits_artifact_replaced_after_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_DASHBOARD_FILES_ROOT", str(tmp_path))
    chart = tmp_path / "chart.svg"
    replacement = tmp_path / "replacement.svg"
    chart.write_text('<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>')
    replacement.write_text('<svg xmlns="http://www.w3.org/2000/svg"><circle/></svg>')
    original_open = oxaide_research_share.os.open

    def swap_before_open(path, flags):
        replacement.replace(chart)
        return original_open(path, flags)

    monkeypatch.setattr(oxaide_research_share.os, "open", swap_before_open)
    result = build_research_snapshot(_session(), [
        {"role": "user", "content": "Question"},
        {"role": "assistant", "content": f"Answer\nMEDIA:{chart}"},
    ])

    assert result["snapshot"]["artifacts"] == []
    assert result["warnings"]


def test_session_fingerprint_is_stable_and_hides_raw_ids():
    fingerprint = session_fingerprint("runtime-secret-key", "session-private-id")
    assert fingerprint == session_fingerprint("runtime-secret-key", "session-private-id")
    assert fingerprint != session_fingerprint("runtime-secret-key", "other")
    assert "session-private-id" not in fingerprint
    assert len(fingerprint) == 64


def test_snapshot_digest_changes_with_public_content(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_DASHBOARD_FILES_ROOT", str(tmp_path))
    first = build_research_snapshot(_session(), [
        {"role": "user", "content": "Question"},
        {"role": "assistant", "content": "First answer"},
    ])
    second = build_research_snapshot(_session(), [
        {"role": "user", "content": "Question"},
        {"role": "assistant", "content": "Changed answer"},
    ])
    assert snapshot_digest(first) != snapshot_digest(second)


def test_recorded_shares_survive_dialog_reopen_and_can_be_removed(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    result = {
        "share_id": "11111111-1111-4111-8111-111111111111",
        "public_url": "https://oxaide.com/r/public-token",
        "expires_at": "2026-08-14T00:00:00Z",
    }
    record_share("session-1", result)
    assert list_recorded_shares("session-1")[0]["public_url"] == result["public_url"]
    remove_recorded_share(result["share_id"])
    assert list_recorded_shares("session-1") == []


def test_local_share_stores_only_token_hash_and_revokes_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    preview = build_research_snapshot(_session(), [
        {"role": "user", "content": "Question"},
        {"role": "assistant", "content": "Answer"},
    ])

    result = publish_local_research_share(
        session_id="session-1",
        preview=preview,
        expires_in_days=7,
        base_url="http://127.0.0.1:9119/",
    )
    token = urlsplit(result["public_url"]).path.rsplit("/", 1)[-1]
    stored_text = (tmp_path / "oxaide-research-share-dev-snapshots.json").read_text()
    stored = json.loads(stored_text)

    assert token not in stored_text
    assert len(stored[0]["token_sha256"]) == 64
    assert load_local_research_share(token)["snapshot"] == preview["snapshot"]

    revoke_local_research_share(result["share_id"])
    assert load_local_research_share(token) is None


def test_research_share_signature_matches_cross_language_vector():
    signature = sign_research_share_body(
        "test-research-share-secret-at-least-32-bytes",
        "1784097723",
        b'{"action":"publish","workspace_id":"workspace-1"}',
    )
    assert signature == "44ef9d2f867a18768286473cce322df37a15d58ed474c8df8f105af334b21aae"
