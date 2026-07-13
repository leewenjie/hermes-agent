# SPY vs QQQ 5-year adjusted daily-return comparison

Source-linked comparison using adjusted-close daily returns from Yahoo Finance chart data.

- SPY source: https://query1.finance.yahoo.com/v8/finance/chart/SPY?range=5y&interval=1d&events=history
- SPY retrieved: 2026-07-13T06:03:21+00:00
- QQQ source: https://query1.finance.yahoo.com/v8/finance/chart/QQQ?range=5y&interval=1d&events=history
- QQQ retrieved: 2026-07-13T06:03:22+00:00

## Core comparison

| Metric | SPY | QQQ | Difference (QQQ - SPY) |
| --- | ---: | ---: | ---: |
| Daily volatility | 1.08% | 1.44% | 0.35% |
| 1st percentile | -2.93% | -3.89% | -0.96% |
| 5th percentile | -1.66% | -2.31% | -0.64% |
| Historical expected shortfall (95%) | -2.47% | -3.25% | -0.79% |
| Skewness | 0.312 | 0.172 | -0.141 |
| Excess kurtosis | 8.985 | 5.061 | -3.925 |
| Positive-day ratio | 54.23% | 54.86% | 0.64% |
| Maximum drawdown | -24.50% | -35.12% | -10.62% |

## Short read

QQQ was more volatile than SPY over this sample, with a larger left tail, more negative expected shortfall, and a materially deeper maximum drawdown. The positive-day ratio was similar, but QQQ’s tail behavior was worse, and its excess kurtosis was lower than SPY’s in this sample while still indicating fat tails for both series. SPY was the calmer proxy, but both series showed non-normal return shapes.

## Limitations

- These are historical observations only; they do not forecast future returns.
- Daily data does not describe intraday risk or monthly compounding behavior.
- The 5-year window mixes different market regimes, including rate, inflation, and liquidity shifts.
- Yahoo Finance is an unofficial source and may revise or omit data.
- SPY is an investable S&P 500 proxy, not the index itself; QQQ is a Nasdaq-100 proxy.

## Useful follow-up tests

1. Compare rolling 63-day and 252-day volatility/expected-shortfall series to see whether the SPY–QQQ gap is stable or regime-dependent.
2. Split the 5-year sample into subperiods or calendar years and compare tail metrics by regime to test whether the drawdown and left-tail gap is concentrated in specific market phases.

## Non-advisory note

This is research and decision support only, not financial advice, a personalized recommendation, portfolio management, or a guarantee of returns.
