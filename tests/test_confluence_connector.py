"""
Tests for Confluence connector (Cloud API endpoint and response handling).
Uses /rest/api/search first (Confluence Cloud); on 404 falls back to
/rest/api/content/search (some Cloud instances or Server/DC).
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestConfluenceConnectorSearchEndpoint:
    """Confluence Cloud uses /rest/api/search; content/search returns 404."""

    @patch("backend.connectors.confluence_connector.requests.get")
    def test_search_by_cql_uses_rest_api_search_first(self, mock_get):
        """search_by_cql calls /rest/api/search first (Confluence Cloud)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"results": [], "size": 0}
        mock_get.return_value = mock_resp

        from backend.connectors.confluence_connector import ConfluenceConnector

        conn = ConfluenceConnector(
            url="https://example.atlassian.net/wiki",
            email="test@example.com",
            api_token="fake-token",
        )
        conn.search_by_cql(cql='type=page AND space="Product"', limit=50)

        mock_get.assert_called_once()
        call_args = mock_get.call_args
        url = call_args[0][0] if call_args[0] else call_args[1].get("url")
        if not url and call_args[1]:
            url = call_args[0][0]
        assert url is not None
        full_url = url if isinstance(url, str) else str(call_args)
        assert "/rest/api/search" in full_url, "Must try /rest/api/search first"
        assert "/rest/api/content/search" not in full_url, "Should not use content/search when search succeeds"

    @patch("backend.connectors.confluence_connector.requests.get")
    def test_search_by_cql_fallback_to_content_search_on_404(self, mock_get):
        """When /rest/api/search returns 404, fall back to /rest/api/content/search."""
        from backend.connectors.confluence_connector import ConfluenceConnector

        conn = ConfluenceConnector(
            url="https://example.atlassian.net/wiki",
            email="test@example.com",
            api_token="fake-token",
        )

        def side_effect(url, **kwargs):
            resp = MagicMock()
            if "/rest/api/search" in url:
                resp.status_code = 404
                resp.raise_for_status.side_effect = Exception("404 Client Error: Not Found")
                return resp
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {"results": [{"id": "1", "title": "Page", "_links": {"webui": "/pages/1"}}], "size": 1}
            return resp

        mock_get.side_effect = side_effect
        out = conn.search_by_cql(cql="type=page", limit=50)
        assert out.get("results") and len(out["results"]) == 1
        assert conn._search_use_content_search is True
        assert mock_get.call_count == 2, "First search (404), then content/search"
        second_url = mock_get.call_args_list[1][0][0]
        assert "/rest/api/content/search" in second_url

    @patch("backend.connectors.confluence_connector.requests.get")
    def test_search_by_cql_with_cursor_passes_cursor_param(self, mock_get):
        """Pagination uses cursor param for Confluence Cloud."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"results": [], "size": 0}
        mock_get.return_value = mock_resp

        from backend.connectors.confluence_connector import ConfluenceConnector

        conn = ConfluenceConnector(
            url="https://example.atlassian.net/wiki",
            email="test@example.com",
            api_token="fake-token",
        )
        conn.search_by_cql(cql="type=page", limit=25, cursor="next-page-cursor")

        mock_get.assert_called_once()
        # params passed as keyword or second positional
        call_kw = mock_get.call_args[1]
        params = call_kw.get("params", {})
        assert params.get("cursor") == "next-page-cursor"

    @staticmethod
    def test_search_result_to_id_title_url_cloud_shape():
        """_search_result_to_id_title_url normalizes Cloud result (content.id, content.title)."""
        from backend.connectors.confluence_connector import ConfluenceConnector

        # Cloud shape: result has content.id, content.title
        item_cloud = {
            "content": {"id": "12345", "title": "Cloud Page"},
            "title": "Cloud Page",
            "url": "https://example.atlassian.net/wiki/spaces/X/pages/12345/Cloud-Page",
        }
        page_id, title, url = ConfluenceConnector._search_result_to_id_title_url(item_cloud)
        assert page_id == "12345"
        assert title == "Cloud Page"
        assert "12345" in url or url == "https://example.atlassian.net/wiki/spaces/X/pages/12345/Cloud-Page"

    @patch("backend.connectors.confluence_connector.requests.get")
    def test_diagnose_credentials_fail(self, mock_get):
        """diagnose reports likely_cause=credentials when /rest/api/space returns 401."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_get.return_value = mock_resp

        from backend.connectors.confluence_connector import ConfluenceConnector

        conn = ConfluenceConnector(
            url="https://example.atlassian.net/wiki",
            email="test@example.com",
            api_token="fake-token",
        )
        result = conn.diagnose(cql="type=page")
        assert result.get("likely_cause") == "credentials"
        assert "401" in (result.get("auth_check") or {}).get("message", "")

    @patch("backend.connectors.confluence_connector.requests.get")
    def test_diagnose_api_path_fail(self, mock_get):
        """diagnose reports likely_cause=api_path when both search endpoints return 404."""
        def side_effect(url, **kwargs):
            r = MagicMock()
            r.status_code = 404 if "search" in url else 200
            r.json.return_value = {} if "search" in url else {"results": []}
            return r
        mock_get.side_effect = side_effect

        from backend.connectors.confluence_connector import ConfluenceConnector

        conn = ConfluenceConnector(
            url="https://example.atlassian.net/wiki",
            email="test@example.com",
            api_token="fake-token",
        )
        result = conn.diagnose(cql="type=page")
        assert result.get("likely_cause") == "api_path"
        assert "Both" in (result.get("summary") or "")
