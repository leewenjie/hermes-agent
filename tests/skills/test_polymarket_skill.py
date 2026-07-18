"""Contract tests for the bundled read-only Polymarket research skill."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = ROOT / "skills" / "research" / "polymarket"


def test_skill_defines_point_in_time_non_objective_contract():
    content = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8").lower()
    normalized = " ".join(content.split())

    assert "point-in-time" in content
    assert "market-implied" in content
    assert "not objective probabilities" in content
    assert "retrieval time" in content
    assert "not a streaming feed" in normalized
    assert "continuous monitoring" in normalized
    assert "alerting system" in normalized
    assert "does not support placing trades" in content


def test_reference_does_not_equate_price_with_probability():
    content = (SKILL_DIR / "references" / "api-endpoints.md").read_text(
        encoding="utf-8"
    ).lower()

    assert "price (probability)" not in content
    assert "market-implied likelihood" in content
    assert "objective probability" in content


def test_helper_is_get_only_and_has_no_trading_credentials():
    content = (SKILL_DIR / "scripts" / "polymarket.py").read_text(
        encoding="utf-8"
    )
    upper = content.upper()

    assert "URLLIB.REQUEST.REQUEST" in upper
    assert "METHOD=" not in upper
    assert "API_KEY" not in upper
    assert "PRIVATE_KEY" not in upper
    assert "SIGNATURE" not in upper