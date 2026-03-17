"""
E2E UI tests for TestRail Sync: ensure running logs are shown in the admin UI.

Uses Playwright to drive the browser. Run with:
  pip install playwright
  playwright install chromium
  pytest tests/test_sync_ui_e2e.py -v

Skips if playwright is not installed.
"""
import json
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Skip entire module if playwright not available
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

pytestmark = pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="playwright not installed")


# Sample sync_log that the mock API will return; we assert this appears in #syncLog
MOCK_SYNC_LOG_LINES = [
    "[12:00:00] Sync started. Projects: [8], delta_days: 0",
    "[12:00:01] Connecting to TestRail and fetching test cases...",
    "[12:00:05] Fetched 333 test cases from TestRail",
]


def _make_mock_app(port: int):
    """Minimal Flask app that serves admin index.html and mock API so the UI shows running log."""
    from flask import Flask, jsonify, send_from_directory

    app = Flask(__name__, static_folder=str(ROOT / "frontend"))
    static = Path(app.static_folder)

    @app.route("/admin")
    def admin():
        return send_from_directory(static, "admin/index.html")

    @app.route("/api/auth/me", methods=["GET"])
    def auth_me():
        """Pretend user is logged in so admin page does not redirect to login."""
        return jsonify({
            "success": True,
            "user": {"username": "testadmin", "role": "admin", "user_id": "test-id"}
        }), 200

    @app.route("/api/admin/sync/status", methods=["GET"])
    def sync_status():
        """Return status with sync_log so the UI populates the running log."""
        return jsonify({
            "success": True,
            "status": {
                "last_sync": "2025-02-04T12:00:00",
                "is_syncing": False,
                "current_sync": None,
                "latest_sync_record": {
                    "timestamp": "2025-02-04T12:00:00",
                    "projects_count": 1,
                    "test_cases_fetched": 333,
                    "duration_seconds": 10.5,
                    "status": "success",
                },
                "sync_log": MOCK_SYNC_LOG_LINES,
                "sync_enabled": True,
                "configured_projects": [8],
                "delta_days": 0,
            }
        }), 200

    return app


@pytest.fixture(scope="module")
def mock_server_port():
    """Find an available port for the mock server."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def mock_server_url(mock_server_port):
    """Start the mock Flask app in a background thread and return base URL."""
    app = _make_mock_app(mock_server_port)
    import flask
    # Disable Flask's reloader and use a simple WSGI server
    def run():
        app.run(host="127.0.0.1", port=mock_server_port, use_reloader=False, threaded=True)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    # Wait until server is up
    url = f"http://127.0.0.1:{mock_server_port}"
    for _ in range(50):
        try:
            import urllib.request
            urllib.request.urlopen(f"{url}/admin", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    else:
        pytest.fail("Mock server did not start in time")
    yield url
    # Daemon thread will exit when process exits


def test_running_log_shown_in_ui(mock_server_url):
    """
    Open the admin page; API returns sync_log; assert the running log element
    displays the log lines (so we ensure running logs are shown in the UI).
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(f"{mock_server_url}/admin", wait_until="networkidle")
            # Admin page loads, checkAuth runs, then after 1s loadSyncStatus runs and fetches sync/status
            # Wait for #syncLog to contain the first mock log line (running log is populated from API)
            first_line = MOCK_SYNC_LOG_LINES[0]
            first_line_js = json.dumps(first_line)
            page.wait_for_function(
                f"""() => {{
                    const el = document.getElementById('syncLog');
                    return el && el.textContent && el.textContent.indexOf({first_line_js}) !== -1;
                }}""",
                timeout=10000
            )
            log_el = page.locator("#syncLog")
            log_text = log_el.inner_text()
            for line in MOCK_SYNC_LOG_LINES:
                assert line in log_text, f"Running log should contain: {line!r}. Got: {log_text[:200]!r}"
        finally:
            browser.close()
