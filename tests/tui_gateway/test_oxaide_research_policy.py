from __future__ import annotations

import pytest

from tui_gateway import server


@pytest.fixture
def oxaide_runtime(monkeypatch):
    monkeypatch.setenv("HERMES_OXAIDE_WORKSPACE_ID", "workspace-1")
    monkeypatch.setenv("HERMES_OXAIDE_RUNTIME_KEY", "a" * 48)
    monkeypatch.setenv("HERMES_OXAIDE_MODEL", "managed-model")
    monkeypatch.setenv("HERMES_OXAIDE_PROVIDER", "managed-provider")


def test_oxaide_tool_policy_resolves_exact_approved_bundle(monkeypatch, oxaide_runtime):
    expected = sorted(server._OXAIDE_APPROVED_TOOLSETS)
    monkeypatch.setenv("HERMES_TUI_TOOLSETS", ",".join(expected))
    assert sorted(server._load_enabled_toolsets()) == expected


def test_oxaide_tool_policy_rejects_missing_or_unapproved_entries(monkeypatch, oxaide_runtime):
    monkeypatch.delenv("HERMES_TUI_TOOLSETS", raising=False)
    with pytest.raises(RuntimeError, match="requires HERMES_TUI_TOOLSETS"):
        server._load_enabled_toolsets()

    approved = sorted(server._OXAIDE_APPROVED_TOOLSETS)
    monkeypatch.setenv("HERMES_TUI_TOOLSETS", ",".join([*approved, "browser"]))
    with pytest.raises(RuntimeError, match="unapproved: browser"):
        server._load_enabled_toolsets()

    monkeypatch.setenv("HERMES_TUI_TOOLSETS", ",".join(approved[1:]))
    with pytest.raises(RuntimeError, match="missing:"):
        server._load_enabled_toolsets()


def test_oxaide_skill_policy_requires_fixed_research_bundle(monkeypatch, oxaide_runtime):
    expected = sorted(server._OXAIDE_REQUIRED_SKILLS)
    monkeypatch.setenv("HERMES_TUI_SKILLS", ",".join(expected))
    assert sorted(server._parse_tui_skills_env()) == expected

    monkeypatch.setenv("HERMES_TUI_SKILLS", "investment-research")
    with pytest.raises(RuntimeError, match="missing:"):
        server._parse_tui_skills_env()

    monkeypatch.setenv("HERMES_TUI_SKILLS", ",".join([*expected, "dcf-model"]))
    with pytest.raises(RuntimeError, match="unapproved: dcf-model"):
        server._parse_tui_skills_env()


def test_session_info_reports_successfully_preloaded_skills(monkeypatch):
    class Agent:
        model = "test-model"
        provider = "test-provider"
        reasoning_config = None
        service_tier = None
        tools = []

    monkeypatch.setattr(server, "_display_session_cwd", lambda _session: "/tmp")
    monkeypatch.setattr(server, "_git_branch_for_cwd", lambda _cwd: "")
    monkeypatch.setattr(server, "_get_usage", lambda _agent: {})
    monkeypatch.setattr(server, "_probe_credentials", lambda _agent: "")

    loaded = ["investment-research", "market-return-analysis", "stocks"]
    info = server._session_info(
        Agent(),
        {"session_key": "session-1", "preloaded_skills": loaded},
    )

    assert info["preloaded_skills"] == loaded


def test_pre_agent_preview_reports_managed_tools_and_skills(monkeypatch, oxaide_runtime):
    monkeypatch.setenv(
        "HERMES_TUI_TOOLSETS",
        ",".join(sorted(server._OXAIDE_APPROVED_TOOLSETS)),
    )
    monkeypatch.setenv(
        "HERMES_TUI_SKILLS",
        ",".join(sorted(server._OXAIDE_REQUIRED_SKILLS)),
    )

    preview = server._pre_agent_capability_preview()

    assert preview["capability_preview"] is True
    assert preview["preloaded_skills"] == sorted(server._OXAIDE_REQUIRED_SKILLS)
    assert preview["tools"]
    assert "web_search" in preview["tools"]["web"]
    assert "terminal" in preview["tools"]["terminal"]
    assert "read_file" in preview["tools"]["file"]


