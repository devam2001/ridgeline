"""Strategies load, compute signals, never peek ahead, and respect risk limits."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from ridgeline.risk import PortfolioState

STRATEGIES = {
    # name: (timeframe of the synthetic candles, informative columns to fake)
    "TrendRider4h": ("4h", ["ema200_1d", "btc_close_1d", "btc_ema200_1d"]),
    "EmaRsiMomentum": ("1h", ["ema_macro_trend_4h"]),
    "TrendDipBuyer1h": ("1h", ["ema200_4h", "btc_close_1d", "btc_ema200_1d"]),
}


def with_informatives(df, columns):
    """Fake the merged higher-timeframe columns causally (shifted, past data only)."""
    df = df.copy()
    slow = df["close"].rolling(300, min_periods=1).mean().shift(6)
    for col in columns:
        df[col] = df["close"].shift(6) if col == "btc_close_1d" else slow
    return df


def signals(strat, df):
    meta = {"pair": "TEST/USDT"}
    out = strat.populate_indicators(df.copy(), meta)
    out = strat.populate_entry_trend(out, meta)
    out = strat.populate_exit_trend(out, meta)
    return out[["enter_long", "exit_long"]].fillna(0).astype(int).to_numpy()


@pytest.mark.parametrize("name", STRATEGIES)
def test_strategy_produces_signals(strategy, ohlcv, name):
    timeframe, informatives = STRATEGIES[name]
    strat = strategy(name)
    assert strat.timeframe == timeframe
    df = with_informatives(ohlcv(n=3000, freq=timeframe), informatives)
    sig = signals(strat, df)
    assert sig.shape == (3000, 2)
    assert set(np.unique(sig)) <= {0, 1}


@pytest.mark.parametrize("name", STRATEGIES)
def test_no_lookahead_prefix_invariance(strategy, ohlcv, name):
    """Signals at bar t must not change when later bars are removed."""
    timeframe, informatives = STRATEGIES[name]
    strat = strategy(name)
    df = with_informatives(ohlcv(n=2500, freq=timeframe, seed=7), informatives)
    full = signals(strat, df)
    for cut in (1500, 2000, 2400):
        assert np.array_equal(signals(strat, df.iloc[:cut].reset_index(drop=True)), full[:cut]), \
            f"{name}: signals before bar {cut} changed when later data was removed"


NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)


def fixed_state(monkeypatch, strat, **kw):
    st = PortfolioState(**{"equity": 1000.0, "peak_equity": 1000.0, "day_start_equity": 1000.0, "open_risk": 0.0, **kw})
    monkeypatch.setattr(type(strat), "portfolio_state", lambda self, now: st)


def confirm(strat, now=NOW):
    return strat.confirm_trade_entry("BTC/USDT", "limit", 1.0, 100.0, "GTC", now, None, "long")


def test_entry_blocked_by_daily_loss(strategy, monkeypatch):
    strat = strategy("TrendRider4h")
    fixed_state(monkeypatch, strat, equity=960.0)  # -4% today
    assert confirm(strat) is False


def test_drawdown_halt_and_backtest_review(strategy, monkeypatch):
    strat = strategy("TrendRider4h")
    fixed_state(monkeypatch, strat, equity=840.0, day_start_equity=840.0)  # -16% from peak
    assert confirm(strat) is False
    # portfolio recovers, but the halt stands until the review period passes
    fixed_state(monkeypatch, strat)
    assert confirm(strat, NOW + timedelta(days=1)) is False
    assert confirm(strat, NOW + timedelta(days=8)) is True


def test_stake_capped_and_skipped_below_minimum(strategy, monkeypatch):
    strat = strategy("TrendRider4h")
    fixed_state(monkeypatch, strat, open_risk=40.0)  # 5 of the 45 risk budget left
    assert strat.apply_risk_caps("BTC/USDT", 300.0, 0.05, NOW) == pytest.approx(100.0)
    assert strat.apply_risk_caps("BTC/USDT", 300.0, 0.05, NOW, min_stake=150.0) == 0.0
    fixed_state(monkeypatch, strat)
    assert strat.apply_risk_caps("BTC/USDT", 900.0, 0.01, NOW) == pytest.approx(350.0)  # 35% position cap


# ---------------------------------------------------------------- cross-sectional

class FakeDataProvider:
    """Just enough of Freqtrade's DataProvider for a strategy that ranks pairs."""

    def __init__(self, frames):
        self.frames = frames

    def current_whitelist(self):
        return list(self.frames)

    def get_pair_dataframe(self, pair, timeframe):
        return self.frames[pair].copy()


def rotation_signals(strat, frames):
    strat.dp = FakeDataProvider(frames)
    out = {}
    for pair, df in frames.items():
        meta = {"pair": pair}
        res = strat.populate_exit_trend(strat.populate_entry_trend(strat.populate_indicators(df.copy(), meta), meta), meta)
        out[pair] = res[["enter_long", "exit_long", "rank"]].fillna(0).to_numpy()
    return out


@pytest.fixture
def rotation_frames(ohlcv):
    frames = {f"P{i}/USDT": ohlcv(n=700, freq="1d", seed=i) for i in range(5)}
    # a pair listed 150 days later than the rest has no rank before then
    frames["LATE/USDT"] = ohlcv(n=550, freq="1d", start="2023-05-31", seed=9)
    return {p: with_informatives(df, ["btc_close_1d", "btc_ema200_1d"]) for p, df in frames.items()}


def test_rotation_ranks_pairs_against_each_other(strategy, rotation_frames):
    sig = rotation_signals(strategy("MomentumRotation1d"), rotation_frames)
    ranks = np.concatenate([s[:, 2] for s in sig.values()])
    assert set(np.unique(ranks[ranks > 0])) == {1, 2, 3, 4, 5, 6}
    assert sum(s[:, 0].sum() for s in sig.values()) > 0, "no entries at all"
    assert (sig["LATE/USDT"][:14, 2] == 0).all()  # unranked until it has 14 days of history


def test_rotation_no_lookahead_across_pairs(strategy, rotation_frames):
    """Freqtrade's lookahead-analysis reloads one pair at a time, which can't
    check a ranking. Remove later days from EVERY pair at once: no earlier
    signal or rank may change."""
    strat = strategy("MomentumRotation1d")
    full = rotation_signals(strat, rotation_frames)
    for cut_date in ("2023-12-01", "2024-06-01", "2024-11-30"):
        cut = {p: df[df["date"] < cut_date].reset_index(drop=True) for p, df in rotation_frames.items()}
        part = rotation_signals(strat, cut)
        for pair, sig in part.items():
            assert np.array_equal(sig, full[pair][:len(sig)]), f"{pair}: signals before {cut_date} changed"
