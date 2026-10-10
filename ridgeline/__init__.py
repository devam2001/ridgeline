"""Shared operational code for ridgeline: risk limits, audit trail, reporting, alerts."""

from pathlib import Path

# Keep in step with pyproject.toml; release tags are v<version>.
__version__ = "0.1.0"

ROOT = Path(__file__).resolve().parent.parent
USERDIR = ROOT / "user_data"
