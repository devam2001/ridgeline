"""Watchdog: checks the trading bot every minute and alerts when something is wrong.

Alerts (Telegram if configured, otherwise console) when:
  - the bot's API is unreachable
  - the bot hasn't processed a cycle for --stale-minutes
  - the bot isn't in the "running" state (stopped / entries paused)
  - drawdown, daily loss or open risk is near (>= 80%) or over its limit
Each problem alerts once, then again with "resolved" when it clears; every
alert is also written to the audit log.

Usage (project root, venv Python):
    python scripts/watchdog.py                 # loop forever
    python scripts/watchdog.py --once          # single check, exit 1 if any problem
    python scripts/watchdog.py --test-alert    # send one test message and exit
Set RIDGELINE_BOT_URL to watch a bot that isn't on the configured API address.
"""

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from panel.bot import PaperBot, PaperBotError  # noqa: E402
from panel.common import merged_config  # noqa: E402
from ridgeline import audit, notify  # noqa: E402

NEAR = 0.8  # fraction of a limit that counts as "near"
LIMIT_KEYS = {
    "drawdown_pct": ("max_drawdown_halt_pct", "drawdown from peak"),
    "day_loss_pct": ("daily_loss_limit_pct", "today's loss"),
    "open_risk_pct": ("max_open_risk_pct", "open risk"),
}


def check(bot: PaperBot, stale_minutes: float) -> dict[str, str]:
    """Return {problem_key: message} for everything wrong right now."""
    problems: dict[str, str] = {}
    try:
        httpx.get(f"{bot.base_url}/api/v1/ping", timeout=5).raise_for_status()
    except httpx.HTTPError:
        return {"down": f"Bot API unreachable at {bot.base_url}"}
    try:
        health = bot._call("GET", "health")
        last = health.get("last_process_ts")
        if last:
            age_min = (time.time() - last) / 60
            if age_min > stale_minutes:
                problems["stale"] = f"Bot hasn't completed a cycle for {age_min:.0f} min"
        risk = bot.risk_status()
    except PaperBotError as e:
        return {"api": str(e)}

    if risk.get("bot_state") != "running":
        problems["state"] = f"Bot state is '{risk.get('bot_state')}' (not opening new trades)"
    if risk.get("halt", {}).get("halted"):
        problems["halt"] = f"Trading HALTED: {risk['halt'].get('reason')}"
    for key, (limit_key, label) in LIMIT_KEYS.items():
        value, limit = risk["usage"][key], risk["limits"][limit_key]
        if limit and value >= limit:
            problems[f"{key}:breach"] = f"{label} {value:.2f}% is at/over the {limit:g}% limit"
        elif limit and value >= NEAR * limit:
            problems[f"{key}:near"] = f"{label} {value:.2f}% is close to the {limit:g}% limit"
    return problems


def alert(text: str, config: dict, event: str) -> None:
    notify.send(f"ridgeline watchdog: {text}", config)
    try:
        audit.append(event, actor="watchdog", details={"message": text})
    except Exception as e:  # alerting must not die on an audit problem
        print(f"[watchdog] audit write failed: {e}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interval", type=float, default=60, help="seconds between checks")
    ap.add_argument("--stale-minutes", type=float, default=10)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--test-alert", action="store_true")
    args = ap.parse_args()

    config = merged_config()
    if args.test_alert:
        sent = notify.send("ridgeline watchdog: test alert - notifications are working.", config)
        print("Telegram: sent" if sent else "Telegram not configured or failed - printed to console instead.")
        return

    bot = PaperBot()
    active: dict[str, str] = {}
    print(f"[watchdog] watching {bot.base_url} every {args.interval:.0f}s", flush=True)
    while True:
        problems = check(bot, args.stale_minutes)
        for key, msg in problems.items():
            if key not in active:
                alert(msg, config, "watchdog_alert")
        for key in set(active) - set(problems):
            alert(f"resolved - {active[key]}", config, "watchdog_resolved")
        active = problems
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"[watchdog {stamp}] {'OK' if not problems else '; '.join(problems.values())}", flush=True)
        if args.once:
            sys.exit(1 if problems else 0)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
