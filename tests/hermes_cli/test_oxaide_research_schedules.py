"""Behavioral coverage for the managed scheduled-research boundary."""

import json

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
        "state",
    }
    assert created["schedule_input"] == "every 1440m"
    assert created["enabled"] is True

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
