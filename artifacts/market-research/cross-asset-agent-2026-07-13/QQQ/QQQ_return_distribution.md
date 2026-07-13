# QQQ return distribution

## Measurement contract

- Proxy: QQQ is analyzed as the selected market proxy.
- Price field: adjusted close
- Frequency: daily simple returns
- Period: 2021-07-12 to 2026-07-10
- Observations: 1,255 prices and 1,254 returns

## Distribution summary

| Statistic | Value |
| --- | ---: |
| Mean daily return | 0.07% |
| Median daily return | 0.11% |
| Daily volatility | 1.44% |
| Annualized return | 15.66% |
| Annualized volatility | 22.78% |
| Positive days | 54.86% |
| 1st percentile | -3.89% |
| 5th percentile | -2.31% |
| Historical expected shortfall (95%) | -3.25% |
| Worst day | -6.21% |
| Best day | 12.00% |
| Maximum drawdown | -35.12% |
| Skewness | 0.172 |
| Excess kurtosis | 5.061 |

![Return distribution](QQQ_return_distribution.svg)

## Limits

- Historical observations do not forecast future returns.
- Daily sampling does not describe intraday or monthly distributions.
- The sample can mix materially different market regimes.
- Yahoo Finance is an unofficial source and may revise or omit data.

## Source

- URL: https://query1.finance.yahoo.com/v8/finance/chart/QQQ?range=5y&interval=1d&events=history
- Retrieved: 2026-07-13T06:03:22+00:00

> Research and decision support only; not financial advice, a personalized recommendation, portfolio management, or a guarantee of returns.
