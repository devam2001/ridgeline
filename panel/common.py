# Shared paths and helpers for the control panel and research scripts.

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USERDIR = ROOT / "user_data"
DATADIR = USERDIR / "data" / "binance"
BASE_CONFIG = USERDIR / "config.json"
PRIVATE_CONFIG = USERDIR / "config.private.json"

# Run freqtrade as a module of the current (venv) Python rather than via the
# freqtrade.exe shim: the shim spawns a second process, so stopping it can
# leave the real bot running.
FREQTRADE = [sys.executable, "-m", "freqtrade"]


def config_args() -> list[str]:
    # config.json pulls in config.private.json itself via add_config_files.
    return ["--userdir", str(USERDIR), "-c", str(BASE_CONFIG)]


def load_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def merged_config() -> dict:
    """Base config with the private overrides applied (shallow per-section)."""
    cfg = load_json(BASE_CONFIG, {})
    for section, values in load_json(PRIVATE_CONFIG, {}).items():
        if isinstance(values, dict):
            cfg.setdefault(section, {}).update(values)
        else:
            cfg[section] = values
    return cfg


def list_strategies() -> list[str]:
    return sorted(
        p.stem for p in (USERDIR / "strategies").glob("*.py") if not p.name.startswith("_")
    )
