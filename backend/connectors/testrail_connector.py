"""
TestRail API Connector
======================
Client for interacting with TestRail API to fetch test cases.

Import and resolution of IDs to labels
---------------------------------------
When fetching cases via get_cases/get_test_cases, the API returns IDs for:
- type_id          -> Resolved via get_case_types() to e.g. "Functional", "Regression"
- priority_id      -> Mapped to P0/P1/P2/P3 via _map_priority (optional: get_priorities() for full name)
- custom_platform  -> Resolved via get_case_fields() config options to e.g. "Web", "API"
- custom_automation_type -> Same (e.g. "Automatable", "Manual")
- Other custom dropdown/multi-select fields -> Resolved if they have options in get_case_fields

Preconditions are always fetched (custom_preconds, with fallback to custom_preconditions when present).
Steps, expected result, and preconditions are stripped of HTML and included in the transformed output.

fetch_and_transform() fetches case types and custom field option maps once, then
transform_to_csv_format() resolves these IDs so the DataFrame has human-readable
values (e.g. Type: "Functional", Platform: "Web") instead of raw IDs.
"""

import base64
import requests
import time
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
import warnings

import pandas as pd
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

# Test case content often contains URL-like strings; we parse HTML, not fetch URLs
warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)

# Mandatory field defaults for add_case (required by some TestRail instances).
# Keys are API field names: TestRail expects system names as-is (refs, time, etc.)
# and custom fields as custom_<name>. Include both so either API style works.
ADD_CASE_MANDATORY_DEFAULTS: Dict[str, Any] = {
    "estimate": "15min",
    "time": "15min",
    "custom_time": "15min",
    "refs": "v4.0.0.0",
    "custom_refs": "v4.0.0.0",
    # Execution Mode: 1=Manual, 2=Automatable (resolved to option ID before send)
    "execution_mode": "Automatable",
    "custom_execution_mode": "Automatable",
    # TestRail expects custom_platform only (not "platform")
    "custom_platform": "web / m-web",
    "web_automation_status_m": "Pending Automation",
    "custom_web_automation_status_m": "Pending Automation",
}


