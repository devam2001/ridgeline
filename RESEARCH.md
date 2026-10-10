# Research results

**Generated:** 2026-10-11 by `python scripts/research.py gates`. Raw data is in
`user_data/research/latest.json`, and the panel's **Research** tab shows the same numbers.

**Setup**
- Long-only Binance spot, starting with $1,000, at most 3 trades open at once.
- 0.1% fee per trade, and protections switched on.
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
| **TrendRider4h** | +10.8% | **+19.3%** | **9.8%** | **1.47** | 99 | 71% | **8 / 9** |
| EmaRsiMomentum | +13.6% | +8.0% | 16.5% | 1.18 | 77 | 43% | 5 / 9 |
| TrendDipBuyer1h | −20.7% | −17.7% | 19.5% | 0.75 | 309 | 36% | 3 / 9 |

**No strategy is eligible for real money yet.**

TrendRider4h fails only the sample-size gate, by **one trade**: 99 against 100.
The gates stay as they are. Each new month of data adds about 6 OOS trades, so
the next monthly re-run will settle it either way.

## Why this run replaces the earlier ones

1. **Hand-picked pairs (15).** OOS +21.1%, IS +82.5%.
   - The 15 pairs were well-known large caps, picked with today's knowledge.
2. **Today's top-26 by volume.** OOS +35.7%. **Rejected: biased.**
   - Ranking by today's volume favours coins that pumped during the test.
   - QNT (+133% in OOS) alone added +$210 from 5 trades.
   - ZEC (+2,171%) pushed the equal-weight benchmark to +68%.
3. **Point-in-time top 25 (this run).** OOS +19.3%, IS +10.8%.
   - Pairs are chosen only from information available at the time.

The gap between the IS results is the most important finding:
- **+82.5% on the hand-picked pairs vs +10.8% point-in-time.** TrendRider4h
  was hyperopted on the 15 hand-picked pairs, and a large part of that IS
  result came from fitting those particular coins.
- **The OOS result barely moved (+21% → +19%).** It's the more trustworthy
  number, but expect live results below even that.

## TrendRider4h in detail

- **Its edge is not losing money in downturns.** Point-in-time walk-forward,
  return vs market for each window:

  | Window | Strategy | Market |
  |---|---|---|
  | 2024-06 → 09 | −5.5% | −38% |
  | 2024-12 → 2025-03 | +0.6% | −41% |
  | 2025-09 → 12 | +0.7% | −43% |
  | 2025-12 → 2026-03 | 0.0%, no trades (BTC filter) | −31% |
  | 2024-09 → 12 | +30.3% | +98% |

  In bull runs it catches only part of the move, as the last row shows.
- **Walk-forward:** profitable in 10 of 14 windows (71%), or 4 of 6 OOS-only.
  Zero-trade windows don't count as wins. The worst window was −7.0%.
- **Robustness:**
  - Still +17.3% OOS with fees raised by half.
  - Freqtrade's lookahead check finds no bias.
  - Indicators are stable whatever the warm-up length (worst deviation 0.2%).
  - 4h vs 1h intra-candle detail gives near-identical results (+35.7% vs
    +36.2%, measured on the earlier 26-pair set).
- **It trades rarely:** 5–7 trades a month. The stats are noisy at this sample size.

### The other two
- **EmaRsiMomentum** improves with point-in-time pairs (+8% OOS) but is
  inconsistent: profitable in only 43% of windows, with a profit factor of 1.18.
- **TrendDipBuyer1h** loses money in every configuration tested.

## Known limitations
- **Survivorship bias.** Binance doesn't serve data for delisted coins, so
  coins that died between 2023 and 2026 are missing from the candidate pool.
- **Tuning on hand-picked pairs.** TrendRider4h's parameters were tuned on the
  hand-picked 15, not on point-in-time universes. Re-tuning on point-in-time IS
  universes is a valid next step. It must not touch OOS.
- **No modelled slippage.** Backtests fill at the candle price plus fees.
  Paper trading is where slippage shows up.

## Next steps
1. **Paper-trade TrendRider4h for 4+ weeks** from the panel. Use Reset wallet
   and pick TrendRider4h. `config.json` now holds today's point-in-time top 25.
2. **Re-run `research.py gates` monthly.** New data adds OOS trades, and the
   100-trade gate will be settled honestly.
3. **Optionally re-hyperopt TrendRider4h** on point-in-time IS universes. Treat
   it as a new candidate and judge it only on OOS.

## Reproduce
```bash
python scripts/research.py universe --download --write-config
python scripts/research.py gates
```
For first-time data setup, see the README's Data section.

Backtests are not a promise of future results. This is not financial advice.