def test_pre_agent_preview_is_empty_for_unmanaged_sessions(monkeypatch):
    monkeypatch.delenv("HERMES_OXAIDE_WORKSPACE_ID", raising=False)
    monkeypatch.delenv("HERMES_OXAIDE_RUNTIME_KEY", raising=False)

    assert server._pre_agent_capability_preview() == {
        "tools": {},
        "skills": {},
        "preloaded_skills": [],
    }


def test_non_oxaide_runtime_keeps_normal_flexible_skill_behavior(monkeypatch):
    monkeypatch.delenv("HERMES_OXAIDE_WORKSPACE_ID", raising=False)
    monkeypatch.delenv("HERMES_OXAIDE_RUNTIME_KEY", raising=False)
    monkeypatch.setenv("HERMES_TUI_SKILLS", "custom-skill,stocks")
    assert server._parse_tui_skills_env() == ["custom-skill", "stocks"]


@pytest.mark.parametrize("prefix", ["replace-with-", "__RePlAcE_WiTh_"])
def test_placeholder_runtime_pins_do_not_activate_managed_policy(monkeypatch, prefix):
    monkeypatch.setenv("HERMES_OXAIDE_WORKSPACE_ID", f"{prefix}workspace-id")
    monkeypatch.setenv("HERMES_OXAIDE_RUNTIME_KEY", f"{prefix}runtime-key")
    monkeypatch.setenv("HERMES_TUI_TOOLSETS", "web,terminal")
    monkeypatch.setenv("HERMES_TUI_SKILLS", "custom-skill,stocks")

    assert server._load_enabled_toolsets() == ["web", "terminal"]
    assert server._parse_tui_skills_env() == ["custom-skill", "stocks"]


def test_oxaide_runtime_requires_and_returns_deployment_model_pin(monkeypatch, oxaide_runtime):
    assert server._oxaide_managed_runtime() == ("managed-model", "managed-provider")

    monkeypatch.delenv("HERMES_OXAIDE_MODEL")
    monkeypatch.delenv("HERMES_MODEL", raising=False)
    monkeypatch.delenv("HERMES_INFERENCE_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="deployment-pinned model and provider"):
        server._oxaide_managed_runtime()


def test_oxaide_runtime_clamps_parent_iterations(monkeypatch, oxaide_runtime):
    monkeypatch.setenv("HERMES_TUI_MAX_TURNS", "900")
    assert server._cfg_max_turns({}, 90) == 16

    monkeypatch.setenv("HERMES_TUI_MAX_TURNS", "8")
    assert server._cfg_max_turns({}, 90) == 8


def test_oxaide_runtime_rejects_model_switch(monkeypatch, oxaide_runtime):
    with pytest.raises(ValueError, match="managed by Oxaide"):
        server._apply_model_switch("sid", {}, "other-model")


def test_oxaide_model_inventory_is_read_only(monkeypatch, oxaide_runtime):
    response = server._methods["model.options"]("rid", {})
    payload = response["result"]

    assert payload["managed"] is True
    assert payload["current_model"] == "managed-model"
    assert payload["current_provider"] == "managed-provider"
    assert payload["providers"][0]["models"] == ["managed-model"]
    assert payload["providers"][0]["read_only"] is True


@pytest.mark.parametrize(
    ("method_name", "params"),
    [
        ("config.set", {"key": "model", "value": "other-model"}),
        ("config.set", {"key": "fast", "value": "on"}),
        ("config.set", {"key": "reasoning", "value": "high"}),
        ("model.save_key", {"slug": "openai", "api_key": "secret"}),
        ("model.disconnect", {"slug": "azure-foundry"}),
        ("preview.restart", {"url": "http://localhost:3000"}),
    ],
)
def test_oxaide_managed_rpc_mutations_are_rejected(
    monkeypatch, oxaide_runtime, method_name, params
):
    response = server._methods[method_name]("rid", params)
    assert response["error"]["code"] == 4030