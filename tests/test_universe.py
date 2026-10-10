"""Point-in-time pair selection must only use data that existed at the time."""

import numpy as np
import pandas as pd
import pytest

import research


def write_daily(datadir, pair, start, end, price, volume):
    dates = pd.date_range(start, end, freq="D", tz="UTC")
    price = np.broadcast_to(np.asarray(price, dtype=float), len(dates)) if np.ndim(price) == 0 else np.asarray(price)
    volume = np.broadcast_to(np.asarray(volume, dtype=float), len(dates)) if np.ndim(volume) == 0 else np.asarray(volume)
    df = pd.DataFrame({"date": dates, "open": price, "high": price, "low": price, "close": price, "volume": volume})
    df.to_feather(datadir / f"{pair}_USDT-1d.feather")


@pytest.fixture
def datadir(tmp_path):
    research.universe_at.cache_clear()
    d = tmp_path / "data"
    d.mkdir()
    write_daily(d, "BIG", "2022-01-01", "2024-12-31", 10.0, 1e6)
    # tiny volume before 2024-06-01, enormous after: a pump in the "future"
    dates = pd.date_range("2022-01-01", "2024-12-31", freq="D", tz="UTC")
    write_daily(d, "PUMP", "2022-01-01", "2024-12-31", 5.0, np.where(dates < "2024-06-01", 10.0, 1e12))
    write_daily(d, "STABLEX", "2022-01-01", "2024-12-31", 1.0005, 1e9)    # stablecoin by behaviour
    write_daily(d, "USDC", "2022-01-01", "2024-12-31", 1.0, 1e9)          # stablecoin by name
    write_daily(d, "NEW", "2024-05-01", "2024-12-31", 3.0, 1e9)           # listed too recently
    write_daily(d, "DEAD", "2022-01-01", "2024-03-01", 2.0, 1e9)          # stopped trading
    return d


def test_ranks_on_past_volume_only(datadir):
    assert research.universe_at("2024-06-01", n=1, datadir=datadir) == ("BIG/USDT",)


def test_future_pump_does_not_leak(datadir):
    universe = research.universe_at("2024-06-01", n=10, datadir=datadir)
    assert universe == ("BIG/USDT", "PUMP/USDT")  # PUMP is eligible but ranked on its small past volume
    later = research.universe_at("2024-09-01", n=1, datadir=datadir)
    assert later == ("PUMP/USDT",)  # once the volume is in the past, it counts


def test_filters(datadir):
    universe = set(research.universe_at("2024-06-01", n=10, datadir=datadir))
    assert "STABLEX/USDT" not in universe  # price pinned at $1
    assert "USDC/USDT" not in universe     # blacklisted
    assert "NEW/USDT" not in universe      # < 210 days of history
    assert "DEAD/USDT" not in universe     # not trading in the lookback window
