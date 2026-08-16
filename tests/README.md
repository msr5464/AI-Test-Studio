# Knowledge-AI Tests

## ⚠️ After any UI/frontend change: run UI self-test

**Whenever you change the UI (customer portal, admin portal, or any frontend HTML/JS/CSS), run the UI self-test checklist below** so we don’t ship broken flows.

- **Customer portal:** `http://localhost:5001/` — Ask, Requirement Analysis, Generated tests (select all/none, Push selected).
- **Admin portal:** `http://localhost:5001/admin` — TestRail Sync, **Confluence Sync**, documents, ChromaDB.

See **[UI self-test checklist](#ui-self-test-checklist)** below.

---

## TestRail Sync (Sync button and process)

Automated tests ensure the Sync button and sync process work end-to-end.

### Run all sync-related tests

```bash
# From project root, with venv activated
python3 -m pytest tests/test_testrail_sync_service.py -v
```

### Run requirement-analysis tests

```bash
# Unit tests only (mocked service; no RAG/LLM needed)
python3 -m pytest tests/test_requirement_analysis.py -v

# Include integration test (real RAG + LLM; requires config/.env)
python3 -m pytest tests/test_requirement_analysis.py -v -m integration
```

For self-testing, set the LLM (`LLM_PROVIDER`, API keys) and ChromaDB values in `config/.env` (copy from `config/env.example`).

### Requirement analysis e2e flow (Confluence + TestRail context)

Unit tests verify the ideal flow: Confluence prior context → TestRail context → merged context for generation.

| Test | What it verifies |
|------|------------------|
| **analyze() calls find_related_specs** | `find_related_specs(spec_text, k=10)` is called with full spec text |
| **specs_context passed to generation** | `_generate_tests_for_requirement(..., specs_context=related_specs)` is called when generating new tests |
| **result includes related_specs** | Analysis result has `related_specs` (prior Confluence chunks) |
| **RAGService.find_related_specs** | Returns `[]` when no vectorstore; returns formatted dicts (title, content, url, similarity_score) when vectorstore returns docs |

### E2E self-tests: priority order and spec coverage

Generated test cases must be in priority order **P0 > P1 > P2 > P3** and cover every point in the spec.

| Test | What it verifies |
|------|------------------|
| **test_generated_tests_sorted_by_priority_p0_first** | LLM returns unsorted priorities; service returns tests ordered P0, P1, P2, P3 (critical first). |
| **test_generated_tests_have_required_fields_and_valid_priority** | Each test has title, priority in {P0,P1,P2,P3}, and steps or expected_result. |
| **test_analyze_result_generated_tests_ordered_by_priority** | Full analyze() returns generated_tests with priority order P0 then P1 then P2 then P3. |
| **Integration test** | When generated_tests exist, each test has valid priority and tests are ordered P0>P1>P2>P3; each has title and steps or expected_result. |

Run: `python3 -m pytest tests/test_requirement_analysis.py -v -m "not integration"` (or with venv: `./venv/bin/python -m pytest ...`).

**Note:** Use the project venv so Flask and dependencies are available (e.g. `./venv/bin/python -m pytest ...`). API tests that call `create_app()` require the full environment.

### Tests needing update / Update with AI (suggest-case-update, update-case)

| Test | What it verifies |
|------|------------------|
| **TestAssessUpdatesNeedsUpdateAndPartial** | `_assess_updates` puts both `needs_update` and `partial` statuses into the needing list; `ok` stays in ok_ids. |
| **TestSuggestCaseUpdateAndUpdateInTestrail** | `suggest_case_update` returns a dict (title, steps, preconditions, expected_result, priority) when LLM returns valid JSON; returns None when invalid; **test_suggest_case_update_real_prompt_generates_updated_testcase** runs the full path and asserts the service returns the expected updated test case dict from a simulated LLM response; `update_case_in_testrail` returns success when connector is called and push is enabled; returns error when push is disabled. |
| **TestRequirementAnalysisSuggestAndUpdateAPI** | POST suggest-case-update: 400 when params missing, 200 with suggestion, 500 when service returns None; POST update-case: 400 when title/testrail_id missing, 200 when service returns success. |
| **TestRequirementAnalysisCreateCaseAPI** | POST create-case: 400 when section_id or title missing, 200 when service returns success (testrail_id), 400 when service returns failure. |

### TestRail connector (update_case)

| Test | What it verifies |
|------|------------------|
| **tests/test_testrail_connector.py** | `update_case` sends only provided fields to the API; includes priority_id when given; when no fields provided, calls `get_case` and does not POST. |

Run: `./venv/bin/python -m pytest tests/test_testrail_connector.py tests/test_requirement_analysis.py -v -m "not integration"` (excludes slow integration test).

**Integration (optional):** `test_suggest_case_update_integration` calls the real suggest-case-update API with the LLM; run with `pytest tests/test_requirement_analysis.py -v -m integration -k suggest_case_update`. Skips if LLM is not configured.

### What is covered (sync)

| Test | What it verifies |
|------|------------------|
| **Unit: is_syncing cleared on validation failure** | When sync fails validation, `is_syncing` is set back to `False` (no stuck "Sync already in progress") |
| **Unit: is_syncing cleared on connector exception** | When the TestRail connector raises, `is_syncing` is cleared in `finally` |
| **Unit: progress and status** | Progress callback runs, success record has `projects_count` and `test_cases_fetched`, and `get_sync_status()` returns `current_sync` structure |
| **Unit: get_sync_status structure** | Response includes `last_sync`, `is_syncing`, `current_sync`, `latest_sync_record`, `configured_projects` |
| **API: GET /sync/status** | Returns 200 and status object with `is_syncing`, `current_sync`, and `sync_log` (so UI can show running log) |
| **API: POST /sync/testrail** | Returns 202 when sync started, 409 when already syncing |
| **Frontend: Sync button** | Admin page has `syncNowBtn`, click handler attached via `addEventListener` (no inline `onclick` that can cause ReferenceError) |
| **Frontend: 409 handling** | When server returns 409 (already in progress), UI shows "already running" and starts polling instead of "Sync failed" |
| **Frontend: Double-click** | `syncInProgress` guard prevents multiple simultaneous sync requests |
| **Frontend: Running log** | `syncLogContainer` and `syncLog` exist and container is visible by default |
| **Stale sync recovery** | If `is_syncing` has been true for > 30 min, `get_sync_status` and POST clear it so a new sync can start; POST after stale returns 202 |

### E2E UI test: running log shown in admin (optional)

A self-test uses a real browser (Playwright) to ensure the **running log** is displayed when the API returns `sync_log`:

```bash
pip install playwright
playwright install chromium
python3 -m pytest tests/test_sync_ui_e2e.py -v
```

- Starts a mock server that serves the admin page and returns `sync_log` from `GET /api/admin/sync/status`.
- Opens the admin UI in headless Chromium; after `loadSyncStatus()` runs, the **Running log** area must contain the mock log lines.
- Skips automatically if `playwright` is not installed.

### Manual E2E (optional)

1. Start the app: `./scripts/run.sh` (or `python backend/app.py`).
2. Open `/admin`, log in as admin.
3. Click **Sync Now**: button should show "Syncing...", then success or error; no "triggerTestRailSync is not defined" or "Unexpected token '||'".
4. If sync runs, progress (Projects X/Y, test cases so far) should update while syncing.

### Requirement Analysis E2E UI (manual / browser)

1. Start the app and open `http://localhost:5001/`.
2. Click **Requirement Analysis** tab.
3. Paste a short spec (e.g. `REQ-001: User must reset password.\nREQ-002: System shall send email.`) and click **Analyze Requirements**.
4. After results load, verify:
   - Summary cards (Requirements, With coverage, Needing update, Uncovered, Generated).
   - Tabs: Related tests, Tests needing update, Uncovered, **Generated tests**.
   - Push options visible when there are generated tests: hint “Select which generated tests to push…”, **Use same section as related tests** / **Choose section manually**, Project/Suite/Section (if manual).
5. Open **Generated tests** tab:
   - **Select all** / **Select none** links and **Push selected to TestRail** button.
   - Each generated test has a **checkbox**; only selected tests are pushed.
6. Select one or more tests, choose section (or “Use same section as related tests”), click **Push selected to TestRail**; toast shows push result.
7. **Note:** The push endpoint is `POST /api/customer/requirement-analysis/push`. If the server was started before this route was added, restart the app so the push button returns JSON instead of 405/HTML.

---

## UI self-test checklist (run after any UI/frontend change)

**Before running the UI checklist:** Kill the old server and start it again so the app loads fresh code and data (e.g. `lsof -i :5001` to find PID, `kill <PID>`, then `./scripts/run.sh`). Wait until the server responds (e.g. `curl -s -o /dev/null -w "%{http_code}" http://localhost:5001/` returns 200).

**Quick API-level E2E (no browser):** With the server running, run `./tests/e2e_api_check.sh [PORT]` (default PORT=5001). It verifies: customer portal page, health, query, requirement-analysis; admin login, sync status, documents, chromadb, admin page. All must return 200/success.

**Full E2E verification (after refactors):** (1) Start app (`./scripts/run.sh` or `python3 backend/app.py`), wait until `curl -s -o /dev/null -w "%{http_code}" http://localhost:5001/` returns 200. (2) Run `./tests/e2e_api_check.sh 5001`. (3) Run `pytest tests/ -v -m "not integration"`. (4) Complete the UI self-test checklist below in a browser.

Then:

### Customer portal (`http://localhost:5001/`)

| Step | What to check |
|------|----------------|
| 1 | Page loads; **Ask** and **Requirement Analysis** tabs visible. |
| 2 | **Ask:** Type a question, click **Get AI Answer**; no console errors, answer or error shown. |
| 3 | **Requirement Analysis:** Paste short spec, click **Analyze Requirements**; button shows "Analyzing...", then results or error. |
| 4 | After results: summary cards (Requirements, With coverage, …), tabs **Related tests**, **Tests needing update**, **Uncovered**, **Generated tests**. |
| 5 | **Generated tests** tab: **Select all** / **Select none** links, **Push selected to TestRail** button, one checkbox per generated test. |
| 6 | Push options (when generated tests exist): hint text, **Use same section as related tests** / **Choose section manually**, Project/Suite/Section if manual. |

### Admin portal (`http://localhost:5001/admin`)

| Step | What to check |
|------|----------------|
| 1 | Login works; dashboard loads. |
| 2 | **TestRail Sync:** Status and **Sync Now** button; click **Sync Now** → "Syncing..." and log/progress or error. |
| 3 | **Confluence Sync:** Section visible with status and **Sync Now**; click the **Confluence** "Sync Now" button (id: `confluenceSyncNowBtn`), not TestRail's; wait for progress/log or error and verify logs in UI and runtime. |
| 4 | **Uploaded Documents** and **ChromaDB Contents** load without errors. |

If any step fails, fix the UI/API before considering the change done.
