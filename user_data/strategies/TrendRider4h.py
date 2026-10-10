# TrendRider4h.py
# Breakout trend-follower for Freqtrade (long-only, spot)
#
# Logic:
#   Entry (long): 4h close breaks above the prior 20-candle high (Donchian
#                 breakout), ADX confirms a real trend, the pair itself is
#                 above its daily EMA200, and BTC is above ITS daily EMA200
#                 (crypto-wide risk-on regime: no new longs in a BTC bear
#                 market, when almost every alt bleeds with it).
#   Exit:         ATR "chandelier" trailing stop: highest high since entry
#                 minus k*ATR. No ROI cap - the whole point of trend
#                 following is a few large winners paying for many small
#                 losers, so winners are never cut at a fixed +x%.
#                 Optional Donchian-low exit (classic turtle exit).
#   Sizing:       risk-based: a stop-out loses ~risk_per_trade of equity.
#
# Starting point for backtesting / hyperopt, not a profit guarantee.

import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import talib.abstract as ta
from freqtrade.persistence import Trade
from freqtrade.strategy import (
    BooleanParameter,
    DecimalParameter,
    IntParameter,
    informative,
    stoploss_from_absolute,
)
from pandas import DataFrame

# Shared ridgeline package (portfolio risk limits, audit log) lives at the
# project root; Freqtrade only puts this strategies folder on the import path.
_ROOT = str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from ridgeline.freqtrade_risk import RiskMixin  # noqa: E402


class TrendRider4h(RiskMixin):
    INTERFACE_VERSION = 3

    timeframe = "4h"
    # Daily EMA200 needs 200 days; informative data is loaded for the same
    # wall-clock warmup as the base timeframe, so 1260 x 4h = 210 days.
    startup_candle_count = 1260
    process_only_new_candles = True

    # Let winners run: exits come from the trailing stop / exit signal only.
    minimal_roi = {"0": 100}
    # Hard floor; must be at least as wide as atr_stop_max (custom stops can
    # only tighten relative to this).
    stoploss = -0.15
    trailing_stop = False
    use_custom_stoploss = True

    risk_per_trade = 0.015

    # --- Entry ---
    breakout_period = IntParameter(10, 40, default=20, space="buy")
    adx_threshold = IntParameter(15, 35, default=20, space="buy")

    # --- Exit ---
    chandelier_mult = DecimalParameter(2.0, 5.0, default=3.0, decimals=1, space="sell")
    exit_period = IntParameter(5, 20, default=10, space="sell")
    use_donchian_exit = BooleanParameter(default=True, space="sell")

    atr_period = 14
    atr_stop_min = 0.03
    atr_stop_max = 0.15

    @property
    def protections(self):
        return [
            {"method": "CooldownPeriod", "stop_duration_candles": 2},
            {
                "method": "StoplossGuard",
                "lookback_period_candles": 18,
                "trade_limit": 2,
                "stop_duration_candles": 6,
                "only_per_pair": True,
            },
            {
                "method": "MaxDrawdown",
                "lookback_period_candles": 42,
                "trade_limit": 4,
                "stop_duration_candles": 12,
                "max_allowed_drawdown": 0.10,
            },
        ]

    @informative("1d")
    def populate_indicators_1d(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema200"] = ta.EMA(dataframe, timeperiod=200)
        return dataframe

    @informative("1d", "BTC/{stake}", fmt="btc_{column}_{timeframe}")
    def populate_indicators_btc_1d(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema200"] = ta.EMA(dataframe, timeperiod=200)
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=self.atr_period)
        dataframe["atr_pct"] = dataframe["atr"] / dataframe["close"]
        # Channels exclude the current candle (shift) so "breaks above the
        # prior N-candle high" doesn't compare a candle against itself.
        for n in set(self.breakout_period.range):
            dataframe[f"dc_high_{n}"] = dataframe["high"].rolling(n).max().shift(1)
        for n in set(self.exit_period.range):
            dataframe[f"dc_low_{n}"] = dataframe["low"].rolling(n).min().shift(1)
        dataframe["btc_risk_on"] = dataframe["btc_close_1d"] > dataframe["btc_ema200_1d"]
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (dataframe["close"] > dataframe[f"dc_high_{self.breakout_period.value}"])
                & (dataframe["adx"] > self.adx_threshold.value)
                & (dataframe["close"] > dataframe["ema200_1d"])
                & dataframe["btc_risk_on"]
                & (dataframe["volume"] > 0)
            ),
            "enter_long",
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (dataframe["close"] < dataframe[f"dc_low_{self.exit_period.value}"])
                & self.use_donchian_exit.value
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
        return max(self.atr_stop_min, min(self.atr_stop_max, atr_pct * self.chandelier_mult.value))

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
        """
        Chandelier stop: max(initial stop, highest high since entry - k*ATR).
        The initial distance is pinned at the first call so it matches the
        distance custom_stake_amount() sized the position against.
        """
        pinned = trade.get_custom_data(key="entry_stop_distance")
        if pinned is None:
            pinned = self._entry_stop_distance(pair)
            trade.set_custom_data(key="entry_stop_distance", value=pinned)
        stop_price = trade.open_rate * (1 - pinned)

        dataframe, _ = self.dp.get_analyzed_dataframe(pair=pair, timeframe=self.timeframe)
        since_entry = dataframe.loc[dataframe["date"] >= trade.open_date_utc]
        if not since_entry.empty:
            atr = since_entry["atr"].iloc[-1]
            chandelier = since_entry["high"].max() - self.chandelier_mult.value * atr
            stop_price = max(stop_price, chandelier)

        return stoploss_from_absolute(stop_price, current_rate)
