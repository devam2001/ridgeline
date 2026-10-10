# EmaRsiMomentum.py
# Momentum / trend-following strategy for Freqtrade
#
# Logic:
#   Entry (long): fast EMA crosses above slow EMA, RSI confirms real momentum
#                 without being overbought, there's real volume, ADX confirms
#                 an actual trend (not chop), and price is above the 4h
#                 EMA200 (don't fight the bigger trend).
#   Exit:         fast EMA crosses back below slow EMA, OR RSI overbought.
#   Stoploss:     ATR-based and volatility-adaptive (see custom_stoploss),
#                 clamped between atr_stop_min and atr_stop_max, pinned once
#                 per trade so it doesn't drift mid-trade.
#   Sizing:       custom_stake_amount() enforces the 1-2% account-risk rule
#                 against that SAME ATR-based distance, so a volatile pair
#                 automatically gets a smaller position for the same $ risk.
#
# This is a STARTING POINT strategy meant to be backtested and tuned before
# any live use. Run in dry-run first. Nothing here is financial advice.

from datetime import datetime
from typing import Optional

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import (
    BooleanParameter,
    DecimalParameter,
    IntParameter,
    IStrategy,
    informative,
    stoploss_from_open,
)


class EmaRsiMomentum(IStrategy):
    INTERFACE_VERSION = 3

    # --- Timeframe ---
    # 1h is a reasonable starting point for a spot momentum strategy: enough
    # signal-to-noise to avoid overtrading on fees, still responsive enough
    # to catch multi-day trends. Tune later once backtested.
    timeframe = "1h"

    # --- Risk / exit framework ---
    # Hard initial stop. Freqtrade's custom_stoploss can only TIGHTEN the stop
    # relative to this, never widen it, so it must be at least as wide as
    # atr_stop_max or the wider ATR stops would silently be capped here.
    # Also the fallback sizing distance if ATR data isn't available.
    stoploss = -0.08

    # Take-profit ladder: exit fully at +8%, or partially earlier if it gets
    # there fast. Freqtrade reads this as "time in minutes: ROI target".
    minimal_roi = {
        "0": 0.08,
        "60": 0.05,
        "180": 0.03,
        "720": 0.01,
    }

    # Trailing stop: once in profit, lock in gains instead of round-tripping
    # back to breakeven.
    trailing_stop = True
    trailing_stop_positive = 0.015
    trailing_stop_positive_offset = 0.03
    trailing_only_offset_is_reached = True

    # --- Risk-per-trade rule (the "1-2% rule") ---
    # Fraction of total account equity you're willing to lose if a single
    # trade's stoploss is hit. Keep this at 0.01-0.02 (1-2%).
    risk_per_trade = 0.015  # 1.5%

    process_only_new_candles = True
    # The 4h EMA200 is the slowest indicator: 200 * 4h = ~34 days of candles,
    # translated into 1h bars for the base timeframe, plus headroom.
    startup_candle_count = 900

    # --- Circuit breakers ---
    # These run inside Freqtrade's ProtectionManager and can lock a pair (or
    # the whole bot) out of new entries without you having to babysit it:
    #   CooldownPeriod  - pause a pair briefly right after any exit
    #   StoplossGuard   - if a pair stops out twice in 24h, lock it out
    #   MaxDrawdown     - the actual "daily drawdown circuit breaker":
    #                     if equity drawdown over the lookback window exceeds
    #                     5%, halt ALL new entries until it cools down
    @property
    def protections(self):
        return [
            {"method": "CooldownPeriod", "stop_duration_candles": 3},
            {
                "method": "StoplossGuard",
                "lookback_period_candles": 24,
                "trade_limit": 2,
                "stop_duration_candles": 12,
                "only_per_pair": True,
            },
            {
                "method": "MaxDrawdown",
                "lookback_period_candles": 48,
                "trade_limit": 5,
                "stop_duration_candles": 24,
                "max_allowed_drawdown": 0.05,
            },
        ]

    # --- Indicator windows ---
    # Tunable values are hyperopt parameters: `freqtrade hyperopt --spaces buy
    # sell ...` searches the ranges, and the defaults are what backtests and
    # live/dry-run use until a hyperopt result is saved next to this file.
    ema_fast_period = IntParameter(8, 20, default=12, space="buy")
    ema_slow_period = IntParameter(21, 55, default=26, space="buy")
    rsi_period = 14
    rsi_buy_floor = IntParameter(30, 55, default=35, space="buy")  # don't buy if momentum is this weak
    rsi_buy_ceiling = IntParameter(60, 80, default=68, space="buy")  # don't buy if already overbought
    rsi_exit_overbought = IntParameter(70, 90, default=78, space="sell")
    # The EMA cross-down exit lost on ~98% of its trades in backtests; this
    # lets hyperopt decide whether it earns its place.
    use_exit_cross = BooleanParameter(default=True, space="sell")

    # --- Trend-strength filter (new) ---
    # ADX measures how much of a trend actually exists, regardless of
    # direction. EMA crossovers whipsaw constantly in a choppy/ranging
    # market; requiring ADX above this threshold filters out the entries
    # most likely to be noise rather than a real move.
    adx_period = 14
    adx_trend_threshold = IntParameter(15, 35, default=20, space="buy")

    # --- Macro-trend filter (new) ---
    # This is a long-only spot bot, so the single biggest lever isn't the
    # entry trigger, it's not fighting the bigger trend: only take an
    # EMA-crossover long if price is above a slow EMA on a HIGHER timeframe.
    # 4h/EMA200 roughly approximates "is this asset in a macro uptrend".
    trend_filter_timeframe = "4h"
    trend_filter_ema_period = 200

    # --- ATR-based dynamic stop (new) ---
    # A flat 5% stop is too tight for a volatile alt and too loose for a
    # quiet BTC range. Sizing the stop off ATR adapts it to each pair's
    # actual current volatility. atr_stop_multiplier is "how many ATRs away"
    # the stop sits; the min/max bounds keep it from going to an absurd
    # extreme on a volatility spike or a dead-quiet market.
    use_custom_stoploss = True
    atr_period = 14
    atr_stop_multiplier = DecimalParameter(1.5, 5.0, default=2.5, decimals=1, space="sell")
    atr_stop_min = DecimalParameter(0.015, 0.05, default=0.02, decimals=3, space="sell")  # never tighter than this
    atr_stop_max = 0.08  # never wider than 8%

    @informative(trend_filter_timeframe)
    def populate_indicators_trend_filter(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Freqtrade auto-suffixes these columns with the timeframe, e.g.
        # "ema_macro_trend_4h", and merges them into the base dataframe.
        dataframe["ema_macro_trend"] = ta.EMA(dataframe, timeperiod=self.trend_filter_ema_period)
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # One column per candidate period: populate_indicators runs once per
        # hyperopt, so every period the search may pick must already exist.
        # Outside hyperopt `.range` is just the current value.
        for period in set(self.ema_fast_period.range) | set(self.ema_slow_period.range):
            dataframe[f"ema_{period}"] = ta.EMA(dataframe, timeperiod=period)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=self.rsi_period)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=self.adx_period)

        # ATR as a % of price, so the same multiplier is comparable across
        # a $60,000 BTC and a $2 altcoin.
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=self.atr_period)
        dataframe["atr_pct"] = dataframe["atr"] / dataframe["close"]

        # Volume filter: ignore candles with abnormally thin volume, common
        # source of false breakouts on lower-liquidity pairs.
        dataframe["volume_mean_20"] = dataframe["volume"].rolling(20).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_fast"] = dataframe[f"ema_{self.ema_fast_period.value}"]
        dataframe["ema_slow"] = dataframe[f"ema_{self.ema_slow_period.value}"]
        dataframe.loc[
            (
                # fast EMA crosses above slow EMA this candle
                (dataframe["ema_fast"] > dataframe["ema_slow"])
                & (dataframe["ema_fast"].shift(1) <= dataframe["ema_slow"].shift(1))
                # momentum confirmed but not already overbought
                & (dataframe["rsi"] > self.rsi_buy_floor.value)
                & (dataframe["rsi"] < self.rsi_buy_ceiling.value)
                # real volume behind the move
                & (dataframe["volume"] > dataframe["volume_mean_20"])
                & (dataframe["volume"] > 0)
                # market is actually trending, not chopping sideways
                & (dataframe["adx"] > self.adx_trend_threshold.value)
                # don't fight the bigger trend: price above 4h EMA200
                & (dataframe["close"] > dataframe[f"ema_macro_trend_{self.trend_filter_timeframe}"])
            ),
            "enter_long",
        ] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        ema_fast = dataframe[f"ema_{self.ema_fast_period.value}"]
        ema_slow = dataframe[f"ema_{self.ema_slow_period.value}"]
        # trend has flipped: fast EMA crosses back below slow EMA
        cross_down = (ema_fast < ema_slow) & (ema_fast.shift(1) >= ema_slow.shift(1))
        dataframe.loc[
            (
                (cross_down & self.use_exit_cross.value)
                | (dataframe["rsi"] > self.rsi_exit_overbought.value)
            )
            & (dataframe["volume"] > 0),
            "exit_long",
        ] = 1

        return dataframe

    def _clamped_atr_stop_distance(self, pair: str) -> float:
        """
        Latest ATR% for `pair`, converted to a stop distance and clamped to
        [atr_stop_min, atr_stop_max]. Falls back to the fixed `stoploss` if
        indicator data isn't available yet (e.g. during warmup).
        """
        try:
            dataframe, _ = self.dp.get_analyzed_dataframe(pair=pair, timeframe=self.timeframe)
            atr_pct = dataframe["atr_pct"].iloc[-1]
        except Exception:
            atr_pct = None

        if atr_pct is None or atr_pct != atr_pct:  # NaN check without numpy import
            return abs(self.stoploss)

        distance = atr_pct * self.atr_stop_multiplier.value
        return max(self.atr_stop_min.value, min(self.atr_stop_max, distance))

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
        """
        Size the position so that hitting the stoploss loses exactly
        `risk_per_trade` (e.g. 1.5%) of total account equity — not 1.5% of
        the position itself.

        position_size = (equity * risk_per_trade) / stoploss_distance

        stoploss_distance now comes from ATR (see _clamped_atr_stop_distance),
        so a volatile pair automatically gets a smaller position than a calm
        one for the same dollar risk.
        """
        try:
            total_equity = self.wallets.get_total_stake_amount()
        except Exception:
            return proposed_stake

        stoploss_distance = self._clamped_atr_stop_distance(pair)
        if stoploss_distance == 0 or total_equity <= 0:
            return proposed_stake

        risk_amount = total_equity * self.risk_per_trade
        stake = risk_amount / stoploss_distance

        # Respect exchange/strategy min & max stake bounds.
        if min_stake is not None:
            stake = max(stake, min_stake)
        stake = min(stake, max_stake)

        return stake

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime,
        entry_tag: Optional[str],
        side: str,
        **kwargs,
    ) -> bool:
        """
        Final safety gate before an order is actually sent. Extend this if
        you want extra checks (e.g. blocking entries around scheduled news,
        or during unusually wide spreads). The ATR stop distance itself is
        pinned in custom_stoploss() below, the first time it's called for
        this trade — see there for why.
        """
        return True

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> float:
        """
        ATR-based stop, pinned once per trade so it doesn't drift as
        volatility changes mid-trade — the exit trigger stays consistent
        with the distance custom_stake_amount() sized the position against.

        Freqtrade's Trade.get_custom_data/set_custom_data is per-trade and
        persisted, so this survives bot restarts and works correctly with
        several concurrent trades on different pairs.
        """
        pinned = trade.get_custom_data(key="atr_stop_distance")
        if pinned is None:
            pinned = self._clamped_atr_stop_distance(pair)
            trade.set_custom_data(key="atr_stop_distance", value=pinned)

        # Freqtrade interprets the return value relative to current_rate, so
        # returning -pinned directly would make this a trailing stop. Convert
        # the entry-relative distance so the stop price stays fixed.
        return stoploss_from_open(-pinned, current_profit)
