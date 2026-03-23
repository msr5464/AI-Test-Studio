"""
Confluence API Connector
=======================
Client for interacting with Confluence REST API to fetch pages (specs).
Syncs Confluence content via CQL for RAG ingestion.
"""

import base64
import re
import requests
import time
from typing import Dict, List, Optional, Any
from urllib.parse import quote
import warnings

import pandas as pd
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)


class ConfluenceConnector:
    """Client for Confluence API integration."""

    def __init__(self, url: str, email: str, api_token: str):
        """
        Initialize Confluence API client.

        Args:
            url: Confluence instance URL (e.g., https://company.atlassian.net/wiki)
            email: User email for authentication
            api_token: API token from Atlassian (Account Settings > Security > API tokens)
        """
        self.url = url.rstrip('/')
        self.email = email
        self.api_token = api_token

        auth_string = f"{email}:{api_token}"
        auth_bytes = auth_string.encode('ascii')
        auth_b64 = base64.b64encode(auth_bytes).decode('ascii')

        self.headers = {
            'Authorization': f'Basic {auth_b64}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }
        # Set when /rest/api/search returns 404; then we use /rest/api/content/search (start-based pagination)
        self._search_use_content_search: bool = False

    def _make_request(self, endpoint: str, params: Optional[Dict] = None, max_retries: int = 3) -> Any:
        """
        Make authenticated request to Confluence REST API with retry logic.
        """
        api_url = f"{self.url}{endpoint}"

        for attempt in range(max_retries):
            try:
                response = requests.get(
                    api_url, headers=self.headers, params=params, timeout=60
                )

                if response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', 60))
                    print(f"⏳ Rate limited. Waiting {retry_after} seconds...")
                    time.sleep(retry_after)
                    continue

                response.raise_for_status()
                return response.json()

            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    print(f"⚠️  Request failed (attempt {attempt + 1}/{max_retries}). Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    raise Exception(
                        f"Confluence API request failed after {max_retries} attempts: {str(e)}"
                    )

        raise Exception("Unexpected error in _make_request")

    def _get_by_url(self, next_link: str, max_retries: int = 3) -> Any:
        """
        GET a Confluence API URL directly (e.g. _links.next from search response).
        Avoids cursor encoding issues by using the exact URL returned by the API.
        next_link: Full URL (https://...) or path with query (/wiki/rest/api/search?cql=...&cursor=...).
        """
        if next_link.startswith('http://') or next_link.startswith('https://'):
            url = next_link
        else:
            path = next_link if next_link.startswith('/') else '/' + next_link
            url = f"{self.url.rstrip('/')}{path}"
        for attempt in range(max_retries):
            try:
                response = requests.get(url, headers=self.headers, timeout=60)
                if response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', 60))
                    print(f"⏳ Rate limited. Waiting {retry_after} seconds...")
                    time.sleep(retry_after)
                    continue
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    print(f"⚠️  Request failed (attempt {attempt + 1}/{max_retries}). Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    raise Exception(
                        f"Confluence API request failed after {max_retries} attempts: {str(e)}"
                    )
        raise Exception("Unexpected error in _get_by_url")

    def diagnose(self, cql: Optional[str] = None) -> Dict[str, Any]:
        """
        Probe Confluence API to determine why sync might fail.
        Distinguishes: credentials (401/403), API path (404), CQL/query (400 or empty).

        Args:
            cql: Optional CQL to test (e.g. from CONFLUENCE_CQL). If None, uses type=page.

        Returns:
            Dict with status_code and message per probe, summary, and likely_cause.
        """
        base = self.url
        cql_to_test = (cql or "").strip() or "type=page"
        out: Dict[str, Any] = {
            "base_url": base,
            "auth_check": None,
            "search_endpoint": None,
            "content_search_endpoint": None,
            "cql_tested": cql_to_test,
            "summary": "",
            "likely_cause": None,
        }

        def get_status(endpoint: str, params: Optional[Dict] = None) -> Dict[str, Any]:
            try:
                r = requests.get(
                    f"{base}{endpoint}",
                    headers=self.headers,
                    params=params or {},
                    timeout=15,
                )
                msg = f"HTTP {r.status_code}"
                extra = {}
                if r.status_code == 200:
                    try:
                        data = r.json()
                        if "results" in data:
                            extra["result_count"] = len(data.get("results", []))
                        if "size" in data:
                            extra["size"] = data.get("size", 0)
                    except Exception:
                        pass
                return {"endpoint": endpoint, "status_code": r.status_code, "message": msg, **extra}
            except Exception as e:
                return {"endpoint": endpoint, "status_code": None, "message": str(e)}

        # 1) Auth: a known endpoint that requires auth (e.g. /rest/api/space returns 200 if auth OK)
        out["auth_check"] = get_status("/rest/api/space", {"limit": 1})
        ac = out["auth_check"]
        if ac.get("status_code") in (401, 403):
            out["summary"] = "Authentication failed (401/403). Check CONFLUENCE_EMAIL and CONFLUENCE_API_TOKEN."
            out["likely_cause"] = "credentials"
            return out
        if ac.get("status_code") == 404:
            out["summary"] = "Base URL returned 404 (e.g. /rest/api/space). Check CONFLUENCE_URL ends with /wiki."
            out["likely_cause"] = "api_path"
            return out

        # 2) Search endpoint (Cloud CQL search)
        out["search_endpoint"] = get_status("/rest/api/search", {"cql": cql_to_test, "limit": 1})
        # 3) Content search endpoint (fallback / Server)
        out["content_search_endpoint"] = get_status(
            "/rest/api/content/search", {"cql": cql_to_test, "limit": 1, "start": 0}
        )

        search_ok = out["search_endpoint"].get("status_code") == 200
        content_ok = out["content_search_endpoint"].get("status_code") == 200

        if not search_ok and not content_ok:
            out["summary"] = (
                "Both /rest/api/search and /rest/api/content/search failed. "
                "Check CONFLUENCE_URL (must end with /wiki) and instance type (Cloud vs Server)."
            )
            out["likely_cause"] = "api_path"
            return out

        if search_ok:
            out["summary"] = "Search OK: /rest/api/search works."
        else:
            out["summary"] = "Search 404; fallback /rest/api/content/search will be used."

        # 200 but 0 results: might be CQL (e.g. wrong space key) or permissions
        cnt = out["content_search_endpoint"].get("result_count") or out["content_search_endpoint"].get("size")
        if search_ok:
            cnt = out["search_endpoint"].get("result_count") or out["search_endpoint"].get("size")
        if (search_ok or content_ok) and (cnt is None or cnt == 0):
            out["summary"] += " API returned 200 but 0 results; check CQL (e.g. space key) and permissions."
            if not out["likely_cause"]:
                out["likely_cause"] = "cql_query"

        return out

    @staticmethod
    def _clean_html(html_text: Optional[str], preserve_headings: bool = True) -> str:
        """Clean HTML content and extract plain text.
        
        Args:
            html_text: Raw HTML content from Confluence
            preserve_headings: If True, convert <h1>-<h6> to Confluence-style 'h1. Title' format
                              so requirement extractor can use section structure
        """
        if not html_text or html_text == '' or str(html_text).lower() == 'nan':
            return ''
        soup = BeautifulSoup(str(html_text), 'html.parser')
        if preserve_headings:
            for level in range(1, 7):
                for tag in soup.find_all(f'h{level}'):
                    heading_text = tag.get_text(strip=True)
                    if heading_text:
                        tag.replace_with(f'\nh{level}. {heading_text}\n')
        return soup.get_text(separator='\n', strip=True)

    @staticmethod
    def _html_to_markdown(html_text: Optional[str]) -> str:
        """Convert Confluence HTML to Markdown for better structure (headings, lists, links). Falls back to plain text if html2text not available."""
        if not html_text or html_text == '' or str(html_text).lower() == 'nan':
            return ''
        try:
            import html2text
            h = html2text.HTML2Text()
            h.ignore_links = False
            h.ignore_images = True
            h.body_width = 0
            return h.handle(str(html_text)).strip()
        except ImportError:
            return ConfluenceConnector._clean_html(html_text)

    def search_by_cql(
        self,
        cql: Optional[str] = None,
        limit: int = 100,
        start: int = 0,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Search Confluence content by CQL.
        Tries /rest/api/search first (Confluence Cloud); on 404 falls back to
        /rest/api/content/search (used by some Cloud instances and Server/DC).

        Args:
            cql: CQL query (default: type=page)
            limit: Max results per request (Cloud: max 25 when expanding body)
            start: Offset for pagination (used by content/search fallback)
            cursor: Pagination cursor from previous response _links.next (Cloud search)

        Returns:
            API response with results list and _links (next/prev)
        """
        query = (cql or "").strip() or "type=page"
        limit = min(limit, 100)

        if self._search_use_content_search:
            params = {'cql': query, 'limit': limit, 'start': start}
            return self._make_request('/rest/api/content/search', params=params)

        params = {'cql': query, 'limit': limit}
        if cursor:
            params['cursor'] = cursor
        elif start > 0:
            params['start'] = start

        try:
            return self._make_request('/rest/api/search', params=params)
        except Exception as e:
            if '404' not in str(e):
                raise
            self._search_use_content_search = True
            params = {'cql': query, 'limit': limit, 'start': start}
            return self._make_request('/rest/api/content/search', params=params)

    def get_page_from_url(self, page_url: str) -> Dict:
        """
        Fetch Confluence page content from a page URL.

        Args:
            page_url: Full Confluence page URL (e.g. https://company.atlassian.net/wiki/spaces/DEV/pages/123456/Page+Title)

        Returns:
            Page dict with id, title, body (plain text), url.
        """
        import re
        # Confluence Cloud URL: /wiki/spaces/XXX/pages/PAGEID/Title or /spaces/XXX/pages/PAGEID/
        # PAGEID is typically a long numeric string
        match = re.search(r'/pages/(\d+)(?:/|$)', page_url)
        if not match:
            raise ValueError(f"Could not extract page ID from Confluence URL: {page_url}")
        page_id = match.group(1)
        page = self.get_page(page_id)
        body_storage = page.get('body', {}).get('storage', {}).get('value', '')
        body_text = self._clean_html(body_storage)
        url_path = page.get('_links', {}).get('webui', '')
        page_url_resolved = f"{self.url}{url_path}" if url_path else page_url
        # Full page body is stored as plain text (HTML stripped) for RAG ingestion
        return {
            'page_id': str(page_id),
            'title': page.get('title', 'Untitled'),
            'body': body_text,
            'url': page_url_resolved,
        }

    def get_page(self, page_id: str, expand: str = "body.storage,version") -> Dict:
        """
        Get a single page by ID with body content.

        Args:
            page_id: Confluence page ID
            expand: Comma-separated expansions (body.storage for HTML content)

        Returns:
            Page object with title, body, etc.
        """
        params = {'expand': expand}
        return self._make_request(
            f'/rest/api/content/{page_id}',
            params=params,
        )

    @staticmethod
    def _search_result_to_id_title_url(item: Dict) -> tuple:
        """Normalize Confluence Cloud vs Server search result: (page_id, title, page_url)."""
        content = item.get('content') or {}
        page_id = content.get('id') or item.get('id')
        title = item.get('title') or content.get('title', 'Untitled')
        # Cloud: result.url or _links.webui; Server: _links.webui
        page_url = item.get('url', '')
        if not page_url:
            url_path = item.get('_links', {}).get('webui', '') or content.get('_links', {}).get('webui', '')
            page_url = url_path if url_path.startswith('http') else ''
        return (str(page_id) if page_id else None, title, page_url)

    def fetch_pages_by_cql(
        self,
        cql: Optional[str] = None,
        progress_callback: Optional[Any] = None,
        log_callback: Optional[Any] = None,
    ) -> List[Dict]:
        """
        Fetch all pages matching the CQL query with full body content.

        Uses search to get page IDs (no expand to avoid 50-result limit),
        then fetches each page individually for body content.
        Confluence Cloud: uses cursor pagination and normalizes content.id / content.title.

        Args:
            cql: CQL query (default: type=page)
            progress_callback: Optional callable(current, total, message)
            log_callback: Optional callable(message)

        Returns:
            List of page dicts with id, title, body (plain text), url
        """
        pages_data = []
        next_link = None  # use API's _links.next URL directly to avoid cursor encoding issues
        batch_size = 50
        query = (cql or "").strip() or "type=page"
        _max_pages = 10_000  # hard ceiling to prevent runaway pagination; CQL is the real filter

        while len(pages_data) < _max_pages:
            if next_link:
                # Use the exact next URL from API (avoids 400 from cursor encoding)
                resp = self._get_by_url(next_link)
            else:
                resp = self.search_by_cql(cql=query, limit=batch_size, start=0)
            results = resp.get('results', [])
            if not results:
                break

            size = resp.get('size', len(results))
            next_link = (resp.get('_links') or {}).get('next', '') or None

            for item in results:
                if len(pages_data) >= _max_pages:
                    break
                page_id, title, page_url = self._search_result_to_id_title_url(item)
                if not page_id:
                    continue
                if not page_url and self.url:
                    url_path = (item.get('_links') or {}).get('webui', '') or ((item.get('content') or {}).get('_links') or {}).get('webui', '')
                    page_url = f"{self.url.rstrip('/')}/{url_path.lstrip('/')}" if url_path else ""

                if progress_callback:
                    progress_callback(
                        len(pages_data),
                        size + len(pages_data),
                        f"Fetching page: {title[:50]}...",
                    )
                if log_callback:
                    log_callback(f"  Fetching page {page_id}: {title[:60]}...")

                try:
                    page = self.get_page(page_id)
                    body_storage = (
                        page.get('body', {})
                        .get('storage', {})
                        .get('value', '')
                    )
                    body_text = self._html_to_markdown(body_storage)
                    pages_data.append({
                        'page_id': str(page_id),
                        'title': title,
                        'body': body_text,
                        'url': page_url,
                        'space_key': page.get('space', {}).get('key', ''),
                    })
                except Exception as e:
                    print(f"⚠️  Failed to fetch page {page_id}: {e}")
                    if log_callback:
                        log_callback(f"    Failed: {e}")
                    # Still add minimal record
                    space = item.get('space') or (item.get('content') or {}).get('space') or {}
                    space_key = space.get('key', '') if isinstance(space, dict) else ''
                    pages_data.append({
                        'page_id': str(page_id),
                        'title': title,
                        'body': '',
                        'url': page_url,
                        'space_key': space_key,
                    })

            if len(results) < batch_size or not next_link:
                break

        return pages_data

    def transform_to_csv_format(self, pages: List[Dict]) -> pd.DataFrame:
        """
        Transform Confluence pages to CSV-like DataFrame for RAG ingestion.

        Args:
            pages: List of page dicts from fetch_pages_by_cql

        Returns:
            DataFrame with columns: page_id, title, body, url
        """
        rows = []
        for p in pages:
            rows.append({
                'page_id': p.get('page_id', ''),
                'title': p.get('title', ''),
                'body': p.get('body', ''),
                'url': p.get('url', ''),
                'space_key': p.get('space_key', ''),
            })
        return pd.DataFrame(rows)

    def fetch_and_transform(
        self,
        cql: Optional[str] = None,
        progress_callback: Optional[Any] = None,
        log_callback: Optional[Any] = None,
    ) -> pd.DataFrame:
        """
        Fetch pages and transform to CSV format for RAG sync.

        Args:
            cql: CQL query (default: type=page)
            progress_callback: Optional callable
            log_callback: Optional callable

        Returns:
            DataFrame for RAG ingestion
        """
        query = (cql or "").strip() or "type=page"
        msg = f"Fetching Confluence pages with CQL: {query[:60]}{'...' if len(query) > 60 else ''}"
        print(f"📅 {msg}")
        if log_callback:
            log_callback(msg)

        pages = self.fetch_pages_by_cql(
            cql=query,
            progress_callback=progress_callback,
            log_callback=log_callback,
        )

        if not pages:
            print("⚠️  No pages found")
            if log_callback:
                log_callback("No pages found (check CQL and permissions)")
            return pd.DataFrame()

        print(f"\n✅ Total pages fetched: {len(pages)}")
        if log_callback:
            log_callback(f"Total fetched: {len(pages)} pages")

        df = self.transform_to_csv_format(pages)
        print(f"✅ Transformation complete. DataFrame shape: {df.shape}")
        return df
