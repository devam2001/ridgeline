# Research results

**Generated:** 2026-10-15 by `python scripts/research.py gates`. Raw data is in
`user_data/research/latest.json`, and the panel's **Research** tab shows the same numbers.
The same run with a 65 USDT (≈100 AUD) account is in `latest-65.json`
(`research.py gates --balance 65`). The case study PDF is built from both
(`python scripts/case_study.py`).

**Setup**
- Long-only Binance spot, starting with $1,000, at most 3 trades open at once.
- 0.1% fee per trade, and protections switched on.
- **The portfolio risk limits are applied**, exactly as in paper and live
  trading: 35% max position, 4.5% max open risk, 3% daily loss limit, 15%
  drawdown halt. See `docs/OPERATIONS.md`.
- **In-sample (IS):** 2023-06-01 → 2025-06-01. Used for design and hyperopt.
- **Out-of-sample (OOS):** 2025-06-01 → 2026-10-10. Never used for tuning.
- **Pairs are chosen point-in-time.** Each window trades the top 25 USDT pairs
  by volume over the 30 days *before* it starts. Stablecoins, wrapped and
  leveraged tokens, and coins listed less than 210 days earlier are excluded.
  There are 14 windows and 87 distinct pairs in total.
  - Example: the OOS window traded BTC ETH SOL PEPE XRP DOGE SUI BNB TRX WIF
    ADA NEIRO ENA WLD AVAX LINK LTC BONK UNI AAVE ETHFI TAO SHIB NEAR ARB.

| Benchmark (buy and hold) | IS | OOS |
|---|---|---|
| Equal-weight, same point-in-time pairs | +89.1% | **−32.8%** |
| BTC only | +293.9% | −21.8% |

## Summary

| Strategy | IS return | OOS return | OOS max DD | OOS profit factor | OOS trades | Walk-forward | Gates |
|---|---|---|---|---|---|---|---|
| **TrendRider4h** | +5.6% | **+17.3%** | 9.8% | 1.43 | 99 | 64% | **8 / 9** |
| MomentumRotation1d | +24.4% | +13.1% | **5.2%** | **1.59** | 63 | 57% | 7 / 9 |
| TrendRider4hPIT (re-tuned) | **+45.6%** | +13.8% | 9.9% | 1.33 | 98 | 50% | 7 / 9 |
| EmaRsiMomentum | +7.7% | +5.1% | 18.1% | 1.13 | 74 | 43% | 6 / 9 |
| TrendDipBuyer1h | −8.0% | −17.8% | 19.2% | 0.73 | 300 | 36% | 4 / 9 |

### What the risk limits changed

| | Without limits | With limits |
|---|---|---|
| TrendRider4h, OOS | +19.3% | **+17.3%** |
| TrendRider4h, IS | +10.8% | +5.6% |
| TrendRider4h, walk-forward | 71% | 64% |
| TrendDipBuyer1h, IS | −20.7% | **−8.0%** |

The limits **cost return on the good strategy**. The 35% position cap blocks
the large positions that risk-based sizing wants in low-volatility coins
(BTC, BNB, TRX). The limits **cut losses on the bad strategy**: the drawdown
halt more than halved TrendDipBuyer1h's in-sample loss. That trade-off is
the point of a risk layer.

**No strategy is eligible for real money yet.**

TrendRider4h fails only the sample-size gate, by **one trade**: 99 against 100.
The gates stay as they are. Each new month of data adds about 6 OOS trades, so
the next monthly re-run will settle it either way.

MomentumRotation1d fails the sample-size gate (63 trades) and the walk-forward
gate (57% against 60%).

## Re-tuning TrendRider4h on point-in-time pairs

TrendRider4h's parameters were tuned on 15 hand-picked pairs, chosen with
today's knowledge. `research.py hyperopt` also had a bug: it tuned on
`config.json`'s live list, today's top coins. It now tunes on the
point-in-time universe at the start of the in-sample period.

TrendRider4hPIT is the same strategy with its own parameter file. It was tuned
that way for 300 trials (Calmar objective), using in-sample data only:

| | Breakout | ADX | Trailing stop | Exit window | IS | OOS | OOS PF | Walk-forward |
|---|---|---|---|---|---|---|---|---|
| TrendRider4h | 15 | 23 | 4.9 × ATR | 6 | +5.6% | **+17.3%** | **1.43** | **64%** |
| TrendRider4hPIT | 28 | 22 | 3.9 × ATR | 5 | **+45.6%** | +13.8% | 1.33 | 50% |

