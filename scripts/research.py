"""Strategy research: honest in-sample / out-of-sample evaluation and go-live gates.

Usage (from the project root, with the venv Python):
    python scripts/research.py baseline  [-s STRAT ...]   IS vs OOS vs buy-and-hold
    python scripts/research.py walkforward [-s STRAT ...] rolling 3-month windows
    python scripts/research.py stress    [-s STRAT ...]   OOS with +50% fees
    python scripts/research.py bias      [-s STRAT ...]   lookahead + recursive analysis
    python scripts/research.py hyperopt  -s STRAT [--epochs N] [--spaces ...]   (IS only)
    python scripts/research.py gates     [-s STRAT ...]   all of the above -> user_data/research/latest.json
    python scripts/research.py universe  [--download]     show (and fetch data for) the point-in-time pair lists

Pairs are chosen point-in-time: each backtest window trades the top
UNIVERSE_SIZE USDT pairs by volume over the 30 days BEFORE that window starts.
Picking pairs by today's volume would favour coins that pumped during the test
(look-ahead in the universe itself). Pass --static to use config.json's pairs.
Coins Binance has since delisted can't be downloaded, so some survivorship
bias remains.

IS (2023-06-01 -> 2025-06-01) is for design and hyperopt. OOS (2025-06-01 ->
latest data) is only looked at to judge the result - tuning against it would
make it in-sample too.
"""

import argparse
import csv
import functools
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from panel.common import DATADIR, FREQTRADE, USERDIR, config_args, list_strategies  # noqa: E402
from panel.replay import buy_and_hold, run_backtest  # noqa: E402

IS_RANGE = ("2023-06-01", "2025-06-01")
OOS_START = "2025-06-01"
WF_START = "2023-06-01"
WF_MONTHS = 3
STRESS_FEE = 0.0015  # Binance spot 0.1% + 50%
RESEARCH_DIR = USERDIR / "research"
WORKERS = 6

UNIVERSE_SIZE = 25
UNIVERSE_LOOKBACK_DAYS = 30
MIN_HISTORY_DAYS = 210  # daily EMA200 warm-up before a pair can be traded
UNIVERSE_BLACKLIST = re.compile(
    r"^(USDC|FDUSD|TUSD|USDP|DAI|BUSD|EUR|AEUR|EURI|USDE|USD1|RLUSD|PYUSD|XUSD|BFUSD|"
    r"PAXG|WBTC|WBETH|BNSOL|.*UP|.*DOWN|.*BULL|.*BEAR)/"
)
STATIC_UNIVERSE = False  # set by --static

GATES = [
    # (key, label, comparison, target)
    ("oos_profit_pct", "OOS profit > 0%", ">", 0),
    ("oos_profit_factor", "OOS profit factor >= 1.3", ">=", 1.3),
    ("oos_max_drawdown_pct", "OOS max drawdown <= 20%", "<=", 20),
    ("oos_calmar", "OOS Calmar >= 1.0", ">=", 1.0),
    ("oos_trades", "OOS trades >= 100", ">=", 100),
    ("wf_pct_profitable", "Profitable in >= 60% of walk-forward windows", ">=", 60),
    ("stress_profit_pct", "OOS still profitable with +50% fees", ">", 0),
    ("lookahead_clean", "No lookahead bias", "==", True),
    ("recursive_max_diff_pct", "Indicators stable vs startup candles (<= 1%)", "<=", 1.0),
]


def data_end() -> str:
    df = pd.read_feather(DATADIR / "BTC_USDT-1h.feather", columns=["date"])
    return df["date"].max().strftime("%Y-%m-%d")


def config_pairs() -> list[str]:
    return json.loads((USERDIR / "config.json").read_text())["exchange"]["pair_whitelist"]


