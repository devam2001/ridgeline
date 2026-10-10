"""Freqtrade glue for ridgeline.risk: a mixin every ridgeline strategy inherits.

    class TrendRider4h(RiskMixin):
        ...
        def custom_stake_amount(...):
            stake = ...  # strategy's own risk-based sizing
            return self.apply_risk_caps(pair, stake, stop_distance, current_time, min_stake)

Entries are vetoed in confirm_trade_entry() when a portfolio limit is hit.
In live / dry-run, every risk event goes to the audit log and to Telegram;
backtests and hyperopt only log, so research runs leave no audit noise.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from freqtrade.enums import RunMode
from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy

from ridgeline import audit
from ridgeline.risk import (
    PortfolioState,
    RiskLimits,
    build_state,
    cap_stake,
    check_entry,
    is_drawdown_breach,
    load_halt,
    set_halt,
)

logger = logging.getLogger(__name__)

_REPORT_REPEAT = timedelta(hours=1)  # don't repeat the same alert more often than this


class RiskMixin(IStrategy):

    _risk_limits: Optional[RiskLimits] = None
    _bt_halt_until: Optional[datetime] = None
    _bt_peak_reset: Optional[datetime] = None

    # --- helpers ---

    @property
    def risk_limits(self) -> RiskLimits:
        if self._risk_limits is None:
            self._risk_limits = RiskLimits.from_config(self.config)
        return self._risk_limits

    def _is_live(self) -> bool:
        dp = getattr(self, "dp", None)  # only attached once Freqtrade wires the strategy up
        return dp is not None and dp.runmode in (RunMode.LIVE, RunMode.DRY_RUN)

    def _last_close(self, pair: str) -> Optional[float]:
        try:
            df, _ = self.dp.get_analyzed_dataframe(pair=pair, timeframe=self.timeframe)
            return float(df["close"].iloc[-1]) if len(df) else None
        except Exception:
            return None

    def _peak_reset_time(self) -> Optional[datetime]:
        """After a reviewed halt, drawdown is measured from equity at resumption."""
        if self._is_live():
            ts = load_halt().get("resumed_at")
            return datetime.fromisoformat(ts) if ts else None
        return self._bt_peak_reset

    def portfolio_state(self, now: datetime) -> PortfolioState:
        wallets = getattr(self, "wallets", None)
        start = wallets.get_starting_balance() if wallets else float(self.config.get("dry_run_wallet", 0))
        closed = [(t.close_date_utc, t.close_profit_abs or 0.0) for t in Trade.get_trades_proxy(is_open=False)]
        open_positions = []
        for t in Trade.get_trades_proxy(is_open=True):
            rate = self._last_close(t.pair) or t.open_rate
            open_positions.append((t.open_rate, t.stop_loss, t.amount, t.calc_profit(rate)))
        return build_state(start, closed, open_positions, now, self._peak_reset_time())

    def _halted(self, now: datetime) -> bool:
        if self._is_live():
            return bool(load_halt().get("halted"))
        if self._bt_halt_until is not None and now >= self._bt_halt_until:
            # backtest model of "a human reviewed it and resumed"
            self._bt_halt_until = None
            self._bt_peak_reset = now
        return self._bt_halt_until is not None

    def _trigger_halt(self, reason: str, now: datetime) -> None:
        if self._is_live():
            set_halt(True, reason)
        else:
            self._bt_halt_until = now + timedelta(days=self.risk_limits.halt_review_days)

    def _report(self, event: str, message: str, now: datetime, **details) -> None:
        logger.info("risk %s: %s %s", event, message, details or "")
        if not self._is_live():
            return
        sent = self.__dict__.setdefault("_risk_reports_sent", {})
        key = (event, message.split(" ")[0])
        if key in sent and now - sent[key] < _REPORT_REPEAT:
            return
        sent[key] = now
        audit.append(event, actor="strategy", details={"message": message, "strategy": self.__class__.__name__, **details})
        try:
            self.dp.send_msg(f"Ridgeline risk ({event}): {message}")
        except Exception:
            logger.exception("could not send risk alert")

    # --- Freqtrade hooks ---

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime, entry_tag: Optional[str],
                            side: str, **kwargs) -> bool:
        state = self.portfolio_state(current_time)
        allowed, reason = check_entry(state, self.risk_limits, self._halted(current_time))
        if not allowed:
            if is_drawdown_breach(reason):
                self._trigger_halt(reason, current_time)
                self._report("risk_halt", reason, current_time)
            else:
                self._report("entry_blocked", reason, current_time, pair=pair)
        return allowed

    def apply_risk_caps(self, pair: str, stake: float, stop_distance: float,
                        current_time: datetime, min_stake: Optional[float] = None) -> float:
        """Call at the end of custom_stake_amount. Returns 0 (= skip the trade)
        if the capped stake falls below the exchange minimum."""
        state = self.portfolio_state(current_time)
        capped, reason = cap_stake(stake, stop_distance, state, self.risk_limits)
        if reason:
            self._report("stake_capped", reason, current_time, pair=pair)
        if min_stake is not None and capped < min_stake:
            return 0.0
        return capped
