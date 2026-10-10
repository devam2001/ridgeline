# Control panel: paper trading (dummy money), fast historical replay, and
# research results. Run with start-panel.ps1, then open http://127.0.0.1:8090

import re
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from panel.bot import MONITOR_MODE, PaperBot, PaperBotError
from panel.common import USERDIR, list_strategies, load_json
from panel.replay import REPLAY_DIR, BacktestError, ReplayJobs, SessionReplays
from ridgeline import audit
from ridgeline.reconcile import session_info
from ridgeline.report import build_tearsheet, load_trades_backtest, load_trades_db, session_balance

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="ridgeline panel")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

bot = PaperBot()
replays = ReplayJobs()
session_replays = SessionReplays()


class PaperStart(BaseModel):
    balance: float = Field(gt=0, le=10_000_000)
    strategy: str


class PaperReset(BaseModel):
    balance: float = Field(gt=0, le=10_000_000)
    strategy: Optional[str] = None


class FlattenRequest(BaseModel):
    confirm: str


class ReplayRequest(BaseModel):
    strategy: str
    balance: float = Field(gt=0, le=10_000_000)
    start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str = Field(pattern=r"^(\d{4}-\d{2}-\d{2})?$")
    max_open_trades: Optional[int] = Field(default=None, ge=1, le=50)


def _check_strategy(name: Optional[str]) -> None:
    if name is not None and name not in list_strategies():
        raise HTTPException(400, f"Unknown strategy {name!r}")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/strategies")
def strategies():
    return list_strategies()


@app.get("/api/paper/status")
def paper_status():
    return bot.status()


@app.get("/api/paper/sessions")
def paper_sessions():
    return bot.sessions()


@app.post("/api/paper/start")
def paper_start(req: PaperStart):
    _check_strategy(req.strategy)
    try:
        return bot.start(req.balance, req.strategy)
    except PaperBotError as e:
        raise HTTPException(409, str(e))


@app.post("/api/paper/stop")
def paper_stop():
    try:
        return bot.stop()
    except PaperBotError as e:
        raise HTTPException(409, str(e))


@app.post("/api/paper/reset")
def paper_reset(req: PaperReset):
    _check_strategy(req.strategy)
    try:
        return bot.reset(req.balance, req.strategy)
    except PaperBotError as e:
        raise HTTPException(409, str(e))


@app.get("/api/mode")
def mode():
    return {"monitor": MONITOR_MODE}


@app.get("/api/risk")
def risk_status():
    try:
        return bot.risk_status()
    except PaperBotError as e:
        raise HTTPException(503, str(e))


@app.post("/api/bot/pause")
def bot_pause():
    try:
        return bot.pause()
    except PaperBotError as e:
        raise HTTPException(409, str(e))


@app.post("/api/bot/resume")
def bot_resume():
    try:
        return bot.resume()
    except PaperBotError as e:
        raise HTTPException(409, str(e))


@app.post("/api/bot/flatten")
def bot_flatten(req: FlattenRequest):
    if req.confirm != "FLATTEN":
        raise HTTPException(400, 'Type FLATTEN to confirm closing every position.')
    try:
        return bot.flatten()
    except PaperBotError as e:
        raise HTTPException(409, str(e))


def _valid_id(value: str) -> str:
    if not re.fullmatch(r"[\w-]+", value):
        raise HTTPException(400, "Invalid id")
    return value


def _valid_month(month: Optional[str]) -> Optional[str]:
    if month and not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(400, "month must be YYYY-MM")
    return month or None


def _session_db(session_id: str) -> Path:
    db = USERDIR / "paper" / f"{_valid_id(session_id)}.sqlite"
    if not db.exists():
        raise HTTPException(404, "No such paper session")
    return db


@app.post("/api/paper/sessions/{session_id}/reconcile")
def reconcile_start(session_id: str):
    db = _session_db(session_id)
    try:
        return session_replays.submit(session_info(db))
    except (ValueError, BacktestError) as e:
        raise HTTPException(409, str(e))


@app.get("/api/paper/sessions/{session_id}/reconcile")
def reconcile_get(session_id: str):
    return session_replays.get(session_id, _session_db(session_id)) or {"status": "none"}


@app.get("/api/report", response_class=HTMLResponse)
def paper_report(session: str, month: Optional[str] = None):
    db = _session_db(session)
    try:
        trades = load_trades_db(db)
        return build_tearsheet(trades, session_balance(db), title="Ridgeline - paper trading report",
                               source_label=f"session {session}", month=_valid_month(month), mode="paper")
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.get("/api/replay/{job_id}/report", response_class=HTMLResponse)
def replay_report(job_id: str):
    directory = REPLAY_DIR / _valid_id(job_id)
    if not (directory / "job.json").exists():
        raise HTTPException(404, "No such replay")
    try:
        trades, balance = load_trades_backtest(directory)
        job = replays.get(job_id) or {}
        return build_tearsheet(trades, balance, title=f"Ridgeline - {job.get('strategy', 'replay')} replay",
                               source_label=f"replay {job_id}", mode="backtest")
    except (ValueError, KeyError, StopIteration) as e:
        raise HTTPException(409, f"No report for this replay: {e}")


@app.get("/api/audit")
def audit_log(limit: int = 100):
    ok, problem = audit.verify()
    return {"chain_ok": ok, "problem": problem, "events": audit.tail(min(max(limit, 1), 1000))}


@app.post("/api/replay")
def replay_start(req: ReplayRequest):
    _check_strategy(req.strategy)
    job_id = replays.submit(req.strategy, req.balance, req.start, req.end, req.max_open_trades)
    return {"id": job_id}


@app.get("/api/replay/{job_id}")
def replay_get(job_id: str):
    job = replays.get(job_id)
    if not job:
        raise HTTPException(404, "No such replay")
    return job


@app.get("/api/replays")
def replay_history():
    return replays.history()


def _latest_reconciliations() -> dict:
    """Newest paper-vs-replay result per strategy (panel sessions and the
    Docker bot's database), for the last go-live gate."""
    out = {}
    for path in [*(USERDIR / "paper").glob("*.reconcile.json"), *USERDIR.glob("*.reconcile.json")]:
        r = load_json(path, None) or {}
        if r.get("status") != "done":
            continue
        strategy = r["session"]["strategy"]
        if strategy not in out or r["generated"] > out[strategy]["generated"]:
            out[strategy] = {"session_id": r["session"]["session_id"], "generated": r["generated"],
                             "days": r["period"]["days"], "close_to_replay": r["close_to_replay"],
                             "passed": sum(c["pass"] for c in r["checks"]), "checks": len(r["checks"])}
    return out


@app.get("/api/research")
def research():
    report = load_json(USERDIR / "research" / "latest.json", None) or {}
    if report:
        report["paper_checks"] = _latest_reconciliations()
    return report
