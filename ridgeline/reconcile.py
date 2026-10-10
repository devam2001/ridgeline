"""Paper vs replay reconciliation.

The last go-live gate: a strategy paper-trades for 4+ weeks, and the paper
results must be close to a replay (backtest) of the same weeks, run with the
same strategy, pairs and starting balance. Passing it means the backtests the
research relies on hold up in near-live execution:
  - signals fire on time
  - fills land near the candle prices the backtest assumes
  - stops and exits behave the same way

  session_info()      what a paper session ran: strategy, pairs, balance, start, end
  load_paper_trades() / load_replay_trades()
  match_trades()      pair trades up by pair and entry time
  reconcile()         summary, return-gap breakdown, checks and a verdict

Running the replay itself (data download + backtest) is replay_session() in
panel/replay.py; scripts/reconcile.py and the panel both call it.
"""

import ast
import hashlib
import json
import re
import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from ridgeline import ROOT, USERDIR

STRATEGY_DIR = USERDIR / "strategies"

MIN_DAYS = 28
# Paper and replay entries for the same signal can be up to a candle apart:
# a session that starts mid-candle acts on the last closed candle at once,
# and an unfilled entry order is retried on the next candle.
MATCH_TOLERANCE_CANDLES = 1.5

CHECKS = [
    # (key, label, comparison, target)
    ("days", f"Paper-traded for at least {MIN_DAYS} days", ">=", MIN_DAYS),
    ("match_rate_pct", "At least 80% of trades happened in both", ">=", 80),
    ("return_gap_pp", "Realised return within 2 points of the replay", "<=", 2.0),
    ("fill_cost_pct", "Fills cost at most 0.3% per side vs the replay", "<=", 0.3),
]

# Audit events the replay can't reproduce: manual actions, and drawdown halts
# (a human resumes them live; the backtest resumes after a fixed review period).
UNMODELLED_EVENTS = {"entries_paused", "trading_resumed", "flatten", "risk_halt"}

TRADE_COLUMNS = ["pair", "open_date", "close_date", "open_rate", "close_rate", "stake",
                 "profit_ratio", "profit_abs", "exit_reason", "is_open"]
CLOSE_COLUMNS = ["close_date", "close_rate", "profit_ratio", "profit_abs", "exit_reason"]

_WHITELIST_RE = re.compile(r"Whitelist with \d+ pairs: (\[[^\]]*\])")
_STRATEGY_RE = re.compile(r"Using resolved strategy (\w+)")
_TIMEFRAME_RE = re.compile(r"^\s+timeframe\s*=\s*[\"'](\w+)[\"']", re.M)


# ---------------------------------------------------------------- session

@dataclass
class SessionInfo:
    session_id: str
    db: Path
    strategy: str
    pairs: list[str]
    balance: float
    start: pd.Timestamp
    end: pd.Timestamp
    max_open_trades: Optional[int] = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"session_id": self.session_id, "strategy": self.strategy, "pairs": self.pairs,
                "balance": self.balance, "start": self.start.isoformat(), "end": self.end.isoformat(),
                "max_open_trades": self.max_open_trades}


def strategy_fingerprint(strategy: str, strategy_dir: Path = STRATEGY_DIR) -> dict[str, Optional[str]]:
    """Hashes of everything that decides a strategy's trades: its code, its
    hyperopt parameters and the shared risk layer."""
    files = [Path(strategy_dir) / f"{strategy}.py", Path(strategy_dir) / f"{strategy}.json",
             ROOT / "ridgeline" / "risk.py", ROOT / "ridgeline" / "freqtrade_risk.py"]
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.exists() else None for p in files}


def strategy_timeframe(strategy: str, strategy_dir: Path = STRATEGY_DIR) -> str:
    match = _TIMEFRAME_RE.search((Path(strategy_dir) / f"{strategy}.py").read_text(encoding="utf-8"))
    if not match:
        raise ValueError(f"Couldn't find the timeframe of {strategy}")
    return match.group(1)


