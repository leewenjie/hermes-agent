from __future__ import annotations

import threading

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
    assert server._load_enabled_toolsets() == sorted(server._OXAIDE_APPROVED_TOOLSETS)

    approved = sorted(server._OXAIDE_APPROVED_TOOLSETS)
    monkeypatch.setenv("HERMES_TUI_TOOLSETS", ",".join([*approved, "browser"]))
    with pytest.raises(RuntimeError, match="unapproved: browser"):
        server._load_enabled_toolsets()

    monkeypatch.setenv("HERMES_TUI_TOOLSETS", ",".join(approved[1:]))
    with pytest.raises(RuntimeError, match="missing:"):
        server._load_enabled_toolsets()


def test_oxaide_skill_policy_requires_fixed_research_bundle(monkeypatch, oxaide_runtime):
    expected = sorted(server._OXAIDE_REQUIRED_SKILLS)
    monkeypatch.delenv("HERMES_TUI_SKILLS", raising=False)
    assert server._parse_tui_skills_env() == expected

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


def test_oxaide_command_catalog_only_exposes_research_actions(oxaide_runtime):
    response = server._methods["commands.catalog"]("rid", {})["result"]

    commands = {pair[0] for pair in response["pairs"]}
    assert commands == {f"/{name}" for name in server._OXAIDE_RESEARCH_COMMANDS}
    assert "/status" not in commands
    assert response["skill_count"] == 0


def test_oxaide_slash_completion_only_returns_research_actions(oxaide_runtime):
    response = server._methods["complete.slash"]("rid", {"text": "/r"})["result"]

    assert {item["text"] for item in response["items"]} == {"/resume", "/retry"}
    assert all(item["text"] != "/reasoning" for item in response["items"])


@pytest.mark.parametrize("method_name,params", [
    ("command.dispatch", {"name": "status", "arg": ""}),
    ("slash.exec", {"command": "status"}),
    ("cli.exec", {"argv": ["config", "set", "model", "other-model"]}),
])
def test_oxaide_direct_operator_command_execution_is_rejected(
    oxaide_runtime, method_name, params
):
    response = server._methods[method_name]("rid", params)

    assert response["error"]["code"] == 4030


@pytest.mark.parametrize(
    "method_name",
    [
        "config.get",
        "commands.catalog",
        "command.dispatch",
        "complete.slash",
        "model.options",
        "tools.list",
        "toolsets.list",
        "skills.manage",
        "shell.exec",
    ],
)
def test_oxaide_dispatch_hides_generic_hermes_rpc_surface(
    oxaide_runtime, method_name
):
    response = server.handle_request({
        "jsonrpc": "2.0",
        "id": "rid",
        "method": method_name,
        "params": {},
    })

    assert response["error"] == {
        "code": -32601,
        "message": f"unknown method: {method_name}",
    }


def test_oxaide_dispatch_keeps_research_session_methods(oxaide_runtime, monkeypatch):
    monkeypatch.setitem(
        server._methods,
        "session.status",
        lambda rid, _params: server._ok(rid, {"status": "idle"}),
    )

    response = server.handle_request({
        "jsonrpc": "2.0",
        "id": "rid",
        "method": "session.status",
        "params": {},
    })

    assert response["result"] == {"status": "idle"}


def test_oxaide_dispatch_allows_read_only_pet_cells(oxaide_runtime, monkeypatch):
    monkeypatch.setitem(
        server._methods,
        "pet.cells",
        lambda rid, _params: server._ok(rid, {"frames": []}),
    )

    response = server.handle_request({
        "jsonrpc": "2.0",
        "id": "rid",
        "method": "pet.cells",
        "params": {"graphics": False, "state": "idle"},
    })

    assert response["result"] == {"frames": []}


