# AI Test Studio Tests

## ⚠️ After any UI/frontend change: run the UI self-test

**Whenever you change the UI (customer pages, admin portal, or any frontend
HTML/JS/CSS), run the [UI self-test checklist](#ui-self-test-checklist-run-after-any-uifrontend-change)**
so broken flows are not shipped.

---

## Running the suite

```bash
source venv/bin/activate
pip install pytest                       # not in requirements.txt
FLASK_DEBUG=true python -m pytest -m "not integration"
```

- Tests that build the app (`create_app()`) need a real `SECRET_KEY` in
  `config/.env` or `FLASK_DEBUG=true`; otherwise the app refuses to start.
- `-m integration` runs the tests that call real services (LLM, RAG, Confluence,
  TestRail) and need them configured in `config/.env`; they skip when not configured.
- Run one file: `python -m pytest tests/test_requirement_runs.py -v`.

| File | Covers |
|------|--------|
| `test_requirement_analysis.py` | Requirement analysis service and API: related specs/tests, priority ordering (P0 > P1 > P2 > P3), suggest/update/create-case endpoints |
| `test_requirement_logic.py` | Coverage and generation rules (which priorities to generate), requirement settings schema |
| `test_requirement_runs.py` | Background runs: live stream then identical replay, cancel, failures, interrupted runs, id validation, TestRail pushes recorded per owner |
| `test_security_guards.py` | Agents proxy dot-segment guard and forwarded role, sign-up username validation and rate limit, user-id collision guard |
| `test_cost_analytics.py` | Cost tracking and the per-model rate card (`LLM_COST_RATES_JSON`), analytics rollups and time saved |
| `test_rag_improvements.py` | Retrieval defaults and thresholds, cache invalidation on document changes, context ordering |
| `test_testrail_connector.py` | `update_case` field handling, custom-field resolution for create-case |
| `test_testrail_sync_service.py` | Sync state machine: `is_syncing` recovery, progress, status structure, API 202/409 |
| `test_confluence_connector.py` | Confluence CQL search (and its fallbacks), connectivity diagnosis |
| `test_sync_ui_e2e.py` | Optional browser test (Playwright): the admin sync log renders. Skips if Playwright is missing (`pip install playwright && playwright install chromium`) |
| `evaluation/` | RAG quality evaluation (`scripts/run_eval.sh`; extra deps in `evaluation/requirements_eval.txt`) |

### Quick API smoke check

`tests/e2e_api_check.sh [PORT]` signs in and curls the main customer and admin
endpoints of a running server, and checks that the customer API refuses a request
with no session. It needs an active admin account:

```bash
E2E_USERNAME=admin E2E_PASSWORD='<password>' ./tests/e2e_api_check.sh 5001
```

The query and requirement-analysis checks make real LLM calls.

---

## UI self-test checklist (run after any UI/frontend change)

**Before you start:** restart the server so it loads fresh code (`lsof -i :5001`,
`kill <PID>`, `./scripts/run.sh`), and wait until
`curl -s -o /dev/null -w "%{http_code}" http://localhost:5001/health` returns 200.
Log in as an active user; use an admin for the admin portal.

### Requirements → Tests (`/test-generator`)

| Step | What to check |
|------|----------------|
| 1 | Page loads with the three input modes (paste / upload / Confluence URL). The button reads **✨ Generate Tests**, or **🔍 Analyze Requirements** when "Generate new tests for uncovered requirements" is unticked. |
| 2 | Paste a short spec and run it: the **Live Run** card shows the six phases progressing; **Cancel** stops it. |
| 3 | Results show the **Related Tests**, **User Story Tests** and **E2E Tests** tabs; no console errors. |
| 4 | Select generated tests and **Push selected to TestRail**: the push dialog offers "same section as related tests" or a manual Project → Suite → Section; the result appears as a toast. (Needs `TESTRAIL_PUSH_ENABLED=true`.) |
| 5 | **Update with AI** on a test needing an update shows a suggested rewrite you can edit and save. |
| 6 | Refresh mid-run: the run reattaches. **History** lists it; opening a past run replays it. |

### Talk to your Tests (`/talk-to-tests`)

| Step | What to check |
|------|----------------|
| 1 | Ask a question with **✨ Get Answer**: an answer or a clear error, sources when shown, no console errors. |

### Agent pages (`/authoring-agent`, `/healing-agent`, `/adaptation-agent`)

Needs the QA-Agent-Network server running (`bash scripts/run-server.sh` in that repo).

These three panels are near-copies of each other, and fixes have historically been
applied to one and not the others. **Run every row below on all three pages** — a row
that passes on two pages and fails on the third is the bug this checklist exists to catch.

| Step | What to check (identical on all three pages) |
|------|----------------------------------------------|
| 1 | Start a run, switch to another page, come back: the console is already streaming — no need to click **view**. |
| 2 | Click **view** on a past run in History: the console replays and the elapsed field reads `—`, **not** a clock counting up from 00:00. |
| 3 | Click a History **row**: the modal shows meta, a Time & cost table, and one section per step with its markdown report and a collapsible **raw step JSON**. |
| 4 | Click **Stop** on a live run: the status badge changes to `cancelling` immediately, not after the process dies. |
| 5 | Let a run finish: a **📋 Result** card appears under the live console, and the card's left border takes the status colour (green / red / grey). |
| 6 | Queue a second run: it appears as a pending row at the top of History. There is no separate "Pending Queue" card on any page. |
| 7 | Stop the agent server and reload: an offline banner **and** pickers that say why they are empty, rather than blank or stuck on "Loading…". |
| 8 | Start a run and navigate away: no orphaned EventSource left open (DevTools → Network → EventStream). |
| 9 | Cancel/retry failures are written to the **run console**, not to a toast or the form's error box. Toasts are only for queue notices ("Queued at position N"). |

### Admin portal (`/admin`)

| Step | What to check |
|------|----------------|
| 1 | Login works; the sidebar shows Users, Connectors, Knowledge Base, Analytics, Agent Settings, Studio Settings. |
| 2 | **Users:** a pending sign-up can be approved; demoting the last admin is refused. |
| 3 | **Connectors → TestRail:** **Sync Now** (`#syncNowBtn`) shows "Syncing…" and a running log, or an error. |
| 4 | **Connectors → Confluence:** click the **Confluence** Sync Now (`#confluenceSyncNowBtn`), not TestRail's; wait for progress/log or an error, and check the server log too. |
| 5 | **Knowledge Base:** uploaded documents and ChromaDB chunks load without errors. |
| 6 | **Analytics:** each window (24h / 7d / 30d / all) and the user filter load; with the agent server stopped, the agent half shows an explanation instead of silently empty numbers. Do **not** click *Reset Analytics & History* on data you want to keep. |
| 7 | **Agent Settings:** values load from the agent server; secrets are masked; saving shows a confirmation. |
| 8 | **Studio Settings:** values load and save. |

If any step fails, fix the UI/API before considering the change done.
