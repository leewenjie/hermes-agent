---
name: market-return-analysis
description: Analyze return distributions with auditable artifacts.
version: 1.0.0
author: Lee Wen Jie, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [markets, investing, returns, risk, charts]
    category: research
    related_skills: [stocks]
---

# Market Return Analysis Skill

Analyze historical market returns with reproducible statistics and chart artifacts. Use adjusted prices and retain source metadata; do not turn descriptive history into personalized investment advice or promised future performance.

## When to Use

- Plot the distribution of S&P 500 or ETF daily returns.
- Compare volatility, drawdowns, tail loss, or positive-day frequency.
- Produce a reusable JSON, Markdown, and SVG evidence pack.
- Check whether a market claim is supported by historical observations.

Do not use this skill for trade execution, personalized allocation, suitability advice, or guaranteed-return claims.

## Prerequisites

- Python 3.9 or newer.
- Network access for live Yahoo Finance chart data.
- The `terminal` tool.
- No API key or third-party Python package is required.

For the S&P 500, the default symbol is `SPY`, a liquid investable proxy. State clearly that SPY is not the index itself and that adjusted-close history incorporates distributions according to the data provider.

## How to Run

From the Hermes repository or an installed skill directory:

```bash
python3 skills/research/market-return-analysis/scripts/analyze_returns.py \
  --symbol SPY --range 10y --output-dir ./artifacts/spy-returns
```

The script writes:

- `SPY_return_distribution.svg`
- `SPY_return_distribution.json`
- `SPY_return_distribution.md`

## Quick Reference

```bash
# Ten-year daily SPY return distribution
python3 skills/research/market-return-analysis/scripts/analyze_returns.py --symbol SPY --range 10y --output-dir ./artifacts/spy

# Five-year Nasdaq-100 proxy
python3 skills/research/market-return-analysis/scripts/analyze_returns.py --symbol QQQ --range 5y --output-dir ./artifacts/qqq

# Deterministic offline fixture
python3 skills/research/market-return-analysis/scripts/analyze_returns.py --input-json prices.json --output-dir ./artifacts/offline
```

## Procedure

1. **Define the question.** Record the proxy, date range, return frequency, and whether adjusted prices are appropriate. Completion means the measurement contract is explicit.
2. **Collect the series.** Run the script and retain its source URL and retrieval timestamp. Completion means at least two valid observations produced one return.
3. **Inspect the distribution.** Review sample size, mean, median, volatility, 5th and 1st percentiles, expected shortfall, skewness, excess kurtosis, worst and best days, and maximum drawdown. Completion means every statistic is tied to the same cleaned series.
4. **Read the chart with the table.** Use the SVG for shape and the JSON for exact values. Completion means no claim relies only on visual inspection.
5. **Write the research note.** Separate observed history from interpretation, list missing evidence, and add the non-advisory boundary. Completion means the note states that historical distributions do not forecast future returns.

For a source-linked memo, use this structure:

1. question and measurement contract
2. executive summary
3. distribution shape and tail observations
4. drawdown and volatility observations
5. evidence supporting the thesis
6. evidence weakening the thesis
7. limitations and missing evidence
8. source URL and retrieval timestamp

## Pitfalls

- **Proxy confusion:** SPY tracks the S&P 500 but has fees, market pricing, and fund mechanics. Do not label it as exact index history.
- **Frequency confusion:** daily risk statistics do not describe intraday or monthly distributions.
- **Adjusted versus raw prices:** adjusted prices are usually appropriate for total-return research; state the choice.
- **Tail overconfidence:** historical 1st/5th percentiles are sample descriptions, not guaranteed loss limits.
- **Regime blindness:** a single long sample mixes different inflation, rate, liquidity, and volatility regimes.
- **Unofficial endpoint:** Yahoo Finance can rate-limit or change its chart API. Preserve errors and never fabricate missing observations.
- **Advice leakage:** describe evidence and scenarios. Do not output “buy,” “sell,” personalized sizing, or guaranteed alpha.

## Verification

- Run the script and confirm all three artifacts exist.
- Confirm `observation_count = return_count + 1`.
- Confirm percentile ordering from the JSON: `p01 <= p05 <= median <= p95 <= p99`.
- Open the SVG and verify bars, zero line, title, and summary statistics are visible.
- Confirm the Markdown includes the symbol, date range, source URL, retrieval timestamp, proxy caveat, and non-advisory disclaimer.
- Run `scripts/run_tests.sh tests/skills/test_market_return_analysis_skill.py -q` from the repository root.
