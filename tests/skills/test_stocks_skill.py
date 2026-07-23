from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
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


def test_bundled_entry_point_resolves_packaged_optional_skill(tmp_path):
    launcher = tmp_path / "installed" / "skills" / "research" / "stocks" / "scripts" / "stocks_client.py"
    launcher.parent.mkdir(parents=True)
    launcher.write_text(
        (SKILL_DIR / "scripts" / "stocks_client.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    optional_root = tmp_path / "optional-skills"
    implementation = optional_root / "finance" / "stocks" / "scripts" / "stocks_client.py"
    implementation.parent.mkdir(parents=True)
    implementation.write_text('print("packaged stocks implementation")\n', encoding="utf-8")
    env = {**os.environ, "HERMES_OPTIONAL_SKILLS": str(optional_root)}

    completed = subprocess.run(
        [sys.executable, str(launcher)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "packaged stocks implementation"


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
    assert result["stats"]["price_field"] == "adjusted_close"
    assert result["source"]["price_field"] == "adjusted_close_with_raw_ohlcv"


def test_history_raw_close_fallback_is_consistent_and_labeled(monkeypatch):
    module = _module()
    chart = _chart()
    chart["chart"]["result"][0]["indicators"].pop("adjclose")
    monkeypatch.setattr(module, "yf_chart", lambda *_args, **_kwargs: chart)
    result = _capture(lambda: module.cmd_history("AAPL", range_="1y"))

    assert result["stats"]["total_return_pct"] == "10.00%"
    assert result["stats"]["price_field"] == "raw_close"
    assert result["source"]["price_field"] == "raw_close_with_raw_ohlcv"


def test_quote_marks_missing_provider_price_as_error(monkeypatch):
    module = _module()
    monkeypatch.setattr(module, "yf_chart", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "yf_quote_summary", lambda *_args, **_kwargs: None)
    result = _capture(lambda: module.cmd_quote(["AAPL"]))

    assert result["symbol"] == "AAPL"
    assert result["error"] == "No current price returned for AAPL"


def test_quote_rejects_non_finite_and_malformed_provider_numbers(monkeypatch):
    module = _module()
    chart = _chart()
    meta = chart["chart"]["result"][0]["meta"]
    meta["regularMarketPrice"] = "not-a-number"
    meta["chartPreviousClose"] = float("inf")
    meta["previousClose"] = 0
    meta["regularMarketVolume"] = float("nan")
    monkeypatch.setattr(module, "yf_chart", lambda *_args, **_kwargs: chart)
    monkeypatch.setattr(module, "yf_quote_summary", lambda *_args, **_kwargs: None)
    result = _capture(lambda: module.cmd_quote(["AAPL"]))

    assert result["price"] is None
    assert result["volume"] is None
    assert result["error"] == "No current price returned for AAPL"


def test_compare_reports_true_trailing_adjusted_return(monkeypatch):
    module = _module()
    monkeypatch.setattr(module, "yf_chart", lambda *_args, **_kwargs: _chart())
    monkeypatch.setattr(module, "yf_quote_summary", lambda *_args, **_kwargs: None)
    result = _capture(lambda: module.cmd_compare(["AAPL", "MSFT"]))

    assert result["comparison"][0]["trailing_1y_return_pct"] == "15.79%"
    assert result["comparison"][0]["trailing_1y_price_field"] == "adjusted_close"
    assert result["comparison"][0]["trailing_1y_coverage_days"] == 365.0
    assert "52w_performance_pct" not in result["comparison"][0]
    assert result["sources"][0]["source_url"].endswith("AAPL?interval=1d&range=1y")
    assert result["sources"][0]["price_field"] == "adjusted_close_with_regular_market_price"
    assert result["schema_version"] == 2


def test_compare_does_not_label_short_history_as_one_year_return(monkeypatch):
    module = _module()
    chart = _chart()
    chart["chart"]["result"][0]["timestamp"] = [1735689600, 1740873600]
    monkeypatch.setattr(module, "yf_chart", lambda *_args, **_kwargs: chart)
    monkeypatch.setattr(module, "yf_quote_summary", lambda *_args, **_kwargs: None)
    result = _capture(lambda: module.cmd_compare(["AAPL", "MSFT"]))

    assert result["comparison"][0]["trailing_1y_return_pct"] is None
    assert result["comparison"][0]["trailing_1y_coverage_days"] == 60.0


def test_compare_raw_close_fallback_is_labeled_in_source(monkeypatch):
    module = _module()
    chart = _chart()
    chart["chart"]["result"][0]["indicators"].pop("adjclose")
    monkeypatch.setattr(module, "yf_chart", lambda *_args, **_kwargs: chart)
    monkeypatch.setattr(module, "yf_quote_summary", lambda *_args, **_kwargs: None)
    result = _capture(lambda: module.cmd_compare(["AAPL", "MSFT"]))

    assert result["comparison"][0]["trailing_1y_price_field"] == "raw_close"
    assert result["sources"][0]["price_field"] == "raw_close_with_regular_market_price"


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


def test_alpha_vantage_enrichment_is_attributed_without_key(monkeypatch):
    module = _module()
    monkeypatch.setenv("ALPHA_VANTAGE_KEY", "super-secret-value")
    monkeypatch.setattr(module, "yf_chart", lambda *_args, **_kwargs: _chart())
    monkeypatch.setattr(module, "yf_quote_summary", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        module,
        "av_overview",
        lambda _symbol: {"Symbol": "AAPL", "MarketCapitalization": "1000000"},
    )
    result = _capture(lambda: module.cmd_quote(["AAPL"]))

    assert result["enrichment_sources"][0]["provider"] == "Alpha Vantage"
    assert result["enrichment_sources"][0]["credential_redacted"] is True
    assert "super-secret-value" not in json.dumps(result)
