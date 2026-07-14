from __future__ import annotations

import pytest

from tui_gateway import server


@pytest.fixture
def oxaide_runtime(monkeypatch):
    monkeypatch.setenv("HERMES_OXAIDE_WORKSPACE_ID", "workspace-1")
    monkeypatch.setenv("HERMES_OXAIDE_RUNTIME_KEY", "a" * 48)


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


def test_non_oxaide_runtime_keeps_normal_flexible_skill_behavior(monkeypatch):
    monkeypatch.delenv("HERMES_OXAIDE_WORKSPACE_ID", raising=False)
    monkeypatch.delenv("HERMES_OXAIDE_RUNTIME_KEY", raising=False)
    monkeypatch.setenv("HERMES_TUI_SKILLS", "custom-skill,stocks")
    assert server._parse_tui_skills_env() == ["custom-skill", "stocks"]