# TrendDipBuyer1h.py
# Buy-the-dip inside an uptrend for Freqtrade (long-only, spot)
#
# Logic:
#   Entry (long): short-term oversold (RSI(4) low AND close below the lower
#                 Bollinger band) while the bigger picture is bullish: price
#                 above the 4h EMA200 and BTC above its daily EMA200.
#   Exit:         snap-back to the mean (close above EMA20) or a short-term
#                 overbought RSI(4), plus a small ROI ladder.
#   Stoploss:     ATR-based, pinned at entry (fixed price, not trailing).
#   Sizing:       risk-based: a stop-out loses ~risk_per_trade of equity.
#
# Mean reversion has a high win rate and small average wins, so it is very
# fee-sensitive - always check the fee stress test before trusting it.

import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import talib.abstract as ta
from freqtrade.persistence import Trade
from freqtrade.strategy import (
    DecimalParameter,
    IntParameter,
    informative,
    stoploss_from_open,
)
from pandas import DataFrame

# Shared ridgeline package (portfolio risk limits, audit log) lives at the
# project root; Freqtrade only puts this strategies folder on the import path.
_ROOT = str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from ridgeline.freqtrade_risk import RiskMixin  # noqa: E402


class TrendDipBuyer1h(RiskMixin):
    INTERFACE_VERSION = 3

    timeframe = "1h"
    # Daily EMA200 (BTC regime) needs 200 days = 4800 1h candles.
    startup_candle_count = 4900
    process_only_new_candles = True

    minimal_roi = {"0": 0.05, "360": 0.025, "1440": 0.01}
    stoploss = -0.10
    trailing_stop = False
    use_custom_stoploss = True

    risk_per_trade = 0.015

    # --- Entry ---
    rsi_entry = IntParameter(10, 35, default=20, space="buy")
    bb_std = DecimalParameter(1.5, 3.0, default=2.0, decimals=1, space="buy")

    # --- Exit ---
    rsi_exit = IntParameter(60, 90, default=75, space="sell")
    atr_stop_multiplier = DecimalParameter(2.0, 5.0, default=3.0, decimals=1, space="sell")

    atr_stop_min = 0.03
    atr_stop_max = 0.10

    @property
    def protections(self):
        return [
            {"method": "CooldownPeriod", "stop_duration_candles": 3},
            {
                "method": "StoplossGuard",
                "lookback_period_candles": 24,
                "trade_limit": 2,
                "stop_duration_candles": 24,
                "only_per_pair": True,
            },
            {
                "method": "MaxDrawdown",
                "lookback_period_candles": 72,
                "trade_limit": 5,
                "stop_duration_candles": 24,
                "max_allowed_drawdown": 0.08,
            },
        ]

    @informative("4h")
    def populate_indicators_4h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema200"] = ta.EMA(dataframe, timeperiod=200)
        return dataframe

    @informative("1d", "BTC/{stake}", fmt="btc_{column}_{timeframe}")
    def populate_indicators_btc_1d(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema200"] = ta.EMA(dataframe, timeperiod=200)
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["rsi4"] = ta.RSI(dataframe, timeperiod=4)
        dataframe["ema20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["atr_pct"] = ta.ATR(dataframe, timeperiod=14) / dataframe["close"]
        mid = dataframe["close"].rolling(20).mean()
        std = dataframe["close"].rolling(20).std()
        for k in set(self.bb_std.range):
            dataframe[f"bb_lower_{k:.1f}"] = mid - k * std
        dataframe["btc_risk_on"] = dataframe["btc_close_1d"] > dataframe["btc_ema200_1d"]
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                (dataframe["rsi4"] < self.rsi_entry.value)
                & (dataframe["close"] < dataframe[f"bb_lower_{self.bb_std.value:.1f}"])
                & (dataframe["close"] > dataframe["ema200_4h"])
                & dataframe["btc_risk_on"]
                & (dataframe["volume"] > 0)
            ),
            "enter_long",
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                ((dataframe["close"] > dataframe["ema20"]) | (dataframe["rsi4"] > self.rsi_exit.value))
                & (dataframe["volume"] > 0)
            ),
            "exit_long",
        ] = 1
        return dataframe

    def _clamped_atr_stop_distance(self, pair: str) -> float:
        try:
            dataframe, _ = self.dp.get_analyzed_dataframe(pair=pair, timeframe=self.timeframe)
            atr_pct = dataframe["atr_pct"].iloc[-1]
        except Exception:
            atr_pct = None
        if atr_pct is None or atr_pct != atr_pct:
            return abs(self.stoploss)
        distance = atr_pct * self.atr_stop_multiplier.value
        return max(self.atr_stop_min, min(self.atr_stop_max, distance))

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
        """Position size such that the stop loses `risk_per_trade` of equity."""
        try:
            total_equity = self.wallets.get_total_stake_amount()
        except Exception:
            return proposed_stake
        distance = self._clamped_atr_stop_distance(pair)
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
        **kwargs,
    ) -> float:
        """ATR stop pinned at entry, held at a fixed price below the open."""
        pinned = trade.get_custom_data(key="atr_stop_distance")
        if pinned is None:
            pinned = self._clamped_atr_stop_distance(pair)
            trade.set_custom_data(key="atr_stop_distance", value=pinned)
        return stoploss_from_open(-pinned, current_profit)
