# MomentumRotation1d.py
# Cross-sectional momentum rotation for Freqtrade (long-only, spot)
#
# Idea: in crypto uptrends the strongest coins tend to keep leading for a
# while. TrendRider4h waits for each coin's own breakout and so catches only
# part of a bull run; this strategy instead ranks the whole whitelist every day
# and holds the leaders.
#
# Logic:
#   Score:  momentum over `mom_period` days divided by the volatility over the
#           same window (a t-stat-like, risk-adjusted momentum), ranked across
#           every pair in the whitelist on each day.
#   Entry:  the pair ranks in the top `top_n`, its momentum is positive, it is
#           above its own daily EMA50, and BTC is above its daily EMA200
#           (crypto-wide risk-on regime).
#   Exit:   the pair drops below rank `exit_rank` (hysteresis, so it isn't sold
#           the moment it slips one place), BTC turns risk-off, or the ATR
#           chandelier trailing stop is hit.
#   Sizing: risk-based, like TrendRider4h: a stop-out loses ~risk_per_trade of
#           equity, under the shared portfolio risk limits.
#
# Daily candles and a handful of positions mean low turnover (small fee drag)
# and positions large enough for Binance's ~5 USDT minimum order even on a
# ~100 AUD account.
#
# Starting point for backtesting, not a profit guarantee.

import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import talib.abstract as ta
from freqtrade.persistence import Trade
from freqtrade.strategy import informative, stoploss_from_absolute
from pandas import DataFrame

_ROOT = str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from ridgeline.freqtrade_risk import RiskMixin  # noqa: E402


