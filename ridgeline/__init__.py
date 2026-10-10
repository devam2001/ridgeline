"""Shared operational code for ridgeline: risk limits, audit trail, reporting, alerts."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USERDIR = ROOT / "user_data"
