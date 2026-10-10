"""Portfolio-level risk limits.

Pure logic with no Freqtrade dependency, so every limit can be unit-tested.
`ridgeline.freqtrade_risk.RiskMixin` wires these checks into strategies.

Limits (defaults, overridable via a "ridgeline_risk" section in config.json):
  max_position_pct       largest single position, % of equity
  max_open_risk_pct      sum over open trades of (stake x distance to stop), % of equity
  daily_loss_limit_pct   loss since UTC midnight, % of equity at midnight -> no new
                         entries until the next UTC day
  max_drawdown_halt_pct  drawdown from peak equity -> new entries halted until a human
                         resumes (live/paper). Backtests auto-resume after
                         halt_review_days to model that review.
"""

import json
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ridgeline import USERDIR

HALT_STATE_FILE = USERDIR / "risk_state.json"


@dataclass(frozen=True)
class RiskLimits:
    max_position_pct: float = 35.0
    max_open_risk_pct: float = 4.5
    daily_loss_limit_pct: float = 3.0
    max_drawdown_halt_pct: float = 15.0
    halt_review_days: float = 7.0  # backtests only

    @classmethod
    def from_config(cls, config: dict) -> "RiskLimits":
        overrides = config.get("ridgeline_risk") or {}
        known = {f.name for f in fields(cls)}
        return cls(**{k: float(v) for k, v in overrides.items() if k in known})


@dataclass(frozen=True)
class PortfolioState:
    equity: float            # starting balance + realised + unrealised P&L
    peak_equity: float       # highest equity seen (realised basis)
    day_start_equity: float  # equity at the most recent UTC midnight (realised basis)
    open_risk: float         # currency lost if every open trade hit its stop now

    def drawdown_pct(self) -> float:
        return max(0.0, (self.peak_equity - self.equity) / self.peak_equity * 100) if self.peak_equity > 0 else 0.0

    def day_loss_pct(self) -> float:
        if self.day_start_equity <= 0:
            return 0.0
        return max(0.0, (self.day_start_equity - self.equity) / self.day_start_equity * 100)

    def open_risk_pct(self) -> float:
        return self.open_risk / self.equity * 100 if self.equity > 0 else 100.0


def build_state(start_balance: float,
                closed: list[tuple[datetime, float]],
                open_positions: list[tuple[float, float, float, float]],
                now: datetime,
                peak_reset: Optional[datetime] = None) -> PortfolioState:
    """Portfolio state from plain data, shared by the strategy (Trade objects),
    the panel and the watchdog (REST API), so they can never disagree.

    closed:         (close_time, profit_abs) per closed trade
    open_positions: (open_rate, stop_price, amount, unrealised_profit_abs)
    peak_reset:     after a reviewed halt, drawdown restarts from equity then
    """
    midnight = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    realised = 0.0
    day_start = peak = start_balance
    for close_time, profit in sorted(closed, key=lambda c: c[0]):
        realised += profit or 0.0
        if close_time < midnight:
            day_start = start_balance + realised
        if peak_reset is not None and close_time < peak_reset:
            peak = start_balance + realised
        else:
            peak = max(peak, start_balance + realised)
    unrealised = sum(p[3] for p in open_positions)
    # loss vs entry if every stop hit now; a stop above entry risks nothing
    open_risk = sum(max(0.0, rate - stop) * amount for rate, stop, amount, _ in open_positions if stop)
    equity = start_balance + realised + unrealised
    return PortfolioState(equity=equity, peak_equity=max(peak, equity),
                          day_start_equity=day_start, open_risk=open_risk)


def check_entry(state: PortfolioState, limits: RiskLimits, halted: bool = False) -> tuple[bool, Optional[str]]:
    """May a new position be opened? Returns (allowed, reason if not)."""
    if halted:
        return False, "trading halted after the drawdown limit was hit - resume manually after review"
    if state.equity <= 0:
        return False, "no equity"
    dd = state.drawdown_pct()
    if dd >= limits.max_drawdown_halt_pct:
        return False, f"drawdown {dd:.2f}% reached the {limits.max_drawdown_halt_pct:g}% halt limit"
    day = state.day_loss_pct()
    if day >= limits.daily_loss_limit_pct:
        return False, f"today's loss {day:.2f}% reached the {limits.daily_loss_limit_pct:g}% daily limit"
    risk = state.open_risk_pct()
    if risk >= limits.max_open_risk_pct:
        return False, f"open risk {risk:.2f}% already at the {limits.max_open_risk_pct:g}% limit"
    return True, None


def is_drawdown_breach(reason: Optional[str]) -> bool:
    return bool(reason) and "halt limit" in reason


def cap_stake(stake: float, stop_distance: float, state: PortfolioState,
              limits: RiskLimits) -> tuple[float, Optional[str]]:
    """Shrink a proposed stake so neither the position cap nor the open-risk
    budget is exceeded. Returns (stake, reason if it was reduced)."""
    caps = {"position cap": state.equity * limits.max_position_pct / 100}
    if stop_distance > 0:
        budget = state.equity * limits.max_open_risk_pct / 100 - state.open_risk
        caps["open-risk budget"] = max(0.0, budget) / stop_distance
    name, cap = min(caps.items(), key=lambda kv: kv[1])
    if stake <= cap:
        return stake, None
    return max(0.0, cap), f"stake {stake:.2f} reduced to {cap:.2f} by the {name}"


# --- manual halt flag (live / paper) ---

def load_halt(path: Path = HALT_STATE_FILE) -> dict:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {"halted": False}


def set_halt(halted: bool, reason: str = "", path: Path = HALT_STATE_FILE) -> dict:
    """Halting records why; resuming records when, so drawdown is measured
    from equity at resumption rather than re-triggering on the old peak."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state = {"halted": halted, "reason": reason, "since": now}
    if not halted:
        state["resumed_at"] = now
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(state, indent=2))
    return state


def limits_summary(state: PortfolioState, limits: RiskLimits) -> dict:
    """Current usage of each limit, for the panel and the watchdog."""
    return {
        "limits": asdict(limits),
        "usage": {
            "drawdown_pct": round(state.drawdown_pct(), 3),
            "day_loss_pct": round(state.day_loss_pct(), 3),
            "open_risk_pct": round(state.open_risk_pct(), 3),
        },
    }