@functools.lru_cache(maxsize=None)
def universe_at(when: str, n: int = UNIVERSE_SIZE) -> tuple[str, ...]:
    """Top-n USDT pairs by quote volume over the lookback window before `when`,
    using only data that existed at that time."""
    if STATIC_UNIVERSE:
        return tuple(config_pairs())
    t = pd.Timestamp(when, tz="UTC")
    lookback_start = t - pd.Timedelta(days=UNIVERSE_LOOKBACK_DAYS)
    ranked = []
    for path in DATADIR.glob("*_USDT-1d.feather"):
        pair = path.stem.rsplit("-", 1)[0].replace("_", "/")
        if UNIVERSE_BLACKLIST.match(pair):
            continue
        df = pd.read_feather(path, columns=["date", "close", "volume"])
        if df.empty or df["date"].min() > t - pd.Timedelta(days=MIN_HISTORY_DAYS):
            continue
        window = df[(df["date"] >= lookback_start) & (df["date"] < t)]
        if len(window) < UNIVERSE_LOOKBACK_DAYS - 5:  # must have been trading throughout
            continue
        ranked.append((pair, float((window["close"] * window["volume"]).sum())))
    ranked.sort(key=lambda r: r[1], reverse=True)
    return tuple(p for p, _ in ranked[:n])


def all_universes() -> dict[str, tuple[str, ...]]:
    end = data_end()
    starts = {IS_RANGE[0], OOS_START, *(w[0] for w in wf_windows(end))}
    return {d: universe_at(d) for d in sorted(starts)}


def tr(start: str, end: str = "") -> str:
    return f"{start.replace('-', '')}-{end.replace('-', '')}"


def bt(strategy, start, end="", tag="", **kw) -> dict:
    out = RESEARCH_DIR / "runs" / f"{strategy}-{tag or tr(start, end)}"
    kw.setdefault("pairs", list(universe_at(start)))
    return run_backtest(strategy, tr(start, end), outdir=out, **kw)["metrics"]


def benchmarks(end: str) -> dict:
    out = {}
    for name, (s, e) in {"is": IS_RANGE, "oos": (OOS_START, end)}.items():
        eq = buy_and_hold(list(universe_at(s)), s, e)
        btc = buy_and_hold(["BTC/USDT"], s, e)
        out[name] = {
            "equal_weight_pct": round((eq["benchmark"].iloc[-1] - 1) * 100, 2),
            "btc_pct": round((btc["benchmark"].iloc[-1] - 1) * 100, 2),
        }
    return out


def baseline(strategies: list[str]) -> dict:
    end = data_end()
    jobs = {(s, p): (s, *rng) for s in strategies
            for p, rng in {"is": IS_RANGE, "oos": (OOS_START, end)}.items()}
    with ThreadPoolExecutor(WORKERS) as pool:
        futures = {k: pool.submit(bt, *args) for k, args in jobs.items()}
        return {k: f.result() for k, f in futures.items()}


def wf_windows(end: str) -> list[tuple[str, str]]:
    starts = pd.date_range(WF_START, end, freq=f"{WF_MONTHS}MS")
    windows = []
    for s in starts:
        e = min(s + pd.DateOffset(months=WF_MONTHS), pd.Timestamp(end))
        if (e - s).days >= 30:
            windows.append((s.strftime("%Y-%m-%d"), e.strftime("%Y-%m-%d")))
    return windows


def walkforward(strategies: list[str]) -> dict:
    windows = wf_windows(data_end())
    with ThreadPoolExecutor(WORKERS) as pool:
        futures = {(s, w): pool.submit(bt, s, *w, tag=f"wf-{tr(*w)}") for s in strategies for w in windows}
        results = {k: f.result() for k, f in futures.items()}
    out = {}
    for s in strategies:
        rows = [{"window": f"{w[0]} -> {w[1]}", "profit_pct": results[(s, w)]["profit_pct"],
                 "trades": results[(s, w)]["trades"],
                 "market_change_pct": results[(s, w)]["market_change_pct"]} for w in windows]
        profitable = sum(r["profit_pct"] > 0 for r in rows)
        out[s] = {"windows": rows, "pct_profitable": round(100 * profitable / len(rows), 1),
                  "worst_pct": min(r["profit_pct"] for r in rows)}
    return out


def stress(strategies: list[str]) -> dict:
    end = data_end()
    with ThreadPoolExecutor(WORKERS) as pool:
        futures = {s: pool.submit(bt, s, OOS_START, end, tag="stress", fee=STRESS_FEE) for s in strategies}
        return {s: f.result() for s, f in futures.items()}


