#!/usr/bin/env python3
"""Create auditable return-distribution artifacts from adjusted prices."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import html
import json
import math
from pathlib import Path
import re
import statistics
from typing import Any, Iterable
import urllib.parse
import urllib.request

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
VALID_RANGES = ("1y", "2y", "5y", "10y", "max")
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9^=.-]{1,32}$")


def normalize_symbol(value: str) -> str:
    symbol = str(value or "").strip().upper()
    if not SYMBOL_PATTERN.fullmatch(symbol):
        raise ValueError(
            "Symbol must be 1-32 characters using letters, numbers, ^, =, ., or -."
        )
    return symbol


def artifact_symbol(symbol: str) -> str:
    return re.sub(r"[^A-Z0-9.-]+", "_", normalize_symbol(symbol)).strip("._")


def normalize_prices(prices: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_date: dict[str, float] = {}
    for item in prices:
        if not isinstance(item, dict):
            continue
        raw_date = str(item.get("date") or "").strip()
        value = item.get("adjusted_close")
        try:
            parsed_date = date.fromisoformat(raw_date).isoformat()
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if numeric > 0 and math.isfinite(numeric):
            by_date[parsed_date] = numeric
    normalized = [
        {"date": observed_at, "adjusted_close": by_date[observed_at]}
        for observed_at in sorted(by_date)
    ]
    if len(normalized) < 2:
        raise ValueError("At least two valid adjusted-price observations are required")
    return normalized


def fetch_prices(symbol: str, period: str) -> tuple[list[dict[str, Any]], str]:
    symbol = normalize_symbol(symbol)
    encoded_symbol = urllib.parse.quote(symbol, safe="")
    source_url = YAHOO_CHART_URL.format(symbol=encoded_symbol)
    source_url += "?" + urllib.parse.urlencode(
        {"range": period, "interval": "1d", "events": "history"}
    )
    request = urllib.request.Request(
        source_url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Hermes-Market-Return-Analysis/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise RuntimeError("Yahoo Finance response exceeded the 8 MiB safety limit")
        payload = json.loads(raw)

    chart = payload.get("chart") if isinstance(payload, dict) else None
    error = chart.get("error") if isinstance(chart, dict) else None
    if error:
        raise RuntimeError(f"Yahoo Finance returned an error: {error}")
    results = chart.get("result") if isinstance(chart, dict) else None
    if not isinstance(results, list) or not results:
        raise RuntimeError("Yahoo Finance returned no chart result")

    result = results[0]
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    adjclose_sets = indicators.get("adjclose") or []
    adjusted = adjclose_sets[0].get("adjclose") if adjclose_sets else None
    if not isinstance(timestamps, list) or not isinstance(adjusted, list):
        raise RuntimeError("Yahoo Finance chart is missing adjusted-close prices")

    prices = []
    for timestamp, value in zip(timestamps, adjusted):
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            continue
        if float(value) <= 0:
            continue
        date = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).date().isoformat()
        prices.append({"date": date, "adjusted_close": float(value)})
    try:
        return normalize_prices(prices), source_url
    except ValueError as exc:
        raise RuntimeError("Fewer than two valid adjusted-price observations") from exc


def load_fixture(path: Path) -> tuple[str, list[dict[str, Any]], str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Input JSON must be an object")
    symbol = normalize_symbol(str(payload.get("symbol") or "SPY"))
    raw_prices = payload.get("prices")
    if not isinstance(raw_prices, list):
        raise ValueError("Input JSON requires a prices array")
    prices = normalize_prices(raw_prices)
    source = str(payload.get("source_url") or path.resolve().as_uri())
    return symbol, prices, source


def percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _moment(values: list[float], order: int, mean: float) -> float:
    return sum((value - mean) ** order for value in values) / len(values)


def _maximum_drawdown(prices: Iterable[float]) -> float:
    peak = 0.0
    worst = 0.0
    for price in prices:
        peak = max(peak, price)
        if peak:
            worst = min(worst, price / peak - 1.0)
    return worst


def analyze(symbol: str, prices: list[dict[str, Any]], source_url: str) -> dict[str, Any]:
    symbol = normalize_symbol(symbol)
    prices = normalize_prices(prices)
    returns = [
        prices[index]["adjusted_close"] / prices[index - 1]["adjusted_close"] - 1.0
        for index in range(1, len(prices))
    ]
    if not returns:
        raise ValueError("At least one return is required")

    mean = statistics.fmean(returns)
    median = statistics.median(returns)
    stdev = statistics.stdev(returns) if len(returns) > 1 else 0.0
    second_moment = _moment(returns, 2, mean)
    skewness = (
        _moment(returns, 3, mean) / second_moment ** 1.5
        if second_moment > 0
        else 0.0
    )
    excess_kurtosis = (
        _moment(returns, 4, mean) / second_moment ** 2 - 3.0
        if second_moment > 0
        else 0.0
    )
    p01 = percentile(returns, 0.01)
    p05 = percentile(returns, 0.05)
    p95 = percentile(returns, 0.95)
    p99 = percentile(returns, 0.99)
    tail = [value for value in returns if value <= p05]
    periods_per_year = 252
    start = date.fromisoformat(prices[0]["date"])
    end = date.fromisoformat(prices[-1]["date"])
    elapsed_days = (end - start).days
    years = max(elapsed_days / 365.2425, 1 / 365.2425)
    annualized_return = (
        (prices[-1]["adjusted_close"] / prices[0]["adjusted_close"]) ** (1 / years)
        - 1.0
    )

    return {
        "schema_version": 1,
        "symbol": symbol,
        "proxy_note": (
            "SPY is an investable S&P 500 proxy, not the index itself."
            if symbol == "SPY"
            else f"{symbol} is analyzed as the selected market proxy."
        ),
        "price_field": "adjusted_close",
        "frequency": "daily",
        "source_url": source_url,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "start_date": prices[0]["date"],
        "end_date": prices[-1]["date"],
        "observation_count": len(prices),
        "return_count": len(returns),
        "statistics": {
            "mean_daily_return": mean,
            "median_daily_return": median,
            "daily_volatility": stdev,
            "annualized_return": annualized_return,
            "annualized_volatility": stdev * math.sqrt(periods_per_year),
            "positive_day_ratio": sum(value > 0 for value in returns) / len(returns),
            "p01": p01,
            "p05": p05,
            "p95": p95,
            "p99": p99,
            "historical_expected_shortfall_95": statistics.fmean(tail),
            "worst_day": min(returns),
            "best_day": max(returns),
            "maximum_drawdown": _maximum_drawdown(
                row["adjusted_close"] for row in prices
            ),
            "skewness": skewness,
            "excess_kurtosis": excess_kurtosis,
        },
        "prices": prices,
        "returns": [
            {"date": prices[index]["date"], "return": returns[index - 1]}
            for index in range(1, len(prices))
        ],
        "limitations": [
            "Historical observations do not forecast future returns.",
            "Daily sampling does not describe intraday or monthly distributions.",
            "The sample can mix materially different market regimes.",
            "Yahoo Finance is an unofficial source and may revise or omit data.",
        ],
        "disclaimer": (
            "Research and decision support only; not financial advice, a personalized "
            "recommendation, portfolio management, or a guarantee of returns."
        ),
    }


def histogram(values: list[float]) -> tuple[list[int], list[float]]:
    count = max(8, min(40, round(math.sqrt(len(values)))))
    low, high = min(values), max(values)
    if math.isclose(low, high):
        low -= 0.005
        high += 0.005
    width = (high - low) / count
    bins = [0] * count
    for value in values:
        index = min(count - 1, max(0, int((value - low) / width)))
        bins[index] += 1
    edges = [low + index * width for index in range(count + 1)]
    return bins, edges


def render_svg(report: dict[str, Any]) -> str:
    values = [row["return"] for row in report["returns"]]
    bins, edges = histogram(values)
    width, height = 960, 560
    left, right, top, bottom = 82, 30, 90, 88
    chart_width = width - left - right
    chart_height = height - top - bottom
    max_count = max(bins) or 1
    bar_width = chart_width / len(bins)
    zero_x = left + (0 - edges[0]) / (edges[-1] - edges[0]) * chart_width
    bars = []
    for index, count in enumerate(bins):
        bar_height = count / max_count * chart_height
        x = left + index * bar_width + 1
        y = top + chart_height - bar_height
        bars.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{max(1, bar_width - 2):.2f}" '
            f'height="{bar_height:.2f}" fill="#10b981" opacity="0.82" />'
        )
    stats = report["statistics"]
    symbol = html.escape(str(report["symbol"]), quote=True)
    start_date = html.escape(str(report["start_date"]), quote=True)
    end_date = html.escape(str(report["end_date"]), quote=True)
    summary = (
        f"n={report['return_count']:,}  mean={stats['mean_daily_return']:.2%}  "
        f"vol={stats['daily_volatility']:.2%}  p05={stats['p05']:.2%}  "
        f"max drawdown={stats['maximum_drawdown']:.2%}"
    )
    labels = []
    for index in range(6):
        value = edges[0] + (edges[-1] - edges[0]) * index / 5
        x = left + chart_width * index / 5
        labels.append(
            f'<text x="{x:.2f}" y="{height - 52}" text-anchor="middle" '
            f'font-size="13" fill="#374151">{value:.1%}</text>'
        )
    zero_line = ""
    if left <= zero_x <= left + chart_width:
        zero_line = (
            f'<line x1="{zero_x:.2f}" y1="{top}" x2="{zero_x:.2f}" '
            f'y2="{top + chart_height}" stroke="#111827" stroke-width="1.5" />'
        )
    return "\n".join(
        [
            '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="560" viewBox="0 0 960 560">',
            '<rect width="960" height="560" fill="#ffffff" />',
            f'<text x="{left}" y="38" font-size="25" font-weight="700" fill="#111827">{symbol} daily adjusted-return distribution</text>',
            f'<text x="{left}" y="66" font-size="14" fill="#4b5563">{start_date} to {end_date}</text>',
            f'<line x1="{left}" y1="{top + chart_height}" x2="{left + chart_width}" y2="{top + chart_height}" stroke="#9ca3af" />',
            *bars,
            zero_line,
            *labels,
            f'<text x="{left}" y="{height - 18}" font-size="14" fill="#111827">{summary}</text>',
            "</svg>",
        ]
    )


def render_markdown(report: dict[str, Any]) -> str:
    stats = report["statistics"]
    pct = lambda value: f"{value:.2%}"
    symbol = str(report["symbol"]).replace("\\", "\\\\").replace("`", "\\`")
    source_url = str(report["source_url"]).replace(">", "%3E").replace("<", "%3C")
    return f"""# {symbol} return distribution