**Re-tuning made it worse where it counts.** In-sample return went up eightfold;
out of sample it fell by 3.5 points, and its out-of-sample quarters went from
3 of 6 profitable to 2 of 6. That is overfitting: the tuning fitted the
in-sample period rather than finding a better strategy. TrendRider4h stays the
lead candidate; TrendRider4hPIT is kept as evidence. The 65 USDT run gives
the same picture (+17.5% against +14.0%).

## Small accounts (≈100 AUD)

Same gates, starting with 65 USDT (about 100 AUD at an assumed 0.65 USD per AUD):

| Strategy | OOS balance (USDT) | OOS return | at 1,000 USDT | OOS max DD | Trades | Gates |
|---|---|---|---|---|---|---|
| TrendRider4h | 65 → 76.35 | +17.5% | +17.3% | 9.7% | 99 | 8 / 9 |
| MomentumRotation1d | 65 → 74.08 | +14.0% | +13.1% | 7.1% | 64 | 7 / 9 |
| TrendRider4hPIT | 65 → 74.07 | +14.0% | +13.8% | 9.8% | 98 | 7 / 9 |
| EmaRsiMomentum | 65 → 68.29 | +5.1% | +5.1% | 18.1% | 74 | 6 / 9 |
| TrendDipBuyer1h | 65 → 53.49 | −17.7% | −17.8% | 19.1% | 300 | 4 / 9 |

- **A 100 AUD account works.** TrendRider4h's positions came out at about
  6–26 USDT (median 8), above Binance's minimum order value. So it placed the
  same trades as the 1,000 USDT account.
- **Fees don't penalise it.** They are proportional, and there's no subscription.
- **The absolute profit is small:** +17% of 100 AUD is about 17 AUD over 16 months.
- **The rotation's walk-forward is weaker at 65 USDT:** 50%, against 57% at
  1,000. Minimum order sizes change a few of its trades. It fails the same two
  gates either way.

## More risk per trade

Can the profit be raised by taking more risk? TrendRider4h at 65 USDT, with
the portfolio caps loosened in proportion:

| Risk per trade | Caps (position / open risk / daily loss / halt) | IS | OOS | OOS max DD | OOS profit factor |
|---|---|---|---|---|---|
| 1.5% (default) | 35% / 4.5% / 3% / 15% | +5.8% | **+17.5%** | 9.7% | 1.44 |
| 3.0% | 50% / 9% / 6% / 25% | +26.9% | +20.7% | 19.2% | 1.26 |
| 4.5% | 70% / 13.5% / 9% / 30% | **−20.3%** | +13.1% | 27.1% | 1.14 |

- **Doubling the risk** added about 3 points of OOS return. It doubled the
  drawdown and pushed the profit factor below the 1.3 gate.
- **Tripling it** lost money in-sample.

More risk doesn't create more edge, so the default stays at 1.5%.

## MomentumRotation1d (new candidate)

**What it does:**
- Every day it ranks the whole point-in-time universe by 14-day
  volatility-adjusted momentum.
- It holds the top 3 while BTC is above its daily EMA200.
- It sells a coin when it drops out of the top 6, or on an ATR trailing stop.

**How it was built:**
- Its settings were chosen on in-sample data only, then the OOS was looked at once.
- After that, one bug flagged by the stability check was fixed: the BTC EMA200
  warm-up went from 210 to 360 days.

**Results:**
- **Lowest drawdown and best profit factor out of sample:** 5.2% and 1.59,
  with +13.1% return. Still +12.4% with fees raised by half.
- **Fragile.** In-sample return swung with the momentum lookback:

  | Lookback | 7d | 10d | 14d | 21d | 30d | 60d |
  |---|---|---|---|---|---|---|
  | IS return (65 USDT, before the warm-up fix) | −15.4% | +25.1% | **+37.2%** | −4.0% | −7.6% | −5.2% |

  A peak that narrow means part of the 14-day result is probably luck.
- **The design idea isn't supported.** It was meant to hold the leaders
  through rallies. In the +98% quarter (2024-09 → 12) it made only +0.8%,
  against +30.7% for TrendRider4h. In the −41% quarter it lost 10.7%.

## Changes to the checks in this run

Both changes measure the same thing more correctly. Neither changes any
strategy's eligibility.

**Lookahead check for cross-sectional strategies.** Freqtrade's
lookahead-analysis re-runs each signal with only that one pair loaded, so a
ranking across pairs always looked "biased" (rank 5 in the full run against 1
when alone).

Strategies that declare `cross_sectional = True` are now checked differently:
- `research.py` backtests 2024 again with later data removed from every pair at once.
- Every earlier trade must be identical.
- For MomentumRotation1d, all 132 compared trades matched.
- A unit test also checks this on synthetic data.

