"""Panel API: input validation, the dry-run safety guard, and the kill switch."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import panel.app as app_mod
import panel.bot as bot_mod


@pytest.fixture
def client(tmp_path, monkeypatch, audit_dir):
    monkeypatch.setattr(bot_mod, "PAPER_DIR", tmp_path / "paper")
    monkeypatch.setattr(bot_mod, "STATE_FILE", tmp_path / "paper" / "state.json")
    (tmp_path / "paper").mkdir()
    return TestClient(app_mod.app)


def test_lists_strategies(client):
    assert {"TrendRider4h", "EmaRsiMomentum", "TrendDipBuyer1h"} <= set(client.get("/api/strategies").json())


def test_replay_rejects_bad_dates(client):
    r = client.post("/api/replay", json={"strategy": "TrendRider4h", "balance": 1000, "start": "2025/06/01", "end": ""})
    assert r.status_code == 422


def test_unknown_strategy_rejected(client):
    r = client.post("/api/paper/start", json={"balance": 1000, "strategy": "../../evil"})
    assert r.status_code == 400


def test_refuses_to_start_a_live_bot(client, monkeypatch):
    """If the resolved config isn't dry-run, nothing is launched."""
    monkeypatch.setattr(bot_mod, "_port_open", lambda url: False)
    monkeypatch.setattr(bot_mod.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(stdout='{"dry_run": false}', stderr=""))

    def no_launch(*a, **k):
        raise AssertionError("a bot process was launched")

    monkeypatch.setattr(bot_mod.subprocess, "Popen", no_launch)
    r = client.post("/api/paper/start", json={"balance": 1000, "strategy": "TrendRider4h"})
    assert r.status_code == 409 and "Refusing" in r.json()["detail"]


def test_flatten_needs_typed_confirmation(client):
    assert client.post("/api/bot/flatten", json={"confirm": "yes"}).status_code == 400


def test_flatten_sequence_and_audit(client, monkeypatch):
    calls = []

    def fake_call(method, path, **kw):
        calls.append(path)
        return [] if path == "status" else {"status": "ok"}

    monkeypatch.setattr(app_mod.bot, "_call", fake_call)
    r = client.post("/api/bot/flatten", json={"confirm": "FLATTEN"})
    assert r.status_code == 200
    assert calls == ["stopentry", "status", "forceexit", "status", "stop"]
    audit = client.get("/api/audit").json()
    assert audit["chain_ok"] and audit["events"][0]["event"] == "flatten"


def test_report_rejects_path_tricks(client):
    assert client.get("/api/report", params={"session": "../config"}).status_code == 400


# ---------------------------------------------------------------- paper vs replay

@pytest.fixture
def userdir(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "USERDIR", tmp_path)
    (tmp_path / "paper").mkdir(exist_ok=True)
    return tmp_path


def make_session(userdir, session_id, meta=None):
    import sqlite3

    with sqlite3.connect(userdir / "paper" / f"{session_id}.sqlite") as con:
        con.execute("CREATE TABLE trades (pair TEXT, strategy TEXT)")
    con.close()
    if meta:
        (userdir / "paper" / f"{session_id}.meta.json").write_text(json.dumps(meta))


def test_reconcile_rejects_bad_and_unknown_sessions(client, userdir):
    assert client.post("/api/paper/sessions/bad.id/reconcile").status_code == 400
    assert client.get("/api/paper/sessions/20990101-000000/reconcile").status_code == 404


def test_reconcile_needs_to_know_the_pairs(client, userdir):
    make_session(userdir, "s1")
    r = client.post("/api/paper/sessions/s1/reconcile")
    assert r.status_code == 409 and "which" in r.json()["detail"]


def test_reconcile_submits_what_the_session_ran(client, userdir, monkeypatch):
    make_session(userdir, "s2", {"strategy": "TrendRider4h", "pairs": ["BTC/USDT"], "balance": 300,
                                 "started": "2026-09-01T00:00:00+00:00"})
    submitted = []
    monkeypatch.setattr(app_mod.session_replays, "submit",
                        lambda info: submitted.append(info) or {"status": "running", "session_id": info.session_id})
    assert client.post("/api/paper/sessions/s2/reconcile").json()["status"] == "running"
    assert (submitted[0].strategy, submitted[0].pairs, submitted[0].balance) == ("TrendRider4h", ["BTC/USDT"], 300)
    assert client.get("/api/paper/sessions/s2/reconcile").json() == {"status": "none"}


def test_research_shows_newest_reconciliation_per_strategy(client, userdir):
    (userdir / "research").mkdir()
    (userdir / "research" / "latest.json").write_text(json.dumps({"strategies": {}}))

    def result(session_id, generated, ok, status="done"):
        return {"status": status, "generated": generated, "close_to_replay": ok,
                "session": {"session_id": session_id, "strategy": "TrendRider4h"}, "period": {"days": 30.0},
                "checks": [{"pass": ok}, {"pass": True}]}

    for sid, gen, ok, status in [("old", "2026-10-01T00:00:00+00:00", False, "done"),
                                 ("new", "2026-10-09T00:00:00+00:00", True, "done"),
                                 ("broken", "2026-10-10T00:00:00+00:00", True, "error")]:
        (userdir / "paper" / f"{sid}.reconcile.json").write_text(json.dumps(result(sid, gen, ok, status)))
    checks = client.get("/api/research").json()["paper_checks"]
    assert checks["TrendRider4h"]["session_id"] == "new" and checks["TrendRider4h"]["passed"] == 2


def test_reconcile_refuses_until_the_first_candle_closes(client, userdir):
    from datetime import datetime, timezone

    make_session(userdir, "s3", {"strategy": "TrendRider4h", "pairs": ["BTC/USDT"], "balance": 300,
                                 "started": datetime.now(timezone.utc).isoformat()})
    r = client.post("/api/paper/sessions/s3/reconcile")
    assert r.status_code == 409 and "Nothing to replay yet" in r.json()["detail"]
    assert not (userdir / "paper" / "s3.reconcile.json").exists()
