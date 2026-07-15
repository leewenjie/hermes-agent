import asyncio

from hermes_cli import web_server


class _FakeSessionDB:
    """Fake backing the /api/sessions/search endpoint.

    The endpoint surfaces direct session-id matches first, then FTS message
    matches, deduping both by compression lineage root. This fake has no
    compression chains (get_session returns no parent), so each session is its
    own lineage root.
    """

    closed = False

    def search_sessions_by_id(self, query, limit=20, include_archived=True):
        assert query == "20260603"
        assert include_archived is True
        return [
            {
                "id": "20260603_090200_exact",
                "preview": "ID match preview",
                "source": "cli",
                "model": "claude",
                "started_at": 100,
            }
        ]

    def search_messages(self, query, limit=20):
        assert query == "20260603*"
        return [
            {
                "session_id": "20260603_090200_exact",
                "snippet": "duplicate content hit should not replace ID hit",
                "role": "user",
                "source": "cli",
                "model": "claude",
                "session_started": 100,
            },
            {
                "session_id": "content_session",
                "snippet": "content hit",
                "role": "assistant",
                "source": "desktop",
                "model": "gpt",
                "session_started": 200,
            },
        ]

    def get_session(self, session_id):
        # No compression chains in this fixture — every session is its own root.
        return {
            "id": session_id,
            "parent_session_id": None,
            "source": "cli" if session_id.startswith("20260603") else "desktop",
            "model": "claude" if session_id.startswith("20260603") else "gpt",
            "title": "Exact ID session" if session_id.startswith("20260603") else "Content session",
            "started_at": 100 if session_id.startswith("20260603") else 200,
            "ended_at": 300,
            "last_active": 300,
            "message_count": 4,
            "tool_call_count": 2,
            "input_tokens": 120,
            "output_tokens": 80,
            "preview": "Stored preview",
        }

    def get_compression_tip(self, session_id):
        return session_id

    def close(self):
        self.closed = True


def test_desktop_session_search_merges_id_matches_before_content_matches(monkeypatch):
    monkeypatch.setattr("hermes_state.SessionDB", _FakeSessionDB)

    response = asyncio.run(web_server.search_sessions(q="20260603", limit=2))

    # ID match surfaces first; the content hit on the SAME session is deduped
    # by lineage root (not double-listed); the unrelated content hit follows.
    assert response == {
        "results": [
            {
                "session_id": "20260603_090200_exact",
                "lineage_root": "20260603_090200_exact",
                "snippet": "ID match preview",
                "role": None,
                "source": "cli",
                "model": "claude",
                "session_started": 100,
                "session": {
                    "id": "20260603_090200_exact",
                    "source": "cli",
                    "model": "claude",
                    "title": "Exact ID session",
                    "started_at": 100,
                    "ended_at": 300,
                    "last_active": 300,
                    "is_active": False,
                    "message_count": 4,
                    "tool_call_count": 2,
                    "input_tokens": 120,
                    "output_tokens": 80,
                    "preview": "Stored preview",
                    "parent_session_id": None,
                },
            },
            {
                "session_id": "content_session",
                "lineage_root": "content_session",
                "snippet": "content hit",
                "role": "assistant",
                "source": "desktop",
                "model": "gpt",
                "session_started": 200,
                "session": {
                    "id": "content_session",
                    "source": "desktop",
                    "model": "gpt",
                    "title": "Content session",
                    "started_at": 200,
                    "ended_at": 300,
                    "last_active": 300,
                    "is_active": False,
                    "message_count": 4,
                    "tool_call_count": 2,
                    "input_tokens": 120,
                    "output_tokens": 80,
                    "preview": "Stored preview",
                    "parent_session_id": None,
                },
            },
        ]
    }
