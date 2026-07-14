from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import re
from unittest.mock import patch

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = ROOT / "skills" / "research" / "stocks"
SCRIPT = ROOT / "optional-skills" / "finance" / "stocks" / "scripts" / "stocks_client.py"


def _module():
    spec = importlib.util.spec_from_file_location("stocks_client", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _chart() -> dict:
    return {
        "chart": {
            "error": None,
            "result": [{
                "meta": {
                    "currency": "USD",
                    "exchangeName": "NMS",
                    "regularMarketPrice": 110,
                    "previousClose": 108,
                    "shortName": "Example Corp",
                },
                "timestamp": [1735689600, 1767225600],
                "indicators": {
                    "quote": [{
                        "open": [99, 108],
                        "close": [100, 110],
                        "high": [101, 111],
                        "low": [98, 107],
                        "volume": [1000, 1200],
                    }],
                    "adjclose": [{"adjclose": [95, 110]}],
                },
            }],
        }
    }


def _capture(callback) -> dict | list:
    output = io.StringIO()
    with redirect_stdout(output):
        callback()
    return json.loads(output.getvalue())


def test_skill_frontmatter_and_bundled_entry_point_are_valid():
    content = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"---\n(.*?)\n---\n", content, re.DOTALL)
    assert match
    frontmatter = yaml.safe_load(match.group(1))
    assert frontmatter["name"] == "stocks"
    assert frontmatter["description"].endswith(".")
    assert len(frontmatter["description"]) <= 60
    assert (SKILL_DIR / "scripts" / "stocks_client.py").is_file()
    assert "## Verification" in content


def test_quote_includes_public_provenance_without_crumbs(monkeypatch):
    module = _module()
    monkeypatch.setattr(module, "yf_chart", lambda *_args, **_kwargs: _chart())
    monkeypatch.setattr(module, "yf_quote_summary", lambda *_args, **_kwargs: None)
    result = _capture(lambda: module.cmd_quote(["aapl"]))

    assert result["symbol"] == "AAPL"
    assert result["price"] == "110.00"
    assert result["source"]["provider"] == "Yahoo Finance"
    assert result["source"]["source_url"].endswith("AAPL?interval=1d&range=1d")
    assert "crumb" not in result["source"]["source_url"]
    assert result["source"]["retrieved_at"].endswith("+00:00")


def test_history_uses_adjusted_close_for_total_return(monkeypatch):
    module = _module()
    monkeypatch.setattr(module, "yf_chart", lambda *_args, **_kwargs: _chart())
    result = _capture(lambda: module.cmd_history("AAPL", range_="1y"))

    assert result["history"][0]["adjusted_close"] == 95
    assert result["stats"]["total_return_pct"] == "15.79%"
    assert result["source"]["price_field"] == "adjusted_close_with_raw_ohlcv"


def test_symbol_and_request_limits_fail_closed():
    module = _module()
    with pytest.raises(ValueError):
        module.normalize_symbol("../../etc/passwd")
    with pytest.raises(ValueError):
        module.normalize_symbols([f"S{index}" for index in range(module.MAX_SYMBOLS + 1)])
    with pytest.raises(ValueError):
        module.normalize_symbols(["SPY", "spy"])
    with pytest.raises(ValueError):
        module.yf_search("x" * (module.MAX_QUERY_LENGTH + 1))


def test_fetch_rejects_oversized_provider_response(monkeypatch):
    module = _module()
    response = io.BytesIO(b"x" * (module.MAX_RESPONSE_BYTES + 1))
    monkeypatch.setattr(module._opener, "open", lambda *_args, **_kwargs: response)
    assert module.fetch_url("https://example.test", retries=1) is None


def test_alpha_vantage_key_never_appears_in_source_metadata(monkeypatch):
    module = _module()
    monkeypatch.setenv("ALPHA_VANTAGE_KEY", "super-secret-value")
    metadata = module.source_metadata(module.public_chart_url("MSFT", "1d", "1d"))
    assert "super-secret-value" not in json.dumps(metadata)