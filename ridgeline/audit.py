"""Append-only, hash-chained audit log.

Every record stores the hash of the record before it, so editing, deleting or
reordering any line breaks the chain and `verify()` reports where. The chain
continues across the monthly files (audit-YYYY-MM.jsonl).

Several processes write here (panel, bot strategy, watchdog), so appends are
serialised with a lock file.
"""

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ridgeline import USERDIR

AUDIT_DIR = USERDIR / "audit"
GENESIS = "0" * 64
_LOCK_STALE_S = 10


def _files(audit_dir: Path) -> list[Path]:
    return sorted(audit_dir.glob("audit-*.jsonl"))


def _hash(prev_hash: str, record: dict) -> str:
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256((prev_hash + canonical).encode()).hexdigest()


def _last_hash(audit_dir: Path) -> str:
    for path in reversed(_files(audit_dir)):
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if lines:
            return json.loads(lines[-1])["hash"]
    return GENESIS


class _Lock:
    def __init__(self, audit_dir: Path):
        self.path = audit_dir / ".lock"

    def __enter__(self):
        deadline = time.monotonic() + 5
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return self
            except FileExistsError:
                try:  # a crashed writer can leave the lock behind
                    if time.time() - self.path.stat().st_mtime > _LOCK_STALE_S:
                        self.path.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() > deadline:
                    raise TimeoutError(f"audit log lock busy: {self.path}")
                time.sleep(0.02)

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


def append(event: str, actor: str, details: Optional[dict[str, Any]] = None,
           audit_dir: Optional[Path] = None) -> dict:
    """Append one event and return the stored record (including its hash)."""
    audit_dir = Path(audit_dir or AUDIT_DIR)
    audit_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    with _Lock(audit_dir):
        prev = _last_hash(audit_dir)
        record = {
            "ts": now.isoformat(timespec="milliseconds"),
            "actor": actor,
            "event": event,
            "details": details or {},
            "prev_hash": prev,
        }
        record["hash"] = _hash(prev, record)
        with open(audit_dir / f"audit-{now:%Y-%m}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    return record


def verify(audit_dir: Optional[Path] = None) -> tuple[bool, Optional[str]]:
    """Check the whole chain. Returns (ok, description of the first problem)."""
    audit_dir = Path(audit_dir or AUDIT_DIR)
    prev = GENESIS
    for path in _files(audit_dir):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            where = f"{path.name}:{lineno}"
            try:
                record = json.loads(line)
                stored = record.pop("hash")
            except (ValueError, KeyError):
                return False, f"{where}: unreadable record"
            if record.get("prev_hash") != prev:
                return False, f"{where}: chain broken (record removed or reordered before this line)"
            if _hash(prev, record) != stored:
                return False, f"{where}: contents changed after it was written"
            prev = stored
    return True, None


def tail(n: int = 100, audit_dir: Optional[Path] = None) -> list[dict]:
    """The most recent `n` records, newest first."""
    audit_dir = Path(audit_dir or AUDIT_DIR)
    out: list[dict] = []
    for path in reversed(_files(audit_dir)):
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        for line in reversed(lines):
            out.append(json.loads(line))
            if len(out) >= n:
                return out
    return out
