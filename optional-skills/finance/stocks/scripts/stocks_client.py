#!/usr/bin/env python3
"""
stocks_client.py - Stock market data CLI tool for the Hermes Agent project.
Zero external dependencies - Python stdlib only.
"""

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.cookiejar import CookieJar

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

USER_AGENT = "Mozilla/5.0 (compatible; HermesAgent/1.0)"
YF_BASE = "https://query1.finance.yahoo.com"
YF_BASE2 = "https://query2.finance.yahoo.com"
AV_BASE = "https://www.alphavantage.co/query"

MAX_RETRIES = 3
BACKOFF_BASE = 1.5  # seconds
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_SYMBOLS = 10
MAX_QUERY_LENGTH = 200
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9^=.-]{1,32}$")

# Global cookie jar + opener (handles Yahoo Finance session cookies)
_cookie_jar = CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cookie_jar))
_crumb: str | None = None

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def print_json(data: dict | list) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def retrieved_at() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_symbol(value: str) -> str:
    symbol = str(value or "").strip().upper()
    if not SYMBOL_PATTERN.fullmatch(symbol):
        raise ValueError(
            "Symbol must be 1-32 characters using letters, numbers, ^, =, ., or -."
        )
    return symbol


def normalize_symbols(values: list[str], *, minimum: int = 1) -> list[str]:
    if not minimum <= len(values) <= MAX_SYMBOLS:
        raise ValueError(f"Expected {minimum}-{MAX_SYMBOLS} symbols")
    normalized = [normalize_symbol(value) for value in values]
    if len(set(normalized)) != len(normalized):
        raise ValueError("Duplicate symbols are not allowed")
    return normalized


def public_chart_url(symbol: str, interval: str, range_: str) -> str:
    encoded = urllib.parse.quote(normalize_symbol(symbol), safe="")
    query = urllib.parse.urlencode({"interval": interval, "range": range_})
    return f"{YF_BASE}/v8/finance/chart/{encoded}?{query}"


def source_metadata(url: str, *, price_field: str | None = None) -> dict:
    metadata = {
        "provider": "Yahoo Finance",
        "source_url": url,
        "retrieved_at": retrieved_at(),
        "unofficial_endpoint": True,
    }
    if price_field:
        metadata["price_field"] = price_field
    return metadata


def finite_number(
    value, *, positive: bool = False, nonnegative: bool = False
) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if (
        not math.isfinite(numeric)
        or (positive and numeric <= 0)
        or (nonnegative and numeric < 0)
    ):
        return None
    return numeric


def fmt_price(value) -> str | None:
    numeric = finite_number(value)
    return f"{numeric:.2f}" if numeric is not None else None


def fmt_large(value) -> str | None:
    """Format large numbers with B/T suffix."""
    v = finite_number(value)
    if v is None:
        return None
    if abs(v) >= 1e12:
        return f"{v / 1e12:.2f}T"
    if abs(v) >= 1e9:
        return f"{v / 1e9:.2f}B"
    if abs(v) >= 1e6:
        return f"{v / 1e6:.2f}M"
    return str(int(v))


def fmt_pct(value) -> str | None:
    numeric = finite_number(value)
    return f"{numeric:.2f}%" if numeric is not None else None