## Measurement contract

- Proxy: {report['proxy_note']}
- Price field: adjusted close
- Frequency: daily simple returns
- Period: {report['start_date']} to {report['end_date']}
- Observations: {report['observation_count']:,} prices and {report['return_count']:,} returns

## Distribution summary

| Statistic | Value |
| --- | ---: |
| Mean daily return | {pct(stats['mean_daily_return'])} |
| Median daily return | {pct(stats['median_daily_return'])} |
| Daily volatility | {pct(stats['daily_volatility'])} |
| Annualized return | {pct(stats['annualized_return'])} |
| Annualized volatility | {pct(stats['annualized_volatility'])} |
| Positive days | {pct(stats['positive_day_ratio'])} |
| 1st percentile | {pct(stats['p01'])} |
| 5th percentile | {pct(stats['p05'])} |
| Historical expected shortfall (95%) | {pct(stats['historical_expected_shortfall_95'])} |
| Worst day | {pct(stats['worst_day'])} |
| Best day | {pct(stats['best_day'])} |
| Maximum drawdown | {pct(stats['maximum_drawdown'])} |
| Skewness | {stats['skewness']:.3f} |
| Excess kurtosis | {stats['excess_kurtosis']:.3f} |

![Return distribution]({artifact_symbol(report['symbol'])}_return_distribution.svg)

