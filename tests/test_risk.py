from datetime import datetime, timedelta, timezone

import pytest

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

LIMITS = RiskLimits()  # 35% position, 4.5% open risk, 3% daily loss, 15% drawdown


def state(equity=1000.0, peak=1000.0, day_start=1000.0, open_risk=0.0):
    return PortfolioState(equity=equity, peak_equity=peak, day_start_equity=day_start, open_risk=open_risk)


def test_healthy_portfolio_may_enter():
    assert check_entry(state(), LIMITS) == (True, None)


@pytest.mark.parametrize("equity,allowed", [(851.0, True), (850.0, False), (800.0, False)])
def test_drawdown_halt_boundary(equity, allowed):
    ok, reason = check_entry(state(equity=equity, day_start=equity), LIMITS)
    assert ok is allowed
    if not allowed:
        assert is_drawdown_breach(reason)


@pytest.mark.parametrize("equity,allowed", [(971.0, True), (970.0, False)])
def test_daily_loss_boundary(equity, allowed):
    ok, reason = check_entry(state(equity=equity), LIMITS)
    assert ok is allowed
    if not allowed:
        assert "daily limit" in reason and not is_drawdown_breach(reason)


@pytest.mark.parametrize("open_risk,allowed", [(44.9, True), (45.0, False)])
def test_open_risk_boundary(open_risk, allowed):
    assert check_entry(state(open_risk=open_risk), LIMITS)[0] is allowed


def test_manual_halt_blocks_everything():
    ok, reason = check_entry(state(), LIMITS, halted=True)
    assert not ok and "resume manually" in reason


def test_cap_stake_position_cap():
    stake, reason = cap_stake(600.0, 0.02, state(), LIMITS)
    assert stake == pytest.approx(350.0) and "position cap" in reason


def test_cap_stake_open_risk_budget():
    # 40 of the 45 risk budget is used; 5 left at a 5% stop -> at most 100 stake
    stake, reason = cap_stake(300.0, 0.05, state(open_risk=40.0), LIMITS)
    assert stake == pytest.approx(100.0) and "open-risk budget" in reason


def test_cap_stake_leaves_small_stakes_alone():
    assert cap_stake(100.0, 0.05, state(), LIMITS) == (100.0, None)


def test_limits_from_config_override_and_ignore_unknown():
    limits = RiskLimits.from_config({"ridgeline_risk": {"daily_loss_limit_pct": "2", "nonsense": 1}})
    assert limits.daily_loss_limit_pct == 2.0 and limits.max_position_pct == 35.0


def test_build_state_midnight_peak_and_open_risk():
    now = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
    closed = [
        (now - timedelta(days=2), 100.0),   # peak 1100
        (now - timedelta(days=1), -50.0),   # 1050 at midnight
        (now - timedelta(hours=3), -20.0),  # today: 1030
    ]
    open_positions = [
        (100.0, 95.0, 2.0, -4.0),   # risk 10
        (50.0, 55.0, 1.0, 6.0),     # stop above entry: risk 0
    ]
    st = build_state(1000.0, closed, open_positions, now)
    assert st.equity == pytest.approx(1032.0)
    assert st.peak_equity == pytest.approx(1100.0)
    assert st.day_start_equity == pytest.approx(1050.0)
    assert st.open_risk == pytest.approx(10.0)


def test_build_state_daily_loss_resets_at_utc_midnight():
    loss_time = datetime(2026, 10, 10, 23, 30, tzinfo=timezone.utc)
    before_midnight = build_state(1000.0, [(loss_time, -40.0)], [], loss_time + timedelta(minutes=10))
    after_midnight = build_state(1000.0, [(loss_time, -40.0)], [], loss_time + timedelta(hours=1))
    assert before_midnight.day_loss_pct() == pytest.approx(4.0)
    assert after_midnight.day_loss_pct() == 0.0


def test_peak_reset_after_reviewed_halt():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    closed = [(t0, 200.0), (t0 + timedelta(days=1), -400.0)]  # peak 1200, now 800
    assert build_state(1000.0, closed, [], t0 + timedelta(days=5)).drawdown_pct() == pytest.approx(33.33, 0.01)
    reset = build_state(1000.0, closed, [], t0 + timedelta(days=5), peak_reset=t0 + timedelta(days=2))
    assert reset.drawdown_pct() == 0.0


def test_halt_flag_roundtrip(tmp_path):
    path = tmp_path / "risk_state.json"
    assert load_halt(path) == {"halted": False}
    set_halt(True, "drawdown", path)
    assert load_halt(path)["halted"] is True
    resumed = set_halt(False, "reviewed", path)
    assert resumed["halted"] is False and "resumed_at" in load_halt(path)
