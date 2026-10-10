"""Paper vs replay reconciliation: session discovery, trade matching, checks."""

import json
import sqlite3

import pandas as pd
import pytest

from ridgeline.reconcile import (
    SessionInfo,
    load_paper_trades,
    load_replay_trades,
    match_trades,
    reconcile,
    session_info,
    strategy_fingerprint,
    strategy_timeframe,
)

T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
H4 = pd.Timedelta("4h")


def trade(pair, opened, closed=None, open_rate=100.0, close_rate=None, profit_abs=None, exit_reason=None, stake=100.0):
    is_open = closed is None
    return {"pair": pair, "open_date": T(opened), "close_date": None if is_open else T(closed),
            "open_rate": open_rate, "close_rate": close_rate, "stake": stake,
            "profit_ratio": None if is_open else profit_abs / stake, "profit_abs": profit_abs,
            "exit_reason": exit_reason, "is_open": is_open}


def frame(*trades):
    df = pd.DataFrame(list(trades), columns=["pair", "open_date", "close_date", "open_rate", "close_rate", "stake",
                                             "profit_ratio", "profit_abs", "exit_reason", "is_open"])
    for col in ("open_date", "close_date"):
        df[col] = pd.to_datetime(df[col], utc=True)
    return df


def info(start="2026-09-01 14:09", end="2026-10-05 10:00", balance=1000.0):
    return SessionInfo(session_id="s1", db=None, strategy="TrendRider4h", pairs=["BTC/USDT", "ETH/USDT"],
                       balance=balance, start=T(start), end=T(end))


def test_matching_is_by_pair_and_nearest_entry():
    paper = frame(trade("BTC/USDT", "2026-09-02 08:00:20"), trade("ETH/USDT", "2026-09-03 00:00:30"),
                  trade("BTC/USDT", "2026-09-10 12:01"))
    replay = frame(trade("BTC/USDT", "2026-09-02 08:00"), trade("BTC/USDT", "2026-09-02 04:00"),
                   trade("SOL/USDT", "2026-09-03 00:00"), trade("BTC/USDT", "2026-09-10 00:00"))
    matched, paper_only, replay_only = match_trades(paper, replay, H4 * 1.5)
    assert matched == [(0, 0)]                  # the 08:00 entry, not the 04:00 one
    assert paper_only == [1, 2]                 # ETH has no partner; 12h apart is too far
    assert replay_only == [1, 2, 3]


def test_reconcile_breaks_down_the_gap():
    paper = frame(
        # same signal, paper paid 0.2% more and got 0.1% less on the same exit
        trade("BTC/USDT", "2026-09-02 08:00:20", "2026-09-05 12:00:05", 100.2, 109.89, 9.0, "exit_signal"),
        trade("ETH/USDT", "2026-09-12 00:00:30", "2026-09-14 04:00:10", 50.0, 49.0, -2.0, "stop_loss"),
        # opened after the replay's data ends: not compared yet
        trade("BTC/USDT", "2026-10-05 09:00"),
    )
    replay = frame(
        trade("BTC/USDT", "2026-09-02 08:00", "2026-09-05 12:00", 100.0, 110.0, 10.0, "exit_signal"),
        trade("SOL/USDT", "2026-09-20 00:00", "2026-09-22 00:00", 20.0, 21.0, 5.0, "exit_signal"),
        trade("ETH/USDT", "2026-10-04 20:00"),   # left open at the end of the replay
    )
    events = [{"ts": "2026-09-15T10:00:00+00:00", "event": "entries_paused"},
              {"ts": "2026-09-15T12:00:00+00:00", "event": "trading_resumed"},
              {"ts": "2026-09-15T12:00:00+00:00", "event": "stake_capped"},   # modelled by the backtest
              {"ts": "2026-08-01T00:00:00+00:00", "event": "flatten"}]       # before the session
    r = reconcile(paper, replay, info=info(), replay_end=T("2026-10-05 04:00"), timeframe="4h", events=events)

    assert r["period"]["start"].startswith("2026-09-01T12:00")  # floored to the 4h candle
    assert r["matching"] == {"matched": 1, "paper_only": 1, "replay_only": 2, "match_rate_pct": 25.0, "tolerance_hours": 6.0}
    assert r["paper"]["trades"] == 2 and r["paper"]["realised_abs"] == 7.0
    assert r["replay"]["open"] == 1 and r["replay"]["realised_abs"] == 15.0
    g = r["gap"]
    assert g["total_abs"] == -8.0
    assert g["matched_abs"] + g["paper_only_abs"] + g["replay_only_abs"] == pytest.approx(g["total_abs"])
    assert g["return_gap_pp"] == pytest.approx(-0.8)
    assert r["fills"]["entry_cost_pct"] == pytest.approx(0.2) and r["fills"]["exit_cost_pct"] == pytest.approx(0.1)
    assert r["fills"]["fill_cost_pct"] == pytest.approx(0.15)

    checks = {c["key"]: c for c in r["checks"]}
    assert checks["days"]["pass"] and checks["return_gap_pp"]["pass"] and checks["fill_cost_pct"]["pass"]
    assert not checks["match_rate_pct"]["pass"] and r["close_to_replay"] is False
    assert any("1 paper trade(s) opened after" in w for w in r["warnings"])
    assert any("entries_paused x1, trading_resumed x1" in w for w in r["warnings"])
    assert not any("flatten" in w or "stake_capped" in w for w in r["warnings"])
    assert [row["status"] for row in r["trades"]] == ["matched", "paper_only", "replay_only", "replay_only"]
    assert r["trades"][-1]["replay_exit"] == "open"


