# ridgeline

A Freqtrade bot for Binance **spot**, with:

- three candidate strategies and an honest research pipeline to pick between them,
- a **paper-money control panel**: live paper trading with a fake wallet you
  choose and can reset, plus fast historical replay,
- the official **FreqUI** dashboard for detailed live monitoring,
- **operations tooling**:
  - portfolio risk limits and a one-click kill switch
  - a tamper-evident audit trail
  - investor-style tear sheets
  - Docker deployment with a watchdog and Telegram alerts
  - tests and CI on every push

[![CI](https://github.com/devam2001/ridgeline/actions/workflows/ci.yml/badge.svg)](https://github.com/devam2001/ridgeline/actions/workflows/ci.yml)

Everything ships in **dry-run** mode. Nothing here touches real money; the
panel cannot start a live bot at all.

## What's in here

| Path | What |
|---|---|
| `user_data/strategies/TrendRider4h.py` | 4h Donchian breakout trend-follower, BTC bear-market filter, ATR chandelier exit |
| `user_data/strategies/TrendRider4hPIT.py` | TrendRider4h re-tuned on point-in-time pairs (candidate; own parameter file) |
| `user_data/strategies/TrendDipBuyer1h.py` | 1h buy-the-dip inside an uptrend (mean reversion) |
| `user_data/strategies/EmaRsiMomentum.py` | original 1h EMA-crossover + RSI strategy (hyperoptable) |
| `user_data/strategies/MomentumRotation1d.py` | daily rotation into the 3 strongest coins by risk-adjusted momentum while BTC is in an uptrend (candidate) |
| `user_data/config.json` | exchange, pairs, risk settings - **no secrets** |
| `user_data/config.private.json` | API login, JWT secret, exchange keys - **never share/commit** (loaded automatically via `add_config_files`) |
| `panel/` | control panel (FastAPI + plain JS) |
| `scripts/research.py` | IS/OOS backtests, walk-forward, fee stress, bias checks, go-live gates |
| `ridgeline/` | shared package: risk limits (`risk.py`, `freqtrade_risk.py`), audit trail, tear-sheet reports, paper-vs-replay reconciliation, alerts |
| `scripts/report.py`, `scripts/watchdog.py` | tear-sheet generator; health/risk watchdog with Telegram alerts |
| `scripts/reconcile.py` | the last go-live gate: compares a paper session with a replay of the same weeks |
| `scripts/update_data.py` | downloads/refreshes market data and the live pair list (monthly) |
| `scripts/case_study.py` | builds the research-format case study PDF from the latest results |
| `installer/`, `.github/workflows/release.yml` | Windows installer and the release pipeline (installer + Docker image) |
| `tests/`, `.github/workflows/ci.yml` | test suite (risk, audit, lookahead, universe, panel, hygiene) run by GitHub Actions |
| `Dockerfile`, `docker-compose.yml` | 24/7 deployment: bot + monitor-mode panel + watchdog |
| `docs/OPERATIONS.md` | runbook: risk limits, kill switch, alerts, VPS deployment, incidents, monthly routine |
| `RESEARCH.md` | latest research results and what they mean |

## 1. Install

### Windows installer (easiest)

Download `Ridgeline-Setup-<version>.exe` from the
[latest release](https://github.com/devam2001/ridgeline/releases/latest) and run it.
- It installs for your user only, so no admin rights are needed.
- It brings its own Python, Freqtrade and FreqUI.
- It adds **Ridgeline** (the panel) and **Update market data** to the Start menu.
- The installer isn't code-signed, so Windows SmartScreen may warn the first
  time. Choose **More info → Run anyway**.
- Your `user_data` (private config, paper sessions, data, audit trail) is kept
  when you upgrade or uninstall.

### Docker (servers)

A server can pull the released image instead of building it. See
[docs/OPERATIONS.md](docs/OPERATIONS.md#deploying-on-a-vps-docker).

### From source

```bash
python -m venv .venv
.venv\Scripts\activate                       # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
freqtrade install-ui                          # downloads FreqUI
freqtrade list-strategies --userdir user_data # all four should show OK
```

`requirements.txt` pins Freqtrade and `joblib<1.6`. The joblib pin is needed
because joblib 1.6 removed a module Freqtrade's hyperopt still imports.

Create the private config (API secrets, gitignored). This never overwrites an existing file:

```bash
python scripts/init_private_config.py
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

Research picks pairs **point-in-time**, so it needs daily candles for every
Binance USDT pair, not just today's favourites. `python scripts/update_data.py`
runs all of the steps below (it's also the installer's **Update market data**
shortcut). Do this once, then top up monthly:

```bash
freqtrade list-pairs --userdir user_data --quote USDT --trading-mode spot --print-json > all_usdt.json
freqtrade download-data --userdir user_data --pairs-file all_usdt.json --timeframes 1d --timerange 20220601-
python scripts/research.py universe --download --write-config
```

The last command does three things:
- prints the pair list used for each backtest window,
- downloads 1h/4h data for those pairs,
- sets `config.json`'s live/paper pair list to **today's** top 25 by volume.

Re-run it monthly to keep the live list current.

The 1d timeframe is also what the daily EMA200 / BTC-regime filters use, and
those need about 200 days of warm-up.

## 4. Research - does any strategy actually have an edge?

```bash
python scripts/research.py baseline     # in-sample vs out-of-sample vs buy-and-hold
python scripts/research.py walkforward  # rolling 3-month windows
python scripts/research.py stress       # out-of-sample with +50% fees
python scripts/research.py bias         # Freqtrade lookahead + recursive analysis
python scripts/research.py hyperopt -s TrendRider4h --epochs 300   # tunes on in-sample only
python scripts/research.py gates        # everything above -> user_data/research/latest.json
```

**How pairs are chosen:** each backtest window trades the top 25 USDT pairs by
trading volume over the 30 days *before* that window starts. It never uses
today's list. That list favours coins that pumped during the test, which would
quietly leak the future into the backtest.

The selection skips:
- stablecoins: by name, plus any coin whose price sat within ±5% of $1 for 90 days,
- wrapped and leveraged tokens,
- coins listed less than 210 days before the window.

Pass `--static` to use `config.json`'s list instead. Delisted coins can't be
downloaded, so some survivorship bias remains.

- **In-sample** (2023-06-01 → 2025-06-01) is the only period used for designing and tuning.
- **Out-of-sample** (2025-06-01 → latest data) is only used to judge the result.

Tuning against OOS would make it in-sample too, and the numbers would stop
meaning anything.

Hyperopt tunes on the point-in-time pairs at the start of the in-sample period,
the same pairs the in-sample backtest uses. It never uses `config.json`'s live
list, because that's today's top coins.

It saves the best parameters to `user_data/strategies/<Strategy>.json`, which
every later backtest and dry-run picks up automatically. Delete that file to go
back to the defaults in the `.py`.

To try new parameters without touching a strategy that's paper trading, tune a
subclass with its own name instead. For example, `TrendRider4hPIT` is
TrendRider4h tuned on point-in-time pairs.

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

### Paper vs replay (the last gate)

Use the panel's **Paper trading → Paper vs replay** card, or run:

```bash
python scripts/reconcile.py <session-id>
```

It replays the exact period the session ran, with the same strategy, pairs and
starting balance. Then it matches the trades one by one (same pair, entries
within 1.5 candles). The session is "close to the replay" when all of these hold:

- it ran for at least 28 days
- at least 80% of trades happened in both
- realised return is within 2 percentage points of the replay
- fills cost at most 0.3% per side compared with the replay's candle prices

The result also splits the return gap into three parts:
- trades in both, which shows execution differences
- paper-only trades
- replay-only trades

It flags anything the replay can't reproduce:
- manual pauses, resumes and flattens
- drawdown halts
- strategy code or parameters that changed mid-session

The Research tab shows the newest result for each strategy.
See [docs/OPERATIONS.md](docs/OPERATIONS.md#paper-vs-replay).

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

## 6. Operations

### Risk limits and kill switch
Every strategy runs under portfolio-level limits, enforced in backtests too:
- **Max single position:** 35% of equity.
- **Max open risk:** 4.5% of equity, i.e. the total loss if every open stop hit at once.
- **Daily loss:** 3%. Hitting it pauses new entries until the next UTC day.
- **Drawdown halt:** 15%. Hitting it stops new entries until a human resumes.

The panel's **Risk controls** card shows live usage of each limit, with three controls:
- **Pause entries**
- **Resume**
- **Flatten all**: the kill switch. It market-closes everything and stops the bot.

### Audit trail
Every control action, risk event and watchdog alert is appended to a
hash-chained log in `user_data/audit/`. The panel shows whether the chain
still verifies.

### Reports
Investor-style tear sheets come as one self-contained HTML file. Use
Print → Save as PDF in your browser to get a PDF. Each covers:
- returns: monthly and year-to-date
- equity vs BTC, and drawdown
- Sharpe, Sortino and Calmar
- trade statistics and the full trade log

```bash
python scripts/report.py --db user_data/paper/<session>.sqlite --month 2026-10
python scripts/report.py --backtest user_data/replays/<job-id>
```

The panel has the same: **Performance tear sheet** on Paper trading, and **Open tear sheet** after a replay.

### Deployment and monitoring
```bash
python scripts/init_private_config.py
docker compose up -d --build        # bot + panel (monitor mode) + watchdog
# or, without building: docker compose pull && docker compose up -d
```

The watchdog sends Telegram alerts when:
- the bot is down or stalled
- the bot is paused or halted
- any risk limit is at 80% of its threshold or more

Setup, VPS hardening and the incident runbook are in
[docs/OPERATIONS.md](docs/OPERATIONS.md).

### Tests
```bash
pip install -r requirements-dev.txt
ruff check . && pytest
```

The tests cover:
- risk-limit boundaries
- audit tamper detection
- a lookahead check: signals must not change when future candles are removed
- point-in-time universe selection
- paper-vs-replay trade matching, gap breakdown and checks
- the panel's dry-run guard and kill-switch sequence
- repo hygiene: no secrets, and no live config in git

GitHub Actions runs them on every push.

## Managing other people's money

The code above is the operational plumbing a fund would expect. **It does not
make running other people's money legal.** In Australia, dealing in or
managing crypto-asset portfolios for others, copy trading, or pooling client
funds generally requires an Australian Financial Services Licence (AFSL).
Pooled funds are usually also a registered managed investment scheme (MIS),
with compliance, custody and audit obligations. A wholesale-only structure
reduces some obligations but doesn't remove the licence question. Other
countries have their own equivalents.

Talk to a financial-services lawyer before taking anyone else's capital.
This section is a pointer, not legal advice.

## Strategy risk settings (EmaRsiMomentum; the other strategies follow the same pattern)

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