## Limits

{chr(10).join(f'- {item}' for item in report['limitations'])}

## Source

- URL: <{source_url}>
- Retrieved: {report['retrieved_at']}

> {report['disclaimer']}
"""


def write_artifacts(report: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{artifact_symbol(report['symbol'])}_return_distribution"
    paths = {
        "json": output_dir / f"{stem}.json",
        "markdown": output_dir / f"{stem}.md",
        "svg": output_dir / f"{stem}.svg",
    }
    paths["json"].write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    paths["svg"].write_text(render_svg(report), encoding="utf-8")
    paths["markdown"].write_text(render_markdown(report), encoding="utf-8")
    return {name: str(path.resolve()) for name, path in paths.items()}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze adjusted daily returns and write SVG/JSON/Markdown artifacts."
    )
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--range", dest="period", choices=VALID_RANGES, default="10y")
    parser.add_argument("--input-json", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.input_json:
        symbol, prices, source_url = load_fixture(args.input_json)
    else:
        try:
            symbol = normalize_symbol(args.symbol)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        prices, source_url = fetch_prices(symbol, args.period)
    report = analyze(symbol, prices, source_url)
    paths = write_artifacts(report, args.output_dir)
    print(json.dumps({"ok": True, "artifacts": paths, "summary": report["statistics"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