def test_a_faithful_month_passes():
    days = range(2, 28, 3)
    paper = frame(*(trade("BTC/USDT", f"2026-09-{d:02d} 04:00:30", f"2026-09-{d + 1:02d} 08:00:10",
                          100.05, 101.0, 1.0, "exit_signal") for d in days))
    replay = frame(*(trade("BTC/USDT", f"2026-09-{d:02d} 04:00", f"2026-09-{d + 1:02d} 08:00",
                           100.0, 101.0, 1.05, "exit_signal") for d in days))
    r = reconcile(paper, replay, info=info(start="2026-09-01 00:05"), replay_end=T("2026-10-05 04:00"), timeframe="4h")
    assert r["matching"]["match_rate_pct"] == 100.0
    assert r["fills"]["same_exit_reason_pct"] == 100.0
    assert r["close_to_replay"] is True, r["checks"]


def test_short_session_without_trades_fails_honestly():
    r = reconcile(frame(), frame(), info=info(start="2026-10-01 00:00"), replay_end=T("2026-10-05 04:00"), timeframe="4h")
    assert r["close_to_replay"] is False
    assert {c["key"]: c["value"] for c in r["checks"]}["match_rate_pct"] is None
    assert any("nothing to compare" in w for w in r["warnings"])


def test_stale_replay_data_is_flagged():
    r = reconcile(frame(), frame(), info=info(), replay_end=T("2026-09-20 00:00"), timeframe="4h")
    assert any("Download fresh data" in w for w in r["warnings"])


# ---------------------------------------------------------------- session files

def make_db(path, start="2026-09-01 14:09:52.1", strategy="TrendRider4h"):
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE KeyValueStore (id INTEGER, key TEXT, value_type TEXT, string_value TEXT, "
                    "datetime_value DATETIME, float_value FLOAT, int_value INTEGER)")
        con.execute("INSERT INTO KeyValueStore VALUES (1, 'bot_start_time', 'datetime', NULL, ?, NULL, NULL)", (start,))
        con.execute("CREATE TABLE trades (pair TEXT, open_date DATETIME, close_date DATETIME, open_rate FLOAT, "
                    "close_rate FLOAT, stake_amount FLOAT, close_profit FLOAT, close_profit_abs FLOAT, "
                    "exit_reason TEXT, is_open BOOLEAN, strategy TEXT)")
        con.execute("INSERT INTO trades VALUES ('BTC/USDT', '2026-09-02 08:00:20.5', '2026-09-05 12:00:05', "
                    "100.2, 109.89, 100, 0.09, 9.0, 'exit_signal', 0, ?)", (strategy,))
        con.execute("INSERT INTO trades VALUES ('ETH/USDT', '2026-09-12 00:00:30', NULL, 50, NULL, 100, NULL, NULL, "
                    "NULL, 1, ?)", (strategy,))
    con.close()


