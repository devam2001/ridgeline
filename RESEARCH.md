# Research results

**Generated:** 2026-10-10 by `python scripts/research.py gates`. The raw data is in
`user_data/research/latest.json`, and the panel's **Research** tab shows the same numbers.

**Setup**
- 15 Binance spot pairs, long-only.
- Starting balance $1,000, at most 3 open trades.
- 0.1% fee on every trade.
- Protections are enabled in every run.
- **In-sample (IS):** 2023-06-01 → 2025-06-01. Used for design and hyperopt.
- **Out-of-sample (OOS):** 2025-06-01 → 2026-10-10. Never used for tuning.

| Benchmark (buy and hold) | IS | OOS |
|---|---|---|
| Equal-weight, all 15 pairs | +163.4% | −16.7% |
| BTC only | +293.9% | −21.8% |

The out-of-sample period was a bear and sideways market. A long-only bot that
kept its capital there did well.

## Summary

| Strategy (tuned) | IS return | OOS return | OOS max DD | OOS profit factor | OOS trades | Gates |
|---|---|---|---|---|---|---|
| **TrendRider4h** | +82.5% | **+21.1%** | **6.0%** | **1.66** | 90 | **8 / 9** |
| EmaRsiMomentum | +56.2% | −1.0% | 20.2% | 0.97 | 60 | 2 / 9 |
| TrendDipBuyer1h | −17.5% | −15.0% | 19.8% | 0.77 | 288 | 4 / 9 |

**No strategy is eligible for real money yet.**

TrendRider4h passes every gate except sample size: 90 out-of-sample trades
against the 100 required. It is the only candidate worth paper trading.

### TrendRider4h: why it works, and the caveats

It enters on a 4h Donchian breakout and lets winners run under an ATR
chandelier trailing stop, with no take-profit cap. It also refuses all new
longs while BTC is below its daily EMA200.

- **That BTC filter does most of the work.** From 2025-12 to 2026-03 the
  market fell 24.5% and the strategy made zero trades, so it sat in cash.
- **It is still profitable with fees +50%** (+19.0% OOS).
- **Walk-forward.** It was profitable in 78.6% of all 3-month windows. Counting
  only the OOS windows, the ones hyperopt never saw, it was profitable in 4 of 6
  (67%). Neither number includes the zero-trade windows as wins.
- **No lookahead bias.** Freqtrade's `lookahead-analysis` is clean.
- **Indicators are stable** whatever the warmup length: the worst deviation was 0.2%.
- **The in-sample result is much better than the out-of-sample one** (+82.5% vs +21.1%).
  Expect live results to come in below backtests too.
- **It trades rarely**, about 5–6 trades a month across 15 pairs. A few trades
  more or less will move the stats a lot. That's exactly why the trade-count gate exists.
- **It lags in strong bull markets.** In-sample buy-and-hold made +163%. This is
  a strategy that loses less in downturns, not one that beats a bull run.

### Why the others fail
- **EmaRsiMomentum** looked great after tuning in-sample (+56%) and broke even
  out-of-sample (−1%). That's textbook overfitting: the tuned parameters fit the
  past, not a lasting edge.
- **TrendDipBuyer1h** loses money in both periods even after tuning. Buying dips
  on 1h crypto charts doesn't survive 0.1% fees here.

## Next steps
1. **Paper-trade TrendRider4h for 4+ weeks.** In the panel, open the Paper
   trading tab, pick TrendRider4h, and press Start. Afterwards, replay the same
   dates in the Replay tab and compare the two.
2. **Grow the sample.** Add more liquid pairs to `config.json`, re-download the
   data, and re-run `research.py gates` to get past 100 OOS trades. Don't re-tune
   on OOS to do this.
3. **Re-run the gates monthly** as new data arrives. Each new month is fresh
   out-of-sample data.

## Reproduce
```bash
freqtrade download-data --userdir user_data --timeframes 1h 4h 1d --timerange 20220601-
python scripts/research.py hyperopt -s TrendRider4h --epochs 300   # optional: tuned params already in TrendRider4h.json
python scripts/research.py gates
```

Backtests are not a promise of future results. This is not financial advice.
