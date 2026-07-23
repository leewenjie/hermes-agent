---
name: stocks
description: Fetch timestamped public stock and crypto data.
version: 1.0.0
author: Mibay (Mibayy), Lee Wen Jie, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [stocks, finance, markets, crypto, research]
    category: research
    related_skills: [investment-research, market-return-analysis]
---

# Stocks Skill

Fetch read-only quotes, adjusted-price history, ticker matches, comparisons, and
crypto prices from Yahoo Finance. This skill preserves source URLs and retrieval
timestamps; it does not provide licensed real-time data, trading, or advice.

## When to Use

- A user needs a recent public quote with currency and exchange context.
- A ticker must be resolved from a company name.
- A comparison needs like-for-like public price observations.
- Historical performance should use adjusted closes when available.
- A public crypto-to-fiat proxy quote is sufficient for research.

## Prerequisites

- Python 3.11 or newer.
- Network access to Yahoo Finance public endpoints.
- The `terminal` tool.
- No API key or third-party Python package is required.

Yahoo Finance is an unofficial, best-effort source. Values may be delayed,
revised, stale, incomplete, or unavailable and must not be described as an
exchange-grade real-time feed.

## How to Run

From the Hermes repository or installed skill directory:

```bash
python3 skills/research/stocks/scripts/stocks_client.py quote AAPL
```

All commands return JSON. Keep the `source` or `sources` object with any result
used in a customer-facing memo.

## Quick Reference

```bash
python3 skills/research/stocks/scripts/stocks_client.py quote AAPL MSFT
python3 skills/research/stocks/scripts/stocks_client.py search "Tesla"
python3 skills/research/stocks/scripts/stocks_client.py history NVDA --range 1y
python3 skills/research/stocks/scripts/stocks_client.py compare SPY QQQ
python3 skills/research/stocks/scripts/stocks_client.py crypto BTC
```

## Procedure

1. Confirm symbol, exchange, currency, requested period, and desired price field.
2. Use `search` when the ticker is uncertain rather than guessing.
3. Use `quote` for a recent provider observation and label its retrieval time.
4. Use `history` for OHLCV and adjusted-close performance. Do not mix raw close
   and adjusted-close return claims without saying so.
5. Use `compare` only for symbols observed on a consistent basis and timestamp.
   Its schema-versioned output reports `trailing_1y_return_pct` only when the
   provider observations span at least 330 days, and identifies the price field.
6. Preserve provider, source URL, retrieval timestamp, and endpoint caveats.
7. Verify material company, filing, and event claims through primary sources
   using `web_search` and `web_extract`; a quote endpoint is not a filing source.

## Pitfalls

- A provider quote is not guaranteed to be live or exchange-licensed.
- Quote timestamps, exchange sessions, currencies, and corporate actions differ.
- Raw OHLC fields and adjusted-close total-return fields answer different questions.
- Market cap, valuation ratios, and 52-week fields can be absent or stale.
- Crypto symbols such as `BTC-USD` are provider instruments, not account balances.
- Do not infer liquidity from one quote or suitability from past returns.
- Do not output buy/sell instructions, personalized sizing, or promised returns.

## Verification

- Confirm every successful response includes `source_url` and `retrieved_at`.
- Confirm quote and comparison requests accept no more than ten unique symbols.
- Confirm history reports the adjusted-close field when Yahoo supplies it.
- Confirm comparison output reports schema version 2, calendar coverage, and
   the exact adjusted- or raw-close basis used for trailing return.
- Confirm malformed symbols and oversized responses fail without writing artifacts.
- Run `scripts/run_tests.sh tests/skills/test_stocks_skill.py -q`.
