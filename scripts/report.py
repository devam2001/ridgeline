"""Generate an investor-style tear sheet (self-contained HTML).

Usage (from the project root, with the venv Python):
    python scripts/report.py --db user_data/paper/<session>.sqlite [--month 2026-10]
    python scripts/report.py --backtest user_data/replays/<job-id> [--month 2026-03]
    python scripts/report.py --db tradesv3.sqlite --live --balance 5000

Writes reports/<name>.html. Open it in a browser; Print -> Save as PDF for a PDF.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ridgeline.report import (  # noqa: E402
    build_tearsheet,
    load_trades_backtest,
    load_trades_db,
    session_balance,
)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--db", type=Path, help="paper or live Freqtrade SQLite database")
    src.add_argument("--backtest", type=Path, help="backtest/replay result directory")
    ap.add_argument("--month", help="report as of the end of this month (YYYY-MM)")
    ap.add_argument("--balance", type=float, help="starting balance (default: from the session/backtest)")
    ap.add_argument("--live", action="store_true", help="label as live trading instead of paper")
    ap.add_argument("--title", default="Ridgeline - performance report")
    ap.add_argument("-o", "--out", type=Path, default=ROOT / "reports")
    args = ap.parse_args()

    if args.db:
        trades = load_trades_db(args.db)
        balance = args.balance or session_balance(args.db)
        label, name = f"session {args.db.stem}", args.db.stem
    else:
        trades, balance = load_trades_backtest(args.backtest)
        balance = args.balance or balance
        label, name = f"backtest {args.backtest.name}", args.backtest.name

    page = build_tearsheet(trades, balance, title=args.title, source_label=label,
                           month=args.month, mode="live" if args.live else "paper" if args.db else "backtest")
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"{name}{'-' + args.month if args.month else ''}.html"
    path.write_text(page, encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
