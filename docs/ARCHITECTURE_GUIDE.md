# AI-Powered QA Agent Network — Complete Architecture Guide

> **How three repositories work together to generate, execute, triage, and self-heal automated tests using Claude AI**

---

## Table of Contents

1. [The Big Picture](#1-the-big-picture)
2. [Repository Overview](#2-repository-overview)
3. [Repo 1 — AI-Test-Studio (The Hub)](#3-repo-1--ai-test-studio-the-hub)
4. [Repo 2 — QA-Agent-Network (The Agents)](#4-repo-2--qa-agent-network-the-agents)
5. [Repo 3 — Jarvis (The Automation Framework)](#5-repo-3--jarvis-the-automation-framework)
6. [How the Three Repos Connect](#6-how-the-three-repos-connect)
7. [Workflow 1 — AI Test Case Generation](#7-workflow-1--ai-test-case-generation)
8. [Workflow 2 — Test Authoring Agent (Text → Java → PR)](#8-workflow-2--test-authoring-agent-text--java--pr)
9. [Workflow 3 — Test Triaging Agent (CI → Classification → Report)](#9-workflow-3--test-triaging-agent-ci--classification--report)
10. [Workflow 4 — Test Healing Agent (Auto-Fix → PR)](#10-workflow-4--test-healing-agent-auto-fix--pr)
11. [Environment Configuration Reference](#11-environment-configuration-reference)
12. [Using a Different Automation Repo Instead of Jarvis](#12-using-a-different-automation-repo-instead-of-jarvis)

---

## 1. The Big Picture

This system is a **multi-agent AI network for QA automation**. It eliminates three of the most time-consuming manual QA tasks:

| Problem | AI Solution |
|---|---|
| Writing test cases from requirements | AI-Test-Studio generates test cases + pushes to TestRail |
| Writing automation code from test steps | Test Authoring Agent writes Java, validates, and raises a PR |
| Investigating CI failures | Test Triaging Agent classifies failures; Healing Agent fixes locators automatically |

All three repos run on the same machine or server and communicate over HTTP and the local filesystem.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         ENGINEER'S BROWSER                                  │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               │ HTTP
┌──────────────────────────────▼──────────────────────────────────────────────┐
│                      AI-TEST-STUDIO  :5001                                  │
│   Flask web app  │  RAG (ChromaDB)  │  TestRail sync  │  Confluence sync    │
│                                                                             │
│   /api/agents/*  ──────────── proxy ──────────────────────────────────────► │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               │ HTTP :8765
┌──────────────────────────────▼──────────────────────────────────────────────┐
│                      QA-AGENT-NETWORK  :8765                                │
│                                                                             │
│  ┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐ │
│  │  Test Authoring     │  │  Test Triaging       │  │  Test Healing       │ │
│  │  Agent              │  │  Agent               │  │  Agent              │ │
│  │                     │  │                      │  │                     │ │
│  │  Plain text → Java  │  │  CI failures →       │  │  Broken locators →  │ │
│  │  → PR               │  │  Classification      │  │  Fixed code → PR   │ │
│  └──────────┬──────────┘  └──────────────────────┘  └────────┬────────────┘ │
└─────────────┼────────────────────────────────────────────────┼──────────────┘
              │ Writes/runs Java files                         │ Reads/fixes Java files
              ▼                                                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      JARVIS  (Java Maven Project)                           │
│                                                                             │
│   Playwright UI tests  │  REST-Assured API tests  │  Appium mobile tests   │
│   TestNG test runner   │  JsonTestReporter → report.json                   │
│   AI Evaluation suite  │  TestRail result upload  │  Slack notifications   │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Screenshot — Admin Dashboard:**
> `docs/admin-dashboard.png`
> The admin panel where users manage documents, sync TestRail/Confluence, manage users, and browse the vector database.

**Screenshot — Customer Portal (Test Generation):**
> `docs/customer-generate-tests-home.png`
> The main portal where engineers paste requirements and trigger the AI agent pipeline.

---

## 2. Repository Overview

| Repo | Language | Role | Port |
|---|---|---|---|
| **AI-Test-Studio** | Python / Flask | Web UI + orchestration hub + RAG engine | 5001 |
| **QA-Agent-Network** | Python + Bash | Multi-agent AI pipeline (3 agents + HTTP server) | 8765 |
| **Jarvis** | Java / Maven | Automation test execution framework (target repo) | — |

**Physical layout on disk:**
```
/your-workspace/
├── AI-Test-Studio/          ← main Flask app
│   ├── QA-Agent-Network/    ← embedded sub-repo (separate git history)
│   └── Jarvis/              ← embedded sub-repo (separate git history)
```

---

## 3. Repo 1 — AI-Test-Studio (The Hub)

**What it is:** A Flask web application that acts as the user-facing front door for the entire system. Engineers use it to generate test cases, author automation, and chat with documentation.

### 3.1 Directory Structure

```
AI-Test-Studio/
├── backend/
│   ├── app.py                    ← Flask app factory, registers all blueprints
│   ├── api/
│   │   ├── auth/routes.py        ← Login, logout, session, brute-force protection
│   │   ├── admin/routes.py       ← Upload docs, TestRail/Confluence sync, ChromaDB mgmt
│   │   ├── customer/routes.py    ← Requirement analysis (SSE), RAG query, TestRail push
│   │   └── agents/proxy.py       ← Reverse proxy: /api/agents/* → QA-Agent-Network :8765
│   ├── services/                 ← RAGService, AuthService, SyncService, SettingsService
│   ├── rag/                      ← ChromaDB helpers, multi-format doc parsing, embedding cache
│   └── connectors/               ← TestRail API, Confluence API
├── frontend/
│   ├── admin/index.html          ← Admin dashboard (Vue-style single page)
│   └── customer/index.html       ← Customer portal: 3 tabs (Generate / Automation / Chat)
├── config/
│   └── env.example               ← All required environment variables documented
├── storage/                      ← chroma_db/, documents/, users/ (runtime data)
├── QA-Agent-Network/             ← sub-repo
└── Jarvis/                       ← sub-repo
```

### 3.2 The Three Customer Features

**Tab 1 — Generate Tests**
1. Engineer pastes a requirement, uploads a file (PDF/DOCX/PPTX/XLSX), or provides a Confluence URL.
2. Backend calls LLM (OpenAI/Gemini/Ollama) to analyse requirements and identify test cases + gaps.
3. Engineer reviews the generated test cases in the UI.
4. One click pushes selected test cases directly to a TestRail project.

**Tab 2 — Tests → Automation**
1. Engineer pastes plain-English test steps or fetches automatable cases from TestRail.
2. UI calls `POST /api/agents/test-authoring-agent/run` which is proxied to QA-Agent-Network.
3. Live console in the browser streams every step of the agent pipeline via Server-Sent Events.
4. When done, the UI shows the GitHub PR link.

**Tab 3 — Talk to Tests**
1. Admin has uploaded test documentation, test plans, or spec files via the Admin panel.
2. Documents are chunked, embedded, and stored in ChromaDB (vector database).
3. Engineer types a question; the RAG pipeline retrieves relevant chunks and an LLM answers.

**Screenshot — Generate Tests Response:**
> `docs/customer-generate-tests-response.png`

**Screenshot — Chat Response:**
> `docs/customer-chat-response.png`

### 3.3 The Agents Proxy (Key Integration Point)

`backend/api/agents/proxy.py` is the bridge between AI-Test-Studio and QA-Agent-Network. Every request to `/api/agents/*` on port 5001 is transparently forwarded to `http://localhost:8765/agents/*`.

```python
# All routes under /api/agents/* forward to QA_AGENT_NETWORK_URL
QA_AGENT_NETWORK_URL = os.getenv("QA_AGENT_NETWORK_URL", "http://localhost:8765")
```

This means AI-Test-Studio does **not** need to know how agents work internally — it just proxies HTTP and streams SSE back to the browser.

### 3.4 Technology Stack

| Layer | Technology |
|---|---|
| Web framework | Flask 3.0 + flask-cors |
| Vector database | ChromaDB |
| RAG | LangChain (langchain-chroma, langchain-openai, langchain-google-genai, langchain-ollama) |
| LLM options | OpenAI GPT-4, Google Gemini, Ollama (local) |
| Document parsing | pypdf, python-docx, python-pptx, pandas, openpyxl, unstructured |
| Scheduling | APScheduler (auto-sync jobs) |
| Production server | Gunicorn |

---

## 4. Repo 2 — QA-Agent-Network (The Agents)

**What it is:** A collection of three independent AI agents plus an HTTP server that exposes the Test Authoring Agent to AI-Test-Studio. All agents use Claude (Anthropic) as their AI backbone, invoked via the Claude CLI as subprocesses.

### 4.1 Directory Structure

```
QA-Agent-Network/
├── agents/
│   ├── test-authoring-agent/     ← Text → Java code → GitHub PR
│   │   ├── run.sh                ← 5-step pipeline orchestrator
│   │   ├── actions/              ← 01_parse.py through 05_ship.py
│   │   ├── queue/                ← Input .txt files (one per feature)
│   │   └── audit/                ← Per-session logs, JSON outputs, gate files
│   ├── test-triaging-agent/      ← CI failures → classification → report
│   │   ├── run.sh
│   │   ├── actions/              ← 01_scout.py through 05_ship.py
│   │   └── lib/                  ← DB, HTML parsers, report generator, memory
│   └── test-healing-agent/       ← AUTOMATION_ISSUE failures → fix → GitHub PR
│       ├── run.sh
│       ├── actions/              ← 01_fix.py, 02_ship.py
│       └── lib/                  ← code_analyzer.py
├── qa_agents_server/             ← Flask HTTP server on :8765
│   ├── app.py                    ← Server factory, CORS config, runner init
│   ├── routes.py                 ← REST + SSE endpoints
│   ├── runner.py                 ← Subprocess management + SSE ring buffer
│   └── audit_reader.py           ← Replay historical sessions
├── shared/                       ← Utilities used by all agents
│   ├── claude.py                 ← Claude CLI subprocess caller
│   ├── mcp_config.py             ← Writes .mcp.json for Playwright MCP
│   ├── github.py                 ← gh CLI wrapper for PR creation
│   ├── slack.py                  ← Slack Bot API notifications
│   └── load_env.sh               ← Layered env file loading
├── connectors/mcp/               ← MCP config files (Playwright, GitHub, Slack)
└── config/.env                   ← All agent credentials and settings
```

### 4.2 How Agents Call Claude

All AI steps across all three agents use the same pattern via `shared/claude.py`:

```bash
claude -p "<prompt>" --model claude-opus-4-6
```

The Claude CLI is invoked as a subprocess. Stdout is streamed in real time; each call is logged with a timestamp. No Python Anthropic SDK is used — everything goes through the CLI.

**Models in use:**
- `claude-opus-4-6` — Test authoring (generation, parsing, fixing), test triaging classification, test healing
- `claude-sonnet-4-6` — Test triaging adversarial reviewer

### 4.3 The HTTP Server (qa_agents_server)

The server wraps the Test Authoring Agent's `run.sh` in an HTTP interface so AI-Test-Studio can trigger it and stream results.

**Endpoint map (all under `/agents/test-authoring-agent/`):**

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/run` | Start a new agent run (module + options) |
| `GET` | `/run/<id>/stream?offset=N` | SSE live stream of run output |
| `GET` | `/run/active` | Check if a run is currently in progress |
| `POST` | `/run/<id>/cancel` | Cancel an active run |
| `GET` | `/queue` | List .txt queue files |
| `POST` | `/queue` | Create a new queue file |
| `GET` | `/sessions` | List audit history |
| `GET` | `/sessions/<id>` | Full session detail |
| `GET` | `/health` | Health check |

**Server-Sent Events format** — the stream sends five event types:

```
event: stdout   → raw line from run.sh output
event: step     → a step JSON file landed in audit/ (triggers UI progress update)
event: status   → run state changed (RUNNING → DONE / FAILED)
event: done     → run finished with exit code
event: heartbeat → keepalive every 15 seconds
```

**Concurrency policy (v1):** Only one agent run at a time. Subsequent requests are queued and run in order.

---

## 5. Repo 3 — Jarvis (The Automation Framework)

**What it is:** A Java 21 / Maven automation framework that the AI agents write code into and run tests against. It is the *target* repo — the agents are its code authors.

### 5.1 Directory Structure

```
Jarvis/
├── src/
│   ├── main/java/automation/
│   │   ├── core/                 ← Framework: TestBase, BasePage, helpers, reporters
│   │   │   ├── Config.java       ← Central runtime config
│   │   │   ├── TestBase.java     ← TestNG base class with data providers
│   │   │   ├── BasePage.java     ← Playwright wrapper for all UI interactions
│   │   │   ├── ApiHelper.java    ← REST-Assured high-level API client
│   │   │   ├── JsonTestReporter.java  ← Writes test-results/report.json (read by agents)
│   │   │   └── TestRailHelper.java    ← Upload results to TestRail
│   │   ├── modules/
│   │   │   ├── github/           ← GitHub web + API module (reference implementation)
│   │   │   └── saucedemo/        ← SauceDemo web + API module (reference implementation)
│   │   └── aiEval/               ← AI response evaluation framework (scores + CI gate)
│   └── test/java/automation/
│       ├── github/               ← GitHubApiTest.java, GitHubLoginTest.java
│       ├── saucedemo/            ← SauceDemoApiTest.java, SauceDemoWebTest.java
│       └── aiEval/               ← Unit tests for all evaluator classes
├── parameters/
│   ├── config.properties         ← Base config (env=staging, browser=chromium)
│   ├── staging-sg.properties     ← Env + country overrides
│   ├── system.properties         ← Local secrets (git-ignored)
│   └── ai-eval.properties        ← AI eval thresholds (minScore=0.85)
├── testng.xml                    ← Full test suite definition
├── run-parallel-tests.xml        ← Parallel execution suite
├── CLAUDE.md                     ← ⭐ Framework conventions — read by ALL AI agents
└── pom.xml                       ← Maven build + all dependencies
```

### 5.2 The Three Test Layers

**Web UI Tests** — Playwright (Java) with Page Object Model
- `BasePage.java` wraps all Playwright interactions (click, fill, getText, waitForElement)
- Page objects extend `BasePage`
- Tests extend `TestBase` (TestNG lifecycle, data providers, user allocation)

**REST API Tests** — REST-Assured
- `ApiDetails` interface defines method + endpoint + expected status
- `ApiHelper` executes requests with shared auth headers
- `BaseApiClient` handles authentication

**Mobile Tests** — Appium
- `AppiumDriverManager` for device/emulator setup
- `BrowserStackHelper` for cloud device farms

### 5.3 The Critical File: CLAUDE.md

`Jarvis/CLAUDE.md` is the **single source of truth for the entire AI agent network**. Every Claude call that generates or fixes Java code starts by reading this file. It defines:

- Package structure for new modules
- Class naming conventions (`{Feature}Data`, `{Feature}Builder`, `{Feature}Helper`, etc.)
- How to write Data POJOs, Builders, API enums, Helpers, Page Objects, and Test classes
- What to do vs. what not to do (explicit DO/DON'T rules)
- How to extend an existing module without breaking existing tests

> **If you change the framework, update `Jarvis/CLAUDE.md`.** The AI will follow it automatically on the next run.

### 5.4 How Jarvis Reports Results to Agents

`JsonTestReporter.java` is a TestNG listener that writes `test-results/report.json` after every test run. This file is what `04_run_and_fix.py` reads to determine pass/fail:

```json
{
  "totalTests": 4,
  "passed": 3,
  "failed": 1,
  "results": [
    { "test": "loginAndAddToCart", "status": "PASSED", "duration": 3241 },
    { "test": "checkoutFlow",      "status": "FAILED", "error": "Element not found: [data-test='checkout']" }
  ]
}
```

### 5.5 The AI Evaluation Subsystem

`Jarvis/src/main/java/automation/aiEval/` is an optional CI gate that evaluates AI-generated responses:

| Dimension | Weight | What it checks |
|---|---|---|
| Accuracy | 40% | Response correctness vs expected |
| Safety | 20% | No forbidden patterns in output |
| Rationale | 15% | Reasoning quality |
| Traceability | 15% | Audit trail present |
| Performance | 10% | Response latency under 8000ms |

`CIGate.java` throws `CIGateException` if the composite score falls below `defaultMinScore=0.85`, failing the CI build automatically.

---

## 6. How the Three Repos Connect

### 6.1 Connection Map

```
AI-Test-Studio ←──HTTP──→ QA-Agent-Network ←──Filesystem/subprocess──→ Jarvis
   :5001                      :8765
```

**Connection 1 — AI-Test-Studio → QA-Agent-Network (HTTP)**
- Config: `QA_AGENT_NETWORK_URL=http://localhost:8765` in `AI-Test-Studio/config/.env`
- Mechanism: `backend/api/agents/proxy.py` proxies all `/api/agents/*` requests
- Protocol: HTTP JSON + SSE (Server-Sent Events for live streaming)

**Connection 2 — QA-Agent-Network → Jarvis (filesystem + subprocess)**
- Config: `WORKSPACE_DIR=/path/to/parent` + `GITHUB_REPO_AUTOMATION=Jarvis` in `QA-Agent-Network/config/.env`
- File writes: `03_generate.py` writes Java files directly to `$WORKSPACE_DIR/Jarvis/src/`
- Test runs: `04_run_and_fix.py` and `01_fix.py` run `mvn test` as a subprocess inside `$WORKSPACE_DIR/Jarvis/`
- Results read: agents parse `test-results/report.json` written by `JsonTestReporter.java`

**Connection 3 — QA-Agent-Network → GitHub (subprocess)**
- Config: `GITHUB_TOKEN`, `GITHUB_ORG`, `GITHUB_REPO_AUTOMATION` in agent `.env`
- Mechanism: `gh pr create` CLI subprocess
- Branch naming: `feat/qa-autocreate/<feature>-<timestamp>` or `chore/qa-autofix/<build-tag>`

**Connection 4 — QA-Agent-Network → MySQL (test-triaging-agent only)**
- Config: `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME=qa_results`
- Reads test run results written by Jarvis's `DatabaseHelper.java`

**Connection 5 — All repos → Slack (HTTP)**
- Config: `SLACK_BOT_TOKEN`, `SLACK_NOTIFY_CHANNEL=#qa-reports`, `SLACK_ALERT_CHANNEL=#qa-critical`
- Jarvis uses `SlackHelper.java` for run summaries
- Agents use `shared/slack.py` for pipeline notifications

### 6.2 Environment Config Load Order

```bash
# QA-Agent-Network agents load in this order (later values win):
1. QA-Agent-Network/config/.env          ← shared base (all agents)
2. agents/<agent-name>/.env              ← agent-specific overrides
```

```bash
# AI-Test-Studio loads:
AI-Test-Studio/config/.env               ← includes QA_AGENT_NETWORK_URL
```

---

## 7. Workflow 1 — AI Test Case Generation

This workflow runs entirely within AI-Test-Studio. No agents or Jarvis are involved.

```
Step 1: Engineer opens customer portal → Tab "Generate Tests"
        │
        ▼
Step 2: Pastes requirement text  OR  uploads file (PDF/DOCX/XLSX/PPTX)
        OR  provides Confluence URL
        │
        ▼
Step 3: POST /api/customer/requirement-analysis/stream
        │  SSE stream begins — LLM analyses requirements in real time
        │
        ▼
Step 4: LLM output streamed to browser:
        - Test cases generated (positive, negative, edge cases)
        - Coverage gaps identified
        - Traceability mapped (requirement → test)
        │
        ▼
Step 5: Engineer reviews, selects test cases in the UI
        │
        ▼
Step 6: "Push to TestRail" button → POST /api/customer/requirement-analysis/push
        │  TestRail connector creates test cases via API
        │
        ▼
Step 7: TestRail shows new test cases under the project
```

**Technology:** LangChain + ChromaDB (if RAG needed) + OpenAI/Gemini/Ollama LLM

---

## 8. Workflow 2 — Test Authoring Agent (Text → Java → PR)

This is the flagship workflow. It spans all three repos.

```
Step 1: Engineer writes test steps in plain English → saves to a .txt file
        OR uses the "Tests → Automation" tab in the customer portal

Step 2: Frontend: POST /api/agents/test-authoring-agent/run
        { "module": "payments", "auto_push": true }
        │
        ▼ proxy.py forwards to :8765
Step 3: qa_agents_server assigns SESSION_ID, spawns run.sh as subprocess
        Live SSE stream available at: GET /run/<session_id>/stream
        │
        ▼
Step 4 [01/05 — Parse]  ~30 seconds
        01_parse.py reads:
          - queue/payments.txt         (plain English input)
          - Jarvis/CLAUDE.md           (framework conventions)
        Calls Claude (claude-opus-4-6):
          "Given these test steps and these framework conventions, produce a JSON
           generation plan: classes needed, fields, endpoints, pages, test methods"
        Writes: audit/<session>/01-parse.json
        │
        ▼
Step 5 [02/05 — Validate Web]  ~2-3 minutes (only for web/both tests)
        02_validate_web.py writes .mcp.json (Playwright MCP config)
        Calls Claude with --allowedTools mcp__playwright__*
        Claude controls a headless Chromium browser:
          - Navigates to staging URL
          - Clicks through the feature flow
          - Extracts confirmed DOM selectors (CSS/XPath/text)
          - Reports STEP_PASSED / STEP_FAILED for each test step
        Writes: audit/<session>/02-validate-web.json  (selector map)
        │
        ▼
Step 6 [03/05 — Generate]  ~1-2 minutes
        03_generate.py reads:
          - 01-parse.json              (generation plan)
          - 02-validate-web.json       (confirmed selectors)
          - Jarvis/CLAUDE.md           (conventions, injected into prompt)
          - 9 reference Java files from Jarvis (GitHubHelper, SauceDemoHelper, etc.)
        Calls Claude:
          "Generate complete, compilable Java files following these exact conventions.
           Use these confirmed selectors. Follow these reference implementations."
        Writes Java files directly to: $WORKSPACE_DIR/Jarvis/src/main/java/automation/modules/payments/
        Writes: audit/<session>/03-generate.json
        │
        ▼
Step 7 [04/05 — Run and Fix]  ~2-5 minutes (up to 3 attempts)
        04_run_and_fix.py runs:
          cd $WORKSPACE_DIR/Jarvis && mvn test -Dtest=PaymentsApiTest,PaymentsWebTest
        Reads: Jarvis/test-results/report.json
        If FAIL:
          - Sends compile errors + test output back to Claude
          - Claude produces corrected Java files
          - Re-runs mvn test
          - Repeats up to MAX_FIX_ATTEMPTS=3
        Writes: audit/<session>/04-run-and-fix.json
               audit/<session>/.fix-passed  (true / false / skipped)
        │
        ▼
Step 8 [05/05 — Ship]  ~30 seconds (if AUTO_PUSH=true)
        05_ship.py:
          - git checkout -b feat/qa-autocreate/payments-20260502-143022
          - git add src/main/java/automation/modules/payments/
          - git commit -m "feat(payments): add automated tests for payment flow"
          - git push origin feat/qa-autocreate/payments-20260502-143022
          - gh pr create → returns PR URL
          - slack.py posts success to #qa-reports
        Writes: audit/<session>/05-ship.json
               audit/<session>/.verdict  (APPROVED / NEEDS-REVIEW)
        │
        ▼
Step 9: Browser displays "PR created: https://github.com/org/Jarvis/pull/42"
```

**Testing/cache mode:** Set `TESTING_MODE=true` to cache steps 01 and 02 outputs. Speeds up iteration during development — steps 01+02 are skipped and restored from `cache/<module>/`.

---

## 9. Workflow 3 — Test Triaging Agent (CI → Classification → Report)

This workflow runs on a schedule or after each CI build. It does not involve AI-Test-Studio.

```
Step 1: CI build finishes → Jarvis runs tests → writes results to MySQL + HTML reports
        (DatabaseHelper.java inserts rows; TestNG HTML report generated)
        │
        ▼
Step 2: run-analyse.sh (manual trigger or cron)
        Calls: make run AGENT=test-triaging-agent
        │
        ▼
Step 3 [01/05 — Scout]  seconds
        01_scout.py queries MySQL:
          "Find build tags with unanalyzed failures, scored by failure count + recency"
        Returns the highest-priority build tag to analyse
        │
        ▼
Step 4 [02/05 — Collect]  ~30 seconds
        02_collect.py:
          - Queries MySQL for all failures in the selected build tag
          - Parses HTML test reports (BeautifulSoup) for stack traces + error messages
          - Detects flaky tests (passed in some runs, failed in others)
          - Computes failure trends across recent builds
        │
        ▼
Step 5 [03/05 — Classify]  ~2-5 minutes
        03_classify.py batches failures in groups of 10:
        For each batch, calls Claude (claude-opus-4-6):
          "Given these test failures, stack traces, and error messages,
           classify each as PRODUCT_BUG or AUTOMATION_ISSUE.
           Provide confidence (HIGH/MEDIUM/LOW) and root cause category."
        Root cause categories: ELEMENT_NOT_FOUND, TIMING, ENV_ISSUE, DATA_ISSUE, etc.
        │
        ▼
Step 6 [04/05 — Review]  ~2-3 minutes
        04_review.py runs independent adversarial review:
          - Claude (claude-sonnet-4-6) reviews the classifier's output
          - Up to MAX_REVIEW_ROUNDS=2 debate rounds
          - Each round: reviewer challenges classifications, classifier defends/updates
          - Writes final .verdict file per failure
        │
        ▼
Step 7 [05/05 — Ship]
        05_ship.py:
          - Generates HTML report with categorised failures, confidence scores, root causes
          - Identifies which failures qualify for auto-healing:
              classification = AUTOMATION_ISSUE
              confidence = HIGH
              root_cause_category = ELEMENT_NOT_FOUND
          - Writes handoff JSON to: agents/test-healing-agent/queue/<build-tag>.json
          - Posts report summary to Slack (#qa-reports or #qa-critical)
```

**Sample report screenshot:**
> `QA-Agent-Network/sample_report.png`
> The HTML report shows each failure with AI classification, confidence level, root cause, and the reviewer's verdict.

---

## 10. Workflow 4 — Test Healing Agent (Auto-Fix → PR)

Triggered after the triaging agent writes handoff files. Fixes broken locators automatically.

```
Step 1: test-healing-agent/queue/<build-tag>.json exists
        (written by test-triaging-agent step 05)
        Contains: list of AUTOMATION_ISSUE + HIGH + ELEMENT_NOT_FOUND failures
        │
        ▼
Step 2: run-autofix.sh (manual trigger or cron)
        Calls: make run AGENT=test-healing-agent
        │
        ▼
Step 3 [01/02 — Fix]  ~2-3 minutes per test (up to AUTO_FIX_MAX_FIXES_PER_RUN=5)
        For each failure in the queue:
          a. code_analyzer.py locates the relevant Java files:
               - Test class: src/test/java/automation/payments/PaymentsWebTest.java
               - Page object: src/main/java/automation/modules/payments/web/PaymentsPage.java
          b. Builds prompt:
               - Failing test method code
               - Page object code (where broken locator lives)
               - Actual error: "Element not found: [data-test='submit-payment']"
               - REPO_CONTEXT_FILE (CONVENTIONS.md — framework rules)
          c. Calls Claude (claude-opus-4-6):
               "Fix the locator in this page object. The selector is stale.
                Suggest a more resilient alternative."
          d. Applies the fix, runs: mvn test -Dtest=PaymentsWebTest#checkoutFlow
          e. If PASS: git commit; if FAIL: rollback, retry with previous error injected
          f. Repeats up to MAX_FIX_ATTEMPTS=2 per test
        │
        ▼
Step 4 [02/02 — Ship]
        02_ship.py:
          - git checkout -b chore/qa-autofix/payments-20260502
          - git push with all committed fixes
          - gh pr create with detailed description of each fix
          - Slack notification: "Auto-fixed 3/4 locator failures in payments module"
```

**Full end-to-end loop summary:**
```
CI runs Jarvis tests
    → Jarvis writes results to MySQL
        → Triaging Agent classifies failures
            → Healing Agent fixes AUTOMATION_ISSUEs
                → GitHub PR reviewed by engineer
                    → Merged → next CI run passes
```

---

## 11. Environment Configuration Reference

### AI-Test-Studio (`config/.env`)

```env
# Required
SECRET_KEY=your-secret-key
QA_AGENT_NETWORK_URL=http://localhost:8765   ← points to QA-Agent-Network server

# LLM (pick one)
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
# or: LLM_PROVIDER=google / GOOGLE_API_KEY=...
# or: LLM_PROVIDER=ollama / OLLAMA_BASE_URL=http://localhost:11434

# TestRail integration
TESTRAIL_URL=https://yourcompany.testrail.io
TESTRAIL_EMAIL=qa@company.com
TESTRAIL_API_KEY=...
TESTRAIL_PROJECT_IDS=1,2,3

# Confluence integration
CONFLUENCE_URL=https://yourcompany.atlassian.net
CONFLUENCE_EMAIL=qa@company.com
CONFLUENCE_API_TOKEN=...
```

### QA-Agent-Network (`config/.env`)

```env
# Claude CLI — required by all agents
CLAUDE_CLI_PATH=claude           ← path to claude binary
# ANTHROPIC_API_KEY is used by the claude CLI automatically

# Automation repo — required by authoring + healing agents
WORKSPACE_DIR=/path/to/parent/of/Jarvis   ← parent directory
GITHUB_REPO_AUTOMATION=Jarvis             ← repo folder name

# GitHub — required for PR creation
GITHUB_TOKEN=ghp_...
GITHUB_ORG=your-org
GITHUB_DEFAULT_BRANCH=main

# Slack — optional
SLACK_BOT_TOKEN=xoxb-...
SLACK_NOTIFY_CHANNEL=#qa-reports
SLACK_ALERT_CHANNEL=#qa-critical

# MySQL — required by triaging agent only
DB_HOST=localhost
DB_USER=qa_user
DB_PASSWORD=...
DB_NAME=qa_results

# Agent behaviour
MAX_FIX_ATTEMPTS=3
AUTO_PUSH=true
AUTOCREATE_ENVIRONMENT=staging
AUTOCREATE_COUNTRY=SG
```

---

## 12. Using a Different Automation Repo Instead of Jarvis

This section documents every file and env var that must change when replacing Jarvis with your own automation repository (e.g., a Python pytest suite, a JavaScript Playwright project, a Ruby RSpec project, or another Java framework).

### 12.1 What Is Tightly Coupled to Jarvis

| Coupling | Location | What It Assumes |
|---|---|---|
| `GITHUB_REPO_AUTOMATION=Jarvis` | `QA-Agent-Network/config/.env` | Folder name of the automation repo |
| `WORKSPACE_DIR` + `Jarvis` path | All 3 agents | Parent dir + repo name = full path |
| `Jarvis/CLAUDE.md` | All agent prompts (01_parse, 03_generate, 04_run_and_fix) | Framework conventions document at this path |
| `mvn test` command | `04_run_and_fix.py`, `01_fix.py` | Maven as the build/test runner |
| `test-results/report.json` | `04_run_and_fix.py` | Specific JSON schema written by JsonTestReporter |
| Java package paths | `03_generate.py` | `src/main/java/automation/modules/{feature}/` structure |
| 9 Java reference files | `03_generate.py` | GitHubHelper, SauceDemoHelper, ApiHelper, etc. as generation examples |
| Java file extension + syntax | Claude prompts in 03_generate | Generates `.java` files |
| `CONVENTIONS.md` | `01_fix.py` (`REPO_CONTEXT_FILE` env var) | Framework conventions for the healing agent |
| MySQL test results schema | `test-triaging-agent/01_scout.py`, `02_collect.py` | `qa_results` DB + `buildTag` column convention |

### 12.2 Step-by-Step Changes Required

---

#### Change A — Point agents at the new repo (all agents)

**File:** `QA-Agent-Network/config/.env`

```env
# Before
WORKSPACE_DIR=/path/to/parent/of/Jarvis
GITHUB_REPO_AUTOMATION=Jarvis

# After
WORKSPACE_DIR=/path/to/parent/of/YourRepo
GITHUB_REPO_AUTOMATION=YourRepo           ← folder name of your automation repo
```

This one change propagates the new path to all three agents automatically.

---

#### Change B — Create a CLAUDE.md in your repo (test-authoring-agent)

This is the most important change. The test-authoring-agent reads `$WORKSPACE_DIR/$GITHUB_REPO_AUTOMATION/CLAUDE.md` at the start of every run and injects it into every Claude prompt. Without this file, the agent has no framework knowledge and will generate incorrect code.

**Create:** `YourRepo/CLAUDE.md` with sections covering:
- Package/directory structure for new modules
- Class naming conventions
- How to write data models, builders, API clients, page objects, test classes
- Explicit DO/DON'T rules (e.g., "DO NOT use Thread.sleep", "DO extend BasePage")
- Any framework-specific patterns unique to your codebase

Refer to `Jarvis/CLAUDE.md` as a template — adapt every section to your framework's conventions.

---

#### Change C — Update the build command (test-authoring + test-healing agents)

**File:** `QA-Agent-Network/agents/test-authoring-agent/actions/04_run_and_fix.py`

```python
# Before (Jarvis — Maven)
cmd = ["mvn", "test", f"-Dtest={test_class}#{method}", f"-Denvironment={env}", ...]

# After — example for pytest (Python)
cmd = ["pytest", f"tests/{module}/test_{feature}.py::{method}", "-v", "--tb=short"]

# After — example for npm/jest (JavaScript)
cmd = ["npm", "test", "--", f"--testNamePattern={method}", f"--testPathPattern={feature}"]

# After — example for gradle (Java, non-Maven)
cmd = ["./gradlew", "test", f"--tests=automation.{feature}.{test_class}.{method}"]
```

**File:** `QA-Agent-Network/agents/test-healing-agent/actions/01_fix.py`

Apply the same build command change to the healing agent's test verification step.

---

#### Change D — Update the test results reader (test-authoring-agent)

**File:** `QA-Agent-Network/agents/test-authoring-agent/actions/04_run_and_fix.py`

The agent reads `test-results/report.json` written by `JsonTestReporter.java`. Your framework must produce an equivalent file, OR you need to update the parser.

**Option 1 — Adapt your framework to write the same JSON format:**
```json
{
  "totalTests": 4,
  "passed": 3,
  "failed": 1,
  "results": [
    { "test": "methodName", "status": "PASSED", "duration": 1234 },
    { "test": "anotherMethod", "status": "FAILED", "error": "AssertionError: expected 200 but got 404" }
  ]
}
```

**Option 2 — Update the parser in `04_run_and_fix.py`** to read your framework's native report format (pytest XML, Jest JSON, Allure, etc.):
```python
# Change this function to parse your report format
def _read_test_results(report_path: str) -> dict:
    # e.g., parse pytest's JUnit XML output
    tree = ET.parse(report_path)
    ...
```

---

#### Change E — Update reference implementation files (test-authoring-agent)

**File:** `QA-Agent-Network/agents/test-authoring-agent/actions/03_generate.py`

The generate step reads 9 specific Java files from Jarvis as `<reference_implementations>` to show Claude how correct code looks. Replace these with equivalent files from your repo:

```python
# Before — Jarvis Java reference files
reference_files = [
    "src/main/java/automation/modules/github/GitHubData.java",
    "src/main/java/automation/modules/github/GitHubHelper.java",
    "src/main/java/automation/core/ApiHelper.java",
    "src/test/java/automation/github/GitHubApiTest.java",
    # ... 5 more files
]

# After — your repo's equivalent reference files
reference_files = [
    "src/your_framework/modules/example/ExampleData.py",     # a well-written data class
    "src/your_framework/modules/example/ExampleHelper.py",   # a well-written helper
    "src/your_framework/core/ApiClient.py",                  # base API client
    "tests/example/test_example_api.py",                     # a well-written test
    # ... equivalent web/UI examples
]
```

---

#### Change F — Update the file path templates in 03_generate.py (test-authoring-agent)

**File:** `QA-Agent-Network/agents/test-authoring-agent/actions/03_generate.py`

The generate step constructs target file paths using Java conventions. Update the path builder to match your language and framework:

```python
# Before — Java Maven structure
target_path = f"src/main/java/automation/modules/{feature}/{class_name}.java"
test_path   = f"src/test/java/automation/{feature}/{test_class}.java"

# After — Python pytest example
target_path = f"src/{feature}/{module_name}.py"
test_path   = f"tests/{feature}/test_{feature}.py"

# After — JavaScript Jest example
target_path = f"src/modules/{feature}/{className}.js"
test_path   = f"tests/{feature}/{className}.test.js"
```

---

#### Change G — Update REPO_CONTEXT_FILE for the healing agent

**File:** `QA-Agent-Network/agents/test-healing-agent/.env` (or `config/.env`)

```env
# Before
REPO_CONTEXT_FILE=CONVENTIONS.md

# After — point to wherever your framework conventions live
REPO_CONTEXT_FILE=CLAUDE.md
# or
REPO_CONTEXT_FILE=docs/FRAMEWORK_GUIDE.md
```

Create this file in your repo (if it doesn't exist) — the healing agent injects it into every fix prompt so Claude knows the framework rules when rewriting locators or selectors.

---

#### Change H — Update the code_analyzer.py locator file detection (test-healing-agent)

**File:** `QA-Agent-Network/agents/test-healing-agent/lib/code_analyzer.py`

This file locates the relevant source files (test class + page object) given a failing test name. It currently assumes Java file extensions and Maven directory conventions. Update it for your language:

```python
# Before — searches for .java files in Maven structure
def find_page_object(module: str, page: str) -> str:
    return f"src/main/java/automation/modules/{module}/web/{page}Page.java"

# After — Python Playwright example
def find_page_object(module: str, page: str) -> str:
    return f"src/pages/{module}/{page}_page.py"

# After — JavaScript Playwright example
def find_page_object(module: str, page: str) -> str:
    return f"src/pages/{module}/{page}Page.js"
```

---

#### Change I — No changes required in AI-Test-Studio

AI-Test-Studio's proxy layer (`backend/api/agents/proxy.py`) is completely repo-agnostic. It forwards HTTP and streams SSE without any knowledge of Jarvis or Java. **No changes are needed in AI-Test-Studio** when switching automation repos.

The only thing you might want to update is the UI label in `frontend/customer/index.html` if you want to show your repo's name instead of "Jarvis" in the live console header.

---

#### Change J — Test Triaging Agent (optional, if using a different results DB)

The triaging agent reads test results from MySQL using the `qa_results` database schema written by Jarvis's `DatabaseHelper.java`. If your framework writes results differently:

**Option 1 — Adapt your framework** to write to the same MySQL schema.

**Option 2 — Update the collector** at `agents/test-triaging-agent/actions/02_collect.py` to read from your results store (different DB schema, JUnit XML files, Allure results, etc.).

The classification and review steps (03, 04) are fully framework-agnostic — they work purely from failure descriptions and stack traces.

---

### 12.3 Quick Checklist for Switching Automation Repos

```
Required changes (agent won't work without these):
[ ] Update WORKSPACE_DIR in QA-Agent-Network/config/.env
[ ] Update GITHUB_REPO_AUTOMATION in QA-Agent-Network/config/.env
[ ] Create YourRepo/CLAUDE.md with complete framework conventions
[ ] Update build command in 04_run_and_fix.py (test-authoring-agent)
[ ] Update build command in 01_fix.py (test-healing-agent)
[ ] Update test results reader in 04_run_and_fix.py OR write report.json from your framework
[ ] Update reference files list in 03_generate.py
[ ] Update file path templates in 03_generate.py

Recommended changes (for better results):
[ ] Update REPO_CONTEXT_FILE env var in test-healing-agent .env
[ ] Create YourRepo/CONVENTIONS.md (or equivalent)
[ ] Update code_analyzer.py file path detection for your language
[ ] Add well-written example modules to your repo as generation references

Optional changes (nice-to-have):
[ ] Update test-triaging-agent collector for your results store
[ ] Update UI labels in customer/index.html
[ ] Update GITHUB_PR_REVIEWERS to your team's GitHub handles
```

### 12.4 Supported Framework Combinations (Effort Estimate)

| Your Automation Stack | Effort | Main Changes |
|---|---|---|
| Another Java/Maven framework | Low | CLAUDE.md + reference files + report format |
| Java/Gradle | Low | CLAUDE.md + build command + reference files + report format |
| Python/pytest | Medium | CLAUDE.md + build cmd + path templates + reference files + report format + code_analyzer |
| JavaScript/Playwright (Node) | Medium | CLAUDE.md + build cmd + path templates + reference files + report format + code_analyzer |
| JavaScript/Cypress | Medium | Same as Playwright/Node above |
| Ruby/RSpec | High | All of the above + Claude prompt language guidance in CLAUDE.md |
| Mixed (Web=JS, API=Python) | High | Consider separate CLAUDE.md sections per test type |

---

## Summary

This three-repo architecture creates a **fully autonomous QA agent loop**:

1. **AI-Test-Studio** is the user-facing hub — engineers interact here for test generation, automation authoring, and documentation chat.
2. **QA-Agent-Network** is the AI backbone — three independent agents (Authoring, Triaging, Healing) that use Claude to write, classify, and fix tests automatically.
3. **Jarvis** is the automation target — a production-grade Java framework that the agents write code into, run tests against, and report results from.

The system is designed so that **the AI agents learn your framework conventions from `CLAUDE.md`** — a single file that acts as the living specification for everything the agents generate. Keep it accurate and the entire network stays aligned with your codebase.

---

*Document generated from codebase analysis of AI-Test-Studio, QA-Agent-Network, and Jarvis repositories.*
*Screenshots referenced: `docs/admin-dashboard.png`, `docs/customer-generate-tests-home.png`, `docs/customer-generate-tests-response.png`, `docs/customer-chat-home.png`, `docs/customer-chat-response.png`, `QA-Agent-Network/sample_report.png`*
