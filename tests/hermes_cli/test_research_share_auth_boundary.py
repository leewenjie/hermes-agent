from hermes_cli.dashboard_auth.public_paths import PUBLIC_API_PATHS
import pytest
from types import SimpleNamespace


class _SessionDb:
    def __init__(self, session):
        self.session = session

    def close(self):
        pass

    def get_messages(self, _session_id):
        return [
            {"role": "user", "content": "Question"},
            {"role": "assistant", "content": "Answer"},
        ]

    def get_session(self, _session_id):
        return self.session

    def resolve_resume_session_id(self, session_id):
        return session_id

    def resolve_session_id(self, session_id):
        return session_id


def test_research_share_management_apis_remain_authenticated():
    assert "/api/research-shares" not in PUBLIC_API_PATHS
    assert "/api/research-shares/preview" not in PUBLIC_API_PATHS


def test_research_share_rejects_session_without_explicit_owner(monkeypatch):
    from fastapi import HTTPException
    import hermes_cli.web_server as web_server

    db = _SessionDb({"id": "session-1", "title": "Research", "user_id": ""})
    monkeypatch.setattr(web_server, "_open_session_db_for_profile", lambda _profile: db)

    with pytest.raises(HTTPException) as exc_info:
        web_server._research_share_preview("session-1", "user-1")

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Session does not belong to this user"


