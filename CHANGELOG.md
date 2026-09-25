# Changelog

All notable changes to AI Test Studio are documented here.

## [Unreleased]

Changes since 1.0.0 (March–September 2026).

### Breaking
- **Login required everywhere.** `/api/customer/*` and `/api/agents/*` now need an
  active, approved session; only `/health`, login/sign-up and the public theme
  setting are open. API clients must log in and send the session cookie.
- **The app refuses to start with a placeholder `SECRET_KEY`** unless
  `FLASK_DEBUG=true`. Set a real one in `config/.env` before upgrading.
- **CORS defaults to localhost only.** Set `CORS_ALLOWED_ORIGINS` for any other origin.
- **QA Agent Network moved to port 6001** (`QA_AGENT_NETWORK_URL` default is now
  `http://localhost:6001`). It is a separate sibling repo, reached over HTTP.
- **RAG modules renamed** (`backend/rag/`): `base_rag` → `rag_engine`,
  `multi_format_rag` → `rag_document_loader`, `chromadb_helper` + `rag_helpers` →
  `rag_helper`, `caching` → `rag_caching`, `settings` → `rag_settings`;
  `services/sync_service` → `testrail_sync_service`.
- **QA Agent Network setting keys renamed** (shown in Admin → Agent Settings), e.g.
  `AUTOCREATE_*` → `AUTHORING_*`, `AUTOFIX_*` → `HEALING_*`, `DB_*` → `TRIAGING_DB_*`.

### Added
- **Auto-Heal Failing Tests** page (Test Healing Agent): pick a test or a
  triaging handoff; Reproduce → Locate → Fix → Ship.
- **Adapt to Product Changes** page (Test Adaptation Agent): change notes, affected
  tests with their intent contract, explore-only and propose-only runs.
- Agent pages: base-branch picker, retry from a step, per-agent History with time
  and cost, run queue, offline banner and health dot; own URLs per page
  (`/test-generator`, `/authoring-agent`, `/healing-agent`, `/adaptation-agent`,
  `/talk-to-tests`).
- **Requirements → Tests runs** in the background with a Live Run card, cancel, and
  a replayable History; TestRail pushes are recorded on the run.
- **Admin → Analytics**: agent cost (exact) and Studio cost (estimated), time saved,
  per user and per window; *Reset Analytics & History*. The QA Agents tab lists
  the agents in workflow order and counts Requirements → Tests as
  `test-design-agent` (estimated cost) in its tiles, trend and breakdown.
- **Admin → Agent Settings**: edit QA Agent Network's configuration from the UI.
- **Self-service sign-up** with admin approval (`pending_approval`, `active`,
  `rejected`, `suspended`) and a last-admin guard.
- Per-model LLM cost rate card (`LLM_COST_RATES_JSON`) and per-answer cost in
  Talk to your Tests.
- Daily Confluence auto-sync schedule (`CONFLUENCE_SCHEDULE_*`).
- Session cookie hardening (`SESSION_COOKIE_SECURE`, `SESSION_COOKIE_SAMESITE`),
  sign-up rate limit, and the `QA_AGENT_PROXY_SECRET` shared with the agent server.

### Changed
- Default time-saved baselines raised: 240 min per test authored (was 120), 60
  per test fixed (was 45), 150 per test adapted (was 30). A value set in Studio
  Settings → Analytics still wins.
- Studio Settings are written back into `config/.env` (previously
  `storage/app_settings.json`, migrated automatically).
- Admin upload accepts CSV/Excel test-case files only (the file picker now says
  so); other documents come in through the Confluence sync. A file that fails
  validation returns 400 instead of 500.
- The customer UI always answers from the knowledge base; LLM-only answers remain
  available through the API (`use_rag: false`).
- Users created by an admin are active immediately (they no longer need approving).

### Fixed
- A TestRail/Confluence sync interrupted by an app restart no longer shows
  "Syncing…" and blocks new syncs for 30 minutes; it is cleared at startup.
- Requirements → Tests Gate 1 now applies the documented acceptance-criteria
  threshold (more criteria than `REQUIREMENT_MIN_TESTS_PER_PRIORITY` raises it).
- Studio Settings → Analytics save button no longer reads "Save undefined Settings".
- The test suite no longer writes rows into `storage/operation_costs.jsonl`.
- An authoring dry run (no PR requested) is no longer reported as a failed push with a NEEDS-REVIEW verdict. The agent treated the branch it deliberately skips as a failed one.
- Admin → Analytics:
  - A resumed agent run no longer counts the idle gap between attempts as time taken. One run showed 10 h for 12 min of work, which pushed time saved to 0.
  - Authored tests count only once their fix gate passes.
  - Per-agent time saved comes from the server and now adds up to the headline tile.
  - The daily trend plots every day (days without runs show as 0) and fills the card.
  - Changing the window or user keeps the current tab.
  - A Requirements → Tests run whose LLM calls are missing from the cost file now records the cost the run reported, not $0.
  - The trend metric buttons stay highlighted.
  - The page's notes (how runs are counted, the time-saved assumptions) sit in one collapsed Notes section at the bottom, on both tabs.
  - "Collected since" shows only when the window starts before the data does, with a spelled-out month.
  - Agent runs are scored the same way for every agent and every launcher:
    - Runs counts only runs that reached a verdict. Cancelled, interrupted and infrastructure-blocked runs are left out, though their spend still counts.
    - Correct stops and hand-offs count as succeeded.
    - A push or PR that fails after the agent's work passed doesn't count against the agent. Its tests are still credited, and the run's History shows the failed delivery.
    - Runs that did nothing (no tokens, no spend) are ignored.
    - See the README's "What counts as a run"; the page's Notes section sums it up.
  - The "approximated timings" and "no run id" notes are gone. Runs and time now come only from run summaries; calls outside a run (for example "suggest case update") still count in spend.

## [1.0.0] - 2026-03-22

### Features
- **Requirement Analysis**: Upload requirements (text, file, or Confluence URL) and generate test cases with AI
- **Multi-source input**: Support for multiple files and multiple Confluence URLs in a single analysis
- **Parallel processing**: Configurable parallel/sequential requirement processing for performance
- **E2E test generation**: Cross-requirement end-to-end workflow tests with impact analysis
- **Test coverage gates**: Two-gate model (count-based + LLM semantic) to avoid redundant generation
- **TestRail integration**: Sync test cases, push generated tests, update existing tests with AI suggestions
- **Confluence integration**: Sync spec pages, use as context for requirement analysis
- **Chat with documents**: RAG-powered Q&A over synced TestRail and Confluence content
- **Admin portal**: Settings management, document sync, data ingestion
- **Dark/light theme**: Full theme support with CSS variables

### Performance
- Gemini 2.5 Flash support with rate-limit-aware retry logic
- Pre-warmed embedding cache for instant vector search
- Thread-safe caches (embedding, query, requirements)
- Concurrent requirement processing with configurable parallelism
- SSE streaming with per-requirement progress events

### Security
- CORS origin restriction (configurable via env var)
- Login brute force protection (10 attempts / 15 min lockout)
- Security headers (X-Frame-Options, X-Content-Type-Options, X-XSS-Protection)
- Path traversal protection on file downloads
- Random admin password generation on first run
