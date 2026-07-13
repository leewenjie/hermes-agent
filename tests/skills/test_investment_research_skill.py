from __future__ import annotations

from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "research" / "investment-research" / "SKILL.md"


def _frontmatter() -> tuple[dict, str]:
    content = SKILL.read_text(encoding="utf-8")
    match = re.match(r"---\n(.*?)\n---\n", content, re.DOTALL)
    assert match
    return yaml.safe_load(match.group(1)), content


def test_skill_has_valid_metadata_and_concise_description():
    frontmatter, content = _frontmatter()

    assert frontmatter["name"] == "investment-research"
    assert frontmatter["description"].endswith(".")
    assert len(frontmatter["description"]) <= 60
    assert frontmatter["metadata"]["hermes"]["category"] == "research"
    assert "## Verification" in content


def test_skill_covers_core_finance_research_contract():
    _, content = _frontmatter()
    lowered = content.lower()

    for required in (
        "measurement contract",
        "counter-evidence",
        "source url",
        "historical inputs",
        "bear/base/bull",
        "place orders",
        "market-return-analysis",
        "comps-analysis",
        "hyperliquid",
    ):
        assert required in lowered


def test_skill_uses_modern_sections_in_order():
    _, content = _frontmatter()
    sections = [
        "## When to Use",
        "## Prerequisites",
        "## How to Run",
        "## Quick Reference",
        "## Procedure",
        "## Pitfalls",
        "## Verification",
    ]
    positions = [content.index(section) for section in sections]
    assert positions == sorted(positions)
