---
name: investment-research
description: Structure evidence-based investment and market research.
version: 1.0.0
author: Lee Wen Jie, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [finance, markets, investing, research, risk, valuation]
    category: research
    related_skills: [market-return-analysis, stocks, hyperliquid, polymarket, blogwatcher, dcf-model, comps-analysis, excel-author]
---

# Investment Research Skill

Structure repeatable, source-linked research on public markets, companies, and
crypto markets. This skill coordinates existing data and modeling skills; it
does not place orders, provide personalized financial advice, or turn a noisy
market signal into a recommendation without stating the evidence and limits.

## When to Use

- A user wants a market brief, asset comparison, thesis review, or watchlist.
- A question combines current facts with historical performance or risk.
- A company needs valuation context from multiples, cash flows, or peers.
- A user wants an auditable long/short equity momentum screen, methodology, or portfolio illustration.
- A crypto or perpetuals question needs funding, liquidity, candles, or trade review.
- A research result must be reproducible as JSON, Markdown, SVG, or an Excel model.

Do not use this workflow for order placement, account changes, personalized
suitability decisions, guaranteed returns, or unsupported price targets.

## Prerequisites

- The `terminal` tool for deterministic scripts and saved artifacts.
- The `web_search` and `web_extract` tools for current public sources when no
  structured finance source is configured.
- Install optional skills only when their data or output is required:
  - `official/blockchain/hyperliquid` for read-only perp/spot data and trade review.
  - `official/finance/excel-author` plus `dcf-model` or `comps-analysis` for models.
  - `official/research/blogwatcher` for recurring feed monitoring.
  - `official/research/polymarket` for prediction-market prices and history.

The bundled `stocks` skill provides timestamped Yahoo quotes, adjusted history,
and comparisons. The bundled `market-return-analysis` skill needs no API key,
uses Yahoo adjusted-close data as a proxy source, and writes an evidence pack.

## How to Run

1. Classify the request as one or more of: market history, current quote,
   company fundamentals, valuation, news/filings, crypto microstructure, or
   portfolio/trade review.
2. Choose the narrowest skill that covers the request. Do not mix live quotes,
   historical adjusted prices, and accounting data without labeling each source
   and timestamp.
3. Define a measurement contract before collecting data: symbols, benchmark,
   currency, frequency, date window, price field, and comparison basis.
4. Collect raw data first. Preserve source URLs, retrieval time, query inputs,
   missing values, and any proxy or survivorship limitations.
5. Compute results with the relevant script or workbook. Keep raw inputs
   separate from derived metrics and use formulas for model-derived values.
6. Produce a short memo with the question, evidence, counter-evidence, risks,
   limitations, and next checks. Cite every current or material factual claim.

## Quick Reference

| Question | Primary skill | Output |
| --- | --- | --- |
| S&P/ETF return distribution, drawdowns, tails | `market-return-analysis` | JSON, Markdown, SVG |
| Current quote, OHLCV, ticker search, side-by-side | `stocks` | JSON |
| Long/short equity momentum research | `stocks` + public primary sources | Screen or methodology memo |
| Peer valuation and operating metrics | `comps-analysis` + `excel-author` | Auditable `.xlsx` |
| Intrinsic-value scenarios and sensitivity | `dcf-model` + `excel-author` | Formula-driven `.xlsx` |
| Crypto candles, funding, order book, trade review | optional `hyperliquid` | JSON or review memo |
| Prediction-market odds and history | optional `polymarket` | Source-linked market brief |
| Recurring company or macro feed watch | `blogwatcher` | Dated feed digest |

## Procedure

### 1. Frame the question

Write one sentence that can be falsified. Separate descriptive questions
("what happened?") from explanatory questions ("what changed?") and decision
questions ("what should I do?"). For a decision question, report evidence and
scenarios rather than silently making the decision for the user.

### 2. Build the evidence table

For each claim, record the value, unit, period, source, retrieval date, and
confidence. Prefer primary filings, exchange or protocol data, and structured
providers. Use Yahoo or public web sources as labeled proxies, not as
institutional truth. If sources disagree, show the disagreement and investigate
period definitions, currency, corporate actions, and stale data.

### 3. Run the analysis

Use adjusted prices for total-return history when appropriate and state that
choice. Compare like-for-like frequencies and windows. Include base rates,
volatility, drawdown, tail behavior, liquidity or funding where relevant, and
at least one counter-signal. For valuation, separate historical inputs,
assumptions, and formulas; show Bear/Base/Bull cases and sensitivity rather
than a single point estimate.

### 4. Research long/short equity momentum