def safe_get(d: dict, *keys, default=None):
    """Safely traverse nested dict."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k, default)
        if cur is None:
            return default
    return cur


def ts_to_date(ts) -> str | None:
    """Convert Unix timestamp to ISO date string."""
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except (OSError, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# HTTP layer with retry + exponential backoff
# ---------------------------------------------------------------------------


def _build_request(url: str, headers: dict | None = None) -> urllib.request.Request:
    req = urllib.request.Request(url)
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/json, */*")
    req.add_header("Accept-Language", "en-US,en;q=0.9")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    return req


def fetch_url(url: str, headers: dict | None = None, retries: int = MAX_RETRIES) -> dict | list | None:
    """Fetch a URL, parse JSON, retry on transient errors."""
    last_err = None
    for attempt in range(retries):
        try:
            req = _build_request(url, headers)
            with _opener.open(req, timeout=15) as resp:
                raw = resp.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ValueError("Provider response exceeded the 8 MiB safety limit")
                return json.loads(raw.decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in {404, 400}:
                break  # no point retrying
            wait = BACKOFF_BASE ** attempt
            time.sleep(wait)
        except urllib.error.URLError as e:
            last_err = e
            wait = BACKOFF_BASE ** attempt
            time.sleep(wait)
        except (json.JSONDecodeError, ValueError) as e:
            last_err = e
            break
    return None


# ---------------------------------------------------------------------------
# Yahoo Finance crumb / cookie management
# ---------------------------------------------------------------------------


def _fetch_crumb() -> str | None:
    """
    Yahoo Finance v8 requires a crumb + consent cookie.
    We hit the consent page once to grab cookies, then fetch the crumb.
    """
    global _crumb
    if _crumb is not None:
        return _crumb

    # Step 1: touch Yahoo Finance to get cookies
    try:
        req = _build_request("https://finance.yahoo.com/")
        with _opener.open(req, timeout=10) as resp:
            resp.read()
    except Exception:
        pass

    # Step 2: fetch crumb
    crumb_url = f"{YF_BASE}/v1/test/getcrumb"
    try:
        req = _build_request(crumb_url)
        with _opener.open(req, timeout=10) as resp:
            crumb_raw = resp.read().decode("utf-8").strip()
            if crumb_raw and crumb_raw != "":
                _crumb = crumb_raw
                return _crumb
    except Exception:
        pass

    return None


def yf_url(path: str, params: dict | None = None) -> str:
    """Build a Yahoo Finance URL, injecting crumb if available."""
    crumb = _fetch_crumb()
    if params is None:
        params = {}
    if crumb:
        params["crumb"] = crumb
    qs = urllib.parse.urlencode(params)
    base = f"{YF_BASE}{path}"
    return f"{base}?{qs}" if qs else base


# ---------------------------------------------------------------------------
# Yahoo Finance API calls
# ---------------------------------------------------------------------------


def yf_chart(symbol: str, interval: str = "1d", range_: str = "1d") -> dict | None:
    symbol = normalize_symbol(symbol)
    params = {"interval": interval, "range": range_}
    crumb = _fetch_crumb()
    if crumb:
        params["crumb"] = crumb
    qs = urllib.parse.urlencode(params)
    url = f"{YF_BASE}/v8/finance/chart/{urllib.parse.quote(symbol, safe='')}?{qs}"
    data = fetch_url(url)
    if data is None:
        # fallback to query2
        url2 = f"{YF_BASE2}/v8/finance/chart/{urllib.parse.quote(symbol, safe='')}?{qs}"
        data = fetch_url(url2)
    return data


def yf_search(query: str, count: int = 5) -> dict | None:
    query = str(query or "").strip()
    if not query or len(query) > MAX_QUERY_LENGTH:
        raise ValueError(f"Search query must be 1-{MAX_QUERY_LENGTH} characters")
    params = {"q": query, "quotesCount": count, "newsCount": 0}
    crumb = _fetch_crumb()
    if crumb:
        params["crumb"] = crumb
    qs = urllib.parse.urlencode(params)
    url = f"{YF_BASE}/v1/finance/search?{qs}"
    data = fetch_url(url)
    if data is None:
        url2 = f"{YF_BASE2}/v1/finance/search?{qs}"
        data = fetch_url(url2)
    return data


def yf_quote_summary(symbol: str) -> dict | None:
    """Fetch detailed quote summary (quoteSummary) for PE, market cap, etc."""
    symbol = normalize_symbol(symbol)
    modules = "summaryDetail,defaultKeyStatistics,price"
    params = {"modules": modules}
    crumb = _fetch_crumb()
    if crumb:
        params["crumb"] = crumb
    qs = urllib.parse.urlencode(params)
    url = f"{YF_BASE}/v11/finance/quoteSummary/{urllib.parse.quote(symbol, safe='')}?{qs}"
    data = fetch_url(url)
    if data is None:
        url2 = f"{YF_BASE2}/v11/finance/quoteSummary/{urllib.parse.quote(symbol, safe='')}?{qs}"
        data = fetch_url(url2)
    return data


# ---------------------------------------------------------------------------
# Alpha Vantage (optional, requires API key)
# ---------------------------------------------------------------------------


def av_overview(symbol: str) -> dict | None:
    key = os.environ.get("ALPHA_VANTAGE_KEY")
    if not key:
        return None
    params = {"function": "OVERVIEW", "symbol": symbol, "apikey": key}
    qs = urllib.parse.urlencode(params)
    url = f"{AV_BASE}?{qs}"
    data = fetch_url(url)
    if isinstance(data, dict) and data.get("Symbol"):
        return data
    return None


# ---------------------------------------------------------------------------
# Data extraction helpers
# ---------------------------------------------------------------------------


def extract_quote_from_chart(symbol: str, chart_data: dict) -> dict:
    """Extract current quote info from v8 chart response."""
    result = {
        "symbol": symbol.upper(),
        "price": None,
        "change": None,
        "change_pct": None,
        "volume": None,
        "market_cap": None,
        "pe_ratio": None,
        "52w_high": None,
        "52w_low": None,
        "currency": None,
        "exchange": None,
        "short_name": None,
    }

    chart = safe_get(chart_data, "chart", "result")
    if not chart or not isinstance(chart, list) or len(chart) == 0:
        return result

    r = chart[0]
    meta = r.get("meta", {})

    result["currency"] = meta.get("currency")
    result["exchange"] = meta.get("exchangeName")
    result["short_name"] = meta.get("shortName") or meta.get("longName")

    # Price
    price = finite_number(meta.get("regularMarketPrice"), positive=True)
    if price is None:
        price = finite_number(meta.get("chartPreviousClose"), positive=True)
    result["price"] = fmt_price(price)

    # Change
    prev_close = finite_number(meta.get("previousClose"), positive=True)
    if prev_close is None:
        prev_close = finite_number(meta.get("chartPreviousClose"), positive=True)
    if price is not None and prev_close is not None:
        chg = price - prev_close
        chg_pct = (chg / prev_close) * 100
        result["change"] = fmt_price(chg)
        result["change_pct"] = fmt_pct(chg_pct)

    result["volume"] = finite_number(
        meta.get("regularMarketVolume"), nonnegative=True
    )
    result["52w_high"] = fmt_price(meta.get("fiftyTwoWeekHigh"))
    result["52w_low"] = fmt_price(meta.get("fiftyTwoWeekLow"))

    return result


def extract_quote_summary_fields(qs_data: dict) -> dict:
    """Extract PE, market cap, etc. from quoteSummary response."""
    out = {
        "market_cap": None,
        "pe_ratio": None,
        "52w_high": None,
        "52w_low": None,
        "volume": None,
        "short_name": None,
    }

    result = safe_get(qs_data, "quoteSummary", "result")
    if not result or not isinstance(result, list) or len(result) == 0:
        return out

    r = result[0]

    # price module
    price_mod = r.get("price", {})
    out["market_cap"] = fmt_large(safe_get(price_mod, "marketCap", "raw"))
    out["short_name"] = price_mod.get("shortName") or price_mod.get("longName")

    # summaryDetail
    sd = r.get("summaryDetail", {})
    pe_raw = safe_get(sd, "trailingPE", "raw")
    out["pe_ratio"] = fmt_price(pe_raw) if pe_raw else None
    out["52w_high"] = fmt_price(safe_get(sd, "fiftyTwoWeekHigh", "raw"))
    out["52w_low"] = fmt_price(safe_get(sd, "fiftyTwoWeekLow", "raw"))
    out["volume"] = finite_number(
        safe_get(sd, "volume", "raw")
        or safe_get(sd, "regularMarketVolume", "raw"),
        nonnegative=True,
    )

    # defaultKeyStatistics
    ks = r.get("defaultKeyStatistics", {})
    if out["pe_ratio"] is None:
        pe_raw = safe_get(ks, "trailingEps", "raw")
        # can't compute PE from EPS alone without price, skip

    return out


def extract_price_series(
    chart_data: dict, *, minimum_coverage_days: float = 0
) -> tuple[str | None, list[tuple[int, float]]]:
    """Return one internally consistent timestamped price series."""
    chart = safe_get(chart_data, "chart", "result")
    if not chart or not isinstance(chart, list):
        return None, []

    result = chart[0]
    timestamps = result.get("timestamp") or []
    if not isinstance(timestamps, list):
        return None, []
    indicators = result.get("indicators") or {}
    adjusted_sets = indicators.get("adjclose") or []
    quote_sets = indicators.get("quote") or []
    adjusted = adjusted_sets[0].get("adjclose") if adjusted_sets else None
    closes = quote_sets[0].get("close") if quote_sets else None

    candidates = []
    for field, values in (("adjusted_close", adjusted), ("raw_close", closes)):
        if not isinstance(values, list):
            continue
        series = []
        for timestamp, value in zip(timestamps, values):
            numeric = finite_number(value, positive=True)
            try:
                observed_at = int(timestamp)
            except (TypeError, ValueError, OverflowError):
                continue
            if numeric is not None:
                series.append((observed_at, numeric))
        if len(series) >= 2:
            candidates.append((field, series))
            coverage_days = (series[-1][0] - series[0][0]) / 86400
            if coverage_days >= minimum_coverage_days:
                return field, series
    if candidates:
        return candidates[0]
    return None, []


def extract_trailing_return(chart_data: dict) -> dict:
    """Calculate a one-year return only when observations span most of a year."""
    field, series = extract_price_series(chart_data, minimum_coverage_days=330)
    if len(series) < 2:
        return {"return_pct": None, "price_field": field, "coverage_days": None}
    coverage_days = (series[-1][0] - series[0][0]) / 86400
    if coverage_days < 330:
        return {
            "return_pct": None,
            "price_field": field,
            "coverage_days": round(coverage_days, 1),
        }
    return {
        "return_pct": fmt_pct((series[-1][1] / series[0][1] - 1.0) * 100),
        "price_field": field,
        "coverage_days": round(coverage_days, 1),
    }


# ---------------------------------------------------------------------------
# Command: quote
# ---------------------------------------------------------------------------


def cmd_quote(symbols: list[str]) -> None:
    results = []

    for sym in normalize_symbols(symbols):
        chart_source = public_chart_url(sym, "1d", "1d")
        entry = {
            "symbol": sym,
            "data_source": "Yahoo Finance",
            "source": source_metadata(chart_source, price_field="regular_market_price"),
        }

        # Fetch chart for price data
        chart_data = yf_chart(sym, interval="1d", range_="1d")
        if chart_data:
            q = extract_quote_from_chart(sym, chart_data)
            entry.update(q)
        if not entry.get("price"):
            entry["error"] = f"No current price returned for {sym}"

        # Fetch quoteSummary for enriched data
        qs_data = yf_quote_summary(sym)
        if qs_data:
            qs_fields = extract_quote_summary_fields(qs_data)
            # Prefer quoteSummary values if chart didn't have them
            for field in ("market_cap", "pe_ratio", "52w_high", "52w_low", "volume", "short_name"):
                if entry.get(field) is None and qs_fields.get(field) is not None:
                    entry[field] = qs_fields[field]
                elif field == "market_cap" and qs_fields.get(field) is not None:
                    # Always prefer formatted market cap from quoteSummary
                    entry[field] = qs_fields[field]

        # Optionally enrich with Alpha Vantage
        av_key = os.environ.get("ALPHA_VANTAGE_KEY")
        if av_key:
            av_data = av_overview(sym)
            if av_data:
                entry["data_source"] = "Yahoo Finance + Alpha Vantage"
                entry["enrichment_sources"] = [{
                    "provider": "Alpha Vantage",
                    "source_url": (
                        f"{AV_BASE}?"
                        + urllib.parse.urlencode({"function": "OVERVIEW", "symbol": sym})
                    ),
                    "retrieved_at": retrieved_at(),
                    "credential_redacted": True,
                }]
                if entry.get("pe_ratio") is None:
                    pe = av_data.get("PERatio")
                    entry["pe_ratio"] = fmt_price(pe)
                if entry.get("market_cap") is None:
                    mc = av_data.get("MarketCapitalization")
                    entry["market_cap"] = fmt_large(mc)
                if entry.get("52w_high") is None:
                    entry["52w_high"] = fmt_price(av_data.get("52WeekHigh"))
                if entry.get("52w_low") is None:
                    entry["52w_low"] = fmt_price(av_data.get("52WeekLow"))

        results.append(entry)

    if len(results) == 1:
        print_json(results[0])
    else:
        print_json(results)


# ---------------------------------------------------------------------------
# Command: search
# ---------------------------------------------------------------------------


def cmd_search(query: str) -> None:
    query = str(query or "").strip()
    data = yf_search(query, count=5)
    if not data:
        print_json({"error": "Search failed or no results", "query": query, "data_source": "Yahoo Finance"})
        return

    quotes = data.get("quotes") or []
    if not quotes:
        print_json({"error": "No matches found", "query": query, "data_source": "Yahoo Finance"})
        return

    results = []
    for q in quotes[:5]:
        results.append({
            "symbol": q.get("symbol"),
            "name": q.get("longname") or q.get("shortname"),
            "exchange": q.get("exchange") or q.get("exchDisp"),
            "type": q.get("quoteType"),
            "sector": q.get("sector"),
        })

    output = {
        "query": query,
        "matches": results,
        "data_source": "Yahoo Finance",
        "source": source_metadata(
            f"{YF_BASE}/v1/finance/search?{urllib.parse.urlencode({'q': query, 'quotesCount': 5, 'newsCount': 0})}"
        ),
    }
    print_json(output)


# ---------------------------------------------------------------------------
# Command: history
# ---------------------------------------------------------------------------


def cmd_history(symbol: str, range_: str = "1mo") -> None:
    valid_ranges = ("1mo", "3mo", "6mo", "1y", "5y")
    if range_ not in valid_ranges:
        print_json({"error": f"Invalid range '{range_}'. Valid: {', '.join(valid_ranges)}"})
        return

    sym = normalize_symbol(symbol)
    chart_data = yf_chart(sym, interval="1d", range_=range_)

    if not chart_data:
        print_json({"error": f"Failed to fetch history for {sym}", "data_source": "Yahoo Finance"})
        return

    chart = safe_get(chart_data, "chart", "result")
    if not chart or not isinstance(chart, list) or len(chart) == 0:
        err = safe_get(chart_data, "chart", "error", "description") or "Unknown error"
        print_json({"error": err, "symbol": sym, "data_source": "Yahoo Finance"})
        return

    r = chart[0]
    timestamps = r.get("timestamp") or []
    indicators = r.get("indicators", {})
    quote_list = indicators.get("quote") or [{}]
    ohlcv = quote_list[0] if quote_list else {}
    adjusted_list = indicators.get("adjclose") or [{}]
    adjusted = adjusted_list[0].get("adjclose") if adjusted_list else []

    opens = ohlcv.get("open") or []
    closes = ohlcv.get("close") or []
    highs = ohlcv.get("high") or []
    lows = ohlcv.get("low") or []
    volumes = ohlcv.get("volume") or []

    def _v(values, index, *, positive=False, nonnegative=False):
        try:
            return finite_number(
                values[index], positive=positive, nonnegative=nonnegative
            )
        except (IndexError, TypeError):
            return None

    history = []
    for i, ts in enumerate(timestamps):
        entry = {
            "date": ts_to_date(ts),
            "open": _v(opens, i, positive=True),
            "close": _v(closes, i, positive=True),
            "high": _v(highs, i, positive=True),
            "low": _v(lows, i, positive=True),
            "volume": _v(volumes, i, nonnegative=True),
            "adjusted_close": _v(adjusted or [], i, positive=True),
        }
        history.append(entry)

    # Never mix adjusted and raw closes in one calculation. If adjusted close
    # is unavailable, label the raw-close fallback explicitly.
    return_price_field, price_series = extract_price_series(chart_data)
    valid_closes = [value for _, value in price_series]
    stats = {"price_field": return_price_field}
    if valid_closes:
        stats["min"] = fmt_price(min(valid_closes))
        stats["max"] = fmt_price(max(valid_closes))
        stats["avg"] = fmt_price(sum(valid_closes) / len(valid_closes))
        if len(valid_closes) >= 2:
            total_return = ((valid_closes[-1] - valid_closes[0]) / valid_closes[0]) * 100
            stats["total_return_pct"] = fmt_pct(total_return)
        else:
            stats["total_return_pct"] = None

    meta = r.get("meta", {})
    output = {
        "symbol": sym,
        "range": range_,
        "currency": meta.get("currency"),
        "exchange": meta.get("exchangeName"),
        "data_points": len(history),
        "stats": stats,
        "history": history,
        "data_source": "Yahoo Finance",
        "source": source_metadata(
            public_chart_url(sym, "1d", range_),
            price_field=(
                "adjusted_close_with_raw_ohlcv"
                if return_price_field == "adjusted_close"
                else (
                    "raw_close_with_raw_ohlcv"
                    if return_price_field == "raw_close"
                    else "return_price_unavailable_with_raw_ohlcv"
                )
            ),
        ),
    }
    print_json(output)


# ---------------------------------------------------------------------------
# Command: compare
# ---------------------------------------------------------------------------


def cmd_compare(symbols: list[str]) -> None:
    if len(symbols) < 2:
        print_json({"error": "compare requires at least 2 symbols"})
        return

    comparisons = []

    normalized_symbols = normalize_symbols(symbols, minimum=2)
    for sym in normalized_symbols:
        entry = {
            "symbol": sym,
            "name": None,
            "price": None,
            "change_pct": None,
            "market_cap": None,
            "pe_ratio": None,
            "52w_high": None,
            "52w_low": None,
            "trailing_1y_return_pct": None,
            "trailing_1y_price_field": None,
            "trailing_1y_coverage_days": None,
        }

        # A one-year chart supplies both the current quote and a true trailing
        # adjusted-close return. Distance from the 52-week low is not return.
        chart_data = yf_chart(sym, interval="1d", range_="1y")
        if chart_data:
            q = extract_quote_from_chart(sym, chart_data)
            entry["name"] = q.get("short_name")
            entry["price"] = q.get("price")
            entry["change_pct"] = q.get("change_pct")
            entry["52w_high"] = q.get("52w_high")
            entry["52w_low"] = q.get("52w_low")
            trailing_return = extract_trailing_return(chart_data)
            entry["trailing_1y_return_pct"] = trailing_return["return_pct"]
            entry["trailing_1y_price_field"] = trailing_return["price_field"]
            entry["trailing_1y_coverage_days"] = trailing_return["coverage_days"]

        # quoteSummary for enrichment
        qs_data = yf_quote_summary(sym)
        if qs_data:
            qs = extract_quote_summary_fields(qs_data)
            if qs.get("market_cap"):
                entry["market_cap"] = qs["market_cap"]
            if qs.get("pe_ratio"):
                entry["pe_ratio"] = qs["pe_ratio"]
            if entry["52w_high"] is None and qs.get("52w_high"):
                entry["52w_high"] = qs["52w_high"]
            if entry["52w_low"] is None and qs.get("52w_low"):
                entry["52w_low"] = qs["52w_low"]
            if entry["name"] is None and qs.get("short_name"):
                entry["name"] = qs["short_name"]

        if not entry.get("price"):
            entry["error"] = f"No current price returned for {sym}"

        comparisons.append(entry)

    output = {
        "schema_version": 2,
        "comparison": comparisons,
        "symbols": normalized_symbols,
        "data_source": "Yahoo Finance",
        "metric_definitions": {
            "trailing_1y_return_pct": (
                "First-to-last daily close return when the provider window spans "
                "at least 330 days; adjusted close is preferred."
            ),
        },
        "sources": [
            source_metadata(
                public_chart_url(entry["symbol"], "1d", "1y"),
                price_field=(
                    "adjusted_close_with_regular_market_price"
                    if entry["trailing_1y_price_field"] == "adjusted_close"
                    else (
                        "raw_close_with_regular_market_price"
                        if entry["trailing_1y_price_field"] == "raw_close"
                        else "regular_market_price_only"
                    )
                ),
            )
            for entry in comparisons
        ],
    }
    print_json(output)


# ---------------------------------------------------------------------------
# Command: crypto
# ---------------------------------------------------------------------------


def cmd_crypto(symbol: str, vs: str = "USD") -> None:
    sym = normalize_symbol(symbol)
    vs = normalize_symbol(vs)

    # If user already passed BTC-USD, keep as-is; otherwise append
    if "-" not in sym:
        ticker = f"{sym}-{vs}"
    else:
        ticker = sym

    chart_data = yf_chart(ticker, interval="1d", range_="1d")

    if not chart_data:
        print_json({
            "error": f"Failed to fetch crypto data for {ticker}",
            "symbol": ticker,
            "data_source": "Yahoo Finance",
        })
        return

    chart = safe_get(chart_data, "chart", "result")
    if not chart or not isinstance(chart, list) or len(chart) == 0:
        err = safe_get(chart_data, "chart", "error", "description") or "Symbol not found"
        print_json({"error": err, "symbol": ticker, "data_source": "Yahoo Finance"})
        return

    r = chart[0]
    meta = r.get("meta", {})

    price = meta.get("regularMarketPrice") or meta.get("chartPreviousClose")
    prev_close = meta.get("previousClose") or meta.get("chartPreviousClose")

    change = None
    change_pct = None
    if price and prev_close:
        try:
            chg = float(price) - float(prev_close)
            chg_pct = (chg / float(prev_close)) * 100
            change = fmt_price(chg)
            change_pct = fmt_pct(chg_pct)
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    # 24h stats from indicators
    indicators = r.get("indicators", {})
    quote_list = indicators.get("quote") or [{}]
    ohlcv = quote_list[0] if quote_list else {}
    highs = [
        numeric
        for value in (ohlcv.get("high") or [])
        if (numeric := finite_number(value, positive=True)) is not None
    ]
    lows = [
        numeric
        for value in (ohlcv.get("low") or [])
        if (numeric := finite_number(value, positive=True)) is not None
    ]
    volumes = [
        numeric
        for value in (ohlcv.get("volume") or [])
        if (numeric := finite_number(value, nonnegative=True)) is not None
    ]

    output = {
        "symbol": ticker,
        "base": sym if "-" not in sym else sym.split("-")[0],
        "quote_currency": vs,
        "price": fmt_price(price),
        "change": change,
        "change_pct": change_pct,
        "day_high": fmt_price(max(highs)) if highs else None,
        "day_low": fmt_price(min(lows)) if lows else None,
        "volume": fmt_large(sum(volumes)) if volumes else None,
        "52w_high": fmt_price(meta.get("fiftyTwoWeekHigh")),
        "52w_low": fmt_price(meta.get("fiftyTwoWeekLow")),
        "exchange": meta.get("exchangeName"),
        "short_name": meta.get("shortName") or meta.get("longName"),
        "data_source": "Yahoo Finance",
        "source": source_metadata(
            public_chart_url(ticker, "1d", "1d"),
            price_field="regular_market_price",
        ),
    }
    print_json(output)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stocks_client",
        description="Stock & crypto market data CLI — Hermes Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  stocks_client.py quote AAPL MSFT GOOGL
  stocks_client.py search "Tesla"
  stocks_client.py history AAPL --range 3mo
  stocks_client.py compare AAPL MSFT GOOGL AMZN
  stocks_client.py crypto BTC
  stocks_client.py crypto ETH --vs EUR
  ALPHA_VANTAGE_KEY=yourkey stocks_client.py quote AAPL
        """,
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # quote
    p_quote = sub.add_parser("quote", help="Get current quote for one or more symbols")
    p_quote.add_argument("symbols", nargs="+", metavar="SYMBOL", help="Stock ticker symbol(s)")

    # search
    p_search = sub.add_parser("search", help="Search for stocks by name or symbol")
    p_search.add_argument("query", help="Search query (company name or partial symbol)")

    # history
    p_history = sub.add_parser("history", help="Price history for a symbol")
    p_history.add_argument("symbol", metavar="SYMBOL", help="Stock ticker symbol")
    p_history.add_argument(
        "--range",
        dest="range_",
        default="1mo",
        choices=["1mo", "3mo", "6mo", "1y", "5y"],
        help="Date range (default: 1mo)",
    )

    # compare
    p_compare = sub.add_parser("compare", help="Compare multiple stocks side by side")
    p_compare.add_argument("symbols", nargs="+", metavar="SYMBOL", help="At least 2 stock symbols")

    # crypto
    p_crypto = sub.add_parser("crypto", help="Crypto price (BTC, ETH, SOL, etc.)")
    p_crypto.add_argument("symbol", metavar="SYMBOL", help="Crypto symbol (e.g. BTC, ETH, SOL)")
    p_crypto.add_argument(
        "--vs",
        default="USD",
        metavar="CURRENCY",
        help="Quote currency (default: USD)",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "quote":
            cmd_quote(args.symbols)
        elif args.command == "search":
            cmd_search(args.query)
        elif args.command == "history":
            cmd_history(args.symbol, range_=args.range_)
        elif args.command == "compare":
            cmd_compare(args.symbols)
        elif args.command == "crypto":
            cmd_crypto(args.symbol, vs=args.vs)
        else:
            parser.print_help()
            sys.exit(1)
    except KeyboardInterrupt:
        print_json({"error": "Interrupted by user"})
        sys.exit(130)
    except Exception as e:
        print_json({"error": f"Unexpected error: {e}", "type": type(e).__name__})
        sys.exit(1)


if __name__ == "__main__":
    main()