def session_info(db: Path, *, log: Optional[Path] = None, strategy: Optional[str] = None,
                 pairs: Optional[list[str]] = None, balance: Optional[float] = None,
                 start: Optional[pd.Timestamp] = None, end: Optional[pd.Timestamp] = None) -> SessionInfo:
    """What a session ran, from the best source available. Explicit arguments win.
    After them, in order:
      - <db>.meta.json, written by the panel when a session starts
      - the bot's log (log, or <db>.log, with its rotated backups)
      - the database itself
    """
    db = Path(db)
    meta = _load_json(db.with_suffix(".meta.json"))
    logs = _log_files(Path(log) if log else db.with_suffix(".log"))
    warnings = []

    strategy = strategy or meta.get("strategy") or _first_match(logs, _STRATEGY_RE) or _db_strategy(db)
    if not strategy:
        raise ValueError(f"Couldn't tell which strategy {db.name} ran. Pass it explicitly.")

    if not pairs:
        pairs = meta.get("pairs")
    if not pairs:
        lists = [ast.literal_eval(m) for m in _all_matches(logs, _WHITELIST_RE)]
        if not lists:
            raise ValueError(f"Couldn't tell which pairs {db.name} traded (no meta file and no whitelist "
                             "in its log). Pass them explicitly.")
        pairs = lists[0]
        if any(set(other) != set(pairs) for other in lists[1:]):
            warnings.append("The pair list changed during the session (a restart with a new config?). "
                            "The replay uses the first one.")

    if balance is None:
        balance = meta.get("balance") or _load_json(db.with_suffix(".config.json")).get("dry_run_wallet")
    if balance is None:
        balance = _load_json(USERDIR / "config.json").get("dry_run_wallet", 1000)
        warnings.append(f"Starting balance not recorded for this session; assuming {balance} from config.json.")

    start = _utc(start) or _utc(meta.get("started")) or _db_start(db)
    if start is None:
        raise ValueError(f"Couldn't tell when {db.name} started. Pass the start explicitly.")
    if end is None:
        # The bot logs a heartbeat every minute, so the newest log write is when
        # the session was last alive (about now, if it's still running).
        if logs:
            end = pd.Timestamp(max(p.stat().st_mtime for p in logs), unit="s", tz="UTC")
        else:
            end = pd.Timestamp.now(tz="UTC")
            warnings.append("No bot log found, so the session is assumed to still be running.")
    end = _utc(end)

    recorded = meta.get("code")
    if recorded:
        current = strategy_fingerprint(strategy)
        changed = [name for name, digest in recorded.items() if current.get(name) != digest]
        if changed:
            warnings.append(f"Changed since the session started: {', '.join(changed)}. "
                            "The replay runs the current version, so some differences are expected.")

    return SessionInfo(session_id=db.stem, db=db, strategy=strategy, pairs=list(pairs), balance=float(balance),
                       start=start, end=end, max_open_trades=meta.get("max_open_trades"), warnings=warnings)


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _utc(value) -> Optional[pd.Timestamp]:
    if value is None or value == "":
        return None
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _log_files(log: Path) -> list[Path]:
    """The log and its rotated backups (log.1 is newer than log.2), oldest first."""
    backups = sorted(log.parent.glob(log.name + ".*"), key=lambda p: -int(p.suffix[1:]) if p.suffix[1:].isdigit() else 0)
    return [p for p in [*backups, log] if p.is_file()]


def _all_matches(logs: list[Path], regex: re.Pattern) -> list[str]:
    return [m.group(1) for p in logs for m in regex.finditer(p.read_text(encoding="utf-8", errors="replace"))]


def _first_match(logs: list[Path], regex: re.Pattern) -> Optional[str]:
    for p in logs:
        m = regex.search(p.read_text(encoding="utf-8", errors="replace"))
        if m:
            return m.group(1)
    return None


def _db_query(db: Path, sql: str):
    if not Path(db).exists():
        return None
    with closing(sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)) as con:
        try:
            row = con.execute(sql).fetchone()
        except sqlite3.Error:
            return None
    return row[0] if row else None


def _db_strategy(db: Path) -> Optional[str]:
    return _db_query(db, "SELECT strategy FROM trades WHERE strategy IS NOT NULL LIMIT 1")


def _db_start(db: Path) -> Optional[pd.Timestamp]:
    # Freqtrade records its first start in the database (naive UTC).
    return _utc(_db_query(db, "SELECT datetime_value FROM KeyValueStore WHERE key = 'bot_start_time'"))


# ---------------------------------------------------------------- trades

def load_paper_trades(db: Path) -> pd.DataFrame:
    with closing(sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)) as con:
        df = pd.read_sql_query(
            "SELECT pair, open_date, close_date, open_rate, close_rate, stake_amount AS stake, "
            "close_profit AS profit_ratio, close_profit_abs AS profit_abs, exit_reason, is_open FROM trades", con)
    return _normalise(df)


