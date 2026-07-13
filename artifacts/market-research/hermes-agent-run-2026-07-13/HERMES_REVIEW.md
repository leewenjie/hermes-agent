# SPY return distribution review

Source: https://query1.finance.yahoo.com/v8/finance/chart/SPY?range=10y&interval=1d&events=history
Retrieved: 2026-07-13T05:25:23+00:00

This memo reviews 10 years of daily adjusted-close returns for SPY, which the helper uses as an investable S&P 500 proxy rather than the index itself. The sample contains 2,514 adjusted-price observations and 2,513 daily returns from 2016-07-12 to 2026-07-10.

## What the distribution looks like

The center is slightly positive: mean daily return is 0.063% and the median is 0.073%. The day-to-day distribution is wide enough to matter, with 1.13% daily volatility and 55.4% positive days. Shape-wise, it is mildly left-skewed (-0.314) with very heavy tails (excess kurtosis 14.979), so large moves occur more often than a normal bell curve would suggest.

## Tail risk

The 5th percentile daily return is -1.67% and the 1st percentile is -3.24%. Historical expected shortfall at the 95% level is -2.74%, meaning the average of the worst 5% of days is materially worse than the 5th percentile cutoff. The worst day in the sample was -10.94%, while the best day was +10.50%.

## Drawdown

Maximum drawdown over the sample was -33.72%. That matters because even with a positive long-run average return, the path can include deep interim losses that last well beyond a single day.

## Useful follow-up regime analysis

The next useful step is to condition the same return series on regime slices instead of looking at the full sample only. Good splits would be:

- rolling volatility buckets, such as quiet vs. stressed months
- drawdown state, comparing days near highs versus days already in drawdown
- calendar regimes, such as 2020 shock periods vs. post-2022 rate-hike periods
- trend regime, comparing rising-20-day moving-average periods versus falling ones

Those cuts would help show whether tail loss and drawdown risk concentrate in specific states rather than being evenly distributed.

## Limitations

- Historical observations do not forecast future returns.
- Daily sampling does not describe intraday or monthly behavior.
- The sample mixes materially different market regimes.
- Yahoo Finance is an unofficial source and may revise or omit data.

## Boundary

This is research and decision support only, not financial advice, a personalized recommendation, portfolio management, or a guarantee of returns.

Artifacts:
- JSON: ./SPY_return_distribution.json
- SVG: ./SPY_return_distribution.svg
- Helper markdown: ./SPY_return_distribution.md
