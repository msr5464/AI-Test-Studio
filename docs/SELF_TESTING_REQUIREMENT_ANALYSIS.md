# Self-Testing: Requirement Analysis

Use this checklist to set up config so the requirement analysis flow can be tested (by you or by automated tests).

---

## 1. Where to update

| Location | Purpose |
|----------|--------|
| **`config/.env`** | Actual values used at runtime. Copy from `config/env.example` if missing. |
| **`config/env.example`** | Template only; keep placeholders for others. You can add real values in `.env` for local testing. |

**Important:** Never commit real API keys or secrets to git. Use `config/.env` (and ensure it is in `.gitignore`).

---

## 2. What to update (by scenario)

### 2.1 Minimal self-test (paste text only, no Confluence, no TestRail push)

Enough for: **Paste requirement spec** → extract requirements → RAG related tests → LLM update assessment → generated tests.

| Variable | Where | What to set | Required for paste-only? |
|----------|--------|-------------|---------------------------|
| **LLM** | `config/.env` | One of: `LLM_PROVIDER=ollama` with `OLLAMA_BASE_URL` and `OLLAMA_MODEL`, or `LLM_PROVIDER=openai` with `OPENAI_API_KEY` and `OPENAI_MODEL` | **Yes** (for assess_updates + generate new tests) |
| **ChromaDB / RAG** | `config/.env` | `CHROMA_DB_DIR=storage/chroma_db` (default). Collection must exist and have test-case documents (run TestRail sync once to populate). | **Yes** (for related_tests and coverage) |
| **Embeddings** | `config/.env` | Same as your RAG setup (e.g. OpenAI or Ollama embeddings). Required for similarity search. | **Yes** |

So for **paste-only self-test** you need:

1. **`config/.env`** (copy from `config/env.example`):
   - Set **LLM**: e.g. `LLM_PROVIDER=ollama`, `OLLAMA_BASE_URL=http://localhost:11434`, `OLLAMA_MODEL=llama3.2:3b` (or OpenAI keys if you use OpenAI).
   - Leave **TestRail** and **Confluence** as placeholders if you are not testing Confluence URL or Push.
   - ChromaDB path is fine as default; ensure RAG has been used at least once (or run TestRail sync) so the collection exists and has test cases.

2. **RAG has data**: Either run a TestRail sync (with TestRail credentials set) so ChromaDB has test cases, or ingest some test-case documents so `find_related_tests` can return results.

---

### 2.2 Confluence URL input (optional)

Required only when using **Confluence URL** as requirement spec input.

| Variable | Where | What to set |
|----------|--------|-------------|
| `CONFLUENCE_URL` | `config/.env` | Base URL, e.g. `https://yourcompany.atlassian.net/wiki` |
| `CONFLUENCE_EMAIL` | `config/.env` | Email for Confluence/Atlassian API |
| `CONFLUENCE_API_TOKEN` | `config/.env` | Atlassian API token (from account settings) |

---

### 2.3 Push generated tests to TestRail (optional)

Required only when you use **Push to TestRail** (UI checkbox or API `push_to_testrail=true`).

| Variable | Where | What to set |
|----------|--------|-------------|
| `TESTRAIL_URL` | `config/.env` | e.g. `https://yourcompany.testrail.io` |
| `TESTRAIL_EMAIL` | `config/.env` | TestRail user email |
| `TESTRAIL_API_KEY` | `config/.env` | TestRail API key (from user settings) |
| `TESTRAIL_PUSH_ENABLED` | `config/.env` | `true` to allow push (default: `false`) |
| Section for push | Chosen in UI | Project → Suite → Section (or “Use same section as related tests”); no env default |

---

### 2.4 Requirement analysis tuning (optional)

| Variable | Where | Default | Description |
|----------|--------|--------|-------------|
| `REQUIREMENT_NEEDS_UPDATE_CONFIDENCE_THRESHOLD` | `config/.env` | `70` (or `0.7`) | LLM must have at least this confidence to mark a test as “needs_update”. Use **0–100** (e.g. `70`) or **0–1** (e.g. `0.7`). |

