import pytest

from hermes_cli import web_server


@pytest.mark.asyncio
async def test_attach_token_reuses_same_session(monkeypatch):
    """Two connects with the same ?attach= token hit one spawned bridge."""
    spawned = []

    class FakeBridge:
        def __init__(self):
            self.alive = True

        def read(self, timeout):
            return b""        # idle forever

        def write(self, data):
            pass

        def resize(self, cols, rows):
            pass

        def close(self):
            self.alive = False

    def fake_spawn(argv, cwd=None, env=None):
        b = FakeBridge()
        spawned.append(b)
        return b

    monkeypatch.setattr(web_server.PtyBridge, "spawn", staticmethod(fake_spawn))
    # bypass auth + argv resolution for the test
    monkeypatch.setattr(
        web_server,
        "_ws_auth_context",
        lambda ws: (None, "test", None),
    )
    monkeypatch.setattr(web_server, "_ws_host_origin_reason", lambda ws: None)
    monkeypatch.setattr(web_server, "_ws_client_reason", lambda ws: None)

    async def fake_argv(**kw):
        return (["x"], "/tmp", {})

    monkeypatch.setattr(web_server, "_resolve_chat_argv_async", fake_argv)

    from starlette.testclient import TestClient

    try:
        client = TestClient(web_server.app)
        with client.websocket_connect("/api/pty?attach=TOK1") as ws1:
            ws1.send_bytes(b"hi")
        with client.websocket_connect("/api/pty?attach=TOK1") as ws2:
            ws2.send_bytes(b"again")
        assert len(spawned) == 1                # reattached, did not respawn
    finally:
        await web_server.PTY_REGISTRY.close_all()


@pytest.mark.asyncio
async def test_attach_token_cannot_reuse_active_child_when_frozen(monkeypatch):
    """Fresh hosted auth replaces a same-token child from another access state."""
    spawned = []
    contexts = iter(
        [
            {"workspace_id": "workspace-1", "user_id": "user-1", "access_state": "active"},
            {"workspace_id": "workspace-1", "user_id": "user-1", "access_state": "frozen"},
        ]
    )

    class FakeBridge:
        def __init__(self):
            self.alive = True

        def read(self, timeout):
            return b""

        def write(self, data):
            pass

        def resize(self, cols, rows):
            pass

        def close(self):
            self.alive = False

    def fake_spawn(argv, cwd=None, env=None):
        bridge = FakeBridge()
        spawned.append(bridge)
        return bridge

    monkeypatch.setattr(web_server.PtyBridge, "spawn", staticmethod(fake_spawn))
    monkeypatch.setattr(
        web_server,
        "_ws_auth_context",
        lambda ws: (None, "test", next(contexts)),
    )
    monkeypatch.setattr(web_server, "_ws_host_origin_reason", lambda ws: None)
    monkeypatch.setattr(web_server, "_ws_client_reason", lambda ws: None)

    async def fake_argv(**kw):
        return (["x"], "/tmp", {})

    monkeypatch.setattr(web_server, "_resolve_chat_argv_async", fake_argv)

    from starlette.testclient import TestClient

    try:
        client = TestClient(web_server.app)
        with client.websocket_connect("/api/pty?attach=TOK-SCOPE") as ws1:
            ws1.send_bytes(b"active")
        with client.websocket_connect("/api/pty?attach=TOK-SCOPE") as ws2:
            ws2.send_bytes(b"frozen")
        assert len(spawned) == 2
        assert spawned[0].alive is False
        assert spawned[1].alive is True
    finally:
        await web_server.PTY_REGISTRY.close_all()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/pty", "/api/pty?attach=FROZEN"])
async def test_frozen_pty_drops_input_but_allows_resize(monkeypatch, path):
    """Managed frozen sockets cannot write bytes in legacy or keep-alive mode."""
    spawned = []

    class FakeBridge:
        def __init__(self):
            self.alive = True
            self.writes = []
            self.resizes = []

        def read(self, timeout):
            return b""

        def write(self, data):
            self.writes.append(data)

        def resize(self, cols, rows):
            self.resizes.append((cols, rows))

        def close(self):
            self.alive = False

    def fake_spawn(argv, cwd=None, env=None):
        bridge = FakeBridge()
        spawned.append(bridge)
        return bridge

    monkeypatch.setattr(web_server.PtyBridge, "spawn", staticmethod(fake_spawn))
    monkeypatch.setattr(
        web_server,
        "_ws_auth_context",
        lambda ws: (
            None,
            "test",
            {
                "workspace_id": "workspace-1",
                "user_id": "user-1",
                "access_state": "frozen",
            },
        ),
    )
    monkeypatch.setattr(web_server, "_ws_host_origin_reason", lambda ws: None)
    monkeypatch.setattr(web_server, "_ws_client_reason", lambda ws: None)

    async def fake_argv(**kw):
        return (["x"], "/tmp", {})

    monkeypatch.setattr(web_server, "_resolve_chat_argv_async", fake_argv)

    from starlette.testclient import TestClient

    try:
        client = TestClient(web_server.app)
        with client.websocket_connect(path) as ws:
            ws.send_bytes(b"blocked keyboard input")
            ws.send_bytes(b"\x1b[RESIZE:120;40]")
        assert len(spawned) == 1
        assert spawned[0].writes == []
        assert spawned[0].resizes == [(120, 40)]
    finally:
        await web_server.PTY_REGISTRY.close_all()
