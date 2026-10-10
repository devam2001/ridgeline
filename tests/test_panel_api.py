"""Panel API: input validation, the dry-run safety guard, and the kill switch."""

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