Coverage: a requirement is covered if it has at least one related test (after retrieval). No separate coverage threshold.

---

## 3. Quick checklist for “I want to self-test once”

1. Copy `config/env.example` to `config/.env` (if you don’t have `.env` yet).
2. In **`config/.env`** set:
   - **LLM**: e.g. `LLM_PROVIDER=ollama`, `OLLAMA_BASE_URL`, `OLLAMA_MODEL` (or OpenAI equivalents).
   - **ChromaDB**: `CHROMA_DB_DIR=storage/chroma_db` (default).
3. Ensure RAG has test-case data (e.g. run TestRail sync once with valid TestRail credentials, or ingest test-case docs).
4. Start app: `./scripts/run.sh` or `python backend/app.py`.
5. Open customer portal → **Requirement Analysis** → paste a short spec, e.g.:
   ```text
   REQ-001: User must be able to reset password via email.
   REQ-002: System shall send verification email within 60 seconds.
   ```
6. Click **Analyze Requirements**. You should see Related tests, Tests needing update (if any), Uncovered, Generated tests.

For **Confluence URL** or **Push to TestRail**, also set the variables in sections 2.2 and 2.3 in **`config/.env`**.

---

## 4. Running the automated requirement-analysis test

From project root **with your venv activated** (so Flask and project deps are available):

```bash
# Unit tests only (mocked service; no RAG/LLM needed)
python3 -m pytest tests/test_requirement_analysis.py -v -m "not integration"

# Include integration test (real RAG + LLM; requires config/.env)
python3 -m pytest tests/test_requirement_analysis.py -v -m integration
```

- **Unit tests**: No `.env` or RAG/LLM needed for most tests; they mock the service and check API shape. Includes `TestRequirementAnalysisConfig` tests that verify `REQUIREMENT_RETRIEVAL_SIMILARITY_THRESHOLD` and `REQUIREMENT_NEEDS_UPDATE_CONFIDENCE_THRESHOLD` load correctly (run with venv so Flask is available for API tests).
- **Integration test**: Uses real endpoint; expects RAG/ChromaDB and LLM configured in `config/.env`. If the app returns non-200 (e.g. RAG not initialized), the test is skipped with a message pointing to this doc.

See `tests/test_requirement_analysis.py` for what is asserted (e.g. response shape, push options passed through, config attribute names).

---

## 5. Full E2E UI checklist (Requirement Analysis + TestRail UX)

After implementing the roadmap in `REQUIREMENT_SPEC_PLAN.md` (Roadmap section), run this manual E2E pass in the customer UI:

1. **Analysis and tabs**
   - Run requirement analysis (paste/upload/URL). Confirm **Related tests**, **Tests needing update**, and **Generated tests** (P0–P1 only) appear as expected.

2. **Push modal**
   - Open **Push selected tests to TestRail**. Confirm layout is not cut on the left; labels and dropdowns are fully visible.
   - Close and reopen the push modal: project/suite/section should be pre-filled from the previous selection.
   - Select one or more generated tests and push. Confirm progress shows **"Pushing... x of y done"**; after success, pushed rows have checkboxes removed/disabled and **TestRail links** shown.

3. **Update with AI**
   - In **Tests needing update**, click **Update with AI** on a test. Confirm suggested content preserves existing title/steps/expected where possible.
   - In the Edit popup, confirm **two** buttons: **Update existing test in TestRail** and **Insert as new test in TestRail**.
   - **Update existing**: submit and confirm the card’s “Update with AI” is removed and the TestRail link (same case) is shown.
   - **Insert as new**: click **Insert as new test in TestRail**; confirm Create modal opens with form pre-filled; select section and create; confirm the **same** card (that had “Update with AI”) now shows the **new** case link and no “Update with AI” button.

4. **No “Generate with AI”**
   - Confirm test cards in “Tests needing update” do **not** show a “Generate with AI” button; only “Update with AI” is present.
