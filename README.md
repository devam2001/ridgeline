# ridgeline

A Freqtrade bot for Binance **spot**, with:

- three candidate strategies and an honest research pipeline to pick between them,
- a **paper-money control panel**: live paper trading with a fake wallet you
  choose and can reset, plus fast historical replay,
- the official **FreqUI** dashboard for detailed live monitoring.

Everything ships in **dry-run** mode. Nothing here touches real money; the
panel cannot start a live bot at all.

## What's in here

| Path | What |
|---|---|
| `user_data/strategies/TrendRider4h.py` | 4h Donchian breakout trend-follower, BTC bear-market filter, ATR chandelier exit |
| `user_data/strategies/TrendDipBuyer1h.py` | 1h buy-the-dip inside an uptrend (mean reversion) |
| `user_data/strategies/EmaRsiMomentum.py` | original 1h EMA-crossover + RSI strategy (hyperoptable) |
| `user_data/config.json` | exchange, pairs, risk settings - **no secrets** |
| `user_data/config.private.json` | API login, JWT secret, exchange keys - **never share/commit** (loaded automatically via `add_config_files`) |
| `panel/` | control panel (FastAPI + plain JS) |
| `scripts/research.py` | IS/OOS backtests, walk-forward, fee stress, bias checks, go-live gates |
| `RESEARCH.md` | latest research results and what they mean |

## 1. Install

```bash
python -m venv .venv
.venv\Scripts\activate                       # macOS/Linux: source .venv/bin/activate
pip install "freqtrade[hyperopt]" "joblib<1.6"
freqtrade install-ui                          # downloads FreqUI
freqtrade list-strategies --userdir user_data # all three should show OK
```

`joblib<1.6` is needed because joblib 1.6 removed a module Freqtrade's
hyperopt still imports.

If `user_data/config.private.json` doesn't exist, create it:

```json
{
    "exchange": {"key": "", "secret": ""},
    "api_server": {
        "username": "freqtrader",
        "password": "<long random string>",
        "jwt_secret_key": "<64 random hex chars>",
        "ws_token": "<random string>"
    }
}
```

## 2. Control panel (paper money + replay)

```bash
.\start-panel.ps1        # or double-click start-panel.bat
```

Open **http://127.0.0.1:8090**.

- **Paper trading:** pick a starting balance (any amount of fake USDT) and a
  strategy, press **Start**. The bot trades live Binance prices without sending
  orders. **Reset wallet** starts a fresh session with a new balance; every
  session keeps its own database in `user_data/paper/`, so old results aren't lost.
  While it runs, **Open FreqUI** (http://127.0.0.1:8080, log in with the username/password
  from `config.private.json`) shows charts, per-trade detail and more.
- **Replay:** pick strategy, balance and date range. It simulates that period in
  seconds and shows the equity curve against buy-and-hold, exit reasons, and every trade.
- **Research:** the go-live gate checklist from the latest `research.py gates` run.

The panel only controls bots it started. If you started one by hand in a
terminal, stop that one first (Ctrl+C) before using Start.

## 3. Data

```bash
freqtrade download-data --userdir user_data --timeframes 1h 4h 1d --timerange 20220601-
```

(Pairs come from `config.json`. The 1d timeframe is needed for the daily
EMA200 / BTC-regime filters, which need ~200 days of warm-up.)

## 4. Research - does any strategy actually have an edge?

```bash
python scripts/research.py baseline     # in-sample vs out-of-sample vs buy-and-hold
python scripts/research.py walkforward  # rolling 3-month windows
python scripts/research.py stress       # out-of-sample with +50% fees
python scripts/research.py bias         # Freqtrade lookahead + recursive analysis
python scripts/research.py hyperopt -s TrendRider4h --epochs 300   # tunes on in-sample only
python scripts/research.py gates        # everything above -> user_data/research/latest.json
```

- **In-sample** (2023-06-01 → 2025-06-01) is the only period used for designing and tuning.
- **Out-of-sample** (2025-06-01 → latest data) is only used to judge the result.

Tuning against OOS would make it in-sample too, and the numbers would stop
meaning anything.

Hyperopt saves the best parameters to `user_data/strategies/<Strategy>.json`,
which every later backtest/dry-run picks up automatically. Delete that file to
go back to the defaults in the `.py`.

### Go-live gates

A strategy must pass **all** of these, out-of-sample, before real money:

- profit > 0%
- profit factor ≥ 1.3
- max drawdown ≤ 20%
- Calmar ≥ 1.0
- ≥ 100 trades
- profitable in ≥ 60% of walk-forward windows
- still profitable with +50% fees
- no lookahead bias
- stable indicators

Then it must paper-trade for **4+ weeks** with results close to a replay of
the same weeks. If nothing passes, the answer is "don't go live yet" - not
"loosen the gates".

Plain backtests still work as usual:

```bash
freqtrade backtesting --userdir user_data --strategy TrendRider4h --timerange 20250601- --enable-protections
```

(`--enable-protections` matters: without it, backtesting ignores the
cooldown / StoplossGuard / MaxDrawdown protections.)

## 5. Going live (manual, only after all gates + 4 weeks of paper trading)

1. Create a Binance API key with **spot trading only, withdrawals disabled**.
   Put it in `config.private.json` (never in `config.json`).
2. Flip `"dry_run": true` → `false` in `config.json` and start from the CLI:
   `freqtrade trade --userdir user_data --strategy <Name>`. The panel will
   never do this for you.
3. Start with an amount you can afford to lose entirely. Scale up only after
   real fills and slippage match the paper results.
4. Turn on `telegram` in the config for entry/exit notifications.

## Risk controls (EmaRsiMomentum; the other strategies follow the same pattern)

- **1.5% risk per trade, ATR-adaptive** — the stoploss distance is no
  longer a flat 5%; it's `min(8%, max(2%, ATR% × 2.5))`, computed per pair
  and pinned once at entry (`custom_stoploss` + `custom_stake_amount`
  agree on the same distance). A volatile altcoin gets a wider stop and a
  smaller position; a quiet BTC range gets a tighter stop and a bigger one
  — same 1.5%-of-equity dollar risk either way.
- **Trend-strength filter (ADX)** — won't enter unless ADX > 20, i.e. the
  market is actually trending rather than chopping sideways, which is
  where EMA-crossover strategies normally bleed out on false signals.
- **Macro-trend filter (4h EMA200)** — this is a long-only spot bot, so
  it only takes an entry if price is above the 4h EMA200. The single
  biggest lever for a long-only momentum bot is not fighting the bigger
  trend.
- **Drawdown circuit breaker** — the `protections` property in the
  strategy halts new entries for 24 candles if drawdown over the last 48
  candles exceeds 5% (Freqtrade's `MaxDrawdown` protection).
- **Stoploss guard** — if a single pair stops out twice in 24 hours, that
  pair gets locked out for 12 candles rather than immediately re-entering
  into what might be a bad regime for it.
- **Cooldown** — a brief pause on a pair right after any exit, to avoid
  whipsawing in and out of the same signal.

None of this makes the strategy profitable by itself — it just controls
how much damage a bad stretch can do. Read the backtest numbers with a
skeptical eye before trusting them.

## Note on Australia

Binance's futures/options/leveraged-token products aren't available to
Australian retail users — this bot only trades **spot**, so that
restriction doesn't affect it. If you ever want to add derivatives or
margin later, that's a separate regulatory conversation.

## Not financial advice

This is a piece of software, not a strategy recommendation. Backtest
results are not a guarantee of future performance, crypto markets are
volatile, and you should only risk money you can afford to lose.
