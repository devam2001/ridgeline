import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]


def make_ohlcv(n: int = 2000, freq: str = "4h", start: str = "2023-01-01", seed: int = 0,
               regimes=((0.0008, 0.015), (-0.0006, 0.02), (0.0004, 0.01)),
               price: float = 100.0, volume: float = 1000.0) -> pd.DataFrame:
    """Random-walk candles moving through trend / crash / chop regimes."""
    rng = np.random.default_rng(seed)
    sizes = [n // len(regimes)] * len(regimes)
    sizes[-1] += n - sum(sizes)
    rets = np.concatenate([rng.normal(mu, sd, k) for (mu, sd), k in zip(regimes, sizes)])
    close = price * np.exp(np.cumsum(rets))
    open_ = np.r_[price, close[:-1]]
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.01, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.01, n))
    return pd.DataFrame({
        "date": pd.date_range(start, periods=n, freq=freq, tz="UTC"),
        "open": open_, "high": high, "low": low, "close": close,
        "volume": volume * rng.uniform(0.5, 1.5, n),
    })


@pytest.fixture
def ohlcv():
    return make_ohlcv


def load_strategy(name: str):
    """Import a strategy file and instantiate it outside Freqtrade's bot loop."""
    from freqtrade.enums import CandleType, RunMode

    path = ROOT / "user_data" / "strategies" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_strategy_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = {"stake_currency": "USDT", "dry_run_wallet": 1000, "runmode": RunMode.BACKTEST,
              "trading_mode": "spot", "candle_type_def": CandleType.SPOT}
    return getattr(module, name)(config)


@pytest.fixture
def strategy():
    return load_strategy


@pytest.fixture
def audit_dir(tmp_path, monkeypatch):
    from ridgeline import audit

    monkeypatch.setattr(audit, "AUDIT_DIR", tmp_path / "audit")
    return tmp_path / "audit"
