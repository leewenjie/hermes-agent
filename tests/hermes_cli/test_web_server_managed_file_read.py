from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from hermes_cli import web_server


def _request() -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/files/read",
        "raw_path": b"/api/files/read",
        "query_string": b"",
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "app": SimpleNamespace(state=SimpleNamespace(auth_required=False)),
    }
    return Request(scope)


def test_managed_file_read_returns_small_text_artifact(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_DASHBOARD_FILES_ROOT", str(tmp_path))
    artifact = tmp_path / "analysis.json"
    artifact.write_text('{"status":"reviewed"}', encoding="utf-8")

    result = asyncio.run(web_server.read_managed_file(_request(), str(artifact)))

    assert result["name"] == "analysis.json"
    assert result["path"] == "/analysis.json"
    assert result["mime_type"] == "application/json"
    assert result["locked_root"] == "/"
    assert result["can_change_path"] is False
    header, encoded = result["data_url"].split(",", 1)
    assert header == "data:application/json;base64"
    assert base64.b64decode(encoded) == b'{"status":"reviewed"}'


def test_managed_file_read_rejects_outside_locked_root(monkeypatch, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside.csv"
    outside.write_text("x,y\n1,2\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_DASHBOARD_FILES_ROOT", str(root))

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(web_server.read_managed_file(_request(), str(outside)))

    # A leading slash is a virtual managed-root path, not a host path. The
    # outside host file is therefore neither reached nor disclosed.
    assert exc_info.value.status_code == 404