def load_replay_trades(directory: Path) -> tuple[pd.DataFrame, dict]:
    """Trades from a backtest result directory, plus its start, end and timeframe.
    Trades the backtest force-closed at its last candle are still open in
    reality, so they come back open, the same way paper shows them."""
    from freqtrade.data.btanalysis import load_backtest_stats

    strat = next(iter(load_backtest_stats(directory)["strategy"].values()))
    df = pd.DataFrame(strat["trades"]).reindex(columns=[*TRADE_COLUMNS, "stake_amount"])
    df["stake"] = df.pop("stake_amount")
    df = _normalise(df)
    end = pd.Timestamp(strat["backtest_end"], tz="UTC")
    left_open = (df["exit_reason"] == "force_exit") & (df["close_date"] >= end)
    df.loc[left_open, CLOSE_COLUMNS] = None
    df.loc[left_open, "is_open"] = True
    return df, {"start": pd.Timestamp(strat["backtest_start"], tz="UTC"), "end": end,
                "timeframe": strat["timeframe"], "starting_balance": float(strat["starting_balance"])}


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in ("open_date", "close_date"):
        df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
    for col in ("open_rate", "close_rate", "stake", "profit_ratio", "profit_abs"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["exit_reason"] = df["exit_reason"].astype(object)
    df["is_open"] = df["is_open"].fillna(False).astype(bool)
    return df[TRADE_COLUMNS].sort_values("open_date").reset_index(drop=True)


def match_trades(paper: pd.DataFrame, replay: pd.DataFrame,
                 tolerance: pd.Timedelta) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Pair each paper trade with the replay trade on the same pair whose entry
    is closest in time, within `tolerance`. Closest pairs are matched first.
    Returns (matched index pairs, paper-only indexes, replay-only indexes)."""
    candidates = []
    for i, p in paper.iterrows():
        same_pair = replay[replay["pair"] == p["pair"]]
        for j, opened in same_pair["open_date"].items():
            gap = abs(p["open_date"] - opened)
            if gap <= tolerance:
                candidates.append((gap, i, j))
    matched, used_p, used_r = [], set(), set()
    for _, i, j in sorted(candidates):
        if i not in used_p and j not in used_r:
            matched.append((i, j))
            used_p.add(i)
            used_r.add(j)
    return (sorted(matched), [i for i in paper.index if i not in used_p],
            [j for j in replay.index if j not in used_r])


# ---------------------------------------------------------------- compare

def _check(op: str, value, target) -> bool:
    if value is None:
        return False
    return {">=": value >= target, "<=": value <= target}[op]


def _realised(t) -> float:
    return 0.0 if t["is_open"] or pd.isna(t["profit_abs"]) else float(t["profit_abs"])


def _r(value, digits: int = 2):
    return None if value is None or pd.isna(value) else round(float(value), digits)


def _when(ts) -> Optional[str]:
    return None if pd.isna(ts) else f"{ts:%Y-%m-%d %H:%M}"


def reconcile(paper: pd.DataFrame, replay: pd.DataFrame, *, info: SessionInfo, replay_end: pd.Timestamp,
              timeframe: str, events: Optional[list[dict]] = None) -> dict:
    """Compare a paper session's trades with a replay of the same period.

    `replay_end` is the open time of the replay's last candle. Paper trades
    opened after that candle closes can't be in the replay yet: they're
    left out, and paper trades closed after it count as still open.
    Fill costs are positive when paper did worse than the replay: it paid more
    on entry or received less on exit.
    """
    tf = pd.Timedelta(timeframe)
    start = info.start.floor(tf)
    cutoff = min(info.end, replay_end + tf)
    paper, replay = _normalise(paper), _normalise(replay)

    too_recent = paper["open_date"] >= cutoff
    paper = paper[~too_recent].copy()
    late = paper["close_date"] >= cutoff
    paper.loc[late, CLOSE_COLUMNS] = None
    paper.loc[late, "is_open"] = True

    matched, paper_only, replay_only = match_trades(paper, replay, tf * MATCH_TOLERANCE_CANDLES)

    rows, entry_costs, exit_costs = [], [], []
    both_closed = same_exit = 0
    gap_matched = 0.0
    for i, j in matched:
        p, r = paper.loc[i], replay.loc[j]
        entry_cost = (p["open_rate"] / r["open_rate"] - 1) * 100
        entry_costs.append(entry_cost)
        exit_cost = None
        if not p["is_open"] and not r["is_open"]:
            both_closed += 1
            # Exit prices are only comparable when both sides left for the same reason.
            if p["exit_reason"] == r["exit_reason"]:
                same_exit += 1
                exit_cost = (1 - p["close_rate"] / r["close_rate"]) * 100
                exit_costs.append(exit_cost)
        gap_matched += _realised(p) - _realised(r)
        rows.append(_row("matched", p, r, entry_cost, exit_cost))
    rows += [_row("paper_only", paper.loc[i], None) for i in paper_only]
    rows += [_row("replay_only", None, replay.loc[j]) for j in replay_only]
    rows.sort(key=lambda row: row["paper_open"] or row["replay_open"])

    def side(df: pd.DataFrame) -> dict:
        closed = df[~df["is_open"]]
        realised = float(closed["profit_abs"].sum())
        return {"trades": len(df), "closed": len(closed), "open": int(df["is_open"].sum()),
                "realised_abs": round(realised, 2), "return_pct": round(realised / info.balance * 100, 2)}

    paper_side, replay_side = side(paper), side(replay)
    total = len(matched) + len(paper_only) + len(replay_only)
    costs = entry_costs + exit_costs
    gap = paper_side["return_pct"] - replay_side["return_pct"]
    values = {
        "days": round((cutoff - info.start).total_seconds() / 86400, 1),
        "match_rate_pct": round(100 * len(matched) / total, 1) if total else None,
        "return_gap_pp": round(abs(gap), 2),
        "fill_cost_pct": _r(sum(costs) / len(costs), 3) if costs else None,
    }
    checks = [{"key": key, "label": label, "value": values[key], "pass": _check(op, values[key], target)}
              for key, label, op, target in CHECKS]

    warnings = list(info.warnings)
    if too_recent.any():
        warnings.append(f"{int(too_recent.sum())} paper trade(s) opened after the replay's last candle "
                        f"({_when(cutoff)} UTC) and aren't compared yet.")
    if info.end - (replay_end + tf) > tf:
        warnings.append(f"The replay's data stops at {_when(replay_end + tf)} UTC, before the session's last "
                        f"activity ({_when(info.end)} UTC). Download fresh data and run it again.")
    if not total:
        warnings.append("No trades on either side yet, so there's nothing to compare.")
    unmodelled = [e for e in events or [] if e.get("event") in UNMODELLED_EVENTS
                  and info.start <= pd.Timestamp(e["ts"]) <= info.end]
    if unmodelled:
        listed = ", ".join(f"{name} x{n}" for name, n in Counter(e["event"] for e in unmodelled).items())
        warnings.append(f"During the session: {listed}. The replay can't reproduce manual actions or "
                        "the manual resume after a halt, so expect differences around them.")

    return {
        "status": "done",
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session": info.to_dict(),
        "timeframe": timeframe,
        "period": {"start": start.isoformat(), "end": cutoff.isoformat(), "days": values["days"]},
        "paper": paper_side,
        "replay": replay_side,
        "matching": {"matched": len(matched), "paper_only": len(paper_only), "replay_only": len(replay_only),
                     "match_rate_pct": values["match_rate_pct"],
                     "tolerance_hours": tf.total_seconds() * MATCH_TOLERANCE_CANDLES / 3600},
        "fills": {
            "entry_cost_pct": _r(sum(entry_costs) / len(entry_costs), 3) if entry_costs else None,
            "exit_cost_pct": _r(sum(exit_costs) / len(exit_costs), 3) if exit_costs else None,
            "fill_cost_pct": values["fill_cost_pct"],
            "same_exit_reason_pct": round(100 * same_exit / both_closed, 1) if both_closed else None,
        },
        # Paper minus replay, in stake currency: the sum of the three parts is the total.
        "gap": {"total_abs": round(paper_side["realised_abs"] - replay_side["realised_abs"], 2),
                "matched_abs": round(gap_matched, 2),
                "paper_only_abs": round(sum(_realised(paper.loc[i]) for i in paper_only), 2),
                "replay_only_abs": round(-sum(_realised(replay.loc[j]) for j in replay_only), 2),
                "return_gap_pp": round(gap, 2)},
        "checks": checks,
        "close_to_replay": all(c["pass"] for c in checks),
        "warnings": warnings,
        "trades": rows,
    }


def _row(status: str, p, r, entry_cost=None, exit_cost=None) -> dict:
    """One line of the trade-by-trade comparison; p or r is None for a one-sided trade."""
    def side(t) -> dict:
        if t is None:
            return {"open": None, "entry": None, "exit": None, "profit_pct": None}
        return {"open": _when(t["open_date"]), "entry": _r(t["open_rate"], 8),
                "exit": "open" if t["is_open"] else t["exit_reason"], "profit_pct": _r(t["profit_ratio"] * 100)}

    ps, rs = side(p), side(r)
    return {
        "pair": (p if p is not None else r)["pair"], "status": status,
        "paper_open": ps["open"], "replay_open": rs["open"],
        "paper_entry": ps["entry"], "replay_entry": rs["entry"], "entry_cost_pct": _r(entry_cost, 3),
        "paper_exit": ps["exit"], "replay_exit": rs["exit"], "exit_cost_pct": _r(exit_cost, 3),
        "paper_profit_pct": ps["profit_pct"], "replay_profit_pct": rs["profit_pct"],
    }
