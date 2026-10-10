"""Download or refresh market data (README, "Data").

1. Daily candles for every Binance USDT spot pair. Point-in-time research
   picks each window's pairs from the whole market, not today's favourites.
2. 1h/4h/1d candles for every pair the research windows use, and
   config.json's live pair list set to today's top 25 by volume
   (`research.py universe --download --write-config`).

Run it monthly. The first run downloads several GB; later runs only top up.

Usage (from the project root, with the venv Python):
    python scripts/update_data.py
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from panel.common import FREQTRADE, USERDIR, config_args  # noqa: E402

SINCE = "20220601"


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("\n>", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, cwd=ROOT, check=True, **kw)


def main() -> None:
    pairs_file = USERDIR / "all_usdt.json"
    listed = run([*FREQTRADE, "list-pairs", *config_args(), "--quote", "USDT", "--trading-mode", "spot",
                  "--print-json"], stdout=subprocess.PIPE, text=True)
    pairs_file.write_text(listed.stdout)
    run([*FREQTRADE, "download-data", *config_args(), "--pairs-file", str(pairs_file),
         "--timeframes", "1d", "--timerange", f"{SINCE}-"])
    run([sys.executable, str(ROOT / "scripts" / "research.py"), "universe", "--download", "--write-config"])
    print("\nDone. Restart any running paper session to pick up the new pair list.")


if __name__ == "__main__":
    main()
