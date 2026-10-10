# Live paper trading ("dummy money"): start/stop/reset a dry-run Freqtrade
# bot with a chosen fake starting balance. Each session gets its own SQLite
# database, so resetting the wallet is just starting a new session - old
# sessions are kept for comparison.
#
# Safety: every bot started here gets a session config, loaded LAST so it
# wins, with dry_run forced on and exchange keys blanked, and the resolved
# config is checked before launch. This module cannot start a live bot.

import json
import os
import signal
import socket
import subprocess
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
import psutil

from panel.common import FREQTRADE, ROOT, USERDIR, config_args, load_json, merged_config

PAPER_DIR = USERDIR / "paper"
STATE_FILE = PAPER_DIR / "state.json"


class PaperBotError(RuntimeError):
    pass


def _api_settings() -> tuple[str, tuple[str, str]]:
    api = merged_config().get("api_server", {})
    host = api.get("listen_ip_address", "127.0.0.1")
    port = api.get("listen_port", 8080)
    return f"http://{host}:{port}", (api.get("username", ""), api.get("password", ""))


def _port_open(url: str) -> bool:
    host, port = url.split("//")[1].split(":")
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, int(port))) == 0


class PaperBot:
    def __init__(self):
        PAPER_DIR.mkdir(parents=True, exist_ok=True)
        self.base_url, self.auth = _api_settings()

    # --- process state ---

    def _state(self) -> dict:
        return load_json(STATE_FILE, {}) or {}

    def _save_state(self, state: dict) -> None:
        STATE_FILE.write_text(json.dumps(state, indent=2))

    def _our_process(self) -> Optional[psutil.Process]:
        pid = self._state().get("pid")
        if not pid:
            return None
        try:
            proc = psutil.Process(pid)
            # Guard against PID reuse: it must still be our freqtrade command.
            if proc.is_running() and "freqtrade" in " ".join(proc.cmdline()):
                return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return None

    # --- actions ---

    def start(self, balance: float, strategy: str) -> dict:
        if balance <= 0:
            raise PaperBotError("Starting balance must be positive.")
        if self._our_process():
            raise PaperBotError("A paper session is already running. Stop or reset it first.")
        if _port_open(self.base_url):
            raise PaperBotError(
                f"Something else is already listening on {self.base_url} (probably a bot you "
                "started by hand). Stop it first - the panel will not kill processes it didn't start."
            )

        session_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        session_cfg = PAPER_DIR / f"{session_id}.config.json"
        session_cfg.write_text(json.dumps({
            "dry_run": True,
            "dry_run_wallet": balance,
            "db_url": f"sqlite:///user_data/paper/{session_id}.sqlite",
            "bot_name": f"paper-{session_id}",
            "exchange": {"key": "", "secret": ""},
        }, indent=2))
        args = [*config_args(), "-c", str(session_cfg)]
        self._verify_dry_run(args)

        log_path = PAPER_DIR / f"{session_id}.log"
        cmd = [*FREQTRADE, "trade", *args, "--strategy", strategy, "--logfile", str(log_path)]
        popen_kw = dict(cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL)
        # Paper sessions run for weeks: detach the bot so closing the panel
        # (or its console) doesn't kill it. The panel re-attaches via state.json.
        if os.name == "nt":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            try:
                proc = subprocess.Popen(cmd, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **popen_kw)
            except OSError:  # parent job doesn't allow breakaway
                proc = subprocess.Popen(cmd, creationflags=flags, **popen_kw)
        else:
            proc = subprocess.Popen(cmd, start_new_session=True, **popen_kw)
        state = {
            "pid": proc.pid,
            "session_id": session_id,
            "strategy": strategy,
            "balance": balance,
            "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self._save_state(state)
        return state

    def _verify_dry_run(self, args: list[str]) -> None:
        out = subprocess.run([*FREQTRADE, "show-config", *args], cwd=ROOT,
                             capture_output=True, text=True, errors="replace")
        if '"dry_run": true' not in out.stdout:
            raise PaperBotError("Refusing to start: resolved config is not dry_run=true.\n"
                                + out.stderr[-1500:])

    def stop(self) -> dict:
        proc = self._our_process()
        if not proc:
            raise PaperBotError("No panel-managed paper session is running.")
        # Ask the bot to stop trading via its API first, so it isn't mid-way
        # through placing a (simulated) order when the process ends.
        try:
            httpx.post(f"{self.base_url}/api/v1/stop", auth=self.auth, timeout=5)
            time.sleep(1)
        except httpx.HTTPError:
            pass
        # On Windows the venv python.exe is a redirector that spawns the real
        # interpreter, so end the whole tree, not just the PID we launched.
        procs = [proc, *proc.children(recursive=True)]
        for p in procs:
            try:
                if os.name == "nt":
                    p.terminate()
                else:
                    p.send_signal(signal.SIGINT)
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(procs, timeout=15)
        for p in alive:
            p.kill()
        state = self._state()
        state.update(pid=None, stopped=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self._save_state(state)
        return state

    def reset(self, balance: float, strategy: Optional[str] = None) -> dict:
        strategy = strategy or self._state().get("strategy")
        if not strategy:
            raise PaperBotError("No strategy given.")
        if self._our_process():
            self.stop()
            # give the OS a moment to release the API port
            for _ in range(20):
                if not _port_open(self.base_url):
                    break
                time.sleep(0.5)
        return self.start(balance, strategy)

    # --- status ---

    def _api(self, client: httpx.Client, path: str):
        r = client.get(f"{self.base_url}/api/v1/{path}", auth=self.auth, timeout=5)
        r.raise_for_status()
        return r.json()

    def status(self) -> dict:
        state = self._state()
        ours = self._our_process() is not None
        listening = _port_open(self.base_url)
        out = {
            "managed": ours,
            "external": listening and not ours,
            "api_up": False,
            "session": state if ours else None,
            "last_session": state if not ours and state else None,
            "freqtrade_ui": self.base_url,
        }
        if not listening:
            return out
        try:
            with httpx.Client() as c:
                cfg = self._api(c, "show_config")
                out.update(
                    api_up=True,
                    bot={k: cfg.get(k) for k in ("bot_name", "strategy", "dry_run", "state",
                                                 "timeframe", "max_open_trades", "stake_currency")},
                    balance=self._api(c, "balance"),
                    profit=self._api(c, "profit"),
                    open_trades=self._api(c, "status"),
                    trades=self._api(c, "trades?limit=50").get("trades", []),
                )
        except httpx.HTTPStatusError as e:
            out["api_error"] = (
                "Bot is running but rejected the panel's login (it was probably started with "
                "different API credentials)." if e.response.status_code == 401 else str(e)
            )
        except httpx.HTTPError as e:
            out["api_error"] = f"Bot API not reachable yet ({type(e).__name__}). It may still be starting."
        return out

    def sessions(self) -> list[dict]:
        rows = []
        for db in sorted(PAPER_DIR.glob("*.sqlite"), reverse=True):
            cfg = load_json(db.with_suffix(".config.json"), {}) or {}
            rows.append({"session_id": db.stem, "balance": cfg.get("dry_run_wallet"),
                         "size_kb": round(db.stat().st_size / 1024, 1)})
        return rows
