import json

from ridgeline import audit


def test_append_and_verify(audit_dir):
    for i in range(3):
        audit.append("event", "test", {"i": i})
    assert audit.verify() == (True, None)
    events = audit.tail(10)
    assert [e["details"]["i"] for e in events] == [2, 1, 0]
    assert events[-1]["prev_hash"] == audit.GENESIS


def _lines(audit_dir):
    path = next(audit_dir.glob("audit-*.jsonl"))
    return path, path.read_text().splitlines()


def test_edited_record_is_detected(audit_dir):
    for i in range(3):
        audit.append("event", "test", {"amount": i})
    path, lines = _lines(audit_dir)
    record = json.loads(lines[1])
    record["details"]["amount"] = 999
    lines[1] = json.dumps(record, sort_keys=True)
    path.write_text("\n".join(lines) + "\n")
    ok, problem = audit.verify()
    assert not ok and ":2:" in problem and "changed" in problem


def test_deleted_record_is_detected(audit_dir):
    for i in range(3):
        audit.append("event", "test", {"i": i})
    path, lines = _lines(audit_dir)
    path.write_text("\n".join([lines[0], lines[2]]) + "\n")
    ok, problem = audit.verify()
    assert not ok and "chain broken" in problem


def test_chain_continues_across_monthly_files(audit_dir):
    audit.append("first", "test")
    path, _ = _lines(audit_dir)
    path.rename(audit_dir / "audit-2000-01.jsonl")  # pretend it was last month
    audit.append("second", "test")
    assert len(list(audit_dir.glob("audit-*.jsonl"))) == 2
    assert audit.verify() == (True, None)
