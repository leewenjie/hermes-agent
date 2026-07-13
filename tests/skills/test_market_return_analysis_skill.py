from __future__ import annotations

import importlib.util
from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = ROOT / "skills" / "research" / "market-return-analysis"
SCRIPT = SKILL_DIR / "scripts" / "analyze_returns.py"


def _module():
    spec = importlib.util.spec_from_file_location("market_return_analysis", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prices() -> list[dict]:
    values = [100, 101, 99, 102, 98, 103, 104, 100, 106, 108, 105, 110]
    return [
        {"date": f"2026-01-{index + 1:02d}", "adjusted_close": value}
        for index, value in enumerate(values)
    ]


def test_skill_frontmatter_matches_repository_contract():
    content = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert content.startswith("---\n")
    match = re.match(r"---\n(.*?)\n---\n", content, re.DOTALL)
    assert match
    frontmatter = yaml.safe_load(match.group(1))
    assert frontmatter["name"] == "market-return-analysis"
    assert frontmatter["description"].endswith(".")
    assert len(frontmatter["description"]) <= 60
    assert "## Verification" in content


def test_analysis_preserves_distribution_invariants():
    module = _module()
    report = module.analyze("SPY", _prices(), "https://example.test/spy")
    stats = report["statistics"]

    assert report["observation_count"] == report["return_count"] + 1
    assert stats["p01"] <= stats["p05"] <= stats["median_daily_return"]
    assert stats["median_daily_return"] <= stats["p95"] <= stats["p99"]
    assert stats["worst_day"] <= stats["historical_expected_shortfall_95"]
    assert stats["maximum_drawdown"] <= 0
    assert 0 <= stats["positive_day_ratio"] <= 1
    assert "not the index itself" in report["proxy_note"]


def test_artifact_pack_is_complete_and_linked(tmp_path):
    module = _module()
    report = module.analyze("SPY", _prices(), "https://example.test/spy")
    paths = module.write_artifacts(report, tmp_path)

    assert set(paths) == {"json", "markdown", "svg"}
    markdown = Path(paths["markdown"]).read_text(encoding="utf-8")
    svg = Path(paths["svg"]).read_text(encoding="utf-8")
    assert "SPY_return_distribution.svg" in markdown
    assert "https://example.test/spy" in markdown
    assert "not financial advice" in markdown
    assert "<svg" in svg
    assert "daily adjusted-return distribution" in svg
