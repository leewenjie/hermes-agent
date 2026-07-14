from __future__ import annotations

import importlib.util
import io
from pathlib import Path
import re
from unittest.mock import patch

import pytest
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


def test_symbol_validation_confines_artifact_paths(tmp_path):
    module = _module()
    for value in ("../../escape", "AAPL<script>", "", "A" * 33):
        with pytest.raises(ValueError):
            module.normalize_symbol(value)

    report = module.analyze("^GSPC", _prices(), "https://example.test/index")
    paths = module.write_artifacts(report, tmp_path)
    assert all(Path(path).parent == tmp_path.resolve() for path in paths.values())
    assert Path(paths["svg"]).name == "GSPC_return_distribution.svg"


def test_prices_are_sorted_deduplicated_and_annualized_by_calendar_time():
    module = _module()
    prices = [
        {"date": "2025-01-01", "adjusted_close": 100},
        {"date": "2026-01-01", "adjusted_close": 110},
        {"date": "2025-01-01", "adjusted_close": 101},
        {"date": "not-a-date", "adjusted_close": 999},
    ]
    report = module.analyze("SPY", prices, "https://example.test/spy")
    assert report["observation_count"] == 2
    assert report["start_date"] == "2025-01-01"
    assert report["end_date"] == "2026-01-01"
    assert report["statistics"]["annualized_return"] == pytest.approx(110 / 101 - 1, rel=0.002)


def test_live_fetch_rejects_oversized_payload():
    module = _module()
    response = io.BytesIO(b"x" * (module.MAX_RESPONSE_BYTES + 1))
    with patch.object(module.urllib.request, "urlopen", return_value=response):
        with pytest.raises(RuntimeError, match="8 MiB"):
            module.fetch_prices("SPY", "1y")
