"""
Pytest fixtures for Knowledge-AI tests.
Ensures project root is on path and provides common fixtures.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Project root
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def temp_storage_dir():
    """Temporary directory for sync metadata (no real storage)."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def app():
    """Flask app for API tests."""
    from backend.app import create_app
    return create_app()


@pytest.fixture
def signed_in(app):
    """Authenticate every request to `app` as an active member.

    Customer and agent routes require a signed-in, approved account. Patching the
    app's own AuthService keeps these tests off storage/users.json, whose admin
    password is random per install.
    """
    import types
    user = types.SimpleNamespace(user_id="0123456789ab", username="tester",
                                 role="member", status="active")
    app.config["AUTH_SERVICE"].get_current_user = lambda: user
    return user


@pytest.fixture(autouse=True)
def isolated_studio_stores(monkeypatch, tmp_path):
    """Keep cost and run records out of the real storage/.

    Analysis code logs every LLM call to storage/operation_costs.jsonl — the file
    Admin → Analytics reads — and a test that runs it with mocked models appended
    zero-cost rows there on every pytest run.
    """
    monkeypatch.setenv("OPERATION_COSTS_FILE", str(tmp_path / "operation_costs.jsonl"))
    monkeypatch.setenv("REQUIREMENT_RUNS_FILE", str(tmp_path / "requirement_runs.jsonl"))
    monkeypatch.setenv("REQUIREMENT_SESSIONS_DIR", str(tmp_path / "requirement_sessions"))