def test_research_share_accepts_owner_repaired_by_trusted_resume(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from hermes_state import SessionDB
    import hermes_cli.web_server as web_server
    from tui_gateway import server

    db_path = tmp_path / "state.db"
    db = SessionDB(db_path=db_path)
    db.create_session("session-1", "tui")
    db.append_message("session-1", "user", "Question")
    db.append_message("session-1", "assistant", "Answer")

    row = db.get_session("session-1")
    repaired = server._reconcile_trusted_session_owner(
        db, "session-1", row, "user-1"
    )
    assert repaired["user_id"] == "user-1"
    db.close()

    monkeypatch.setattr(
        web_server,
        "_open_session_db_for_profile",
        lambda _profile: SessionDB(db_path=db_path),
    )
    session_id, preview = web_server._research_share_preview("session-1", "user-1")
    assert session_id == "session-1"
    assert [message["content"] for message in preview["snapshot"]["messages"]] == [
        "Question",
        "Answer",
    ]

    with pytest.raises(HTTPException) as exc_info:
        web_server._research_share_preview("session-1", "user-2")
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Session does not belong to this user"


def test_loopback_oxaide_dev_share_publish_view_and_revoke(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import hermes_cli.web_server as web_server

    db = _SessionDb({
        "id": "session-1",
        "title": "<script>alert(1)</script> Research",
        "user_id": "",
    })
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_INTERNAL_OXAIDE_LOOPBACK_DEV", "1")
    monkeypatch.delenv("HERMES_OXAIDE_RUNTIME_KEY", raising=False)
    monkeypatch.setattr(web_server, "_open_session_db_for_profile", lambda _profile: db)
    monkeypatch.setattr(
        web_server,
        "_dashboard_branding_settings",
        lambda: {"product": "oxaide"},
    )
    monkeypatch.setattr(web_server.app.state, "bound_host", "127.0.0.1", raising=False)
    monkeypatch.setattr(web_server.app.state, "auth_required", False, raising=False)
    client = TestClient(web_server.app, base_url="http://127.0.0.1")
    auth = {web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN}

    preview = client.post(
        "/api/research-shares/preview",
        headers=auth,
        json={"session_id": "session-1"},
    )
    assert preview.status_code == 200
    published = client.post(
        "/api/research-shares",
        headers=auth,
        json={
            "session_id": "session-1",
            "snapshot_sha256": preview.json()["snapshot_sha256"],
            "expires_in_days": 7,
        },
    )
    assert published.status_code == 200
    public_path = published.json()["public_url"].removeprefix("http://127.0.0.1")

    viewed = client.get(public_path)
    assert viewed.status_code == 200
    assert "<script>alert(1)</script>" not in viewed.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in viewed.text
    assert viewed.headers["cache-control"].startswith("private, no-store")
    assert viewed.headers["x-robots-tag"] == "noindex, nofollow, noarchive"
    assert "default-src 'none'" in viewed.headers["content-security-policy"]

    revoked = client.post(
        "/api/research-shares",
        headers=auth,
        json={"action": "revoke", "share_id": published.json()["share_id"]},
    )
    assert revoked.status_code == 200
    assert client.get(public_path).status_code == 404


def test_local_share_mode_requires_loopback_and_internal_marker(monkeypatch):
    import hermes_cli.web_server as web_server

    monkeypatch.setattr(
        web_server,
        "_dashboard_branding_settings",
        lambda: {"product": "oxaide"},
    )
    monkeypatch.setattr(web_server.app.state, "auth_required", False, raising=False)
    monkeypatch.setattr(web_server.app.state, "bound_host", "127.0.0.1", raising=False)
    monkeypatch.delenv("HERMES_INTERNAL_OXAIDE_LOOPBACK_DEV", raising=False)
    assert web_server._local_research_share_dev_enabled() is False

    monkeypatch.setenv("HERMES_INTERNAL_OXAIDE_LOOPBACK_DEV", "1")
    monkeypatch.setattr(web_server.app.state, "bound_host", "0.0.0.0", raising=False)
    assert web_server._local_research_share_dev_enabled() is False


def test_managed_session_reads_require_matching_owner(monkeypatch):
    from fastapi import HTTPException
    import hermes_cli.web_server as web_server

    monkeypatch.setattr(
        web_server,
        "_dashboard_branding_settings",
        lambda: {"product": "oxaide"},
    )
    monkeypatch.setenv("HERMES_OXAIDE_RUNTIME_KEY", "r" * 48)
    monkeypatch.delenv("HERMES_INTERNAL_OXAIDE_LOOPBACK_DEV", raising=False)
    request = SimpleNamespace(
        state=SimpleNamespace(
            session=SimpleNamespace(
                provider="oxaide-demo",
                org_id="workspace-1",
                user_id="user-1",
            )
        )
    )

    web_server._require_oxaide_session_owner(
        request,
        {"id": "owned", "user_id": "user-1"},
    )

    for session in (
        {"id": "foreign", "user_id": "user-2"},
        {"id": "ownerless", "user_id": ""},
    ):
        with pytest.raises(HTTPException) as exc_info:
            web_server._require_oxaide_session_owner(request, session)
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "Session does not belong to this user"

    with pytest.raises(HTTPException) as exc_info:
        web_server._require_oxaide_session_owner(
            request,
            {"id": "owned", "user_id": "user-1"},
            profile="sibling-profile",
        )
    assert exc_info.value.status_code == 403


def test_managed_session_index_only_lists_authenticated_owner(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from hermes_state import SessionDB
    import hermes_cli.web_server as web_server

    db_path = tmp_path / "state.db"
    db = SessionDB(db_path=db_path)
    try:
        db.create_session("owned", "cli", user_id="user-1")
        db.create_session("foreign", "cli", user_id="user-2")
        db.create_session("ownerless", "cli")
        db.append_message("owned", "user", "shared search needle owned")
        db.append_message("foreign", "user", "shared search needle foreign")
        db.append_message("ownerless", "user", "shared search needle ownerless")
    finally:
        db.close()

    monkeypatch.setattr(
        web_server,
        "_dashboard_branding_settings",
        lambda: {"product": "oxaide"},
    )
    monkeypatch.setattr(
        web_server,
        "_open_session_db_for_profile",
        lambda _profile, **_kwargs: SessionDB(db_path=db_path),
    )
    monkeypatch.setattr(web_server, "_local_research_share_dev_enabled", lambda _request: False)
    monkeypatch.setattr(
        web_server,
        "_oxaide_share_identity",
        lambda _request: ("workspace-1", "user-1", "runtime-key"),
    )

    client = TestClient(web_server.app)
    headers = {web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN}
    response = client.get(
        "/api/sessions?limit=20&offset=0",
        headers=headers,
    )

    assert response.status_code == 200
    assert [row["id"] for row in response.json()["sessions"]] == ["owned"]
    assert response.json()["total"] == 1

    searched = client.get("/api/sessions/search?q=shared+search+needle", headers=headers)
    assert searched.status_code == 200
    assert [row["session_id"] for row in searched.json()["results"]] == ["owned"]

    assert client.get("/api/sessions/owned/export", headers=headers).status_code == 200
    for path in ("foreign", "ownerless"):
        assert client.get(f"/api/sessions/{path}/export", headers=headers).status_code == 403
        assert client.patch(
            f"/api/sessions/{path}",
            headers=headers,
            json={"title": "unauthorized"},
        ).status_code == 403
        assert client.delete(f"/api/sessions/{path}", headers=headers).status_code == 403

    verify_db = SessionDB(db_path=db_path)
    try:
        assert verify_db.get_session("foreign") is not None
        assert verify_db.get_session("ownerless") is not None
    finally:
        verify_db.close()