class MomentumRotation1d(RiskMixin):
    INTERFACE_VERSION = 3

    timeframe = "1d"
    # BTC's daily EMA200 is the slowest indicator. EMAs carry a trace of their
    # starting value: 210 days left it ~4% off; 360 settles it to ~0.1%.
    startup_candle_count = 360
    process_only_new_candles = True
    # Signals depend on other pairs (the ranking). research.py checks these
    # strategies for lookahead with all pairs loaded; see research.lookahead().
    cross_sectional = True

    minimal_roi = {"0": 100}
    # Hard floor; must be at least as wide as atr_stop_max.
    stoploss = -0.25
    trailing_stop = False
    use_custom_stoploss = True

    risk_per_trade = 0.015

    # Ranking and holding rules. Plain attributes, not hyperopt parameters:
    # the score of every pair feeds every other pair's rank, so they were
    # chosen by hand on in-sample data (2023-06 -> 2025-06) only. In-sample
    # returns were very sensitive to the lookback (7d -15%, 10d +25%,
    # 14d +37%, 21d -4%, 30d -8%), so treat the 14-day choice as fragile.
    mom_period = 14
    top_n = 3
    exit_rank = 6

    atr_period = 14
    chandelier_mult = 3.0
    atr_stop_min = 0.05
    atr_stop_max = 0.25

    @property
    def protections(self):
        return [
            {"method": "CooldownPeriod", "stop_duration_candles": 1},
            {
                "method": "StoplossGuard",
                "lookback_period_candles": 14,
                "trade_limit": 2,
                "stop_duration_candles": 7,
                "only_per_pair": True,
            },
            {
                "method": "MaxDrawdown",
                "lookback_period_candles": 14,
                "trade_limit": 4,
                "stop_duration_candles": 3,
                "max_allowed_drawdown": 0.10,
            },
        ]

    @informative("1d", "BTC/{stake}", fmt="btc_{column}_{timeframe}")
    def populate_indicators_btc_1d(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema200"] = ta.EMA(dataframe, timeperiod=200)
        return dataframe

    def _score(self, dataframe: DataFrame) -> pd.Series:
        n = self.mom_period
        momentum = dataframe["close"] / dataframe["close"].shift(n) - 1
        volatility = dataframe["close"].pct_change().rolling(n).std() * np.sqrt(n)
        return momentum / volatility.replace(0, np.nan)

    def _rank(self, dataframe: DataFrame, pair: str) -> pd.Series:
        """This pair's rank (1 = strongest) among the whitelist on each day.
        Only same-day scores are compared, and each score only uses closes up
        to that day, so the ranking never looks ahead. Pairs that didn't trade
        yet on a day have no score and aren't ranked."""
        scores = {pair: pd.Series(self._score(dataframe).values, index=dataframe["date"])}
        for other in self.dp.current_whitelist():
            if other in scores:
                continue
            df = self.dp.get_pair_dataframe(pair=other, timeframe=self.timeframe)
            if df is None or df.empty:
                continue
            scores[other] = pd.Series(self._score(df).values, index=df["date"])
        ranks = pd.DataFrame(scores).rank(axis=1, ascending=False, method="min")[pair]
        return dataframe["date"].map(ranks)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=self.atr_period)
        dataframe["atr_pct"] = dataframe["atr"] / dataframe["close"]
        dataframe["score"] = self._score(dataframe)
        dataframe["rank"] = self._rank(dataframe, metadata["pair"])
        dataframe["btc_risk_on"] = dataframe["btc_close_1d"] > dataframe["btc_ema200_1d"]
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (dataframe["rank"] <= self.top_n)
                & (dataframe["score"] > 0)
                & (dataframe["close"] > dataframe["ema50"])
                & dataframe["btc_risk_on"]
                & (dataframe["volume"] > 0)
            ),
            "enter_long",
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                ((dataframe["rank"] > self.exit_rank) | ~dataframe["btc_risk_on"])
                & (dataframe["volume"] > 0)
            ),
            "exit_long",
        ] = 1
        return dataframe

    def _entry_stop_distance(self, pair: str) -> float:
        """Initial stop distance (fraction of price) from the latest ATR."""
        try:
            dataframe, _ = self.dp.get_analyzed_dataframe(pair=pair, timeframe=self.timeframe)
            atr_pct = dataframe["atr_pct"].iloc[-1]
        except Exception:
            atr_pct = None
        if atr_pct is None or atr_pct != atr_pct:
            return abs(self.stoploss)
        return max(self.atr_stop_min, min(self.atr_stop_max, atr_pct * self.chandelier_mult))

    def custom_stake_amount(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_stake: float,
        min_stake: Optional[float],
        max_stake: float,
        leverage: float,
        entry_tag: Optional[str],
        side: str,
        **kwargs,
    ) -> float:
        """Position size such that the initial stop loses `risk_per_trade` of equity."""
        try:
            total_equity = self.wallets.get_total_stake_amount()
        except Exception:
            return proposed_stake
        distance = self._entry_stop_distance(pair)
        if distance <= 0 or total_equity <= 0:
            return proposed_stake
        stake = total_equity * self.risk_per_trade / distance
        if min_stake is not None:
            stake = max(stake, min_stake)
        stake = min(stake, max_stake)
        return self.apply_risk_caps(pair, stake, distance, current_time, min_stake)

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> Optional[float]:
        """Chandelier stop: max(initial stop, highest high since entry - k*ATR).
        The initial distance is pinned at the first call so it matches the
        distance custom_stake_amount() sized the position against."""
        pinned = trade.get_custom_data(key="entry_stop_distance")
        if pinned is None:
            pinned = self._entry_stop_distance(pair)
            trade.set_custom_data(key="entry_stop_distance", value=pinned)
        stop_price = trade.open_rate * (1 - pinned)

        dataframe, _ = self.dp.get_analyzed_dataframe(pair=pair, timeframe=self.timeframe)
        since_entry = dataframe.loc[dataframe["date"] >= trade.open_date_utc]
        if not since_entry.empty:
            atr = since_entry["atr"].iloc[-1]
            stop_price = max(stop_price, since_entry["high"].max() - self.chandelier_mult * atr)

        return stoploss_from_absolute(stop_price, current_rate)
