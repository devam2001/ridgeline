"""Compare a paper-trading session with a replay of the same weeks.

The last go-live gate: 4+ weeks of paper trading whose results are close to
a replay (backtest) of the same period, with the same strategy, pairs and
balance. This tops up candle data, runs that replay, matches the trades and
checks how close they are.

Usage (from the project root, with the venv Python):
    python scripts/reconcile.py 20261011-010946           a panel session in user_data/paper/
    python scripts/reconcile.py --db user_data/tradesv3.docker.sqlite --log user_data/logs/bot.log
                                                          the Docker bot

What the session ran is read from its files; --strategy, --pairs, --balance,
--start and --end override it. --no-download uses the data already on disk.

Writes <session>.reconcile.json next to the database; the panel's Research
tab shows the newest result per strategy.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from panel.common import USERDIR  # noqa: E402
from panel.replay import reconcile_path, replay_session  # noqa: E402
from ridgeline.reconcile import session_info  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("session", nargs="?", help="panel paper session id (user_data/paper/<id>.sqlite)")
    src.add_argument("--db", type=Path, help="any paper/dry-run Freqtrade SQLite database")
    ap.add_argument("--log", type=Path, help="the bot's log file (default: <db>.log)")
    ap.add_argument("--strategy")
    ap.add_argument("--pairs", nargs="+")
    ap.add_argument("--balance", type=float)
    ap.add_argument("--start", help="UTC, e.g. 2026-10-01 or '2026-10-01 14:00'")
    ap.add_argument("--end", help="UTC (default: the bot's last log write)")
    ap.add_argument("--no-download", action="store_true", help="don't top up candle data first")
    args = ap.parse_args()

    db = args.db or USERDIR / "paper" / f"{args.session}.sqlite"
    if not db.exists():
        sys.exit(f"No database at {db}")
    info = session_info(db, log=args.log, strategy=args.strategy, pairs=args.pairs, balance=args.balance,
                        start=args.start, end=args.end)
    print(f"{info.session_id}: {info.strategy}, {len(info.pairs)} pairs, {info.balance:g} starting balance, "
          f"{info.start:%Y-%m-%d %H:%M} -> {info.end:%Y-%m-%d %H:%M} UTC")
    print("Replaying the same period...")
    result = replay_session(info, download=not args.no_download)
    reconcile_path(db).write_text(json.dumps(result, indent=2, default=str))

    p, r, m, g = result["paper"], result["replay"], result["matching"], result["gap"]
    print(f"\n{'':10}{'trades':>8}{'closed':>8}{'realised':>11}{'return':>9}")
    for name, s in (("paper", p), ("replay", r)):
        print(f"{name:10}{s['trades']:>8}{s['closed']:>8}{s['realised_abs']:>11.2f}{s['return_pct']:>8.2f}%")
    print(f"\nmatched {m['matched']}, paper only {m['paper_only']}, replay only {m['replay_only']}")
    print(f"return gap {g['return_gap_pp']:+.2f} points = matched trades {g['matched_abs']:+.2f} "
          f"+ paper-only {g['paper_only_abs']:+.2f} + replay-only {g['replay_only_abs']:+.2f} (stake currency)")
    for row in result["trades"]:
        print(f"  {row['status']:<12}{row['pair']:<14}{row['paper_open'] or '-':<18}{row['replay_open'] or '-':<18}"
              f"entry {_fmt(row['entry_cost_pct'])}  exit {row['paper_exit'] or '-'} / {row['replay_exit'] or '-'}")
    print()
    for c in result["checks"]:
        print(f"  [{'PASS' if c['pass'] else 'FAIL'}] {c['label']}  (got {c['value']})")
    for w in result["warnings"]:
        print(f"  note: {w}")
    verdict = "CLOSE to the replay" if result["close_to_replay"] else "NOT close to the replay (yet)"
    print(f"\nPaper trading is {verdict}.\nwritten to {reconcile_path(db)}")


def _fmt(cost) -> str:
    return "-" if cost is None else f"{cost:+.3f}%"


if __name__ == "__main__":
    main()