def test_oxaide_history_projection_removes_internal_messages_and_reasoning(oxaide_runtime):
    messages = server._history_to_messages([
        {"role": "system", "content": "private system prompt"},
        {"role": "user", "content": "customer question"},
        {
            "role": "assistant",
            "content": "customer answer",
            "reasoning": "private reasoning",
        },
        {"role": "tool", "content": "private tool result", "tool_name": "terminal"},
        {"role": "assistant", "content": "[CONTEXT SUMMARY]: private handoff"},
        {
            "role": "assistant",
            "content": (
                "[CONTEXT SUMMARY]: private handoff\n"
                "--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---\n"
                "customer-visible answer after compaction"
            ),
        },
    ])

    assert messages == [
        {"role": "user", "text": "customer question"},
        {"role": "assistant", "text": "customer answer"},
        {"role": "assistant", "text": "customer-visible answer after compaction"},
    ]


def test_oxaide_history_projection_uses_safe_attachment_placeholders(oxaide_runtime):
    messages = server._history_to_messages([
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Review these"},
                {"type": "image_url", "image_url": {"url": "file:///opt/data/private.png"}},
                {"type": "input_audio", "data": "private-audio"},
                {"type": "private_blob", "path": "/opt/data/private.bin"},
            ],
        },
        {
            "role": "assistant",
            "content": {"type": "image_url", "image_url": "file:///opt/data/result.png"},
        },
    ])

    assert messages == [
        {"role": "user", "text": "Review these\n[image]\n[audio]\n[attachment]"},
        {"role": "assistant", "text": "[image]"},
    ]


def test_oxaide_history_projection_redacts_host_paths(oxaide_runtime):
    messages = server._history_to_messages([
        {
            "role": "assistant",
            "content": "Saved /opt/data/private/report.xlsx and C:\\Users\\ops\\secret.csv",
        },
    ])

    assert messages == [{
        "role": "assistant",
        "text": "Saved [research file] and [research file]",
    }]


def test_oxaide_session_rpc_rows_hide_runtime_metadata(monkeypatch, oxaide_runtime):
    class DB:
        def list_sessions_rich(self, **_kwargs):
            return [{
                "id": "stored-1",
                "title": "Research",
                "preview": "Customer preview",
                "started_at": 1,
                "message_count": 2,
                "source": "desktop",
                "model": "private-model",
            }]

    monkeypatch.setattr(server, "_get_db", lambda: DB())
    listed = server._methods["session.list"]("rid", {})["result"]["sessions"]
    assert listed == [{
        "id": "stored-1",
        "title": "Research",
        "preview": "Customer preview",
        "started_at": 1,
        "message_count": 2,
    }]

    session = {
        "agent": None,
        "created_at": 1,
        "history": [{"role": "user", "content": "Question"}],
        "history_lock": threading.RLock(),
        "last_active": 2,
        "session_key": "stored-1",
    }
    monkeypatch.setattr(server, "_session_live_title", lambda *_args: "Research")
    live = server._session_live_item("runtime-1", session)
    assert set(live) == {
        "current",
        "id",
        "last_active",
        "message_count",
        "preview",
        "session_key",
        "started_at",
        "status",
        "title",
    }

    payload = server._live_session_payload("runtime-1", session)
    assert payload["info"] == {
        "model": "Oxaide Research Engine",
        "skills": {},
        "tools": {},
        "lazy": True,
    }
    assert payload["session_id"] == "runtime-1"
    assert payload["session_key"] == "stored-1"


def test_oxaide_session_info_events_are_projected(monkeypatch, oxaide_runtime):
    written = []
    monkeypatch.setattr(server, "write_json", written.append)

    server._emit(
        "session.info",
        "runtime-1",
        {
            "model": "private-model",
            "provider": "private-provider",
            "cwd": "/private/workspace",
            "skills": {"private": ["skill"]},
            "tools": {"private": ["tool"]},
            "lazy": True,
        },
    )

    assert written[0]["params"]["payload"] == {
        "model": "Oxaide Research Engine",
        "skills": {},
        "tools": {},
        "lazy": True,
    }