def test_session_info_from_log_and_database(tmp_path):
    db = tmp_path / "20260901-010946.sqlite"
    make_db(db)
    (tmp_path / "20260901-010946.config.json").write_text(json.dumps({"dry_run_wallet": 500}))
    (tmp_path / "20260901-010946.log.1").write_text(
        "... - INFO - Using resolved strategy TrendRider4h from 'x'...\n"
        "... - INFO - Whitelist with 2 pairs: ['BTC/USDT', 'ETH/USDT']\n")
    (tmp_path / "20260901-010946.log").write_text("... - INFO - Whitelist with 1 pairs: ['BTC/USDT']\n")
    s = session_info(db)
    assert (s.strategy, s.pairs, s.balance) == ("TrendRider4h", ["BTC/USDT", "ETH/USDT"], 500)  # oldest log first
    assert s.start == T("2026-09-01 14:09:52.1")
    assert abs(s.end - pd.Timestamp.now(tz="UTC")) < pd.Timedelta(minutes=5)  # last log write
    assert any("pair list changed" in w for w in s.warnings)

    trades = load_paper_trades(db)
    assert list(trades["is_open"]) == [False, True] and trades["open_date"].dt.tz is not None


def test_meta_file_wins_and_code_changes_are_flagged(tmp_path):
    db = tmp_path / "s.sqlite"
    make_db(db)
    code = strategy_fingerprint("TrendRider4h")
    code["TrendRider4h.py"] = "0" * 16
    (tmp_path / "s.meta.json").write_text(json.dumps({
        "strategy": "TrendRider4h", "pairs": ["SOL/USDT"], "balance": 250, "max_open_trades": 2,
        "started": "2026-09-03T00:00:00+00:00", "code": code}))
    s = session_info(db, end="2026-10-01")
    assert (s.pairs, s.balance, s.max_open_trades, s.start) == (["SOL/USDT"], 250, 2, T("2026-09-03"))
    assert s.end == T("2026-10-01")
    assert any("TrendRider4h.py" in w for w in s.warnings)
    assert not any("TrendRider4h.json" in w for w in s.warnings)


def test_missing_pairs_need_to_be_given(tmp_path):
    db = tmp_path / "s.sqlite"
    make_db(db)
    with pytest.raises(ValueError, match="which pairs"):
        session_info(db)
    assert session_info(db, pairs=["BTC/USDT"]).pairs == ["BTC/USDT"]


def test_strategy_timeframes():
    assert strategy_timeframe("TrendRider4h") == "4h"
    assert strategy_timeframe("EmaRsiMomentum") == "1h"


def test_replay_trades_left_open_come_back_open(tmp_path):
    def bt_trade(pair, opened, closed, reason):
        return {"pair": pair, "open_date": opened, "close_date": closed, "open_rate": 1.0, "close_rate": 1.1,
                "stake_amount": 100.0, "profit_ratio": 0.1, "profit_abs": 10.0, "exit_reason": reason, "is_open": False}

    result = {"strategy": {"TrendRider4h": {
        "backtest_start": "2026-09-01 12:00:00", "backtest_end": "2026-10-05 04:00:00", "timeframe": "4h",
        "starting_balance": 1000, "trades": [
            bt_trade("BTC/USDT", "2026-09-02 08:00:00+00:00", "2026-09-05 12:00:00+00:00", "force_exit"),  # flatten-like, mid-run
            bt_trade("ETH/USDT", "2026-10-04 20:00:00+00:00", "2026-10-05 04:00:00+00:00", "force_exit"),
        ]}}}
    (tmp_path / "backtest-result-x.json").write_text(json.dumps(result))
    (tmp_path / ".last_result.json").write_text(json.dumps({"latest_backtest": "backtest-result-x.json"}))
    df, meta = load_replay_trades(tmp_path)
    assert list(df["is_open"]) == [False, True]
    assert pd.isna(df.loc[1, "profit_abs"]) and df.loc[0, "profit_abs"] == 10.0
    assert meta["end"] == T("2026-10-05 04:00") and meta["timeframe"] == "4h"
