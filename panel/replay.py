# Fast historical replay: run a Freqtrade backtest with a chosen starting
# balance and date range, then reduce the result to what the UI and the
# research scripts need (metrics, equity curve vs buy-and-hold, trades).
# Also replays a paper session's exact period to reconcile it against paper
# trading (ridgeline/reconcile.py has the comparison itself).

import json
import re
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from panel.common import DATADIR, FREQTRADE, USERDIR, config_args
from ridgeline import audit
from ridgeline.reconcile import SessionInfo, load_paper_trades, load_replay_trades, reconcile, strategy_timeframe

REPLAY_DIR = USERDIR / "replays"
# Daily EMA200 filters need ~200 days of history before the first candle.
WARMUP_DAYS = 250


class BacktestError(RuntimeError):
    pass


def run_backtest(
    strategy: str,
    timerange: str,
    balance: float = 1000,
    pairs: Optional[list[str]] = None,
    max_open_trades: Optional[int] = None,
    enable_protections: bool = True,
    fee: Optional[float] = None,
    outdir: Optional[Path] = None,
    extra_args: Optional[list[str]] = None,
) -> dict:
    """Run one backtest in a subprocess and return a summarized result dict."""
    outdir = Path(outdir or REPLAY_DIR / f"tmp-{uuid.uuid4().hex[:8]}")
    outdir.mkdir(parents=True, exist_ok=True)

    cmd = [
        *FREQTRADE, "backtesting", *config_args(),
        "--strategy", strategy,
        "--timerange", timerange,
        "--dry-run-wallet", str(balance),
        "--backtest-directory", str(outdir),
        "--export", "trades",
        "--cache", "none",
    ]
    if enable_protections:
        cmd.append("--enable-protections")
    if max_open_trades:
        cmd += ["--max-open-trades", str(max_open_trades)]
    if fee is not None:
        cmd += ["--fee", str(fee)]
    if pairs:
        cmd += ["--pairs", *pairs]
    cmd += extra_args or []

    log_path = outdir / "backtest.log"
    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT)
    if proc.returncode != 0:
        tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
        raise BacktestError("\n".join(tail))

    return summarize(outdir, strategy)


def download_data(pairs: list[str], since: pd.Timestamp, log_path: Path,
                  timeframes: tuple[str, ...] = ("1h", "4h", "1d")) -> None:
    """Top up candle data for these pairs to the latest closed candle."""
    cmd = [*FREQTRADE, "download-data", *config_args(), "--pairs", *pairs,
           "--timeframes", *timeframes, "--timerange", f"{since:%Y%m%d}-"]
    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT)
    if proc.returncode != 0:
        tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
        raise BacktestError("Data download failed:\n" + "\n".join(tail))


def replay_window(info: SessionInfo) -> tuple[str, pd.Timestamp]:
    """The strategy's timeframe and the replay's first candle: the one the
    session's first decision was based on. Raises if it hasn't closed yet."""
    timeframe = strategy_timeframe(info.strategy)
    start = info.start.floor(pd.Timedelta(timeframe))
    first_close = start + pd.Timedelta(timeframe)
    if first_close > pd.Timestamp.now(tz="UTC"):
        raise BacktestError(f"Nothing to replay yet: the session's first {timeframe} candle closes at "
                            f"{first_close:%Y-%m-%d %H:%M} UTC.")
    return timeframe, start


def replay_session(info: SessionInfo, outdir: Optional[Path] = None, download: bool = True) -> dict:
    """Replay a paper session's exact period (same strategy, pairs, balance)
    and reconcile it with what paper trading did."""
    timeframe, start = replay_window(info)
    outdir = Path(outdir or REPLAY_DIR / f"session-{info.session_id}")
    outdir.mkdir(parents=True, exist_ok=True)
    if download:
        # BTC/USDT feeds the strategies' market-regime filter even when it isn't traded.
        download_data(sorted({*info.pairs, "BTC/USDT"}), start - pd.Timedelta(days=WARMUP_DAYS),
                      outdir / "download.log")
    run_backtest(info.strategy, f"{int(start.timestamp())}-{int(info.end.timestamp())}", info.balance,
                 pairs=info.pairs, max_open_trades=info.max_open_trades, outdir=outdir)
    replay, meta = load_replay_trades(outdir)
    return reconcile(load_paper_trades(info.db), replay, info=info, replay_end=meta["end"],
                     timeframe=timeframe, events=audit.tail(100_000))


