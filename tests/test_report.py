import pandas as pd
import pytest

from ridgeline.report import build_tearsheet, daily_equity, risk_stats, trade_stats


def trades():
    t = pd.Timestamp
    return pd.DataFrame([
        {"pair": "BTC/USDT", "open_date": t("2026-01-02", tz="UTC"), "close_date": t("2026-01-05", tz="UTC"),
         "stake": 300.0, "profit_abs": 30.0, "exit_reason": "trailing_stop_loss", "is_open": False},
        {"pair": "ETH/USDT", "open_date": t("2026-01-10", tz="UTC"), "close_date": t("2026-01-12", tz="UTC"),
         "stake": 200.0, "profit_abs": -10.0, "exit_reason": "stop_loss", "is_open": False},
        {"pair": "SOL/USDT", "open_date": t("2026-01-28", tz="UTC"), "close_date": t("2026-02-03", tz="UTC"),
         "stake": 250.0, "profit_abs": 25.0, "exit_reason": "exit_signal", "is_open": False},
    ])


def test_equity_and_stats():
    eq = daily_equity(trades(), 1000.0, pd.Timestamp("2026-01-02", tz="UTC"), pd.Timestamp("2026-02-10", tz="UTC"))
    assert eq.iloc[0] == 1000.0 and eq.iloc[-1] == pytest.approx(1045.0)
    stats = risk_stats(eq)
    assert stats["total_return"] == pytest.approx(0.045)
    assert stats["max_drawdown"] == pytest.approx(10 / 1030)
    ts = trade_stats(trades(), pd.Timestamp("2026-01-02", tz="UTC"), pd.Timestamp("2026-02-10", tz="UTC"))
    assert ts["trades"] == 3 and ts["win_rate"] == pytest.approx(2 / 3) and ts["profit_factor"] == pytest.approx(5.5)


def test_tearsheet_contents():
    page = build_tearsheet(trades(), 1000.0, title="Test", source_label="unit test", mode="backtest")
    assert "+4.5%" in page and "BACKTEST" in page and "solicitation or financial advice" in page
    assert "<svg" in page and "SOL/USDT" in page


def test_month_view_treats_later_closes_as_open():
    page = build_tearsheet(trades(), 1000.0, title="Test", source_label="unit test", month="2026-01")
    # SOL closed in February, so at the end of January it was still open
    assert "Open positions at report date</td><td>1" in page
    assert "+2.0%" in page  # only BTC (+30) and ETH (-10) realised by month end


def test_empty_source_is_an_error():
    with pytest.raises(ValueError):
        build_tearsheet(trades().iloc[0:0], 1000.0, title="t", source_label="s")