Treat momentum as a cross-sectional research method, not a standalone trade
signal. First classify the request as a current screen, historical study, or
portfolio-construction illustration and define:

- the as-of date and information cutoff;
- region, exchanges, security types, currency, benchmark, and rebalance schedule;
- the point-in-time eligible-universe source, IPO seasoning, minimum history,
  liquidity, price, and market-cap rules;
- adjusted-price and corporate-action treatment; and
- the holding period, long and short quantiles, and comparison basis.

Never substitute today's index members for historical constituents without
disclosure. If point-in-time membership, delistings, or suspended securities
are unavailable, label the work a **current-universe retrospective
illustration**, not an unbiased backtest, and list the unavailable data.

Use 12-1 momentum as the primary signal unless the research contract says
otherwise: rank each security by adjusted return from 12 months ago through
one month ago. The skip-month convention excludes the most recent month to
reduce short-term reversal contamination. Report cross-sectional percentiles
or robust z-scores, plus 6-1 and 3-1 rank stability when data permits. Flag
short histories, stale prices, missing observations, and incomparable windows.

For a long/short construction illustration:

1. Winsorize or robustly scale the signal, then neutralize within sector or
  industry. Sector-neutral is not the same as market-neutral.
2. Estimate beta from lagged data only, constrain portfolio beta near zero,
  and report residual sector, industry, size, and beta exposures.
3. State gross and net exposure, name and sector caps, rebalance buffers,
  turnover, and liquidity-aware position limits.
4. On the short side, verify locate and borrow availability, borrow fees and
  recall risk, short interest or days to cover, ADV participation, days to
  liquidate, crowding or ownership proxies, and hard-to-borrow exclusions.
  Mark unavailable borrow or crowding evidence as missing; never infer it
  from price history.
5. Model commissions, spread, market impact, financing, borrow, and turnover
  costs. Keep the out-of-sample boundary explicit and do not optimize on the
  same observations used to claim performance.

Separate statistical momentum evidence from fundamental confirmation. For
candidate names or cohorts, review filings, earnings, guidance, regulation,
capital allocation, and dated catalysts; show counter-evidence and conditions
that would invalidate the interpretation.

Report long, short, and long-minus-short contribution; volatility, drawdown,
tail loss, concentration, turnover, and factor exposures. Include momentum
crash, sharp reversal, sector rotation, short squeeze, borrow recall, and
liquidity-stress scenarios, with sensitivity to horizon, universe, rebalance
date, neutralization, and cost assumptions.

### 5. Review quality and risk

Before writing conclusions, check symbol and benchmark identity, timezone,
currency, missing observations, look-ahead bias, survivorship bias, fees,
slippage, funding, leverage, and data-source reliability. A backtest or
historical relationship is not proof of future performance. Treat prediction-
market prices as probabilities under market conditions, not certainties.

### 6. Deliver the memo

Use this order:

1. Question and measurement contract.
2. Bottom line, limited to what the evidence supports.
3. Key observations with dates and citations.
4. Counter-evidence and alternative explanations.
5. Risk, data-quality, and model limitations.
6. Reproducibility: commands, artifact paths, and source metadata.
7. Non-advisory note and follow-up checks.

## Pitfalls

- Do not call SPY “the S&P 500”; label it as an investable proxy.
- Do not compare adjusted-close total returns with raw-price returns silently.
- Do not infer fundamentals from a quote endpoint or infer liquidity from one L2 snapshot.
- Do not treat annualized returns, Sharpe-like statistics, or DCF outputs as forecasts.
- Do not use web search as the primary source when a configured structured source exists.
- Do not run a backtest without explicit costs, execution assumptions, and an out-of-sample boundary.
- Do not call a current-universe retrospective screen a point-in-time backtest.
- Do not describe sector neutrality as beta neutrality or market neutrality.
- Do not fabricate borrow, liquidity, crowding, constituent, or delisting data when unavailable.
- Do not expose account data or credentials in a memo or saved artifact.

## Verification

- Confirm every report has a source URL or an explicit user-provided source.
- Confirm dates, symbols, units, and observation counts are internally consistent.
- Confirm derived metrics can be regenerated from saved raw inputs.
- Confirm model workbooks recalculate with zero formula errors and contain source comments.
- Confirm momentum work states the 12-1 skip-month signal, point-in-time universe limits,
  sector and beta neutralization, gross and net exposure, implementation costs,
  turnover, borrow, liquidity, crowding, and adverse scenarios.
- Confirm the memo includes counter-evidence, limitations, and no personalized trade instruction.
- Run the relevant skill tests through `scripts/run_tests.sh` before delivery.