def reconcile_path(db: Path) -> Path:
    """Where a session's latest reconciliation is kept: next to its database."""
    return Path(db).with_suffix(".reconcile.json")


class SessionReplays:
    """Paper-vs-replay reconciliations for the panel, one at a time. The result
    is written next to the session's database, so it outlives the panel."""

    def __init__(self):
        self._pool = ThreadPoolExecutor(max_workers=1)
        self._lock = threading.Lock()
        self._running: dict[str, dict] = {}

    def submit(self, info: SessionInfo) -> dict:
        replay_window(info)  # fail fast if there's nothing to replay yet
        with self._lock:
            if info.session_id in self._running:
                return self._running[info.session_id]
            job = {"status": "running", "session_id": info.session_id,
                   "started": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            self._running[info.session_id] = job
        self._pool.submit(self._run, info)
        return job

    def _run(self, info: SessionInfo) -> None:
        try:
            result = replay_session(info)
        except Exception as e:  # surfaced to the UI as-is
            result = {"status": "error", "error": str(e), "session": info.to_dict(),
                      "generated": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        try:
            reconcile_path(info.db).write_text(json.dumps(result, indent=2, default=str))
        finally:
            with self._lock:
                self._running.pop(info.session_id, None)

    def get(self, session_id: str, db: Path) -> Optional[dict]:
        with self._lock:
            if session_id in self._running:
                return self._running[session_id]
        path = reconcile_path(db)
        return json.loads(path.read_text()) if path.exists() else None


def summarize(outdir: Path, strategy: str) -> dict:
    from freqtrade.data.btanalysis import load_backtest_stats

    stats = load_backtest_stats(outdir)["strategy"][strategy]
    balance = float(stats["starting_balance"])

    # Daily equity from realised profit, plus an equal-weight buy-and-hold of
    # the same pairs over the same window as the benchmark.
    bench = buy_and_hold(stats["pairlist"], stats["backtest_start"], stats["backtest_end"])
    # Freqtrade omits daily_profit (and most ratios) when a run has no trades.
    daily = pd.DataFrame(stats.get("daily_profit") or [], columns=["date", "profit"])
    if daily.empty:
        daily = pd.DataFrame({"date": bench["date"], "profit": 0.0})
    daily["date"] = pd.to_datetime(daily["date"]).dt.strftime("%Y-%m-%d")
    daily["equity"] = balance + daily["profit"].cumsum()
    curve = daily[["date", "equity"]].merge(bench, on="date", how="left")
    curve["benchmark"] = (curve["benchmark"].ffill().bfill() * balance).round(2)
    curve["equity"] = curve["equity"].round(2)

    trades = [
        {
            "pair": t["pair"],
            "open_date": str(t["open_date"])[:16],
            "close_date": str(t["close_date"])[:16],
            "open_rate": t["open_rate"],
            "close_rate": t["close_rate"],
            "stake": round(t["stake_amount"], 2),
            "profit_pct": round(t["profit_ratio"] * 100, 2),
            "profit_abs": round(t["profit_abs"], 2),
            "exit_reason": t["exit_reason"],
        }
        for t in stats["trades"]
    ]

    metrics = {
        "strategy": strategy,
        "timerange": stats["timerange"],
        "start": stats["backtest_start"],
        "end": stats["backtest_end"],
        "starting_balance": balance,
        "final_balance": round(stats.get("final_balance", balance), 2),
        "profit_pct": round(stats.get("profit_total", 0) * 100, 2),
        "market_change_pct": round(stats.get("market_change", 0) * 100, 2),
        "max_drawdown_pct": round(stats.get("max_drawdown_account", 0) * 100, 2),
        "profit_factor": round(stats.get("profit_factor", 0), 2),
        "winrate_pct": round(stats.get("winrate", 0) * 100, 1),
        "trades": stats["total_trades"],
        "calmar": round(stats.get("calmar", 0), 2),
        "sharpe": round(stats.get("sharpe", 0), 2),
        "cagr_pct": round(stats.get("cagr", 0) * 100, 2),
        "exit_reasons": {
            r["key"]: {"count": r["trades"], "profit_abs": round(r["profit_total_abs"], 2)}
            for r in stats.get("exit_reason_summary", [])
            if r["key"] != "TOTAL"
        },
    }
    return {"metrics": metrics, "curve": curve.to_dict("records"), "trades": trades}


def buy_and_hold(pairs: list[str], start: str, end: str) -> pd.DataFrame:
    """Equal-weight buy-and-hold growth factor per day (1.0 at start)."""
    series = []
    for pair in pairs:
        path = DATADIR / f"{pair.replace('/', '_')}-1d.feather"
        if not path.exists():
            continue
        df = pd.read_feather(path, columns=["date", "close"])
        df = df[(df["date"] >= pd.Timestamp(start, tz="UTC")) & (df["date"] <= pd.Timestamp(end, tz="UTC"))]
        if df.empty:
            continue
        series.append((df.set_index("date")["close"] / df["close"].iloc[0]).rename(pair))
    if not series:
        return pd.DataFrame(columns=["date", "benchmark"])
    growth = pd.concat(series, axis=1).ffill().mean(axis=1)
    return pd.DataFrame({"date": growth.index.strftime("%Y-%m-%d"), "benchmark": growth.values})


class ReplayJobs:
    """Background replay jobs for the panel. Results are persisted so the
    history survives panel restarts."""

    def __init__(self):
        self._pool = ThreadPoolExecutor(max_workers=2)
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}

    def submit(self, strategy: str, balance: float, start: str, end: str,
               max_open_trades: Optional[int] = None) -> str:
        job_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        timerange = f"{start.replace('-', '')}-{end.replace('-', '')}"
        job = {
            "id": job_id, "status": "running", "strategy": strategy, "balance": balance,
            "timerange": timerange, "max_open_trades": max_open_trades,
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        with self._lock:
            self._jobs[job_id] = job
        self._pool.submit(self._run, job)
        return job_id

    def _run(self, job: dict) -> None:
        outdir = REPLAY_DIR / job["id"]
        try:
            result = run_backtest(
                job["strategy"], job["timerange"], job["balance"],
                max_open_trades=job["max_open_trades"], outdir=outdir,
            )
            job.update(status="done", result=result)
        except Exception as e:  # surfaced to the UI as-is
            job.update(status="error", error=str(e))
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "job.json").write_text(json.dumps(job, default=str))

    def get(self, job_id: str) -> Optional[dict]:
        if not re.fullmatch(r"[\w-]+", job_id):
            return None
        with self._lock:
            if job_id in self._jobs:
                return self._jobs[job_id]
        path = REPLAY_DIR / job_id / "job.json"
        return json.loads(path.read_text()) if path.exists() else None

    def history(self) -> list[dict]:
        items = {}
        for path in REPLAY_DIR.glob("*/job.json"):
            try:
                job = json.loads(path.read_text())
            except ValueError:
                continue
            items[job["id"]] = job
        with self._lock:
            items.update(self._jobs)
        out = []
        for job in items.values():
            m = (job.get("result") or {}).get("metrics", {})
            out.append({k: job.get(k) for k in ("id", "status", "strategy", "balance", "timerange", "created")}
                       | {"profit_pct": m.get("profit_pct"), "max_drawdown_pct": m.get("max_drawdown_pct")})
        return sorted(out, key=lambda j: j["id"], reverse=True)
