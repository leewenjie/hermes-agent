"""Behavioral coverage for the managed scheduled-research boundary."""

import json
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from hermes_cli import web_server


@pytest.fixture
def managed_schedule_client(monkeypatch, tmp_path):
    hermes_home = tmp_path / "oxaide-profile"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text("model: test-model\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(
        web_server,
        "_dashboard_branding_settings",
        lambda: {"product": "oxaide"},
    )

    from cron import jobs as cron_jobs

    monkeypatch.setattr(
        cron_jobs,
        "_compute_provider_model_snapshots",
        lambda **_kwargs: (None, None),
    )

    previous_auth = getattr(web_server.app.state, "auth_required", None)
    previous_host = getattr(web_server.app.state, "bound_host", None)
    web_server.app.state.auth_required = False
    web_server.app.state.bound_host = None

    client = TestClient(web_server.app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    try:
        yield client, hermes_home
    finally:
        client.close()
        if previous_auth is None:
            delattr(web_server.app.state, "auth_required")
        else:
            web_server.app.state.auth_required = previous_auth
        if previous_host is None:
            if hasattr(web_server.app.state, "bound_host"):
                delattr(web_server.app.state, "bound_host")
        else:
            web_server.app.state.bound_host = previous_host


def _create(client, **overrides):
    payload = {
        "name": "Daily evidence review",
        "prompt": "Review material evidence changes for my saved thesis.",
        "schedule": "every 1d",
        **overrides,
    }
    return client.post("/api/research-schedules", json=payload)


def test_managed_schedule_lifecycle_and_safe_projection(managed_schedule_client):
    client, hermes_home = managed_schedule_client

    assert client.get("/api/research-schedules").json() == []

    created_response = _create(client)
    assert created_response.status_code == 200
    created = created_response.json()
    job_id = created["id"]
    assert set(created) == {
        "created_at",
        "enabled",
        "id",
        "last_run_at",
        "last_status",
        "name",
        "next_run_at",
        "prompt",
        "schedule",
        "schedule_display",
        "schedule_input",
        "completion_email_enabled",
        "state",
    }
    assert created["schedule_input"] == "every 1440m"
    assert created["enabled"] is True
    assert created["completion_email_enabled"] is False

    stored = json.loads(
        (hermes_home / "cron" / "jobs.json").read_text(encoding="utf-8")
    )["jobs"][0]
    assert stored["deliver"] == "local"
    assert stored["origin"] == {
        "type": web_server._OXAIDE_RESEARCH_SCHEDULE_ORIGIN,
    }
    assert stored["skills"] == sorted(web_server._OXAIDE_RESEARCH_SKILLS)
    assert stored["enabled_toolsets"] == sorted(web_server._OXAIDE_RESEARCH_TOOLSETS)
    assert stored["script"] is None
    assert stored["provider"] is None
    assert stored["workdir"] is None
    assert stored["attach_to_session"] is False

    updated = client.put(
        f"/api/research-schedules/{job_id}",
        json={
            "name": "Weekly thesis review",
            "prompt": "Review thesis evidence, risks, catalysts, and missing evidence.",
            "schedule": "0 9 * * 1",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Weekly thesis review"
    assert updated.json()["schedule_input"] == "0 9 * * 1"

    paused = client.post(f"/api/research-schedules/{job_id}/pause")
    assert paused.status_code == 200
    assert paused.json()["state"] == "paused"
    assert paused.json()["enabled"] is False

    resumed = client.post(f"/api/research-schedules/{job_id}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["state"] == "scheduled"
    assert resumed.json()["enabled"] is True

    removed = client.delete(f"/api/research-schedules/{job_id}")
    assert removed.status_code == 200
    assert removed.json() == {"ok": True}
    assert client.get("/api/research-schedules").json() == []


def test_managed_schedule_rejects_advanced_fields_and_profile_spoofing(
    managed_schedule_client,
):
    client, _hermes_home = managed_schedule_client

    advanced = _create(
        client,
        script="collect.py",
        provider="private-provider",
        enabled_toolsets=["browser"],
    )
    assert advanced.status_code == 422

    spoofed = client.get("/api/research-schedules?profile=sibling")
    assert spoofed.status_code == 404
    assert spoofed.json() == {"detail": "Not found"}

    generic = client.get("/api/cron/jobs")
    assert generic.status_code == 404
    assert generic.json() == {"detail": "Not found"}


def test_local_schedule_rejects_completion_email_delivery(managed_schedule_client):
    client, _hermes_home = managed_schedule_client

    response = _create(client, completion_email_enabled=True)

    assert response.status_code == 400
    assert response.json() == {
        "detail": "completion_email_not_supported_in_local_mode",
    }


def test_managed_schedule_cannot_mutate_unowned_cron_job(managed_schedule_client):
    client, _hermes_home = managed_schedule_client
    generic = web_server._call_cron_for_active_home(
        "create_job",
        prompt="operator-owned task",
        schedule="every 1h",
        name="operator task",
    )

    assert client.get("/api/research-schedules").json() == []
    assert client.post(
        f"/api/research-schedules/{generic['id']}/pause"
    ).status_code == 404
    assert client.delete(
        f"/api/research-schedules/{generic['id']}"
    ).status_code == 404


def test_hosted_managed_runtime_hides_scheduled_research(
    managed_schedule_client,
    monkeypatch,
):
    client, _hermes_home = managed_schedule_client
    monkeypatch.setenv("HERMES_OXAIDE_WORKSPACE_ID", "workspace-123")
    monkeypatch.setenv("HERMES_OXAIDE_RUNTIME_KEY", "runtime-123")

    assert web_server._oxaide_scheduled_research_enabled() is False
    assert client.get("/api/research-schedules").status_code == 404
    assert _create(client).status_code == 404


def test_hosted_schedule_gate_cannot_be_bypassed_by_config(
    managed_schedule_client,
    monkeypatch,
):
    client, hermes_home = managed_schedule_client
    monkeypatch.setenv("HERMES_OXAIDE_WORKSPACE_ID", "workspace-123")
    monkeypatch.setenv("HERMES_OXAIDE_RUNTIME_KEY", "runtime-123")
    (hermes_home / "config.yaml").write_text(
        "dashboard:\n  scheduled_research_enabled: true\nmodel: test-model\n",
        encoding="utf-8",
    )

    assert web_server._oxaide_scheduled_research_enabled() is False
    assert client.get("/api/research-schedules").status_code == 404


def test_hosted_schedule_capability_is_enabled_only_with_complete_runtime_configuration(
    managed_schedule_client,
    monkeypatch,
):
    client, _hermes_home = managed_schedule_client
    monkeypatch.setenv("HERMES_OXAIDE_WORKSPACE_ID", "workspace-123")
    monkeypatch.setenv("HERMES_OXAIDE_RUNTIME_KEY", "runtimekey1234567890abcd")
    monkeypatch.setenv("HERMES_OXAIDE_SCHEDULED_RESEARCH_SIGNING_SECRET", "s" * 43)
    monkeypatch.setenv("HERMES_OXAIDE_USAGE_SIGNING_SECRET", "u" * 43)
    from hermes_cli import oxaide_scheduled_research_control as control
    monkeypatch.setattr(control, "request_control", lambda *_args, **_kwargs: [])

    assert web_server._oxaide_scheduled_research_enabled() is True
    assert client.get("/api/research-schedules").status_code != 404


def test_local_schedule_capability_can_be_disabled(
    managed_schedule_client,
):
    client, hermes_home = managed_schedule_client
    (hermes_home / "config.yaml").write_text(
        "dashboard:\n  scheduled_research_enabled: false\nmodel: test-model\n",
        encoding="utf-8",
    )

    assert web_server._oxaide_scheduled_research_enabled() is False
    assert client.get("/api/research-schedules").status_code == 404


def test_hosted_schedule_control_proxies_create_and_revision_fenced_update(monkeypatch):
    from hermes_cli import oxaide_scheduled_research_control as control

    calls = []
    schedules = [{
        "id": "00000000-0000-4000-8000-000000000001",
        "revision": 3,
        "schedule": {"kind": "interval", "minutes": 60, "display": "Every hour"},
    }]

    def fake_request(user_id, action, **fields):
        calls.append((user_id, action, fields))
        if action == "list":
            return schedules
        return {"id": schedules[0]["id"], "revision": 4, **fields}

    monkeypatch.setattr(control, "request_control", fake_request)
    body = web_server.ResearchScheduleMutation(
        name="Market review",
        prompt="Review overnight markets.",
        schedule="every 1h",
        completion_email_enabled=True,
        request_id="00000000-0000-4000-8000-000000000099",
    )

    created = web_server._hosted_schedule_control_sync(
        "user-1", "create", body=body
    )
    assert created["mutation"]["schedule"]["kind"] == "interval"
    assert calls[-1][1] == "create"
    assert calls[-1][2]["request_id"] == body.request_id
    assert calls[-1][2]["mutation"]["completion_email_enabled"] is True

    updated = web_server._hosted_schedule_control_sync(
        "user-1", "update", job_id=schedules[0]["id"], body=body
    )
    assert updated["expected_revision"] == 3
    assert calls[-1][2]["schedule_id"] == schedules[0]["id"]
    assert calls[-1][2]["request_id"] == body.request_id


def test_hosted_control_response_projection_drops_private_runtime_fields(monkeypatch):
    from hermes_cli import oxaide_scheduled_research_control as control

    decoded = {
        "ok": True,
        "schedules": [{
            "id": "schedule-1",
            "revision": 2,
            "name": "Market review",
            "schedule": {
                "kind": "interval",
                "minutes": 60,
                "private_model": "private-model",
            },
            "provider": "private-provider",
            "model": "private-model",
        }],
    }

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return json.dumps(decoded).encode()

    monkeypatch.setenv("HERMES_OXAIDE_WORKSPACE_ID", "workspace-123")
    monkeypatch.setenv("HERMES_OXAIDE_RUNTIME_KEY", "runtime-123")
    monkeypatch.setenv("HERMES_OXAIDE_SCHEDULED_RESEARCH_SIGNING_SECRET", "s" * 43)
    monkeypatch.setattr(control.urllib.request, "urlopen", lambda *_a, **_k: Response())

    schedules = control.request_control("user-1", "list")

    assert schedules == [{
        "id": "schedule-1",
        "revision": 2,
        "name": "Market review",
        "schedule": {"kind": "interval", "minutes": 60},
    }]


def test_hosted_schedule_mutation_requires_stable_request_id(monkeypatch):
    from hermes_cli import oxaide_scheduled_research_control as control

    monkeypatch.setattr(control, "request_control", lambda *_args, **_kwargs: [])
    body = web_server.ResearchScheduleMutation(
        name="Market review",
        prompt="Review overnight markets.",
        schedule="every 1h",
    )

    with pytest.raises(web_server.HTTPException) as exc_info:
        web_server._hosted_schedule_control_sync("user-1", "create", body=body)
    assert exc_info.value.status_code == 400


def test_hosted_consent_control_forwards_authenticated_identity_and_request_id(monkeypatch):
    from hermes_cli import oxaide_scheduled_research_control as control

    calls = []
    consent = {
        "consentType": "scheduled_research_emails",
        "granted": False,
        "active": False,
        "confirmedEmail": "owner@example.com",
        "grantedAt": None,
        "withdrawnAt": "2026-07-31T00:00:00Z",
        "expiresAt": None,
        "method": "explicit",
        "legalBasis": "consent",
    }

    def fake_request(user_id, action, **fields):
        calls.append((user_id, action, fields))
        return consent

    monkeypatch.setattr(control, "request_control", fake_request)

    assert web_server._hosted_research_consent_control_sync(
        "user-1", "get_consent"
    ) == consent
    assert web_server._hosted_research_consent_control_sync(
        "user-1",
        "set_consent",
        enabled=False,
        request_id="00000000-0000-4000-8000-000000000099",
    ) == consent

    assert calls[0] == ("user-1", "get_consent", {})
    assert calls[1] == (
        "user-1",
        "set_consent",
        {
            "request_id": "00000000-0000-4000-8000-000000000099",
            "enabled": False,
        },
    )


def test_hosted_consent_user_id_comes_from_verified_session():
    request = SimpleNamespace(
        state=SimpleNamespace(session=SimpleNamespace(user_id="session-user-1"))
    )
    assert web_server._hosted_research_user_id(request) == "session-user-1"

    with pytest.raises(web_server.HTTPException) as exc_info:
        web_server._hosted_research_user_id(
            SimpleNamespace(state=SimpleNamespace(session=None))
        )
    assert exc_info.value.status_code == 401


def test_consent_path_has_an_exact_browser_allowlist():
    assert web_server._oxaide_research_api_allowed(
        "GET", "/api/research-schedules/consent"
    ) is True
    assert web_server._oxaide_research_api_allowed(
        "PUT", "/api/research-schedules/consent"
    ) is True
    assert web_server._oxaide_research_api_allowed(
        "POST", "/api/research-schedules/consent"
    ) is False
    assert web_server._oxaide_research_api_allowed(
        "GET", "/api/research-schedules/consent", scheduled_research_enabled=False
    ) is False


def test_loopback_session_token_can_reach_hosted_consent_route(monkeypatch, managed_schedule_client):
    client, _hermes_home = managed_schedule_client
    monkeypatch.setenv("HERMES_OXAIDE_WORKSPACE_ID", "workspace-123")
    monkeypatch.setenv("HERMES_OXAIDE_RUNTIME_KEY", "runtimekey1234567890abcd")
    monkeypatch.setenv("HERMES_OXAIDE_SCHEDULED_RESEARCH_SIGNING_SECRET", "s" * 43)
    monkeypatch.setenv("HERMES_OXAIDE_USAGE_SIGNING_SECRET", "u" * 43)
    monkeypatch.setattr(web_server, "_is_oxaide_hosted_runtime", lambda: True)
    monkeypatch.setattr(web_server, "_hosted_research_user_id", lambda _request: "loopback-user")

    from hermes_cli import oxaide_scheduled_research_control as control

    monkeypatch.setattr(
        control,
        "request_control",
        lambda user_id, action, **_fields: {
            "consentType": "scheduled_research_emails",
            "granted": True,
            "active": True,
            "confirmedEmail": "owner@example.com",
            "grantedAt": None,
            "withdrawnAt": None,
            "expiresAt": None,
            "method": "explicit",
            "legalBasis": "consent",
        },
    )

    response = client.get("/api/research-schedules/consent")
    assert response.status_code == 200
    assert response.json()["consentType"] == "scheduled_research_emails"
