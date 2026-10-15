# Architecture Guide

How AI Test Studio is built, how a request moves through it, and how it connects
to QA Agent Network and the automation repo. For setup, see the
[README](../README.md); for endpoints, [API.md](API.md).

## Table of Contents

1. [The big picture](#1-the-big-picture)
2. [Inside the Studio](#2-inside-the-studio)
3. [Request flows](#3-request-flows)
4. [Data and state](#4-data-and-state)
5. [Configuration](#5-configuration)
6. [Integration with QA Agent Network](#6-integration-with-qa-agent-network)

---

## 1. The big picture

Three repositories, cloned side by side (QA Agent Network calls their parent
directory `WORKSPACE_DIR`):

```
<WORKSPACE_DIR>/
├── AI-Test-Studio/          this repo — the web app people use
├── QA-Agent-Network/        the agents + qa_agents_server (github.com/msr5464/QA-AI-Agent)
└── <automation repo>/       Java + TestNG tests the agents write and fix
                             (GITHUB_REPO_AUTOMATION, e.g. Playwright-Automation-Framework)
```

```
 Browser ──► AI Test Studio (Flask, :5001)
               │  Requirements → Tests, Talk to Tests ──► LLM (Ollama / OpenAI / Gemini)
               │                                     └─► ChromaDB  ◄── TestRail / Confluence sync
               │
               │  /api/agents/*  (login required; identity headers added)
               ▼
             qa_agents_server (QA Agent Network, 127.0.0.1:6001)
               │  authoring / healing / adaptation agents ──► Claude CLI
               ▼
             automation repo (per-run git worktree) ──► GitHub PR
```

The Studio owns requirements analysis and chat itself. It does **not** run the
agents: it proxies to QA Agent Network's server, which runs them against the
automation repo. The fourth agent, triaging, runs from the CLI/CI only.

---

## 2. Inside the Studio

```
frontend/customer/index.html   five pages, one SPA: /design-agent /authoring-agent
                               /healing-agent /adaptation-agent /talk-to-tests
frontend/admin/*.html          login + admin portal (Users, Connectors, Knowledge Base,
                               Analytics, Agent Settings, Studio Settings)
        │  fetch / EventSource (session cookie)
        ▼
backend/app.py                 Flask app: pages, security settings, blueprints
  /api/auth      backend/api/auth/routes.py       login, sign-up, sessions, users
  /api/admin     backend/api/admin/routes.py      admin-only: KB, sync, settings, analytics
  /api/customer  backend/api/customer/routes.py   login required: analysis, runs, chat, TestRail
  /api/agents    backend/api/agents/proxy.py      login required: proxy to qa_agents_server
        │
        ▼
backend/services/   rag_service · requirement_analysis_service · requirement_runs
                    testrail_sync_service · confluence_sync_service · scheduler_service
                    settings_service · analytics_service · auth_service
backend/rag/        rag_engine (embeddings, LLM, vector store, retrieval) · rag_document_loader
                    · rag_settings (RAGConfig) · rag_helper · rag_caching
backend/connectors/ testrail_connector · confluence_connector
backend/extractors/ requirement_extractor
backend/models/     user (file-backed UserStorage)
backend/cost_tracker.py   per-call LLM cost log
        │
        ▼
storage/            ChromaDB, documents, users, run history, cost log (see §4)
```

| Module | Responsibility |
|--------|----------------|
| `rag_service.py` | Knowledge-base lifecycle (upload with test-case validation, list, delete, reset, stats) and chat `query()` |
| `requirement_analysis_service.py` | Extract requirements, find related tests and specs, assess coverage, generate User Story and E2E tests |
| `requirement_runs.py` | Runs analyses in the background so they outlive the request; events kept in memory and in `<sid>.events.jsonl` for History replay |
| `testrail_sync_service.py`, `confluence_sync_service.py` | Pull TestRail cases and Confluence pages into ChromaDB |
| `scheduler_service.py` | Daily auto-syncs at the configured UTC times (APScheduler) |
| `settings_service.py` | Admin → Studio Settings schema; reads and writes `config/.env` |
| `analytics_service.py` | Studio-side cost and time-saved rollups for Admin → Analytics |
| `auth_service.py`, `models/user.py` | Login, sign-up with approval, roles, default admin |
| `cost_tracker.py` | Logs every LLM call's tokens and estimated cost to `storage/operation_costs.jsonl` |

**Single process by design.** Gunicorn runs one worker with many threads
(`gunicorn_config.py`), because the vector store, warmed embedding caches and
the live-run registry are all in-process. A run whose process died reads as
"interrupted".

---

## 3. Request flows

### 3.1 Authentication

1. `POST /api/auth/login` checks the password and stores `user_id`, `username`,
   `role` and `status` in the signed session cookie. Login and sign-up are
   rate-limited per IP.
2. `POST /api/auth/signup` creates a `member` in `pending_approval`; an admin
   approves, rejects or suspends them in Admin → Users.
3. Every `/api/customer/*` and `/api/agents/*` request requires an **active**
   account (`require_auth`); `/api/admin/*` requires role `admin`. Only `/health`,
   the static pages, the login/sign-up endpoints and `GET /api/admin/settings/public`
   (the default theme) are open.

User ids are `md5(username)[:12]`. The first start with no admin creates `admin`
with a random password printed once.

### 3.2 Requirements → Tests (a background run)

1. `POST /api/customer/requirement-analysis/runs` with pasted text, files or
   Confluence URLs starts a run and returns its id; the run continues if the tab
   closes, and is stopped after 20 minutes.
2. `requirement_analysis_service` extracts requirements (optionally enriched with
   document context), then for each — in parallel by default:
   - finds **related tests** (`source_type=testcase`) and **related specs**
     (`source_type=specs`) in ChromaDB, with hybrid search and optional reranking,
     each against its own similarity threshold;
   - asks the LLM which related tests need updating;
   - generates **User Story Tests** for uncovered priorities (P0–P1 by default)
     and **E2E Tests** with regression impact.
3. Progress, per-stage timing and cost stream to the page over SSE; the same
   events are stored so **History** can replay the run.
4. Pushing tests to TestRail (`TESTRAIL_PUSH_ENABLED=true`) creates the cases,
   records the push on the run, and re-ingests them into ChromaDB.

### 3.3 Talk to your Tests (RAG query)

`POST /api/customer/query` → `RAGService.query()` → `rag_engine`:
query cache → optional query expansion → vector (or hybrid BM25 + vector)
retrieval → optional CrossEncoder rerank → similarity threshold → prompt with
the retrieved chunks → LLM → answer, sources and per-call cost metrics. The page
always uses RAG; the API also accepts `use_rag: false` for an LLM-only answer.
Chat retrieval is tuned by the `CHAT_*` settings.

### 3.4 Filling the knowledge base

- **TestRail sync** fetches cases per project (newest first), writes one CSV per
  suite and loads each through `RAGService.upload_document()`, tagged
  `source_type=testcase`.
- **Confluence sync** runs the configured CQL and loads pages as
  `source_type=specs`.
- **Manual upload** (Admin → Knowledge Base) accepts CSV/Excel test-case files
  only, validated for the expected columns.
- Syncs run in the background (status via `GET /api/admin/sync/status`) and
  daily on a schedule if enabled. Delta syncs use `*_DELTA_DAYS`.

Documents are split (`CHUNK_SIZE`, `CHUNK_OVERLAP`), embedded (local
sentence-transformers, or OpenAI's embeddings when `LLM_PROVIDER=openai`) and
stored in one ChromaDB collection.

### 3.5 Agent pages

The browser calls `/api/agents/<agent>/...`; `proxy.py` forwards it to
`QA_AGENT_NETWORK_URL` — see [§6](#6-integration-with-qa-agent-network). Live
consoles are SSE streams passed through unbuffered.

### 3.6 Admin analytics and settings

- **Analytics:** `GET /api/admin/analytics` merges the agent server's
  `/analytics/summary` (exact agent cost, reported by the Claude CLI) with
  `analytics_service` (estimated Studio cost from the rate card) and applies the
  time-saved baselines. The two costs are labelled separately, never summed
  silently: the QA Agents tab adds the Requirements → Tests runs to the agent
  totals as `test-design-agent` and labels that cost as estimated. Reset deletes
  Studio run history and, through the agent server, the agents' analytics and
  session history.
- **Studio Settings:** `settings_service` validates and writes `config/.env`.
- **Agent Settings:** proxied to the agent server's admin-only `/settings`, which
  writes QA Agent Network's `config/.env`.

---

## 4. Data and state

Everything stateful lives in `storage/` (git-ignored):

| Path | Contents |
|------|----------|
| `chroma_db/` | Vector store (persistent) |
| `documents/`, `documents_metadata.json` | Uploaded test-case files and their metadata |
| `embedding_cache/` | On-disk embedding cache |
| `users.json` | Accounts, roles, statuses (file-locked) |
| `requirement_sessions/` | One `<sid>.json` + `<sid>.events.jsonl` per Requirements → Tests run (50 kept per user) |
| `requirement_runs.jsonl` | One row per completed run (Analytics) |
| `operation_costs.jsonl` | One row per LLM call (Analytics) |
| `testrail_sync_metadata.json`, `confluence_sync_metadata.json` | Last sync, history, delta state |

The query cache is in memory.

---

## 5. Configuration

[`config/env.example`](../config/env.example) is the reference. Structure worth
knowing:

- `config/.env` is loaded at startup; Admin → Studio Settings writes back to it.
- `RAGConfig` (`backend/rag/rag_settings.py`) reads two prefixed families:
  `CHAT_*` for Talk to Tests retrieval and `REQUIREMENT_*` for requirement
  analysis, so each can be tuned without affecting the other.
- The LLM is built once at startup (restart after changing it); switching
  embeddings (to/from OpenAI) needs a knowledge-base reset and re-sync.

---

## 6. Integration with QA Agent Network

### The proxy (`backend/api/agents/proxy.py`)

| Route | Forwards to |
|-------|-------------|
| `GET /api/agents/health` | `/health` |
| `/api/agents/<agent>/<path>` | `/agents/<agent>/<path>` — `<agent>` must be `test-authoring-agent`, `test-healing-agent` or `test-adaptation-agent` |
| `GET/PUT /api/admin/agent-settings` (admin) | `/settings` |
| `GET/DELETE /api/admin/analytics` (admin) | `/analytics/summary`, `/analytics/clear` |

What the proxy adds, because the agent server has no login of its own:

- **Auth first:** every `/api/agents/*` call needs an active Studio session.
- **Identity:** it strips any `X-User-*` headers the browser sent and injects
  `X-User-ID`, `X-User-Name` and `X-User-Role` from the session, plus
  `X-Proxy-Secret` when `QA_AGENT_PROXY_SECRET` is set. The agent server scopes
  queues, history and analytics to that user and lets admins see everyone's.
- **Containment:** unknown agents return 404, and any `.` / `..` path segment
  (including encoded forms) is refused, so a path cannot escape to server-wide
  routes like `/settings`.
- **Streaming:** a `GET` ending in `/stream` is relayed as SSE without buffering;
  everything else is a JSON round trip with `QA_AGENT_NETWORK_TIMEOUT`.

Keep the agent server on `127.0.0.1` unless both repos share
`QA_AGENT_PROXY_SECRET` — it trusts these headers.

### Where to read about the agents

| Topic | QA Agent Network doc |
|-------|---------------------|
| What each agent does, handoffs, audit trails | [ARCHITECTURE.md](https://github.com/msr5464/QA-AI-Agent/blob/main/docs/ARCHITECTURE.md) |
| Every server endpoint, request and response | [SERVER_API.md](https://github.com/msr5464/QA-AI-Agent/blob/main/docs/SERVER_API.md) |
| What the automation repo must provide; other frameworks | [FRAMEWORK_INTEGRATION.md](https://github.com/msr5464/QA-AI-Agent/blob/main/docs/FRAMEWORK_INTEGRATION.md) |
| Running the agent server for a team; CI | [DEPLOYMENT.md](https://github.com/msr5464/QA-AI-Agent/blob/main/docs/DEPLOYMENT.md) |

### Keeping the two repos in step

- Each agent's step keys and labels are defined in QA Agent Network's
  `qa_agents_server/agents.py` and mirrored in `frontend/customer/index.html`
  (`STEP_LABELS`, the progress-step `data-step` attributes). Renaming a step is a
  two-repo change.
- A new agent needs an entry in `_ALLOWED_AGENTS` (`proxy.py`) and a page.
- `QA_AGENT_PROXY_SECRET` must match on both sides.
