"""Create user_data/config.private.json with freshly generated API secrets.

Run once on a new machine/server (or in CI). Never overwrites an existing
file. Afterwards, add Telegram and (only when going live) exchange keys by
editing the file - it is gitignored and must never be committed.

    python scripts/init_private_config.py [--bind-all]

--bind-all makes the bot's API listen on 0.0.0.0, which Docker needs so the
panel/watchdog containers can reach it (docker-compose still only publishes
ports on 127.0.0.1).
"""

import argparse
import json
import secrets
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "user_data" / "config.private.json"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bind-all", action="store_true")
    args = ap.parse_args()
    if PATH.exists():
        print(f"{PATH} already exists - left unchanged.")
        return
    config = {
        "exchange": {"key": "", "secret": ""},
        "telegram": {"enabled": False, "token": "", "chat_id": ""},
        "api_server": {
            "username": "freqtrader",
            "password": secrets.token_urlsafe(18),
            "jwt_secret_key": secrets.token_hex(32),
            "ws_token": secrets.token_urlsafe(24),
        },
    }
    if args.bind_all:
        config["api_server"]["listen_ip_address"] = "0.0.0.0"
    PATH.write_text(json.dumps(config, indent=4))
    print(f"Created {PATH} (API username: freqtrader; password is in the file).")


if __name__ == "__main__":
    main()
