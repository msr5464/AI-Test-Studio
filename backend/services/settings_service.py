"""
Settings Service
================
Manages admin-configurable settings persisted directly to config/.env.
The .env file is updated in-place (comments and unrelated keys preserved),
making it the single source of truth for all configuration.
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

# Canonical path to the config/.env file
_ENV_FILE = Path(__file__).parent.parent.parent / "config" / ".env"

# ---------------------------------------------------------------------------
# Schema definition
# ---------------------------------------------------------------------------
# Each entry describes one configurable setting.
# Fields:
#   key         - internal key used in API and frontend
#   env_var     - exact os.environ / .env key
#   label       - display label in UI
#   description - tooltip / help text
#   type        - text | password | number | boolean | select | time
#   category    - testrail | confluence | llm | chat | requirements
#   default     - default value (used when key absent from env)
#   sensitive   - if True, value is partially masked in API responses
#   options     - list of {"value": ..., "label": ...} for type=select
#   min / max   - for type=number

SETTINGS_SCHEMA: List[Dict[str, Any]] = [
    # ── TestRail ─────────────────────────────────────────────────────────────
    {
        "key": "testrail_url",
        "env_var": "TESTRAIL_URL",
        "label": "TestRail URL",
        "description": "Base URL of your TestRail instance (e.g. https://company.testrail.io)",
        "type": "text",
        "category": "testrail",
        "default": "",
        "sensitive": False,
    },
    {
        "key": "testrail_email",
        "env_var": "TESTRAIL_EMAIL",
        "label": "TestRail Email",
        "description": "Email address used for TestRail API authentication",
        "type": "text",
        "category": "testrail",
        "default": "",
        "sensitive": False,
    },
    {
        "key": "testrail_api_key",
        "env_var": "TESTRAIL_API_KEY",
        "label": "TestRail API Key",
        "description": "API key for TestRail (generate from My Settings → API Keys in TestRail)",
        "type": "password",
        "category": "testrail",
        "default": "",
        "sensitive": True,
    },
    {
        "key": "testrail_project_ids",
        "env_var": "TESTRAIL_PROJECT_IDS",
        "label": "Project IDs",
        "description": "Comma-separated TestRail project IDs to sync (e.g. 1,2,3)",
        "type": "text",
        "category": "testrail",
        "default": "",
        "sensitive": False,
    },
    {
        "key": "testrail_delta_days",
        "env_var": "TESTRAIL_DELTA_DAYS",
        "label": "Delta Days",
        "description": "Only sync test cases updated in the last N days (0 = fetch all)",
        "type": "number",
        "category": "testrail",
        "default": 0,
        "sensitive": False,
        "min": 0,
        "max": 3650,
    },
    {
        "key": "testrail_push_enabled",
        "env_var": "TESTRAIL_PUSH_ENABLED",
        "label": "Enable Push to TestRail",
        "description": "Allow pushing AI-generated test cases back to TestRail",
        "type": "boolean",
        "category": "testrail",
        "default": False,
        "sensitive": False,
    },
    {
        "key": "testrail_schedule_enabled",
        "env_var": "TESTRAIL_SCHEDULE_ENABLED",
        "label": "Auto-Sync Daily",
        "description": "Automatically run TestRail sync once a day at the scheduled time (UTC)",
        "type": "boolean",
        "category": "testrail",
        "default": False,
        "sensitive": False,
        "inline_with": "testrail_schedule_time",
    },
    {
        "key": "testrail_schedule_time",
        "env_var": "TESTRAIL_SCHEDULE_TIME",
        "label": "Auto-Sync Time (UTC)",
        "description": "Time of day to run the automatic TestRail sync (24-hour UTC, e.g. 02:00)",
        "type": "time",
        "category": "testrail",
        "default": "02:00",
        "sensitive": False,
    },
    # ── Confluence ────────────────────────────────────────────────────────────
    {
        "key": "confluence_url",
        "env_var": "CONFLUENCE_URL",
        "label": "Confluence URL",
        "description": "Confluence instance URL — must end with /wiki (e.g. https://company.atlassian.net/wiki)",
        "type": "text",
        "category": "confluence",
        "default": "",
        "sensitive": False,
    },
    {
        "key": "confluence_email",
        "env_var": "CONFLUENCE_EMAIL",
        "label": "Confluence Email",
        "description": "Email address used for Confluence API authentication",
        "type": "text",
        "category": "confluence",
        "default": "",
        "sensitive": False,
    },
    {
        "key": "confluence_api_token",
        "env_var": "CONFLUENCE_API_TOKEN",
        "label": "Confluence API Token",
        "description": "Atlassian API token (generate at id.atlassian.com → Security → API tokens)",
        "type": "password",
        "category": "confluence",
        "default": "",
        "sensitive": True,
    },
    {
        "key": "confluence_cql",
        "env_var": "CONFLUENCE_CQL",
        "label": "CQL Filter",
        "description": "Confluence Query Language filter for which pages to sync (e.g. type=page AND space=QA)",
        "type": "text",
        "category": "confluence",
        "default": "type=page",
        "sensitive": False,
    },
    {
        "key": "confluence_delta_days",
        "env_var": "CONFLUENCE_DELTA_DAYS",
        "label": "Delta Days",
        "description": "Only sync pages updated in the last N days (0 = fetch all, no date filter)",
        "type": "number",
        "category": "confluence",
        "default": 0,
        "sensitive": False,
        "min": 0,
        "max": 3650,
    },
    {
        "key": "confluence_schedule_enabled",
        "env_var": "CONFLUENCE_SCHEDULE_ENABLED",
        "label": "Auto-Sync Daily",
        "description": "Automatically run Confluence sync once a day at the scheduled time (UTC)",
        "type": "boolean",
        "category": "confluence",
        "default": False,
        "sensitive": False,
        "inline_with": "confluence_schedule_time",
    },
    {
        "key": "confluence_schedule_time",
        "env_var": "CONFLUENCE_SCHEDULE_TIME",
        "label": "Auto-Sync Time (UTC)",
        "description": "Time of day to run the automatic Confluence sync (24-hour UTC, e.g. 03:00)",
        "type": "time",
        "category": "confluence",
        "default": "03:00",
        "sensitive": False,
    },
    # ── LLM ──────────────────────────────────────────────────────────────────
    {
        "key": "company_name",
        "env_var": "COMPANY_NAME",
        "label": "Company Name",
        "description": "Your company/product name — used in AI-generated test prompts for context",
        "type": "text",
        "category": "llm",
        "default": "your company",
        "sensitive": False,
    },
    {
        "key": "llm_provider",
        "env_var": "LLM_PROVIDER",
        "label": "LLM Provider",
        "description": "Which LLM backend to use for AI features",
        "type": "select",
        "category": "llm",
        "default": "ollama",
        "sensitive": False,
        "options": [
            {"value": "ollama", "label": "Ollama (local)"},
            {"value": "openai", "label": "OpenAI"},
            {"value": "gemini", "label": "Google Gemini"},
        ],
    },
    {
        "key": "ollama_base_url",
        "env_var": "OLLAMA_BASE_URL",
        "label": "Ollama Base URL",
        "description": "URL of the Ollama API server (e.g. http://localhost:11434)",
        "type": "text",
        "category": "llm",
        "default": "http://localhost:11434",
        "sensitive": False,
    },
    {
        "key": "ollama_model",
        "env_var": "OLLAMA_MODEL",
        "label": "Ollama Model",
        "description": "Ollama model name to use (e.g. llama3, mistral, phi3)",
        "type": "text",
        "category": "llm",
        "default": "llama3",
        "sensitive": False,
    },
    {
        "key": "openai_api_key",
        "env_var": "OPENAI_API_KEY",
        "label": "OpenAI API Key",
        "description": "OpenAI API key (starts with sk-...)",
        "type": "password",
        "category": "llm",
        "default": "",
        "sensitive": True,
    },
    {
        "key": "openai_model",
        "env_var": "OPENAI_MODEL",
        "label": "OpenAI Model",
        "description": "OpenAI model name to use (e.g. gpt-4o, gpt-4-turbo)",
        "type": "text",
        "category": "llm",
        "default": "gpt-4o",
        "sensitive": False,
    },
    {
        "key": "google_api_key",
        "env_var": "GOOGLE_API_KEY",
        "label": "Google API Key",
        "description": "Google AI API key for Gemini models",
        "type": "password",
        "category": "llm",
        "default": "",
        "sensitive": True,
    },
    {
        "key": "gemini_model",
        "env_var": "GEMINI_MODEL",
        "label": "Gemini Model",
        "description": "Gemini model name to use (e.g. gemini-1.5-pro)",
        "type": "text",
        "category": "llm",
        "default": "gemini-1.5-pro",
        "sensitive": False,
    },
    # ── Common RAG & Retrieval ────────────────────────────────────────────────
    {
        "key": "chunk_size",
        "env_var": "CHUNK_SIZE",
        "label": "Chunk Size",
        "description": "Number of characters per document chunk when indexing. Smaller = more precise, larger = more context.",
        "type": "number",
        "category": "llm",
        "default": 1000,
        "sensitive": False,
        "min": 100,
        "max": 10000,
    },
    {
        "key": "chunk_overlap",
        "env_var": "CHUNK_OVERLAP",
        "label": "Chunk Overlap",
        "description": "Character overlap between adjacent chunks (must be less than chunk size)",
        "type": "number",
        "category": "llm",
        "default": 200,
        "sensitive": False,
        "min": 0,
        "max": 5000,
    },
    # ── Chat Specifics ────────────────────────────────────────────────────────
    {
        "key": "chat_retrieval_k",
        "env_var": "CHAT_RETRIEVAL_K",
        "label": "Retrieval K",
        "description": "Number of document chunks to retrieve per chat query",
        "type": "number",
        "category": "chat",
        "default": 10,
        "sensitive": False,
        "min": 1,
        "max": 50,
    },
    {
        "key": "chat_min_similarity_threshold",
        "env_var": "CHAT_MIN_SIMILARITY_THRESHOLD",
        "label": "Min Similarity %",
        "description": "Minimum similarity percentage (0–100) for a chunk to appear as a source",
        "type": "number",
        "category": "chat",
        "default": 15.0,
        "sensitive": False,
        "min": 0,
        "max": 100,
    },
    {
        "key": "chat_use_hybrid_search",
        "env_var": "CHAT_USE_HYBRID_SEARCH",
        "label": "Hybrid Search",
        "description": "Combine semantic (vector) search with BM25 keyword search for better recall",
        "type": "boolean",
        "category": "chat",
        "default": False,
        "sensitive": False,
    },
    {
        "key": "chat_use_reranking",
        "env_var": "CHAT_USE_RERANKING",
        "label": "Reranking",
        "description": "Re-rank retrieved chunks with a CrossEncoder model for higher precision",
        "type": "boolean",
        "category": "chat",
        "default": False,
        "sensitive": False,
    },
    {
        "key": "show_matching_sources",
        "env_var": "SHOW_MATCHING_SOURCES",
        "label": "Show Matching Sources",
        "description": "Display source document references below each chat response",
        "type": "boolean",
        "category": "chat",
        "default": False,
        "sensitive": False,
    },
    # ── Requirements Specifics ────────────────────────────────────────────────
    {
        "key": "requirement_retrieval_k",
        "env_var": "REQUIREMENT_RETRIEVAL_K",
        "label": "Retrieval K",
        "description": "Number of related tests/specs to retrieve when analysing requirements",
        "type": "number",
        "category": "requirements",
        "default": 10,
        "sensitive": False,
        "min": 1,
        "max": 50,
    },
    {
        "key": "requirement_tests_similarity_threshold",
        "env_var": "REQUIREMENT_TESTS_SIMILARITY_THRESHOLD",
        "label": "Retrieval Similarity % (Tests)",
        "description": "Min similarity % to retrieve a related test case (0–100). Raise to keep only strong matches; lower to cast a wider net.",
        "type": "number",
        "category": "requirements",
        "default": 60.0,
        "sensitive": False,
        "min": 0,
        "max": 100,
        "inline_with": "requirement_specs_similarity_threshold",
    },
    {
        "key": "requirement_specs_similarity_threshold",
        "env_var": "REQUIREMENT_SPECS_SIMILARITY_THRESHOLD",
        "label": "Retrieval Similarity % (Specs)",
        "description": "Min similarity % to retrieve a related Confluence spec page (0–100). Typically lower than tests because spec pages use broader business language.",
        "type": "number",
        "category": "requirements",
        "default": 50.0,
        "sensitive": False,
        "min": 0,
        "max": 100,
    },
    {
        "key": "requirement_use_hybrid_search",
        "env_var": "REQUIREMENT_USE_HYBRID_SEARCH",
        "label": "Hybrid Search",
        "description": "Use hybrid search when finding related tests for requirement analysis",
        "type": "boolean",
        "category": "requirements",
        "default": False,
        "sensitive": False,
    },
    {
        "key": "requirement_use_reranking",
        "env_var": "REQUIREMENT_USE_RERANKING",
        "label": "Reranking",
        "description": "Rerank retrieved tests with CrossEncoder during requirement analysis",
        "type": "boolean",
        "category": "requirements",
        "default": False,
        "sensitive": False,
    },
    {
        "key": "requirement_min_tests_per_priority",
        "env_var": "REQUIREMENT_MIN_TESTS_PER_PRIORITY",
        "label": "Min Tests Per Priority",
        "description": "Don't generate new tests for a priority level (P0/P1/P2/P3) if at least this many related tests already exist",
        "type": "number",
        "category": "requirements",
        "default": 3,
        "sensitive": False,
        "min": 0,
        "max": 50,
    },
    {
        "key": "requirement_tests_coverage_min_similarity",
        "env_var": "REQUIREMENT_TESTS_COVERAGE_MIN_SIMILARITY",
        "label": "Coverage Similarity % (Tests)",
        "description": "Min similarity % for a test to count toward coverage (0–100). Tests below this score are retrieved but excluded from the coverage count.",
        "type": "number",
        "category": "requirements",
        "default": 80,
        "sensitive": False,
        "min": 0,
        "max": 100,
    },
    {
        "key": "requirement_parallel_processing",
        "env_var": "REQUIREMENT_PARALLEL_PROCESSING",
        "label": "Parallel Processing",
        "description": "Process requirements in parallel (faster) or sequentially (easier to debug, preserves order)",
        "type": "boolean",
        "category": "requirements",
        "default": True,
        "sensitive": False,
    },
]

# Build a lookup dict for quick access by key
_SCHEMA_BY_KEY: Dict[str, Dict[str, Any]] = {s["key"]: s for s in SETTINGS_SCHEMA}


class SettingsService:
    """
    Manages admin-configurable application settings.

    Persistence: config/.env (updated in-place; all comments and unrelated keys preserved)
    Runtime effect: values are in os.environ (loaded from .env by dotenv at startup and
    after each save), so get_config() and os.getenv() across the app see them immediately.
    """

    def __init__(self, env_path: Optional[Path] = None):
        self._env_path = Path(env_path) if env_path else _ENV_FILE
        self._migrate_from_json()   # one-time: import any legacy app_settings.json values
        self.apply_to_env()

    # ── Startup ───────────────────────────────────────────────────────────────

    def apply_to_env(self) -> None:
        """
        (Re-)load config/.env into os.environ and reset the RAGConfig singleton.
        Called at startup and after each save so all services see current values.
        """
        try:
            from dotenv import load_dotenv
            load_dotenv(self._env_path, override=True)
        except Exception as e:
            print(f"⚠️  Failed to load .env: {e}")
        self._reset_config()

    # ── Read ──────────────────────────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        """
        Return the setting value with priority:
          1. Current os.environ[env_var]  (populated from .env by apply_to_env)
          2. Schema default
          3. Caller-supplied default
        """
        entry = _SCHEMA_BY_KEY.get(key)
        if entry:
            val = os.environ.get(entry["env_var"])
            if val is not None:
                return val
            return entry.get("default", default)
        return default

    def get_all_for_api(self) -> Dict[str, Any]:
        """
        Return schema + current values safe for the frontend.
        Sensitive fields are partially masked (first 3 + ***** + last 3 chars).
        """
        values: Dict[str, Any] = {}
        for entry in SETTINGS_SCHEMA:
            key = entry["key"]
            raw = self.get(key)
            if entry["sensitive"]:
                display = self._partial_mask(str(raw)) if (raw not in (None, "")) else ""
            else:
                display = raw if raw is not None else entry.get("default", "")
            values[key] = display
        return {"schema": SETTINGS_SCHEMA, "values": values}

    # ── Write ─────────────────────────────────────────────────────────────────

    def set_many(self, updates: Dict[str, Any]) -> None:
        """
        Save a batch of setting updates to config/.env and os.environ.

        Rules:
          - Unknown keys (not in SETTINGS_SCHEMA) are silently ignored for security.
          - Sensitive fields: if the submitted value matches the partial mask of the
            currently stored value, skip the update to preserve the real secret.
        """
        env_updates: Dict[str, str] = {}  # env_var → string value to write to .env

        for key, value in updates.items():
            entry = _SCHEMA_BY_KEY.get(key)
            if entry is None:
                continue
            if entry["sensitive"]:
                current = str(self.get(key, "") or "")
                if current and value == self._partial_mask(current):
                    continue  # admin submitted the display mask unchanged — preserve real value
            value = self._coerce(entry, value)
            env_str = self._to_env_str(value)
            env_updates[entry["env_var"]] = env_str
            os.environ[entry["env_var"]] = env_str  # immediate in-process effect

        if env_updates:
            self._update_env_file(env_updates)

        self._reset_config()

    # ── .env file I/O ─────────────────────────────────────────────────────────

    def _update_env_file(self, updates: Dict[str, str]) -> None:
        """
        Update config/.env in-place, preserving all comments and unrelated keys.

        For each key in updates:
          - If an uncommented KEY=... line exists → replace that line's value.
          - If not found → append under a trailing section at the end of the file.
        """
        try:
            lines = (
                self._env_path.read_text(encoding="utf-8").splitlines(keepends=True)
                if self._env_path.exists()
                else []
            )
            remaining = dict(updates)

            # Pass 1: update existing lines in-place
            for i, line in enumerate(lines):
                m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*=', line)
                if m and m.group(1) in remaining:
                    env_var = m.group(1)
                    lines[i] = f"{env_var}={remaining.pop(env_var)}\n"

            # Pass 2: append any keys not already present in the file
            if remaining:
                if lines and not lines[-1].endswith("\n"):
                    lines[-1] += "\n"
                lines.append("\n# --- Settings updated via Admin UI ---\n")
                for env_var, val in remaining.items():
                    lines.append(f"{env_var}={val}\n")

            self._env_path.write_text("".join(lines), encoding="utf-8")
        except Exception as e:
            print(f"⚠️  Failed to update .env: {e}")

    # ── Migration ─────────────────────────────────────────────────────────────

    def _migrate_from_json(self) -> None:
        """
        One-time migration: if storage/app_settings.json exists, write its values
        into config/.env, then rename the file so it is not re-applied on next restart.
        """
        import json as _json
        json_path = Path("storage") / "app_settings.json"
        if not json_path.exists():
            return
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
            if not isinstance(data, dict) or not data:
                json_path.rename(json_path.with_suffix(".json.migrated"))
                return
            env_updates: Dict[str, str] = {}
            for key, value in data.items():
                entry = _SCHEMA_BY_KEY.get(key)
                if entry:
                    env_updates[entry["env_var"]] = self._to_env_str(value)
            if env_updates:
                self._update_env_file(env_updates)
                print(f"✅ Migrated {len(env_updates)} settings from app_settings.json → config/.env")
            json_path.rename(json_path.with_suffix(".json.migrated"))
        except Exception as e:
            print(f"⚠️  Migration from app_settings.json failed: {e}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _partial_mask(value: str) -> str:
        """Show first 3 and last 3 characters; mask the middle with 10 stars.
        For values <= 6 chars, mask entirely as *** to avoid exposing the full value."""
        s = str(value)
        if len(s) <= 6:
            return "***"
        return s[:3] + "**********" + s[-3:]

    @staticmethod
    def _to_env_str(value: Any) -> str:
        """Convert a Python value to a string suitable for .env and os.environ."""
        if isinstance(value, bool):
            return "True" if value else "False"
        return str(value)

    @staticmethod
    def _coerce(entry: Dict[str, Any], value: Any) -> Any:
        """Coerce submitted value to the appropriate Python type."""
        t = entry.get("type")
        if t == "boolean":
            if isinstance(value, bool):
                return value
            return str(value).lower() in ("true", "1", "yes", "on")
        if t == "number":
            if value == "" or value is None:
                return entry.get("default", 0)
            try:
                default = entry.get("default", 0)
                return int(value) if isinstance(default, int) else float(value)
            except (TypeError, ValueError):
                return entry.get("default", 0)
        # text, password, select, time → string
        return str(value) if value is not None else ""

    @staticmethod
    def _reset_config() -> None:
        """Reset the RAGConfig singleton so the next get_config() call re-reads os.environ."""
        try:
            from backend.rag.settings import reset_config
            reset_config()
        except Exception:
            pass