class TestRailConnector:
    """Client for TestRail API integration."""
    
    def __init__(self, url: str, email: str, api_key: str):
        """
        Initialize TestRail API client.
        
        Args:
            url: TestRail instance URL (e.g., https://company.testrail.io)
            email: User email for authentication
            api_key: API key from TestRail (Profile > Settings > API Keys)
        """
        self.url = url.rstrip('/')
        self.email = email
        self.api_key = api_key
        
        # Setup authentication header (base64 encoded email:api_key)
        auth_string = f"{email}:{api_key}"
        auth_bytes = auth_string.encode('ascii')
        auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
        
        self.headers = {
            'Authorization': f'Basic {auth_b64}',
            'Content-Type': 'application/json'
        }
        # TestRail Cloud (*.testrail.io) uses /index.php?/api/v2/... ; direct /api/v2/ returns 404
        self.use_legacy_path = 'testrail.io' in url.lower()
    
    def _make_request(self, endpoint: str, params: Optional[Dict] = None, max_retries: int = 3) -> Any:
        """
        Make authenticated request to TestRail API with retry logic and path correction.
        """
        # Construct URL based on legacy path flag
        base_endpoint = endpoint
        if self.use_legacy_path and not endpoint.startswith('/index.php?'):
            base_endpoint = f"/index.php?{endpoint}"
            
        api_url = f"{self.url}{base_endpoint}"
        
        for attempt in range(max_retries):
            try:
                response = requests.get(api_url, headers=self.headers, params=params, timeout=30)
                
                # Handle rate limiting
                if response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', 60))
                    print(f"⏳ Rate limited. Waiting {retry_after} seconds...")
                    time.sleep(retry_after)
                    continue
                
                # Handle 404 - switch to legacy path (TestRail Cloud uses /index.php?/api/v2/...)
                if response.status_code == 404 and not self.use_legacy_path:
                    print("⚠️  404 encountered. Trying legacy path format (/index.php?)...")
                    self.use_legacy_path = True
                    return self._make_request(endpoint, params, max_retries)
                
                response.raise_for_status()
                return response.json()
                
            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # Exponential backoff
                    print(f"⚠️  Request failed (attempt {attempt + 1}/{max_retries}). Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    raise Exception(f"TestRail API request failed after {max_retries} attempts: {str(e)}")
        
        raise Exception("Unexpected error in _make_request")

    def _make_post_request(self, endpoint: str, data: Dict[str, Any], max_retries: int = 3) -> Any:
        """Make authenticated POST request to TestRail API (e.g. add_case)."""
        # add_case: custom_execution_mode is required; ensure it is always an int (2 = Automatable)
        if "/add_case/" in endpoint and isinstance(data, dict):
            data = dict(data)
            val = data.get("custom_execution_mode") or data.get("execution_mode")
            if isinstance(val, (int, float)) and int(val) in (1, 2):
                data["custom_execution_mode"] = int(val)
            else:
                data["custom_execution_mode"] = 2  # Automatable
            data.pop("execution_mode", None)
        base_endpoint = endpoint
        if self.use_legacy_path and not endpoint.startswith('/index.php?'):
            base_endpoint = f"/index.php?{endpoint}"
        api_url = f"{self.url}{base_endpoint}"
        for attempt in range(max_retries):
            try:
                response = requests.post(
                    api_url, headers=self.headers, json=data, timeout=30
                )
                if response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', 60))
                    print(f"⏳ Rate limited. Waiting {retry_after} seconds...")
                    time.sleep(retry_after)
                    continue
                if response.status_code == 404 and not self.use_legacy_path:
                    self.use_legacy_path = True
                    return self._make_post_request(endpoint, data, max_retries)
                if 400 <= response.status_code < 500:
                    # Include TestRail's error body so user sees required/missing field details
                    try:
                        err_body = response.json()
                        err_msg = err_body.get("error") or err_body.get("message") or str(err_body)
                    except Exception:
                        err_msg = response.text or response.reason
                    raise Exception(
                        f"TestRail POST failed after {attempt + 1} attempt(s): "
                        f"{response.status_code} {response.reason}. {err_msg}"
                    )
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise Exception(f"TestRail POST failed after {max_retries} attempts: {str(e)}")
        raise Exception("Unexpected error in _make_post_request")

    def get_required_case_fields_with_defaults(
        self, project_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Return required case fields and their default values from get_case_fields.
        Use for add_case so we send all required fields (avoids 400).
        project_id: if set, only include fields that apply to this project (context.project_ids or is_global).
        Returns e.g. {"estimate": "0s", "custom_automation_type": 1, ...}.
        """
        result: Dict[str, Any] = {}
        try:
            fields = self.get_case_fields() or []
            for field in fields:
                system_name = field.get("system_name") or field.get("name")
                if not system_name:
                    continue
                if not system_name.startswith("custom_"):
                    system_name = f"custom_{system_name}"
                configs = field.get("configs") or []
                for config in configs:
                    opts = config.get("options") or {}
                    if not opts.get("is_required"):
                        continue
                    ctx = config.get("context") or {}
                    if project_id is not None and not ctx.get("is_global"):
                        ids = ctx.get("project_ids") or []
                        if ids is not None and project_id not in ids:
                            continue
                    default = opts.get("default_value")
                    if default is not None and default != "":
                        try:
                            if field.get("type_id") in (2, 6) and str(default).isdigit():
                                result[system_name] = int(default)
                            else:
                                result[system_name] = default
                        except (TypeError, ValueError):
                            result[system_name] = default
                    else:
                        if field.get("type_id") == 2:
                            result[system_name] = 0
                        elif field.get("type_id") == 5:
                            result[system_name] = False
                        elif field.get("type_id") == 6:
                            items = opts.get("items") or ""
                            if isinstance(items, str) and "\n" in items:
                                first = items.strip().split("\n")[0].strip()
                                idx = first.find(",")
                                if idx >= 0 and first[:idx].strip().isdigit():
                                    result[system_name] = int(first[:idx].strip())
                                    break
                            result[system_name] = "" if default is not None else ""
                        else:
                            result[system_name] = ""
                    break
        except Exception as e:
            print(f"⚠️  get_required_case_fields_with_defaults: {e}")
        return result

    def _get_dropdown_label_to_id(self) -> Dict[str, Tuple[Dict[str, int], int]]:
        """
        Build per-field label -> option ID and type for dropdown/select case fields.
        TestRail: dropdown (type_id 2) = single int; multi-select (type_id 6, 12) = array of ints.
        Returns dict keyed by field key: (label_lower -> id, type_id).
        """
        result: Dict[str, Tuple[Dict[str, int], int]] = {}
        try:
            fields = self.get_case_fields() or []
            for field in fields:
                type_id = field.get("type_id")
                # 2=dropdown (single), 6=multi-select, 12=multi-select (some TestRail instances use 12 for Platform etc.)
                if type_id not in (2, 6, 12):
                    continue
                # Treat 12 like 6 so resolution sends array of ints
                effective_type = 6 if type_id == 12 else int(type_id)
                system_name = field.get("system_name") or field.get("name")
                if not system_name:
                    continue
                if not system_name.startswith("custom_"):
                    system_name = f"custom_{system_name}"
                configs = field.get("configs") or []
                label_to_id: Dict[str, int] = {}
                for config in configs:
                    opts = config.get("options") or {}
                    items = opts.get("items")
                    if not items or not isinstance(items, str):
                        continue
                    for line in items.strip().split("\n"):
                        line = line.strip()
                        if not line:
                            continue
                        idx = line.find(",")
                        if idx >= 0:
                            id_str, label = line[:idx].strip(), line[idx + 1 :].strip()
                            try:
                                id_val = int(id_str)
                                label_to_id[label.strip().lower()] = id_val
                            except ValueError:
                                pass
                    break
                if label_to_id:
                    result[system_name] = (label_to_id, effective_type)
                    if system_name.startswith("custom_"):
                        result[system_name[7:]] = (label_to_id, effective_type)
        except Exception as e:
            print(f"⚠️  _get_dropdown_label_to_id: {e}")
        return result

    @staticmethod
    def _resolve_dropdown_label_flexible(val: str, label_map: Dict[str, int]) -> Optional[int]:
        """Try to resolve a dropdown label with flexible matching (e.g. 'web / m-web' -> try 'web', 'm-web')."""
        if not val or not label_map:
            return None
        val = val.strip().lower()
        if not val:
            return None
        direct = label_map.get(val)
        if direct is not None:
            return direct
        for part in val.replace(" / ", ",").split(","):
            part = part.strip()
            if part:
                rid = label_map.get(part)
                if rid is not None:
                    return rid
        first_word = val.split()[0] if val.split() else val
        for label, rid in label_map.items():
            if label.startswith(first_word) or first_word in label:
                return rid
        return None

    @staticmethod
    def _resolve_multiselect_label_to_ids(val: str, label_map: Dict[str, int]) -> List[int]:
        """
        Resolve a multi-select field value to a list of option IDs.
        Tries full string first (e.g. 'web / m-web' as one option), then splits by ' / ' and ','.
        """
        if not val or not label_map:
            return []
        val = (val or "").strip().lower()
        if not val:
            return []
        # Full string might be one option (e.g. "web / m-web" -> id 2)
        rid = label_map.get(val)
        if rid is not None:
            return [rid]
        ids: List[int] = []
        seen: set = set()
        for part in val.replace(" / ", ",").split(","):
            part = part.strip()
            if not part:
                continue
            rid = label_map.get(part)
            if rid is not None and rid not in seen:
                ids.append(rid)
                seen.add(rid)
                continue
            for label, lid in label_map.items():
                if (part in label or label.startswith(part)) and lid not in seen:
                    ids.append(lid)
                    seen.add(lid)
                    break
        return ids

    def _get_requirement_field_system_name(self) -> Optional[str]:
        """
        Return the system_name of a custom case field that stores requirement/source requirement text.
        Looks for a String (type_id=1) or Text (type_id=3) field whose label/name contains 'requirement'.
        Used so we can store requirement text in TestRail and have it survive sync / DB reset.
        """
        try:
            fields = self.get_case_fields() or []
            for field in fields:
                type_id = field.get("type_id")
                if type_id not in (1, 3):  # 1=String, 3=Text
                    continue
                label = (field.get("label") or field.get("name") or "").strip().lower()
                if "requirement" not in label:
                    continue
                system_name = field.get("system_name") or field.get("name")
                if not system_name:
                    continue
                if not system_name.startswith("custom_"):
                    system_name = f"custom_{system_name}"
                return system_name
        except Exception:
            pass
        return None

    def _get_add_case_field_names(self) -> Dict[str, str]:
        """
        Resolve TestRail case field system names for preconditions, steps, expected, and optionally requirement.
        Some instances use custom_preconditions vs custom_preconds; use get_case_fields to match.
        Returns e.g. {"preconditions": "custom_preconds", "steps": "custom_steps", "expected": "custom_expected", "requirement": "custom_requirement"}.
        """
        fallback = {
            "preconditions": "custom_preconds",
            "steps": "custom_steps",
            "expected": "custom_expected",
        }
        try:
            fields = self.get_case_fields() or []
            result = dict(fallback)
            for field in fields:
                system_name = field.get("system_name") or field.get("name")
                if not system_name:
                    continue
                if not system_name.startswith("custom_"):
                    system_name = f"custom_{system_name}"
                label = (field.get("label") or field.get("name") or "").strip().lower()
                if not label:
                    continue
                if "precond" in label or "pre cond" in label:
                    result["preconditions"] = system_name
                elif ("expected" in label or "expected result" in label) and "step" not in label:
                    result["expected"] = system_name
                elif "step" in label and "expected" not in label:
                    result["steps"] = system_name
                elif "requirement" in label and field.get("type_id") in (1, 3):
                    result["requirement"] = system_name
            return result
        except Exception:
            return fallback

    def add_case(self, section_id: int, title: str, steps: Optional[str] = None,
                 expected_result: Optional[str] = None, preconditions: Optional[str] = None,
                 priority_id: int = 2, type_id: Optional[int] = None,
                 requirement_text: Optional[str] = None, platform: Optional[str] = None) -> Dict:
        """
        Add a test case to a section.

        Args:
            section_id: TestRail section ID
            title: Case title
            steps: Steps (plain text or HTML)
            expected_result: Expected result
            preconditions: Preconditions
            priority_id: 1=Low, 2=Medium, 3=High, 4=Critical
            type_id: Optional case type (e.g. 1=Functional). If None, first type from get_case_types is used when project requires it.
            requirement_text: Optional requirement/source requirement text; stored if project has a custom String/Text field whose label contains "requirement" (survives sync).
            platform: Optional platform label (e.g. "Web", "API", "api / backend"). Resolved to TestRail option ID; if omitted, default from ADD_CASE_MANDATORY_DEFAULTS is used, with fallback to first option so the field is never blank.

        Returns:
            Created case dict from TestRail
        """
        payload: Dict[str, Any] = {"title": title, "priority_id": priority_id}
        if platform is not None and (platform or "").strip():
            payload["custom_platform"] = (platform or "").strip()
        if type_id is not None:
            payload["type_id"] = type_id
        else:
            try:
                types = self.get_case_types() or []
                if types:
                    payload["type_id"] = int(types[0].get("id", 1))
            except Exception:
                pass
        field_names = self._get_add_case_field_names()
        if preconditions:
            payload[field_names["preconditions"]] = preconditions
        if expected_result:
            payload[field_names["expected"]] = expected_result
        if steps:
            payload["custom_steps"] = steps
        if requirement_text and (requirement_text or "").strip() and "requirement" in field_names:
            payload[field_names["requirement"]] = (requirement_text or "").strip()[:2000]
        for key, value in ADD_CASE_MANDATORY_DEFAULTS.items():
            if key not in payload:
                payload[key] = value
        required_defaults = self.get_required_case_fields_with_defaults()
        for key, value in required_defaults.items():
            if key not in payload:
                payload[key] = value
        # TestRail: dropdown (type 2) = single int; multi-select (type 6) = array of ints
        field_maps = self._get_dropdown_label_to_id()
        dropdown_like_keys = frozenset((
            "execution_mode", "custom_execution_mode", "platform", "custom_platform",
            "web_automation_status_m", "custom_web_automation_status_m",
        ))
        # If API uses custom_automation_type but payload has custom_execution_mode, resolve and send under API key
        for alt_key in ("custom_automation_type", "automation_type"):
            if alt_key in field_maps and "custom_execution_mode" in payload and "custom_execution_mode" not in field_maps:
                label_map, type_id = field_maps[alt_key]
                val = payload.get("custom_execution_mode")
                if isinstance(val, str):
                    resolved = label_map.get(val.strip().lower())
                    if resolved is not None:
                        payload[alt_key] = int(resolved)
                elif isinstance(val, (int, float)) and int(val) != 0:
                    payload[alt_key] = int(val)
                elif isinstance(val, (int, float)) and label_map and int(val) == 0:
                    payload[alt_key] = int(next(iter(label_map.values())))
                payload.pop("custom_execution_mode", None)
                payload.pop("execution_mode", None)
                break
        for key in list(payload.keys()):
            field_info = field_maps.get(key)
            if not field_info:
                v = payload.get(key)
                if key in dropdown_like_keys and (isinstance(v, str) or v == 0):
                    payload.pop(key, None)
                continue
            label_map, type_id = field_info
            val = payload[key]
            if isinstance(val, str):
                val_lower = val.strip().lower()
                resolved_id = label_map.get(val_lower)
                if type_id == 6:
                    # Multi-select: resolve string to list of option IDs (e.g. "web / m-web" -> [id_web, id_mweb])
                    ids = self._resolve_multiselect_label_to_ids(val, label_map)
                    if ids:
                        payload[key] = [int(x) for x in ids]
                    elif key in ("custom_platform", "platform") and label_map:
                        payload["custom_platform"] = [int(next(iter(label_map.values())))]
                        if key == "platform":
                            payload.pop("platform", None)
                    else:
                        payload.pop(key, None)
                else:
                    if resolved_id is not None:
                        payload[key] = int(resolved_id)
                    else:
                        resolved_id = self._resolve_dropdown_label_flexible(val, label_map)
                        if resolved_id is not None:
                            payload[key] = int(resolved_id)
                        elif key in ("custom_platform", "platform") and label_map:
                            payload["custom_platform"] = int(next(iter(label_map.values())))
                            if key == "platform":
                                payload.pop("platform", None)
                        else:
                            payload.pop(key, None)
            elif isinstance(val, (int, float)) and type_id == 2:
                v = int(val)
                # TestRail often rejects 0 as option ID; use first option if available
                if v == 0 and label_map:
                    first_id = next(iter(label_map.values()), None)
                    if first_id is not None:
                        v = first_id
                payload[key] = v
            elif isinstance(val, list) and type_id == 6:
                ids = []
                for item in val:
                    if isinstance(item, int):
                        ids.append(int(item))
                    elif isinstance(item, str):
                        rid = label_map.get(item.strip().lower())
                        if rid is not None and rid not in ids:
                            ids.append(int(rid))
                if ids:
                    payload[key] = ids
                else:
                    payload.pop(key, None)
        # Final sanitization: never send dropdown-like keys as string (TestRail expects int or array of ints)
        for key in list(payload.keys()):
            if key not in dropdown_like_keys:
                continue
            v = payload.get(key)
            if isinstance(v, str):
                payload.pop(key, None)
            elif isinstance(v, list) and not all(isinstance(x, int) for x in v):
                payload.pop(key, None)
            elif isinstance(v, (int, float)) and key in ("custom_execution_mode", "execution_mode"):
                if "custom_automation_type" not in payload:
                    payload["custom_automation_type"] = int(v)
                payload.pop(key, None)
        # Never send custom_execution_mode / execution_mode; TestRail add_case expects custom_automation_type
        payload.pop("custom_execution_mode", None)
        payload.pop("execution_mode", None)
        # TestRail expects custom_platform only (not "platform")
        payload.pop("platform", None)
        # If we have no execution mode value (e.g. get_case_fields failed), default to 2 = Automatable
        if "custom_automation_type" not in payload:
            payload["custom_automation_type"] = 2
        return self._make_post_request(f"/api/v2/add_case/{section_id}", payload)

    def update_case(
        self,
        case_id: int,
        title: Optional[str] = None,
        steps: Optional[str] = None,
        expected_result: Optional[str] = None,
        preconditions: Optional[str] = None,
        priority_id: Optional[int] = None,
    ) -> Dict:
        """
        Update an existing test case (partial updates supported; only include fields to change).

        Args:
            case_id: TestRail case ID (numeric, e.g. 129563)
            title: New title (optional)
            steps: Steps (optional)
            expected_result: Expected result (optional)
            preconditions: Preconditions (optional)
            priority_id: 1=Low, 2=Medium, 3=High, 4=Critical (optional)

        Returns:
            Updated case dict from TestRail
        """
        payload: Dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if preconditions is not None:
            payload["custom_preconds"] = preconditions
        if expected_result is not None:
            payload["custom_expected"] = expected_result
        if steps is not None:
            payload["custom_steps"] = steps
        if priority_id is not None:
            payload["priority_id"] = priority_id
        if not payload:
            return self.get_case(case_id)
        return self._make_post_request(f"/api/v2/update_case/{case_id}", payload)

    def get_sections(self, project_id: int, suite_id: Optional[int] = None) -> List[Dict]:
        """
        Get sections for a project and optionally a suite.
        Single-suite projects may omit suite_id.
        """
        params = {}
        if suite_id is not None:
            params["suite_id"] = suite_id
        response = self._make_request(f"/api/v2/get_sections/{project_id}", params=params or None)
        if isinstance(response, dict) and "sections" in response:
            return response["sections"]
        return response if isinstance(response, list) else []

    def get_case(self, case_id: int) -> Dict:
        """Get a single test case by ID (returns section_id, etc.)."""
        return self._make_request(f"/api/v2/get_case/{case_id}")

    def add_suite(self, project_id: int, name: str, description: Optional[str] = None) -> Dict:
        """Create a new test suite in a project. Returns the created suite."""
        payload = {"name": name}
        if description:
            payload["description"] = description
        return self._make_post_request(f"/api/v2/add_suite/{project_id}", payload)

    def add_section(self, project_id: int, name: str, suite_id: Optional[int] = None,
                   parent_id: Optional[int] = None, description: Optional[str] = None) -> Dict:
        """Create a new section. Single-suite projects may omit suite_id."""
        payload = {"name": name}
        if suite_id is not None:
            payload["suite_id"] = suite_id
        if parent_id is not None:
            payload["parent_id"] = parent_id
        if description:
            payload["description"] = description
        return self._make_post_request(f"/api/v2/add_section/{project_id}", payload)

    def get_project(self, project_id: int) -> Dict:
        """
        Get project details.
        
        Args:
            project_id: TestRail project ID
            
        Returns:
            Project dictionary
        """
        return self._make_request(f'/api/v2/get_project/{project_id}')

    def get_projects(self) -> List[Dict]:
        """
        Get list of all accessible projects.
        
        Returns:
            List of project dictionaries
        """
        response = self._make_request('/api/v2/get_projects')
        # Handle wrapped response (e.g. {'offset': 0, 'limit': 250, 'projects': [...]})
        if isinstance(response, dict) and 'projects' in response:
            return response['projects']
        return response
    
    def get_suites(self, project_id: int) -> List[Dict]:
        """
        Get all test suites for a project.
        
        Args:
            project_id: TestRail project ID
            
        Returns:
            List of test suite dictionaries
        """
        response = self._make_request(f'/api/v2/get_suites/{project_id}')
        # Handle wrapped response if applicable
        if isinstance(response, dict) and 'suites' in response:
            return response['suites']
        return response
    
    def get_test_cases(
        self, 
        project_id: int, 
        suite_id: Optional[int] = None,
        updated_after: Optional[datetime] = None
    ) -> List[Dict]:
        """
        Get test cases from a project/suite with optional delta filter.
        
        Args:
            project_id: TestRail project ID
            suite_id: Optional suite ID to filter by
            updated_after: Only return cases updated after this datetime
            
        Returns:
            List of test case dictionaries
        """
        params = {}
        
        if suite_id:
            params['suite_id'] = suite_id
        
        if updated_after:
            # Convert to Unix timestamp
            timestamp = int(updated_after.timestamp())
            params['updated_after'] = timestamp
        
        response = self._make_request(f'/api/v2/get_cases/{project_id}', params=params)
        # Handle wrapped response (e.g. {'offset': 0, 'limit': 250, 'cases': [...]})
        if isinstance(response, dict) and 'cases' in response:
            return response['cases']
        return response
    
    def get_case_fields(self) -> List[Dict]:
        """
        Get custom field definitions for test cases.
        
        Returns:
            List of field definition dictionaries
        """
        return self._make_request('/api/v2/get_case_fields')

    def get_case_types(self) -> List[Dict]:
        """
        Get case types (e.g. Functional, Regression). Used to resolve type_id to label.
        
        Returns:
            List of dicts with 'id' and 'name' (e.g. [{"id": 1, "name": "Functional"}])
        """
        return self._make_request('/api/v2/get_case_types')

    def get_priorities(self) -> List[Dict]:
        """
        Get priority definitions. Used to resolve priority_id to label.
        
        Returns:
            List of dicts with 'id', 'name', etc. (e.g. [{"id": 4, "name": "4 - Critical"}])
        """
        return self._make_request('/api/v2/get_priorities')
    
    @staticmethod
    def _clean_html(html_text: Optional[str]) -> str:
        """
        Clean HTML content and extract plain text.
        
        Args:
            html_text: HTML string
            
        Returns:
            Cleaned plain text
        """
        if not html_text or html_text == '' or str(html_text).lower() == 'nan':
            return ''
        
        soup = BeautifulSoup(str(html_text), 'html.parser')
        return soup.get_text(separator='\n', strip=True)
    
    def _build_case_type_map(self) -> Dict[int, str]:
        """Fetch case types and return mapping id -> name."""
        try:
            types = self.get_case_types()
            return {int(t["id"]): str(t.get("name", "")) for t in (types or []) if t.get("id") is not None}
        except Exception as e:
            print(f"⚠️  Could not fetch case types: {e}")
            return {}

    def _build_priority_map(self) -> Dict[int, str]:
        """Fetch priorities and return mapping id -> name (optional; we still use _map_priority for P0/P3)."""
        try:
            priorities = self.get_priorities()
            return {int(p["id"]): str(p.get("name", "")) for p in (priorities or []) if p.get("id") is not None}
        except Exception as e:
            print(f"⚠️  Could not fetch priorities: {e}")
            return {}

    def _build_custom_field_option_maps(self) -> Dict[str, Dict[Any, str]]:
        """
        Fetch case fields and build per-field option maps: system_name -> { value_id: label }.
        Handles dropdown/select options from configs[].options.items ("id, Label\\n").
        Returns dict keyed by system_name (e.g. 'custom_platform') for lookup in case dict.
        """
        result: Dict[str, Dict[Any, str]] = {}
        try:
            fields = self.get_case_fields() or []
            for field in fields:
                system_name = field.get("system_name") or field.get("name")
                if not system_name:
                    continue
                if not system_name.startswith("custom_"):
                    system_name = f"custom_{system_name}"
                configs = field.get("configs") or []
                for config in configs:
                    opts = config.get("options") or {}
                    items = opts.get("items")
                    if not items:
                        continue
                    # Format "id, Label\\n" or "id, Label"
                    if isinstance(items, str):
                        id_to_label: Dict[Any, str] = {}
                        for line in items.strip().split("\n"):
                            line = line.strip()
                            if not line:
                                continue
                            # First comma separates id from label (label may contain commas)
                            idx = line.find(",")
                            if idx >= 0:
                                id_str = line[:idx].strip()
                                label = line[idx + 1 :].strip()
                                try:
                                    id_val = int(id_str)
                                    id_to_label[id_val] = label
                                except ValueError:
                                    id_to_label[id_str] = label
                            else:
                                id_to_label[line] = line
                        if id_to_label:
                            result[system_name] = id_to_label
                    break
        except Exception as e:
            print(f"⚠️  Could not build custom field options: {e}")
        return result

    @staticmethod
    def _map_priority(priority_id: Optional[int]) -> str:
        """
        Map TestRail priority ID to P0/P1/P2/P3 format.
        
        Args:
            priority_id: TestRail priority ID (typically 1-4)
            
        Returns:
            Priority string (P0, P1, P2, or P3)
        """
        # TestRail typically uses: 4=Critical, 3=High, 2=Medium, 1=Low
        # Map to: P0, P1, P2, P3
        priority_map = {
            4: 'P0',  # Critical
            3: 'P1',  # High
            2: 'P2',  # Medium
            1: 'P3',  # Low
        }
        return priority_map.get(priority_id, 'P3')
    
    def _resolve_custom_field_value(
        self,
        value: Any,
        option_map: Dict[Any, str],
    ) -> str:
        """Resolve a custom field value (int or list of int) to label(s) using option_map."""
        if value is None or (isinstance(value, (list, str)) and not value):
            return ""
        if isinstance(value, list):
            labels = [option_map.get(v, str(v)) for v in value]
            return ", ".join(labels) if labels else ""
        return option_map.get(value, str(value))

    def _build_section_hierarchy_map(
        self, sections: List[Dict]
    ) -> Dict[int, str]:
        """
        Build section_id -> human-readable hierarchy (e.g. "Parent > Child") from
        get_sections response. Each section has id, name, parent_id.
        """
        if not sections:
            return {}
        by_id = {s["id"]: s for s in sections}
        result = {}
        for s in sections:
            path = [s.get("name", "")]
            parent_id = s.get("parent_id")
            while parent_id and parent_id in by_id:
                path.insert(0, by_id[parent_id].get("name", ""))
                parent_id = by_id[parent_id].get("parent_id")
            result[s["id"]] = " > ".join(p for p in path if p).strip() or str(s["id"])
        return result

    def transform_to_csv_format(
        self,
        test_cases: List[Dict],
        case_type_map: Optional[Dict[int, str]] = None,
        custom_field_options: Optional[Dict[str, Dict[Any, str]]] = None,
        section_id_to_label: Optional[Dict[Tuple[int, Optional[int], int], str]] = None,
    ) -> pd.DataFrame:
        """
        Transform TestRail API response to CSV-like DataFrame format.
        Resolves type_id, custom field IDs, and section_id to human-readable labels when maps are provided.

        Args:
            test_cases: List of test case dicts from TestRail API
            case_type_map: Optional id -> name for case types (from get_case_types)
            custom_field_options: Optional dict of system_name -> {value_id: label} for custom fields
            section_id_to_label: Optional (project_id, suite_id, section_id) -> section name/hierarchy

        Returns:
            DataFrame matching expected CSV structure
        """
        transformed_data = []
        case_type_map = case_type_map or {}
        custom_field_options = custom_field_options or {}
        section_id_to_label = section_id_to_label or {}
        req_field = self._get_requirement_field_system_name()

        for case in test_cases:
            # Extract and clean data
            case_id = f"C{case.get('id', '')}"
            title = case.get('title', '')
            priority = self._map_priority(case.get('priority_id'))

            # Handle custom fields (these vary by TestRail configuration)
            # Preconditions: standard is custom_preconds; some instances use custom_preconditions
            raw_preconds = case.get('custom_preconds') or case.get('custom_preconditions') or ''
            preconditions = self._clean_html(raw_preconds)
            expected_result = self._clean_html(case.get('custom_expected', ''))

            # Handle steps (can be in different formats)
            steps_raw = case.get('custom_steps', '') or case.get('custom_steps_separated', [])
            if isinstance(steps_raw, list):
                steps = '\n'.join([
                    f"{i+1}. {self._clean_html(step.get('content', ''))}"
                    for i, step in enumerate(steps_raw)
                ])
            else:
                steps = self._clean_html(steps_raw)

            # Resolve type_id to label (e.g. 9 -> "Functional")
            raw_type = case.get('type_id')
            if raw_type is not None and case_type_map:
                case_type = case_type_map.get(int(raw_type), str(raw_type))
            else:
                case_type = str(raw_type) if raw_type is not None else ""

            # Resolve custom dropdown/multi-select fields to labels
            platform = case.get('custom_platform', '')
            opts_platform = custom_field_options.get("custom_platform")
            if opts_platform is not None:
                platform = self._resolve_custom_field_value(platform, opts_platform)
            if platform == "":
                platform = "api / backend"

            execution_mode = case.get('custom_automation_type', '')
            opts_automation = custom_field_options.get("custom_automation_type")
            if opts_automation is not None:
                execution_mode = self._resolve_custom_field_value(execution_mode, opts_automation)
            if execution_mode == "":
                execution_mode = "Automatable"

            raw_section_id = case.get('section_id')
            if raw_section_id is not None and raw_section_id != "":
                pid = case.get("_project_id")
                suid = case.get("_suite_id")
                section = section_id_to_label.get((pid, suid, int(raw_section_id)), str(raw_section_id))
            else:
                section = ""

            requirement = (case.get(req_field) or '').strip() if req_field else ''
            transformed_data.append({
                'ID': case_id,
                'Title': title,
                'Execution Mode': execution_mode,
                'Expected Result': expected_result,
                'Platform': platform,
                'Preconditions': preconditions,
                'Priority': priority,
                'Section Hierarchy': section,
                'Steps': steps,
                'Type': case_type,
                'Suite': str(case.get('_suite_name', '') or 'Default').strip() or 'Default',
                'Requirement': requirement,
            })

        return pd.DataFrame(transformed_data)
    
    def fetch_and_transform(
        self,
        project_ids: List[int],
        delta_days: Optional[int] = None,
        progress_callback: Optional[Any] = None,
        log_callback: Optional[Any] = None
    ) -> pd.DataFrame:
        """
        Fetch test cases from multiple projects and transform to CSV format.
        
        Args:
            project_ids: List of project IDs to sync
            delta_days: Only fetch cases updated in last N days (None = all)
            progress_callback: Optional callable(projects_done, projects_total, test_cases_so_far, message)
        log_callback: Optional callable(message) for running log lines
            
        Returns:
            Combined DataFrame of all test cases
        """
        all_cases = []
        section_id_to_label: Dict[Tuple[int, Optional[int], int], str] = {}
        updated_after = None
        total_projects = len(project_ids)

        if delta_days and delta_days > 0:
            updated_after = datetime.now() - timedelta(days=delta_days)
            msg = f"Fetching test cases updated since {updated_after.strftime('%Y-%m-%d %H:%M:%S')}"
            print(f"📅 {msg}")
            if log_callback:
                log_callback(msg)
        else:
            msg = "Fetching all test cases (no date filter)"
            print(f"📅 {msg}")
            if log_callback:
                log_callback(msg)

        for idx, project_id in enumerate(project_ids):
            print(f"\n🔄 Processing Project ID: {project_id} ({idx + 1}/{total_projects})")
            if progress_callback:
                progress_callback(idx, total_projects, len(all_cases), f"Fetching project {project_id}...")
            if log_callback:
                log_callback(f"Processing project {project_id} ({idx + 1}/{total_projects})...")

            try:
                # Get project details to check suite mode
                project = self.get_project(project_id)
                suite_mode = project.get('suite_mode', 1)  # Default to 1 (single suite)
                project_name = project.get('name', str(project_id))
                print(f"   Project: {project_name}, Suite Mode: {suite_mode}")
                
                # Fetch suites based on mode
                suites = []
                try:
                    if suite_mode != 1:
                        suites = self.get_suites(project_id)
                        print(f"   Found {len(suites)} suite(s)")
                except Exception as e:
                    print(f"   ⚠️ Could not fetch suites (possibly single-suite mode or API difference): {e}")
                
                # Logic to fetch cases
                if not suites:
                    # Single suite mode or fallback: tag with project name so sync can use one file per "suite" (project)
                    print(f"   📂 Fetching from project (Single Suite Mode)")
                    if log_callback:
                        log_callback(f"  Project {project_name}: fetching (single suite)")
                    try:
                        sections = self.get_sections(project_id)
                        hier_map = self._build_section_hierarchy_map(sections)
                        for sid, label in hier_map.items():
                            section_id_to_label[(project_id, 0, sid)] = label
                    except Exception as e:
                        print(f"   ⚠️ Could not fetch sections for project: {e}")
                    cases = self.get_test_cases(project_id, updated_after=updated_after)
                    print(f"      Retrieved {len(cases)} test case(s)")
                    if log_callback:
                        log_callback(f"  Project {project_name}: retrieved {len(cases)} test cases")
                    for c in cases:
                        c['_suite_name'] = project_name or f"Project_{project_id}"
                        c['_suite_id'] = 0
                        c['_project_id'] = project_id
                        all_cases.append(c)
                else:
                    # Multi-suite mode: tag each case with suite name for one-file-per-suite sync
                    for suite in suites:
                        suite_id = suite['id']
                        suite_name = suite['name']
                        print(f"   📂 Fetching from suite: {suite_name}")
                        if log_callback:
                            log_callback(f"  Suite: {suite_name}")

                        try:
                            sections = self.get_sections(project_id, suite_id)
                            hier_map = self._build_section_hierarchy_map(sections)
                            for sid, label in hier_map.items():
                                section_id_to_label[(project_id, suite_id, sid)] = label
                        except Exception as e:
                            print(f"   ⚠️ Could not fetch sections for suite {suite_name}: {e}")
                        try:
                            # Get test cases from suite
                            cases = self.get_test_cases(project_id, suite_id, updated_after)
                            print(f"      Retrieved {len(cases)} test case(s)")
                            if log_callback:
                                log_callback(f"    Retrieved {len(cases)} test cases")
                            for c in cases:
                                c['_suite_name'] = suite_name
                                c['_suite_id'] = suite_id
                                c['_project_id'] = project_id
                                all_cases.append(c)
                        except Exception as e:
                            print(f"      ❌ Failed to fetch cases from suite {suite_name}: {e}")
                            if log_callback:
                                log_callback(f"    Failed: {e}")

                if progress_callback:
                    progress_callback(idx + 1, total_projects, len(all_cases), f"Project {project_name}: {len(all_cases)} test cases so far")
                            
            except Exception as e:
                print(f"❌ Failed to process Project ID {project_id}: {e}")
                if log_callback:
                    log_callback(f"Project {project_id} failed: {e}")
                if progress_callback:
                    progress_callback(idx + 1, total_projects, len(all_cases), f"Project {project_id} failed: {e}")

        if progress_callback:
            progress_callback(total_projects, total_projects, len(all_cases), f"Fetched {len(all_cases)} test cases from {total_projects} project(s)")
        if log_callback:
            log_callback(f"Total fetched: {len(all_cases)} test cases from {total_projects} project(s)")

        print(f"\n✅ Total test cases fetched: {len(all_cases)}")

        if not all_cases:
            if log_callback:
                log_callback("No test cases found (check project IDs and delta_days)")
            print("⚠️  No test cases found")
            return pd.DataFrame()

        # Newest first: sort by case ID descending so CSV/ChromaDB store and retrieval see latest entries first
        all_cases.sort(key=lambda c: int(c.get("id") or 0), reverse=True)
        if log_callback:
            log_callback("Sorted test cases by ID descending (newest first)")

        # Resolve type_id and custom field IDs to labels
        if log_callback:
            log_callback("Fetching case types and custom field options...")
        print("🔄 Fetching case types and custom field options for label resolution...")
        case_type_map = self._build_case_type_map()
        custom_field_options = self._build_custom_field_option_maps()
        if case_type_map:
            print(f"   Resolved {len(case_type_map)} case type(s)")
        if custom_field_options:
            print(f"   Resolved options for {len(custom_field_options)} custom field(s)")

        # Transform to CSV format (with ID->label maps so Type, Platform, etc. show names not IDs)
        if log_callback:
            log_callback("Transforming to CSV format...")
        print("🔄 Transforming data to CSV format...")
        df = self.transform_to_csv_format(
            all_cases,
            case_type_map=case_type_map,
            custom_field_options=custom_field_options,
            section_id_to_label=section_id_to_label,
        )
        print(f"✅ Transformation complete. DataFrame shape: {df.shape}")

        return df
