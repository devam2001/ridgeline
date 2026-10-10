"""Guards against publishing secrets or shipping a live-trading config."""

import json
import shutil
import subprocess

import pytest

from conftest import ROOT

CONFIG = json.loads((ROOT / "user_data" / "config.json").read_text())


def test_config_is_dry_run():
    assert CONFIG["dry_run"] is True


def test_no_secrets_in_public_config():
    assert not CONFIG["exchange"].get("key") and not CONFIG["exchange"].get("secret")
    assert not CONFIG.get("telegram", {}).get("token")
    for field in ("password", "jwt_secret_key", "ws_token", "username"):
        assert field not in CONFIG.get("api_server", {}), f"api_server.{field} belongs in config.private.json"


def test_private_files_are_ignored():
    ignored = (ROOT / ".gitignore").read_text()
    for pattern in ("user_data/config.private.json", "*.docx", "*.sqlite", "user_data/audit/"):
        assert pattern in ignored, f"{pattern} missing from .gitignore"


@pytest.mark.skipif(not shutil.which("git") or not (ROOT / ".git").exists(), reason="not a git checkout")
def test_nothing_private_is_tracked():
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.splitlines()
    bad = [f for f in tracked if f.endswith((".docx", ".sqlite")) or f.endswith("config.private.json")]
    assert not bad, f"private files tracked by git: {bad}"


def test_version_is_consistent():
    import re

    from ridgeline import __version__

    pyproject = (ROOT / "pyproject.toml").read_text()
    assert re.search(r'^version = "(.+)"', pyproject, re.M).group(1) == __version__
    assert (ROOT / "docs" / "releases" / f"v{__version__}.md").exists(), "write release notes for this version"
