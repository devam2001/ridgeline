# Live paper trading ("dummy money"): start/stop/reset a dry-run Freqtrade
# bot with a chosen fake starting balance. Each session gets its own SQLite
# database, so resetting the wallet is just starting a new session - old
# sessions are kept for comparison.
#
# Safety: every bot started here gets a session config, loaded LAST so it
# wins, with dry_run forced on and exchange keys blanked, and the resolved
# config is checked before launch. This module cannot start a live bot.
#
# Risk controls (pause / resume / flatten) talk to whichever bot answers on
# the API port - panel-managed, started by hand, or the Docker `bot` service -
# and every control action is written to the audit log.
#
# PANEL_MODE=monitor (used in Docker) disables process start/stop/reset: the
# container runtime owns the bot process there. RIDGELINE_BOT_URL overrides
# the bot's API address.

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
from ridgeline import audit
from ridgeline.reconcile import strategy_fingerprint
from ridgeline.risk import RiskLimits, build_state, limits_summary, load_halt, set_halt

MONITOR_MODE = os.environ.get("PANEL_MODE", "").lower() == "monitor"

PAPER_DIR = USERDIR / "paper"
STATE_FILE = PAPER_DIR / "state.json"


class PaperBotError(RuntimeError):
    pass


def _api_settings() -> tuple[str, tuple[str, str]]:
    api = merged_config().get("api_server", {})
    host = api.get("listen_ip_address", "127.0.0.1")
    port = api.get("listen_port", 8080)
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    url = os.environ.get("RIDGELINE_BOT_URL") or f"http://{host}:{port}"
    return url.rstrip("/"), (api.get("username", ""), api.get("password", ""))


def _port_open(url: str) -> bool:
    host, port = url.split("//")[1].split("/")[0].split(":")
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
        if MONITOR_MODE:
            raise PaperBotError("Process control is disabled in monitor mode (Docker manages the bot).")
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
        # What this session runs, for reconciling it with a replay later:
        # config.json's pair list changes monthly, and code can change mid-session.
        cfg = merged_config()
        (PAPER_DIR / f"{session_id}.meta.json").write_text(json.dumps({
            "session_id": session_id,
            "strategy": strategy,
            "balance": balance,
            "pairs": cfg.get("exchange", {}).get("pair_whitelist", []),
            "max_open_trades": cfg.get("max_open_trades"),
            "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "code": strategy_fingerprint(strategy),
        }, indent=2))

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
        audit.append("paper_session_started", "panel",
                     {"session_id": session_id, "strategy": strategy, "balance": balance})
        return state

    def _verify_dry_run(self, args: list[str]) -> None:
        out = subprocess.run([*FREQTRADE, "show-config", *args], cwd=ROOT,
                             capture_output=True, text=True, errors="replace")
        if '"dry_run": true' not in out.stdout:
            raise PaperBotError("Refusing to start: resolved config is not dry_run=true.\n"
                                + out.stderr[-1500:])

    def stop(self) -> dict:
        if MONITOR_MODE:
            raise PaperBotError("Process control is disabled in monitor mode (Docker manages the bot).")
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
        audit.append("paper_session_stopped", "panel", {"session_id": state.get("session_id")})
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

    # --- risk controls (any bot reachable on the API port) ---

    def _call(self, method: str, path: str, **kw):
        try:
            r = httpx.request(method, f"{self.base_url}/api/v1/{path}", auth=self.auth, timeout=20, **kw)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as e:
            raise PaperBotError(f"Bot API refused {path}: HTTP {e.response.status_code}") from e
        except httpx.HTTPError as e:
            raise PaperBotError(f"Bot API not reachable ({type(e).__name__})") from e

    def pause(self) -> dict:
        """Stop opening new trades; open trades keep their stops and exits."""
        result = self._call("POST", "stopentry")
        audit.append("entries_paused", "panel", {"bot_reply": result})
        return result

    def resume(self) -> dict:
        """Resume trading and clear a drawdown halt (after human review)."""
        was = load_halt()
        result = self._call("POST", "start")
        set_halt(False, reason="resumed from panel")
        audit.append("trading_resumed", "panel", {"bot_reply": result, "cleared_halt": bool(was.get("halted"))})
        return result

    def flatten(self, wait_s: float = 60) -> dict:
        """Kill switch: stop entries, market-close every position, stop the bot."""
        steps = {"stopentry": self._call("POST", "stopentry")}
        open_before = len(self._call("GET", "status"))
        steps["forceexit"] = self._call("POST", "forceexit", json={"tradeid": "all"})
        deadline = time.monotonic() + wait_s
        remaining = open_before
        while time.monotonic() < deadline:
            remaining = len(self._call("GET", "status"))
            if remaining == 0:
                break
            time.sleep(1)
        steps["stop"] = self._call("POST", "stop")
        result = {"closed": open_before - remaining, "still_open": remaining, "steps": steps}
        audit.append("flatten", "panel", result)
        if remaining:
            raise PaperBotError(f"Flatten: {remaining} position(s) still open after {wait_s:.0f}s - "
                                "check the exchange/FreqUI and close them manually.")
        return result

    def risk_status(self) -> dict:
        """Same portfolio numbers the strategy enforces, rebuilt from the API."""
        cfg = self._call("GET", "show_config")
        # Ask the bot itself (a bot started elsewhere may not match local
        # state.json). starting_capital is what the strategy's RiskMixin uses
        # too (Wallets.get_starting_balance, net of tradable_balance_ratio).
        start = float(self._call("GET", "balance").get("starting_capital") or 0)
        closed = [
            (datetime.fromtimestamp(t["close_timestamp"] / 1000, timezone.utc), t.get("profit_abs") or 0.0)
            for t in self._call("GET", "trades?limit=500").get("trades", [])
            if not t.get("is_open") and t.get("close_timestamp")
        ]
        open_positions = [
            (t["open_rate"], t.get("stop_loss_abs") or 0.0, t["amount"], t.get("profit_abs") or 0.0)
            for t in self._call("GET", "status")
        ]
        halt = load_halt()
        reset = datetime.fromisoformat(halt["resumed_at"]) if halt.get("resumed_at") else None
        state = build_state(start, closed, open_positions, datetime.now(timezone.utc), reset)
        limits = RiskLimits.from_config(merged_config())
        return {**limits_summary(state, limits), "equity": round(state.equity, 2),
                "halt": halt, "bot_state": cfg.get("state")}

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
            meta = load_json(db.with_suffix(".meta.json"), {}) or {}
            rows.append({"session_id": db.stem, "balance": cfg.get("dry_run_wallet"),
                         "strategy": meta.get("strategy"), "size_kb": round(db.stat().st_size / 1024, 1)})
        return rows