def lookahead(strategy: str) -> dict:
    out = RESEARCH_DIR / "bias" / strategy
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / "lookahead.csv"
    csv_path.unlink(missing_ok=True)
    # lookahead-analysis simulates with market orders, which Freqtrade only
    # accepts with price_side "other" - override that just for this check.
    override = out / "market-orders.json"
    override.write_text(json.dumps({"entry_pricing": {"price_side": "other"},
                                    "exit_pricing": {"price_side": "other"}}))
    cmd = [*FREQTRADE, "lookahead-analysis", *config_args(), "-c", str(override),
           "--strategy", strategy,
           "--timerange", tr("2024-01-01", "2025-01-01"), "--backtest-directory", str(out),
           "--lookahead-analysis-exportfilename", str(csv_path)]
    log = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    (out / "lookahead.log").write_text(log.stdout + log.stderr)
    if not csv_path.exists():
        return {"lookahead_clean": None, "lookahead_note": "analysis did not produce results"}
    rows = [r for r in csv.DictReader(csv_path.open()) if r.get("strategy") == strategy]
    if not rows:
        return {"lookahead_clean": None, "lookahead_note": "no result row"}
    row = rows[-1]
    return {
        "lookahead_clean": row["has_bias"].strip().lower() == "false",
        "lookahead_note": f"{row.get('total_signals')} signals checked; biased indicators: "
                          f"{row.get('biased_indicators') or 'none'}",
    }


def recursive(strategy: str) -> dict:
    cmd = [*FREQTRADE, "recursive-analysis", *config_args(), "--strategy", strategy,
           "--timerange", tr("2024-06-01", "2025-01-01")]
    log = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    text = log.stdout + log.stderr
    out = RESEARCH_DIR / "bias" / strategy
    out.mkdir(parents=True, exist_ok=True)
    (out / "recursive.log").write_text(text)
    # Table rows look like "| rsi | 0.005% | ..." - take the worst deviation.
    pcts = [abs(float(m)) for m in re.findall(r"(-?\d+\.\d+)%", text)]
    if log.returncode != 0:
        return {"recursive_max_diff_pct": None, "recursive_note": "analysis failed, see recursive.log"}
    if not pcts and "No variance on indicator" in text:
        return {"recursive_max_diff_pct": 0.0, "recursive_note": "no variance"}
    return {"recursive_max_diff_pct": round(max(pcts), 4) if pcts else 0.0,
            "recursive_note": "worst indicator deviation with fewer startup candles"}


def bias(strategies: list[str]) -> dict:
    with ThreadPoolExecutor(WORKERS) as pool:
        la = {s: pool.submit(lookahead, s) for s in strategies}
        rc = {s: pool.submit(recursive, s) for s in strategies}
        return {s: la[s].result() | rc[s].result() for s in strategies}


def hyperopt(strategy: str, epochs: int, spaces: list[str], loss: str) -> None:
    cmd = [*FREQTRADE, "hyperopt", *config_args(), "--strategy", strategy,
           "--timerange", tr(*IS_RANGE), "--enable-protections",
           "--hyperopt-loss", loss, "--spaces", *spaces, "--epochs", str(epochs),
           "--min-trades", "60", "-j", "-2", "--random-state", "42"]
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)


def check(op, value, target) -> bool:
    if value is None:
        return False
    return {">": value > target, ">=": value >= target, "<=": value <= target, "==": value == target}[op]


