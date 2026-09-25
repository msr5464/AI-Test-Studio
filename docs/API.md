# API Reference

REST API of AI Test Studio. Base URL: `http://localhost:5001/api`.

- [Authentication and access](#authentication-and-access)
- [Auth API](#auth-api) — `/api/auth`
- [Customer API](#customer-api) — `/api/customer`
- [Admin API](#admin-api) — `/api/admin`
- [Agents proxy](#agents-proxy) — `/api/agents`
- [Errors and limits](#errors-and-limits)

---

## Authentication and access

The API uses the Flask **session cookie** set by `POST /api/auth/login`. Send it
with every request (`curl -b cookies.txt`, `fetch(..., {credentials: 'include'})`).

| Prefix | Who may call it |
|--------|-----------------|
| `/api/auth/login`, `/api/auth/signup`, `/api/auth/logout`, `/api/auth/me` | anyone |
| `/api/customer/*`, `/api/agents/*`, `/api/auth/change-password` | any **active** user (admin, member or customer) |
| `/api/admin/*`, `/api/auth/users*` | admins only |
| `GET /api/admin/settings/public`, `GET /health` | anyone |

A user who is not signed in gets **401**; a signed-in user who is
`pending_approval`, `rejected` or `suspended`, or a non-admin on an admin route,
gets **403**.

```bash
# Log in and keep the cookie
curl -c cookies.txt -X POST http://localhost:5001/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "<printed at first start>"}'

# Use it
curl -b cookies.txt -X POST http://localhost:5001/api/customer/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Which tests cover password reset?"}'
```

The default `admin` account is created on first start with a random password,
printed once to the console. User ids are `md5(username)[:12]`.

---

## Auth API

### `POST /api/auth/login`

```json
{"username": "admin", "password": "…"}
```
**200** `{"success": true, "user": {"user_id", "username", "role"}}` — sets the
session cookie. **401** invalid credentials, or
`{"success": false, "error": "Account pending approval or suspended", "status": "pending_approval"}`.
**429** after 10 failed attempts from one IP in 15 minutes.

### `POST /api/auth/signup`

```json
{"username": "jane", "password": "…"}
```
**201** `{"success": true, "status": "pending_approval", "message": "…"}`. Creates a
`member` who cannot use the app until an admin approves them. **400** for a taken
or invalid username. Limited to 10 sign-ups per IP per 15 minutes (**429**).

### `POST /api/auth/logout`

**200** `{"success": true, "message": "Logged out successfully"}`

### `GET /api/auth/me`

**200** `{"success": true, "user": {"user_id", "username", "role", "status"}}`.
**403** `{"success": false, "status": "<status>", …}` for an inactive account,
**401** when not signed in.

### `POST /api/auth/change-password` (signed in)

```json
{"old_password": "…", "new_password": "…"}
```

### User management (admin)

| Method | Path | Body | Notes |
|--------|------|------|-------|
| `GET` | `/api/auth/users` | — | `{"success", "users": [{"user_id", "username", "role", "status", "created_at", …}]}` |
| `POST` | `/api/auth/users` | `{"username", "password", "role"}` | `role` is `admin` or `customer` (default `customer`). **201** `{"success", "user": {…}}` |
| `PUT` | `/api/auth/users/<user_id>` | `{"role"?, "status"?}` | `role`: `admin` / `customer` / `member`; `status`: `pending_approval` / `active` / `rejected` / `suspended`. Refuses (400) to demote or deactivate the last active admin. **200** `{"success": true}` |
| `DELETE` | `/api/auth/users/<user_id>` | — | |
| `POST` | `/api/auth/users/<user_id>/reset-password` | `{"new_password"}` | |

A user created through `POST /users` is `active` immediately — an admin creating
the account is its approval.

---

## Customer API

All routes need an active account.

### `GET /api/customer/config`

`{"testrail_url": "https://yourcompany.testrail.io"}` — used by the page to link case ids.

### `GET /api/customer/health`

`{"success": true, "status": "healthy", "service": "rag-customer-api"}`. For an
unauthenticated health check use `GET /health`.

### `POST /api/customer/query` — Talk to your Tests

```json
{"question": "Which tests cover password reset?", "use_rag": true, "bypass_cache": false, "session_id": "optional"}
```

`use_rag: false` asks the LLM directly, without retrieval (`"mode": "direct_llm"`
in the response); the page always sends `true` (`"mode": "rag"`).

**200**
```json
{
  "success": true,
  "question": "…",
  "answer": "…",
  "mode": "rag",
  "sources": ["…"],
  "source_documents": [{"…": "…"}],
  "query_time_ms": 1840,
  "cache_hit": false,
  "metrics": {"calls": 1, "cost_usd": 0.0012, "input_tokens": 2310, "output_tokens": 240, "duration_s": 1.9}
}
```
**400** missing or empty `question`; **500** on an LLM or retrieval error.

### Requirements → Tests

#### Input (shared by the two endpoints below)

JSON, or `multipart/form-data` for files. Provide **exactly one** input type
(mixing them is a 400):

| Field | Type | |
|-------|------|---|
| `requirement_spec` | string | Pasted text |
| `file` | file, repeatable | `.txt`, `.pdf`, `.docx`, `.doc` |
| `confluence_urls` (or `confluence_url`) | list, or newline-separated string | Confluence page URLs (needs `CONFLUENCE_*` configured) |

Options: `generate_new_tests` (default `true`), `generate_p2_p3_tests` (default
`false`; P0–P1 otherwise), `push_to_testrail` (default `false`),
`target_section_id` (int), `use_section_of_related` (push each requirement's
tests into the section of its first related test).

#### `POST /api/customer/requirement-analysis/runs` — background run (what the UI uses)

A run executes in the background, so it survives a refresh or a closed tab, and
is stopped after 20 minutes. Every event it emits is recorded for History. Runs
are private to the user who started them — anyone else gets **404**.

**201** — the run's History row:
```json
{
  "session_id": "20260916-143012-req-3f9a1c",
  "status": "running",
  "started_at": "2026-09-16T14:30:12",
  "started_epoch": 1789551012.4,
  "source": "Pasted text",
  "title": "",
  "source_type": "text",
  "generate_new_tests": true,
  "duration_s": null, "cost_usd": null, "input_tokens": null, "output_tokens": null,
  "llm_calls": null, "requirements": null, "tests_generated": null, "error": ""
}
```

| Method | Path | |
|--------|------|---|
| `GET` | `/requirement-analysis/runs?limit=20` | `{"items": [row, …]}`, newest first (limit 1–50; 50 kept per user). `status`: `running`, `completed`, `failed`, `cancelled`, `interrupted` (server restarted mid-run) |
| `GET` | `/requirement-analysis/runs/<session_id>/stream` | `text/event-stream`: every recorded event, then live ones until the run ends. Frames carry `id:`; reconnecting with `Last-Event-ID` resumes after it |
| `GET` | `/requirement-analysis/runs/<session_id>/events` | `{"events": […]}` — the same events in one response, for replay |
| `POST` | `/requirement-analysis/runs/<session_id>/cancel` | `{"status": "cancelling", "session_id"}`; **409** if not running. Ends `cancelled` with what it analysed so far |

Events (each carries `ts`, epoch seconds), in order:
- `{"type": "config", …}` — session, source, options and similarity thresholds
- `{"type": "phase", "state": "start" | "done", "index", "total", "key", "name", "duration_s"?, "cost_usd"?, "llm_calls"?}` — six phases in order: Extract Requirements (`extract`), Derive Acceptance Criteria (`criteria`), Find Related Tests (`find-tests`), Check Coverage (`coverage`), Write Missing Tests (`write-tests`), Build E2E Tests (`e2e`)
- `{"type": "log", "phase", "text"}` — a console line within the open phase
- `{"stage", "message", "progress", "closed_stage"?}` — progress (0–1)
- `{"type": "doc_summary", "data"}`, `{"type": "requirement_step", "req_id", "step"}`
- `{"type": "requirement_result", "req_id", "data"}` — one per requirement
- `{"type": "testrail_push", …}` — a later push recorded against the run
- the final event: the full analysis result (below) plus `"status"`, or `{"success": false, "error", "status"}`

```bash
curl -b cookies.txt -X POST http://localhost:5001/api/customer/requirement-analysis/runs \
  -H "Content-Type: application/json" \
  -d '{"requirement_spec": "REQ-001: User must reset password via email.", "generate_new_tests": true}'
curl -b cookies.txt -N http://localhost:5001/api/customer/requirement-analysis/runs/<session_id>/stream
```

#### `POST /api/customer/requirement-analysis` — synchronous

Same input; blocks until the analysis finishes and returns the result directly.
Kept for scripts — the UI uses runs.

**Result** (also the final event of a run):
```json
{
  "success": true,
  "requirements_analyzed": 2,
  "requirements": [{"id": "REQ-001", "title": "…", "description": "…"}],
  "related_tests": {"REQ-001": [{"testrail_id": "C123", "title": "…", "similarity_score": 0.85}]},
  "related_specs": [], "related_specs_per_req": {},
  "tests_needing_update": {"REQ-001": [{"testrail_id": "C123", "status": "needs_update", "suggested_changes": ["…"], "reason": "…"}]},
  "tests_ok": {},
  "uncovered_requirements": ["REQ-002"],
  "generated_tests": {"REQ-002": [{"title": "…", "priority": "P1", "steps": "…", "expected_result": "…"}]},
  "e2e_workflow_tests": [], "existing_e2e_tests": [],
  "coverage_per_req": {}, "coverage_gap_reason_per_req": {},
  "pushed_to_testrail": [],
  "run_id": "…", "duration_s": 312.4, "stage_timings": [],
  "llm_calls": 24, "total_estimated_cost_usd": 0.041, "input_tokens": 48210, "output_tokens": 9120,
  "summary": {
    "total_requirements": 2, "requirements_with_coverage": 1, "uncovered_count": 1,
    "generated_count": 1, "total_generated_tests": 3, "e2e_workflow_tests_count": 1,
    "needing_update_count": 1, "pushed_count": 0, "overall_coverage_pct": 50.0,
    "requirements_fully_covered": 1, "coverage_min_similarity": 70, "retrieval_similarity_threshold": 60.0
  }
}
```

#### Working with TestRail cases

Writes to TestRail need `TESTRAIL_PUSH_ENABLED=true` and TestRail credentials.
Passing `session_id` (and optionally `target`) records the push on that
Requirements → Tests run. Created and updated cases are re-ingested into
ChromaDB so the next analysis sees them.

| Method | Path | Body | Returns |
|--------|------|------|---------|
| `POST` | `/requirement-analysis/push` | `{"generated_tests": {req_id: [test]}, "related_tests"?, "use_section_of_related"?, "target_section_id"?}` | `{"success", "pushed_to_testrail": […]}` |
| `POST` | `/requirement-analysis/suggest-case-update` | `{"testrail_id", "requirement_text", "suggested_changes"?: [..], "reason"?, "current_title"?, "current_content"?}` | `{"success", "title", "steps", "preconditions", "expected_result", "priority"}` — AI-rewritten case; fetched from TestRail when `current_content` is omitted |
| `POST` | `/requirement-analysis/update-case` | `{"testrail_id", "title", "steps"?, "preconditions"?, "expected_result"?, "priority"?, "session_id"?, "target"?}` | `{"success", "testrail_id"}` |
| `POST` | `/requirement-analysis/create-case` | `{"section_id", "title", "steps"?, "preconditions"?, "expected_result"?, "priority"?, "platform"?, "requirement_text"?, "case_type"?, "session_id"?, "target"?}` | `{"success", "testrail_id"}` |

`priority` is `P0`–`P3`.

### TestRail browsing (for the pickers)

All return **503** when TestRail is not configured.

| Method | Path | |
|--------|------|---|
| `GET` | `/testrail/projects` | `{"success", "projects": […]}` |
| `GET` | `/testrail/projects/<project_id>/suites` | `{"success", "suites": […]}` |
| `POST` | `/testrail/projects/<project_id>/suites` | body `{"name", "description"?}` → `{"success", "suite"}` |
| `GET` | `/testrail/projects/<project_id>/sections?suite_id=` | `{"success", "sections": […]}` |
| `POST` | `/testrail/projects/<project_id>/sections` | body `{"suite_id", "name", "parent_id"?, "description"?}` → `{"success", "section"}` |
| `GET` | `/testrail/unautomated-cases?project_id=&suite_id=&section_id=` | Cases whose execution-mode field is "Automatable" and that carry a "Pending Automation" value; the fields are discovered by option label, not name. → `{"success", "cases": [{"ID", "Title", "Priority", "Steps", "Preconditions", "expected_result", "section", "automation_statuses", …}], "total_in_project", "pending_count", "_debug"}` |
| `POST` | `/testrail/improve-for-automation` | body `{"testrail_id" or "title", "preconditions"?, "steps"?, "expected_result"?}` → `{"success", "title", "priority", "preconditions", "steps", "expected_result"}` — the LLM rewrites vague manual steps into deterministic ones |

---

## Admin API

All routes need an admin session, except `GET /settings/public`.

### Knowledge base

| Method | Path | |
|--------|------|---|
| `POST` | `/api/admin/upload` | multipart `file`. **CSV/Excel test-case files only** (at least 7 of the 10 expected columns). Re-uploading a name replaces it. → `{"success", "document_id", "message", …}`; a file that fails validation returns **400** with the reason |
| `GET` | `/api/admin/documents` | `{"success", "documents": [{"id", "name", "path", "uploaded_at", "status"}], "count"}` — manual uploads only (sync files are hidden) |
| `DELETE` | `/api/admin/documents/<doc_id>` | removes the file, its chunks and metadata |
| `GET` | `/api/admin/documents/<doc_id>/download` | the original file |
| `GET` | `/api/admin/stats` | `{"success", "total_documents", "total_chunks", "rag_config", …}` |
| `GET` | `/api/admin/chromadb?limit=N` | collection name and chunks (`limit` returns only the first N) |
| `POST` | `/api/admin/chromadb/reset` | body `{"delete_all": true}`. Deletes the vector store **and** both sync logs |

### Sync

| Method | Path | |
|--------|------|---|
| `POST` | `/api/admin/sync/testrail` | **202** `{"success", "status": "started"}`; **409** if a TestRail sync is running |
| `POST` | `/api/admin/sync/confluence` | **202**, or **409** if a Confluence sync is running |
| `GET` | `/api/admin/sync/status` | `{"success", "status": {"last_sync", "is_syncing", "current_sync", "latest_sync_record", "sync_log", "total_syncs", …, "confluence": {…}}}` — TestRail at the top level, Confluence nested |
| `GET` | `/api/admin/sync/schedule` | next scheduled runs: `{"success", "testrail": {…}, "confluence": {…}}` (times are UTC) |
| `GET` | `/api/admin/confluence-diagnose` | tells credentials (401/403), API path (404) and CQL (400 / no results) problems apart |

### Settings

| Method | Path | |
|--------|------|---|
| `GET` | `/api/admin/settings` | `{"success", …schema and current values}`; secrets masked as `****` |
| `PUT` | `/api/admin/settings` | body `{"<schema key>": value}` using the **lowercase schema keys** (`"chat_retrieval_k": 10`), not env names. Unknown keys are ignored; a masked secret sent back unchanged is kept. Writes `config/.env`, applies immediately, reconfigures the sync scheduler, and returns the full settings. LLM provider/model changes still need a restart |
| `GET` | `/api/admin/settings/public` | **no auth**: `{"success", "default_theme"}` |
| `GET` | `/api/admin/agent-settings` | QA Agent Network's settings schema and values (proxied to its admin-only `/settings`) |
| `PUT` | `/api/admin/agent-settings` | saves them to QA Agent Network's `config/.env` — including `GITHUB_TOKEN` |

### Analytics

```
GET /api/admin/analytics?window=7d&user_id=
```
`window`: `24h`, `7d`, `30d` or `all` (default: the `ANALYTICS_DEFAULT_WINDOW`
setting). `user_id` narrows to one user.

```json
{
  "success": true,
  "window": "7d",
  "default_window": "7d",
  "baselines": {"min_per_test_authored": 240, "min_per_test_fixed": 60, "min_per_test_adapted": 150, "min_per_test_case_written": 15},
  "agents": {"window": {…}, "overall": {…}, "by_agent": {…}, "series": [ … ]},
  "agents_error": null,
  "studio": {"window": {…}, "outcomes": {…}, "run_duration_s": 0, "requirements": {…}, "requirements_series": [ … ], "by_operation": {…}, "…": "…"},
  "time_saved": {"agents_min": 0, "studio_min": 0, "total_min": 0, "by_agent": {"test-healing-agent": 0}, "basis": "estimate"}
}
```

`agents` comes from QA Agent Network's `/analytics/summary` and is exact (the
Claude CLI reports it); `studio` is estimated from the token rate card. The API
never sums them. `studio.requirements` totals the Requirements → Tests runs in
the same shape as an agent (`runs`, `succeeded`, `failed`, `cost_usd`,
`duration_s`, `llm_calls`, `input_tokens`, `output_tokens`, `tests_generated`),
and `studio.requirements_series` splits them by day. The QA Agents tab shows
them as `test-design-agent` and adds them to its totals, labelled as estimated.
If the agent server is down, `agents` is empty and `agents_error` says why.

```
DELETE /api/admin/analytics?window=7d&user_id=
```
**Irreversible.** Clears Studio analytics and run history for the window (and
user, if given), and asks QA Agent Network to delete its analytics, run registry
entries and session audit directories for the same window.

---

## Agents proxy

`/api/agents/*` forwards to the QA Agent Network server at `QA_AGENT_NETWORK_URL`
(default `http://localhost:6001`). It requires an active session, strips any
`X-User-*` headers from the client, and injects the signed-in user's
`X-User-ID`, `X-User-Name`, `X-User-Role` (and `X-Proxy-Secret` when
`QA_AGENT_PROXY_SECRET` is set).

| Path | Forwards to |
|------|-------------|
| `GET /api/agents/health` | `/health` |
| `/api/agents/<agent>/<path>` (any method, query string passed through) | `/agents/<agent>/<path>` |

- `<agent>` must be `test-authoring-agent`, `test-healing-agent` or
  `test-adaptation-agent`; anything else is **404**.
- A `.` or `..` path segment (including encoded forms) is **404**.
- A `GET` whose path ends in `/stream` is relayed as Server-Sent Events;
  everything else is a JSON round trip with `QA_AGENT_NETWORK_TIMEOUT` (default 30 s).
- An unreachable agent server returns an error the pages show as "offline".

Everything behind `<path>` — queues, runs, streams, history, retries,
artefacts — is documented in QA Agent Network's
[SERVER_API.md](https://github.com/msr5464/QA-AI-Agent/blob/main/docs/SERVER_API.md).

```bash
curl -b cookies.txt http://localhost:5001/api/agents/test-healing-agent/run/active
```

---

## Errors and limits

Errors are JSON: `{"success": false, "error": "…"}`.

| Code | Meaning |
|------|---------|
| 200 / 201 | OK / created (sign-up, run started, user created) |
| 202 | Sync started in the background |
| 400 | Invalid input (missing field, mixed inputs, bad role/status, last-admin guard, rejected upload) |
| 401 | Not signed in, or bad credentials |
| 403 | Account not active, or admin required |
| 404 | Unknown resource, another user's run, unknown agent |
| 409 | A sync is already running; cancelling a run that is not running |
| 429 | Too many login or sign-up attempts (10 per IP per 15 minutes) |
| 500 | Server error |
| 502 / 504 | Agents proxy: the agent server errored / timed out |
| 503 | TestRail not configured |

Other limits: uploads up to `ADMIN_UPLOAD_MAX_SIZE_MB` (default 50 MB); sessions
last 2 hours; Requirements → Tests runs stop after 20 minutes.