**Indicator stability is measured at the strategy's own warm-up or longer.**
The old check also counted runs with fewer warm-up candles than the strategy
declares, which never happens: Freqtrade always loads the declared warm-up.
- MomentumRotation1d: 0.39% at its 360 candles (was reported as 6.0%, from the 199-candle column).
- TrendRider4h: now 0.0% (was 0.2%, from columns below its 1,260 candles).

## Why this run replaces the earlier ones

1. **Hand-picked pairs (15).** OOS +21.1%, IS +82.5%.
   - The 15 pairs were well-known large caps, picked with today's knowledge.
2. **Today's top-26 by volume.** OOS +35.7%. **Rejected: biased.**
   - Ranking by today's volume favours coins that pumped during the test.
   - QNT (+133% in OOS) alone added +$210 from 5 trades.
   - ZEC (+2,171%) pushed the equal-weight benchmark to +68%.
3. **Point-in-time top 25 (this run).** OOS +19.3%, IS +10.8% before the risk
   limits; +17.3% and +5.6% with them.
   - Pairs are chosen only from information available at the time.

The gap between the IS results is the most important finding:
- **+82.5% on the hand-picked pairs vs +10.8% point-in-time** (both without
  risk limits). TrendRider4h was hyperopted on the 15 hand-picked pairs,
  and a large part of that IS result came from fitting those particular coins.
- **The OOS result barely moved (+21% → +19% before risk limits).** It's the more trustworthy
  number, but expect live results below even that.

## TrendRider4h in detail

- **Its edge is not losing money in downturns.** Point-in-time walk-forward,
  return vs market for each window:

  | Window | Strategy | Market |
  |---|---|---|
  | 2024-06 → 09 | −5.5% | −38% |
  | 2024-12 → 2025-03 | +0.6% | −41% |
  | 2025-09 → 12 | −0.9% | −43% |
  | 2025-12 → 2026-03 | 0.0%, no trades (BTC filter) | −31% |
  | 2024-09 → 12 | +30.7% | +98% |

  In bull runs it catches only part of the move, as the last row shows.
- **Walk-forward (with risk limits):** profitable in 9 of 14 windows (64%),
  or 3 of 6 OOS-only. Zero-trade windows don't count as wins. The worst
  window was −7.0%.
- **Robustness:**
  - Still +15.4% OOS with fees raised by half.
  - Freqtrade's lookahead check finds no bias.
  - Indicators are stable at its warm-up length (0.0% deviation at 1,260 candles or more).
  - 4h vs 1h intra-candle detail gives near-identical results (+35.7% vs
    +36.2%, measured on the earlier 26-pair set).
- **It trades rarely:** 5–7 trades a month. The stats are noisy at this sample size.

### The other two
- **EmaRsiMomentum** is positive OOS (+5.1%) but inconsistent: profitable
  in only 43% of windows, with a profit factor of 1.13.
- **TrendDipBuyer1h** loses money in every configuration tested.

## Known limitations
- **Survivorship bias.** Binance doesn't serve data for delisted coins, so
  coins that died between 2023 and 2026 are missing from the candidate pool.
- **Tuning on hand-picked pairs.** TrendRider4h's parameters were tuned on the
  hand-picked 15. Re-tuning on point-in-time pairs (TrendRider4hPIT) did worse
  out of sample, so the original parameters stay.
- **No modelled slippage.** Backtests fill at the candle price plus fees.
  Paper trading is where slippage shows up.

## Next steps
1. **Paper-trade TrendRider4h for 4+ weeks** from the panel. Use Reset wallet
   and pick TrendRider4h. `config.json` now holds today's point-in-time top 25.
   - Then run **Paper vs replay** on that session, or
     `python scripts/reconcile.py <session>`.
   - That's the last gate: the paper results must be close to a replay of
     the same weeks.
2. **Re-run `research.py gates` monthly.** New data adds OOS trades, and the
   100-trade gate will be settled honestly.
3. **Don't re-tune TrendRider4h again on this in-sample period.** The
   point-in-time re-tune overfitted. More tuning would just fit it harder.
4. **Keep MomentumRotation1d as a candidate, not a replacement.** It needs more
   OOS trades and a better walk-forward record. Don't re-tune its lookback on
   the OOS period.

## Reproduce
```bash
python scripts/research.py universe --download --write-config
python scripts/research.py gates
```
For first-time data setup, see the README's Data section.

Backtests are not a promise of future results. This is not financial advice.
