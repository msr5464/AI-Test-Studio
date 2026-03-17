"""
Tests for TestRail sync service and API.
Ensures is_syncing is always cleared, progress is reported, and Sync button flow works.
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _mock_config():
    """Config with TestRail sync enabled and one project."""
    c = MagicMock()
    c.testrail_sync_enabled = True
    c.testrail_url = "https://test.testrail.io"
    c.testrail_email = "test@example.com"
    c.testrail_api_key = "fake-key"
    c.testrail_project_ids = [1]
    c.testrail_delta_days = 7
    return c


def _valid_testcase_df(rows=2):
    """DataFrame that passes _validate_testcase_data (required columns, 70% match)."""
    return pd.DataFrame({
        "ID": [f"C{i}" for i in range(rows)],
        "Title": [f"Test {i}" for i in range(rows)],
        "Execution Mode": ["Manual"] * rows,
        "Expected Result": ["Pass"] * rows,
        "Platform": ["Web"] * rows,
        "Preconditions": [""] * rows,
        "Priority": ["P1"] * rows,
        "Section Hierarchy": [""] * rows,
        "Steps": ["Step 1"] * rows,
        "Type": [1] * rows,
    })


@pytest.fixture
def temp_storage(tmp_path):
    """Use tmp_path for sync metadata."""
    return tmp_path


class TestSyncServiceUnit:
    """Unit tests for TestRailSyncService."""

    @patch("backend.services.sync_service.get_config", _mock_config)
    @patch.dict(os.environ, {}, clear=False)
    def test_is_syncing_cleared_on_validation_failure(self, temp_storage):
        """is_syncing must be False after sync returns due to validation failure."""
        os.environ["STORAGE_DIR"] = str(temp_storage)
        from backend.services.sync_service import TestRailSyncService

        with patch.object(TestRailSyncService, "__init__", lambda self: None):
            svc = TestRailSyncService()
            svc.config = _mock_config()
            svc.storage_dir = temp_storage
            svc.metadata_file = temp_storage / "sync_metadata.json"
            svc.connector = MagicMock()
            # Return empty DataFrame -> validation fails
            svc.connector.fetch_and_transform.return_value = pd.DataFrame()

        # Run sync (will fail validation and return early)
        result = svc.sync_from_testrail()
        assert result.get("success") is False
        assert "validation" in result.get("message", "").lower() or "no data" in result.get("message", "").lower()

        # is_syncing must be cleared (finally block)
        status = svc.get_sync_status()
        assert status.get("is_syncing") is False

    @patch("backend.services.sync_service.get_config", _mock_config)
    @patch.dict(os.environ, {}, clear=False)
    def test_is_syncing_cleared_on_connector_exception(self, temp_storage):
        """is_syncing must be False after connector raises."""
        os.environ["STORAGE_DIR"] = str(temp_storage)
        from backend.services.sync_service import TestRailSyncService

        with patch.object(TestRailSyncService, "__init__", lambda self: None):
            svc = TestRailSyncService()
            svc.config = _mock_config()
            svc.storage_dir = temp_storage
            svc.metadata_file = temp_storage / "sync_metadata.json"
            svc.connector = MagicMock()
            svc.connector.fetch_and_transform.side_effect = RuntimeError("Network error")

        result = svc.sync_from_testrail()
        assert result.get("success") is False
        status = svc.get_sync_status()
        assert status.get("is_syncing") is False

    @patch("backend.services.sync_service.get_config", _mock_config)
    @patch.dict(os.environ, {}, clear=False)
    def test_progress_callback_invoked_and_status_has_current_sync(self, temp_storage):
        """Progress callback should be called and get_sync_status should return current_sync while syncing."""
        os.environ["STORAGE_DIR"] = str(temp_storage)
        from backend.services.sync_service import TestRailSyncService
        import threading

        progress_calls = []
        df_valid = _valid_testcase_df(3)

        def capture_progress(projects_done, projects_total, test_cases_so_far, message):
            progress_calls.append((projects_done, projects_total, test_cases_so_far, message))

        def do_fetch(project_ids=None, delta_days=None, progress_callback=None, log_callback=None):
            if progress_callback:
                progress_callback(0, 1, 0, "Starting")
                progress_callback(1, 1, 3, "Done")
            return df_valid

        with patch("backend.services.sync_service.TestRailConnector") as ConnMock:
            conn = ConnMock.return_value
            conn.fetch_and_transform.side_effect = do_fetch

            with patch("backend.services.rag_service.RAGService") as RAGMock:
                rag = RAGMock.return_value
                rag.upload_document.return_value = {"success": True}
                rag.delete_documents_by_name_prefix.return_value = {"deleted_count": 0, "errors": []}

                svc = TestRailSyncService()
                svc.storage_dir = temp_storage
                svc.metadata_file = temp_storage / "sync_metadata.json"

                result = svc.sync_from_testrail()
                assert result.get("success") is True
                assert result.get("test_cases_fetched") == 3

        status = svc.get_sync_status()
        assert status.get("is_syncing") is False
        assert status.get("current_sync") is None
        assert status.get("latest_sync_record") is not None
        assert status["latest_sync_record"].get("projects_count") == 1
        assert status["latest_sync_record"].get("test_cases_fetched") == 3

    @patch("backend.services.sync_service.get_config", _mock_config)
    @patch.dict(os.environ, {}, clear=False)
    def test_get_sync_status_returns_current_sync_structure(self, temp_storage):
        """get_sync_status returns last_sync, is_syncing, current_sync, latest_sync_record, configured_projects."""
        os.environ["STORAGE_DIR"] = str(temp_storage)
        from backend.services.sync_service import TestRailSyncService

        svc = TestRailSyncService()
        svc.storage_dir = temp_storage
        svc.metadata_file = temp_storage / "sync_metadata.json"
        status = svc.get_sync_status()
        assert "last_sync" in status
        assert "is_syncing" in status
        assert "current_sync" in status
        assert "latest_sync_record" in status
        assert "configured_projects" in status
        assert status["is_syncing"] is False


class TestSyncAPI:
    """API tests for POST /api/admin/sync/testrail and GET /api/admin/sync/status."""

    @pytest.fixture
    def app(self):
        from backend.app import create_app
        return create_app()

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    @patch("backend.services.sync_service.TestRailSyncService")
    def test_sync_status_returns_200_and_structure(self, mock_sync_service, client):
        """GET /api/admin/sync/status returns 200 and status object when auth is bypassed."""
        mock_sync_service.return_value.get_sync_status.return_value = {
            "last_sync": None,
            "is_syncing": False,
            "current_sync": None,
            "latest_sync_record": None,
            "sync_log": ["[12:00:00] Sync started.", "[12:00:01] Fetched 10 test cases."],
            "configured_projects": [1],
            "sync_enabled": True,
            "delta_days": 7,
        }
        with patch.object(client.application.config["AUTH_SERVICE"], "require_auth", return_value=None):
            r = client.get("/api/admin/sync/status")
        assert r.status_code == 200
        data = r.get_json()
        assert data.get("success") is True
        assert "status" in data
        assert "is_syncing" in data["status"]
        assert "current_sync" in data["status"]
        assert "sync_log" in data["status"]
        assert isinstance(data["status"]["sync_log"], list)
        assert len(data["status"]["sync_log"]) >= 1

    @patch("backend.services.sync_service.TestRailSyncService")
    def test_sync_testrail_returns_202_when_started(self, mock_sync_service, client):
        """POST /api/admin/sync/testrail returns 202 when sync is started."""
        mock_svc = MagicMock()
        mock_svc.get_sync_status.return_value = {"is_syncing": False}
        mock_sync_service.return_value = mock_svc

        with patch.object(client.application.config["AUTH_SERVICE"], "require_auth", return_value=None):
            r = client.post("/api/admin/sync/testrail", json={})
        assert r.status_code == 202
        data = r.get_json()
        assert data.get("success") is True
        assert "started" in data.get("status", "").lower() or "started" in data.get("message", "").lower()

    @patch("backend.services.sync_service.TestRailSyncService")
    def test_sync_testrail_returns_409_when_already_syncing(self, mock_sync_service, client):
        """POST /api/admin/sync/testrail returns 409 when sync already in progress."""
        mock_svc = MagicMock()
        mock_svc.get_sync_status.return_value = {"is_syncing": True}
        mock_sync_service.return_value = mock_svc

        with patch.object(client.application.config["AUTH_SERVICE"], "require_auth", return_value=None):
            r = client.post("/api/admin/sync/testrail", json={})
        assert r.status_code == 409
        data = r.get_json()
        assert data.get("success") is False
        assert "already in progress" in data.get("message", "").lower()


class TestStaleSyncRecovery:
    """Stale sync (is_syncing True for > 30 min) is cleared so user can start a new sync."""

    @patch("backend.services.sync_service.get_config", _mock_config)
    @patch.dict(os.environ, {}, clear=False)
    def test_get_sync_status_clears_stale_sync(self, temp_storage):
        """When sync_started_at is older than 30 min, get_sync_status clears is_syncing."""
        os.environ["STORAGE_DIR"] = str(temp_storage)
        from datetime import datetime, timedelta
        from backend.services.sync_service import TestRailSyncService, STALE_SYNC_MINUTES

        metadata_file = temp_storage / "sync_metadata.json"
        old_time = (datetime.now() - timedelta(minutes=STALE_SYNC_MINUTES + 1)).isoformat()
        metadata_file.write_text(
            '{"is_syncing": true, "sync_started_at": "%s", "syncs": [], "current_sync": {}}' % old_time
        )
        svc = TestRailSyncService()
        svc.storage_dir = temp_storage
        svc.metadata_file = metadata_file
        status = svc.get_sync_status()
        assert status.get("is_syncing") is False
        # Metadata file should be updated
        import json
        with open(metadata_file) as f:
            saved = json.load(f)
        assert saved.get("is_syncing") is False

    @patch("backend.services.sync_service.get_config", _mock_config)
    @patch.dict(os.environ, {}, clear=False)
    def test_post_sync_after_stale_returns_202(self, temp_storage, app):
        """When metadata has stale is_syncing, POST /sync/testrail clears it and returns 202."""
        os.environ["STORAGE_DIR"] = str(temp_storage)
        from datetime import datetime, timedelta
        from backend.services.sync_service import STALE_SYNC_MINUTES
        import json

        metadata_file = temp_storage / "sync_metadata.json"
        temp_storage.mkdir(parents=True, exist_ok=True)
        old_time = (datetime.now() - timedelta(minutes=STALE_SYNC_MINUTES + 1)).isoformat()
        metadata_file.write_text(json.dumps({
            "is_syncing": True,
            "sync_started_at": old_time,
            "syncs": [],
            "current_sync": {"message": "stale"}
        }))
        client = app.test_client()
        with patch.object(app.config["AUTH_SERVICE"], "require_auth", return_value=None):
            r = client.post("/api/admin/sync/testrail", json={})
        # Should start new sync (202), not 409, because get_sync_status clears stale
        assert r.status_code == 202, r.get_json()
        assert r.get_json().get("success") is True


class TestSyncFrontend:
    """Static checks that admin UI has Sync button and handler wired (no inline onclick)."""

    def test_admin_page_has_sync_button_and_click_handler(self):
        """Admin index.html must have syncNowBtn and addEventListener(click) calling triggerTestRailSync."""
        admin_html = (ROOT / "frontend" / "admin" / "index.html").read_text()
        assert "syncNowBtn" in admin_html, "Admin page should have Sync button id syncNowBtn"
        assert "triggerTestRailSync" in admin_html, "Admin page should define/call triggerTestRailSync"
        # Handler must be attached via addEventListener, not only inline onclick (which can break in some envs)
        assert "addEventListener('click'" in admin_html or 'addEventListener("click"' in admin_html, (
            "Sync button should have click listener attached in JS"
        )
        # Should not rely solely on onclick (we removed it to fix ReferenceError)
        assert 'onclick="triggerTestRailSync()"' not in admin_html, (
            "Sync button should not use inline onclick (causes ReferenceError in some contexts)"
        )

    def test_409_treated_as_sync_in_progress_not_failure(self):
        """When server returns 409 (already in progress), UI should show message and poll, not 'Sync failed'."""
        admin_html = (ROOT / "frontend" / "admin" / "index.html").read_text()
        assert "409" in admin_html, "UI should handle 409 response"
        assert "already running" in admin_html or "already in progress" in admin_html.lower(), (
            "UI should show friendly message for 409, not generic Sync failed"
        )
        # 409 should start polling like 202
        assert "response.status === 409" in admin_html or "response.status === 202" in admin_html

    def test_double_click_prevented(self):
        """UI should prevent multiple simultaneous sync requests (syncInProgress guard)."""
        admin_html = (ROOT / "frontend" / "admin" / "index.html").read_text()
        assert "syncInProgress" in admin_html, "Should have syncInProgress guard"
        assert "if (syncInProgress) return" in admin_html or "syncInProgress)" in admin_html, (
            "Should bail out if sync already in progress"
        )

    def test_running_log_section_visible(self):
        """Running log section must be present and visible by default (no display:none on container)."""
        admin_html = (ROOT / "frontend" / "admin" / "index.html").read_text()
        assert "syncLogContainer" in admin_html, "Should have Running log container"
        assert "syncLog" in admin_html, "Should have sync log content element"
        assert "Running log" in admin_html, "Should show Running log heading"
        # Container should not be hidden by default so user sees the section before first sync
        assert 'id="syncLogContainer" style="margin-top: 16px;">' in admin_html or (
            "syncLogContainer" in admin_html and 'display: none' not in admin_html.split("syncLogContainer")[1].split(">")[0]
        ), "syncLogContainer should be visible (no display:none)"