def gates(strategies: list[str]) -> dict:
    end = data_end()
    base = baseline(strategies)
    wf = walkforward(strategies)
    st = stress(strategies)
    bs = bias(strategies)

    report = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "periods": {"is": list(IS_RANGE), "oos": [OOS_START, end]},
        "benchmarks": benchmarks(end),
        "universe": {
            "method": "static (config.json)" if STATIC_UNIVERSE else
                      f"point-in-time top {UNIVERSE_SIZE} by {UNIVERSE_LOOKBACK_DAYS}d volume",
            "is": list(universe_at(IS_RANGE[0])),
            "oos": list(universe_at(OOS_START)),
        },
        "strategies": {},
    }
    for s in strategies:
        oos = base[(s, "oos")]
        values = {
            "oos_profit_pct": oos["profit_pct"],
            "oos_profit_factor": oos["profit_factor"],
            "oos_max_drawdown_pct": oos["max_drawdown_pct"],
            "oos_calmar": oos["calmar"],
            "oos_trades": oos["trades"],
            "wf_pct_profitable": wf[s]["pct_profitable"],
            "stress_profit_pct": st[s]["profit_pct"],
            **bs[s],
        }
        results = [{"label": label, "value": values.get(key), "pass": check(op, values.get(key), target)}
                   for key, label, op, target in GATES]
        report["strategies"][s] = {
            "is": base[(s, "is")], "oos": oos, "stress": st[s], "walkforward": wf[s], "bias": bs[s],
            "gates": results,
            "eligible_for_paper": all(g["pass"] for g in results),
        }
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    for name in ("latest.json", f"report-{stamp}.json"):
        (RESEARCH_DIR / name).write_text(json.dumps(report, indent=2, default=str))
    return report


def print_metrics_table(rows: list[tuple[str, dict]]) -> None:
    cols = ["profit_pct", "market_change_pct", "max_drawdown_pct", "profit_factor",
            "calmar", "winrate_pct", "trades"]
    df = pd.DataFrame([{"run": name, **{c: m[c] for c in cols}} for name, m in rows])
    print(df.to_string(index=False))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["baseline", "walkforward", "stress", "bias", "hyperopt", "gates", "universe"])
    ap.add_argument("-s", "--strategies", nargs="+", default=None)
    ap.add_argument("--epochs", type=int, default=300)
    ap.add_argument("--spaces", nargs="+", default=["buy", "sell"])
    ap.add_argument("--loss", default="CalmarHyperOptLoss")
    ap.add_argument("--static", action="store_true", help="use config.json pairs instead of point-in-time universes")
    ap.add_argument("--download", action="store_true", help="with 'universe': download 1h/4h data for every listed pair")
    args = ap.parse_args()
    global STATIC_UNIVERSE
    STATIC_UNIVERSE = args.static
    strategies = args.strategies or list_strategies()

    if args.command == "universe":
        universes = all_universes()
        for d, pairs in universes.items():
            print(f"{d}: {' '.join(p.split('/')[0] for p in pairs)}")
        union = sorted({p for pairs in universes.values() for p in pairs})
        print(f"\n{len(union)} distinct pairs across all windows")
        print(f"today ({data_end()}): {' '.join(p.split('/')[0] for p in universe_at(data_end()))}")
        if args.download:
            subprocess.run([*FREQTRADE, "download-data", *config_args(), "--pairs", *union,
                            "--timeframes", "1h", "4h", "1d", "--timerange", "20220601-"], check=True)
        return

    if args.command == "baseline":
        res = baseline(strategies)
        print_metrics_table([(f"{s} {p.upper()}", m) for (s, p), m in res.items()])
        print("\nbenchmarks:", json.dumps(benchmarks(data_end())))
    elif args.command == "walkforward":
        for s, r in walkforward(strategies).items():
            print(f"\n{s}: {r['pct_profitable']}% of windows profitable, worst {r['worst_pct']}%")
            print(pd.DataFrame(r["windows"]).to_string(index=False))
    elif args.command == "stress":
        print_metrics_table([(f"{s} OOS fee={STRESS_FEE}", m) for s, m in stress(strategies).items()])
    elif args.command == "bias":
        print(json.dumps(bias(strategies), indent=2))
    elif args.command == "hyperopt":
        for s in strategies:
            hyperopt(s, args.epochs, args.spaces, args.loss)
    elif args.command == "gates":
        report = gates(strategies)
        print("benchmarks:", json.dumps(report["benchmarks"]))
        for s, r in report["strategies"].items():
            verdict = "ELIGIBLE for paper trading" if r["eligible_for_paper"] else "NOT eligible"
            print(f"\n{s}: {verdict}")
            for g in r["gates"]:
                print(f"  [{'PASS' if g['pass'] else 'FAIL'}] {g['label']}  (got {g['value']})")
        print(f"\nwritten to {RESEARCH_DIR / 'latest.json'}")


if __name__ == "__main__":
    main()
