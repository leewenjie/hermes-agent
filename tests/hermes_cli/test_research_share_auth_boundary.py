from hermes_cli.dashboard_auth.public_paths import PUBLIC_API_PATHS
import pytest


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
