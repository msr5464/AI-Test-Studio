"""
Tests for TestRail connector (add_case, update_case, get_case, etc.).
Uses mocks; no real TestRail API calls.
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestTestRailConnectorUpdateCase:
    """TestRailConnector.update_case builds correct payload and calls API."""

    def test_update_case_sends_only_provided_fields(self):
        """update_case sends only non-None fields to _make_post_request."""
        from backend.connectors.testrail_connector import TestRailConnector

        conn = TestRailConnector(url="https://test.testrail.io", email="u@x.com", api_key="key")
        with patch.object(conn, "_make_post_request") as mock_post:
            mock_post.return_value = {"id": 42, "title": "Updated"}
            conn.update_case(42, title="New title", steps="1. Step one")
            mock_post.assert_called_once()
            call_endpoint, call_payload = mock_post.call_args[0]
            assert "/api/v2/update_case/42" in call_endpoint or "update_case/42" in call_endpoint
            assert call_payload.get("title") == "New title"
            assert call_payload.get("custom_steps") == "1. Step one"
            assert "custom_expected" not in call_payload
            assert "custom_preconds" not in call_payload
            assert "priority_id" not in call_payload

    def test_update_case_with_priority_id(self):
        """update_case includes priority_id when provided."""
        from backend.connectors.testrail_connector import TestRailConnector

        conn = TestRailConnector(url="https://test.testrail.io", email="u@x.com", api_key="key")
        with patch.object(conn, "_make_post_request") as mock_post:
            mock_post.return_value = {"id": 99, "priority_id": 4}
            conn.update_case(99, title="Critical test", priority_id=4)
            call_payload = mock_post.call_args[0][1]
            assert call_payload.get("priority_id") == 4
            assert call_payload.get("title") == "Critical test"

    def test_update_case_empty_payload_calls_get_case(self):
        """When no fields provided, update_case calls get_case and does not POST."""
        from backend.connectors.testrail_connector import TestRailConnector

        conn = TestRailConnector(url="https://test.testrail.io", email="u@x.com", api_key="key")
        with patch.object(conn, "_make_post_request") as mock_post:
            with patch.object(conn, "get_case") as mock_get:
                mock_get.return_value = {"id": 10, "title": "Existing"}
                result = conn.update_case(10)
                mock_post.assert_not_called()
                mock_get.assert_called_once_with(10)
                assert result == {"id": 10, "title": "Existing"}


class TestTestRailConnectorAddCaseDropdownResolution:
    """add_case resolves dropdown string labels to option IDs before POST."""

    def test_add_case_resolves_execution_mode_label_to_id(self):
        """When payload has custom_execution_mode 'Automatable', it is sent as option ID (int)."""
        from backend.connectors.testrail_connector import TestRailConnector

        conn = TestRailConnector(url="https://test.testrail.io", email="u@x.com", api_key="key")
        # TestRail data: 1=Manual, 2=Automatable
        case_fields = [
            {
                "type_id": 2,
                "system_name": "custom_execution_mode",
                "name": "Execution Mode",
                "configs": [{"options": {"items": "1, Manual\n2, Automatable"}}],
            },
        ]
        with patch.object(conn, "get_case_fields", return_value=case_fields):
            with patch.object(
                conn,
                "get_required_case_fields_with_defaults",
                return_value={"custom_execution_mode": "Automatable"},
            ):
                with patch.object(conn, "_get_add_case_field_names", return_value={"preconditions": "custom_preconds", "steps": "custom_steps", "expected": "custom_expected"}):
                    with patch.object(conn, "_make_post_request") as mock_post:
                        mock_post.return_value = {"id": 100}
                        conn.add_case(123, "My test", steps="1. Step")
                        call_payload = mock_post.call_args[0][1]
                        # add_case resolves to custom_automation_type; _make_post_request then adds custom_execution_mode for instances that require it
                        assert call_payload.get("custom_automation_type") == 2

    def test_add_case_resolves_platform_multi_select_to_array(self):
        """custom_platform (multi-select) is sent as array of option IDs."""
        from backend.connectors.testrail_connector import TestRailConnector

        conn = TestRailConnector(url="https://test.testrail.io", email="u@x.com", api_key="key")
        # TestRail data: 1=api/backend, 2=web/m-web, 3=android, 4=ios
        case_fields = [
            {"type_id": 2, "system_name": "custom_execution_mode", "configs": [{"options": {"items": "1, Manual\n2, Automatable"}}]},
            {
                "type_id": 6,
                "system_name": "custom_platform",
                "configs": [{"options": {"items": "1, api / backend\n2, web / m-web\n3, android\n4, ios"}}],
            },
        ]
        with patch.object(conn, "get_case_fields", return_value=case_fields):
            with patch.object(conn, "get_required_case_fields_with_defaults", return_value={}):
                with patch.object(conn, "_get_add_case_field_names", return_value={"preconditions": "custom_preconds", "steps": "custom_steps", "expected": "custom_expected"}):
                    with patch.object(conn, "_make_post_request") as mock_post:
                        mock_post.return_value = {"id": 101}
                        conn.add_case(124, "Platform test", steps="1. Step")
                        call_payload = mock_post.call_args[0][1]
                        assert isinstance(call_payload.get("custom_platform"), list)
                        # ADD_CASE_MANDATORY_DEFAULTS has "web / m-web" -> option ID 2
                        assert call_payload.get("custom_platform") == [2]

    def test_add_case_resolves_web_automation_status_to_id(self):
        """custom_web_automation_status_m (Web - Automation Status) is sent as option ID."""
        from backend.connectors.testrail_connector import TestRailConnector

        conn = TestRailConnector(url="https://test.testrail.io", email="u@x.com", api_key="key")
        # TestRail data: 1=Pending Automation, 2=Already Automated
        case_fields = [
            {"type_id": 2, "system_name": "custom_execution_mode", "configs": [{"options": {"items": "1, Manual\n2, Automatable"}}]},
            {"type_id": 2, "system_name": "custom_platform", "configs": [{"options": {"items": "1, api / backend\n2, web / m-web"}}]},
            {
                "type_id": 2,
                "system_name": "custom_web_automation_status_m",
                "name": "Web - Automation Status",
                "configs": [{"options": {"items": "1, Pending Automation\n2, Already Automated"}}],
            },
        ]
        with patch.object(conn, "get_case_fields", return_value=case_fields):
            with patch.object(conn, "get_required_case_fields_with_defaults", return_value={}):
                with patch.object(conn, "_get_add_case_field_names", return_value={"preconditions": "custom_preconds", "steps": "custom_steps", "expected": "custom_expected"}):
                    with patch.object(conn, "_make_post_request") as mock_post:
                        mock_post.return_value = {"id": 102}
                        conn.add_case(125, "Web automation test", steps="1. Step")
                        call_payload = mock_post.call_args[0][1]
                        # ADD_CASE_MANDATORY_DEFAULTS has "Pending Automation" -> option ID 1
                        assert call_payload.get("custom_web_automation_status_m") == 1
                        assert not isinstance(call_payload.get("custom_web_automation_status_m"), str)

    def test_add_case_sends_custom_steps_string(self):
        """add_case sends steps as custom_steps (string)."""
        from backend.connectors.testrail_connector import TestRailConnector

        conn = TestRailConnector(url="https://test.testrail.io", email="u@x.com", api_key="key")
        with patch.object(conn, "get_case_fields", return_value=[]):
            with patch.object(conn, "get_required_case_fields_with_defaults", return_value={}):
                with patch.object(conn, "_get_add_case_field_names", return_value={"preconditions": "custom_preconds", "steps": "custom_steps", "expected": "custom_expected"}):
                    with patch.object(conn, "_make_post_request") as mock_post:
                        mock_post.return_value = {"id": 200}
                        conn.add_case(
                            100,
                            "Login test",
                            steps="1. Input valid username.\n2. Verify login.",
                            expected_result="User is logged in.",
                        )
                        call_payload = mock_post.call_args[0][1]
                        assert call_payload.get("custom_steps") == "1. Input valid username.\n2. Verify login."
                        assert call_payload.get("custom_expected") == "User is logged in."

