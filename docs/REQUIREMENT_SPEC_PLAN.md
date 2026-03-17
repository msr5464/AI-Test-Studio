# Requirement Spec → Test Intelligence Plan

**Goal:** Pass a requirement spec into the tool and get:
1. **Related existing test cases** (from TestRail)
2. **Tests that need updating** (based on updated requirements)
3. **New test cases** for requirements not covered in TestRail

---

## Ideal Requirement Analysis Flow (Implemented)

1. **New requirement doc** → Review it (parse input: paste / file / Confluence URL).
2. **Search Confluence (KB)** → Prior requirement/spec docs in ChromaDB (`source_type: specs`) for **overall feature context**.
3. **Search TestRail (KB)** → Related test-case chunks for **test-case-level context**.
4. **Merge both contexts** → Use Confluence context (feature) + TestRail context (structure) to generate new test cases for uncovered requirements.
5. **Search TestRail** for relevant **existing test cases** to update → assess which need changes vs OK.
6. **Output:** `related_specs`, `related_tests`, `tests_ok` (use as-is), `tests_needing_update` (to modify), `generated_tests` (new to create).

The **Confluence connector** is used to sync Confluence pages into ChromaDB; requirement analysis now uses that synced data via `find_related_specs()` as prior context when generating new tests.

---

## Current State (As-Is)

| Capability | Status | Notes |
|------------|--------|-------|
| TestRail sync → ChromaDB | ✅ Exists | Test cases indexed and queryable via RAG |
| Confluence sync → ChromaDB | ✅ Exists | Specs synced from Confluence via CQL |
| Prior Confluence context in analysis | ✅ Done | find_related_specs() used for feature context when generating new tests |
| Semantic search for tests | ✅ Exists | RAG retrieves relevant chunks by similarity |
| LLM test generation | ✅ Partial | System prompt supports generation when explicitly asked |
| **Requirement spec input** | ✅ **Done** | **Three methods: upload file, Confluence URL, paste text** |
| **RequirementExtractor** | ✅ **Done** | **Regex-based extraction: REQ-001, Requirement 1, etc.** |
| **Requirement–test mapping** | ✅ **Done** | **find_related_tests() with source_type filter** |
| **Gap analysis (uncovered)** | ✅ **Done** | **Min similarity threshold; generate new tests for uncovered** |
| **Update suggestions** | ✅ **Done** | **LLM comparison via _assess_updates(); tests_needing_update in API & UI** |
| **Push new tests to TestRail** | ✅ **Done** | **add_case in connector; push_to_testrail + target_section_id in API & UI** |

---

## Architecture Overview

```
┌─────────────────────┐     ┌──────────────────────┐     ┌─────────────────────┐
│  Requirement Spec   │────▶│  Requirement Spec    │────▶│  ChromaDB           │
│  (PDF/DOCX/Text)    │     │  Ingestion Service   │     │  (requirements_*)    │
└─────────────────────┘     └──────────────────────┘     └─────────────────────┘
           │                               │                          │
           │                               │                          │
           ▼                               ▼                          ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                    Requirement Analysis Orchestrator                          │
│  1. Parse requirements (structured: Req-001, Req-002, ...)                    │
│  2. For each requirement:                                                     │
│     a) RAG search (requirement text) → related test chunks                    │
│     b) LLM: requirement vs tests → update needed? / coverage ok? / no tests   │
│  3. Aggregate: related_tests, needs_update, uncovered_requirements            │
└──────────────────────────────────────────────────────────────────────────────┘
           │                               │                          │
           ▼                               ▼                          ▼
┌─────────────────────┐     ┌──────────────────────┐     ┌─────────────────────┐
│  Related Tests      │     │  Needs Update        │     │  New Test Cases     │
│  (TestRail IDs)     │     │  (TestRail ID +      │     │  (LLM-generated,    │
│                     │     │   suggested changes) │     │   optionally push   │
└─────────────────────┘     └──────────────────────┘     └─────────────────────┘
```

---

## Phase 1: Requirement Spec Ingestion

### 1.1 Add Requirement Spec as a Document Type

**Approach:** Treat requirement specs like other documents but with:
- **Logical filename pattern:** `requirements_<name>.pdf` or `requirements_spec.docx` so they can be identified and optionally versioned.
- **Metadata tagging:** Store `doc_type: "requirements"` in metadata so RAG can filter or prioritize requirement chunks vs test chunks when needed.
- **Structured parsing (optional):** If requirements follow patterns like `REQ-001: ...`, `Requirement 1: ...`, parse into discrete requirements for per-requirement analysis.

**Implementation:**
- Admin upload: same flow as today; add optional `document_type` (default: `testcase`) vs `requirements`.
- `rag_service.upload_document()`: accept optional `doc_type` and pass to `add_files` metadata.
- `multi_format_rag.py`: when adding documents, set `metadata["doc_type"] = doc_type` on chunks.
- No new API surface needed initially—reuse upload; add `doc_type` param later.

### 1.2 Requirement Extraction (Structured)

For the analysis orchestrator to work per-requirement, we need a way to split the spec into individual requirements:

- **Simple regex:** `REQ-\d+`, `Requirement \d+`, `R-\d+`, section headings.
- **LLM extraction:** Pass full spec to LLM; return structured list: `[{id, title, description}, ...]`.
- **Hybrid:** Regex for IDs, LLM for description cleanup if needed.

**Deliverable:** `RequirementExtractor` module that outputs `List[{id, title, description}]`.

---

## Phase 2: Find Related Existing Test Cases

### 2.1 Per-Requirement RAG Search

For each requirement (id, title, description):

1. **Query:** Concatenate title + description (or just description).
2. **RAG retrieval:** Use existing `rag.query()` or lower-level `vectorstore.similarity_search(query, k=N)`.
3. **Optional filter:** Restrict to `doc_type == "testcase"` or `file_path` containing `testrail` so we don’t retrieve requirement chunks as "tests".
4. **Deduplicate:** Map chunks → TestRail IDs (from metadata); return unique test case IDs with relevance scores.

### 2.2 Output Format

```json
{
  "requirement_id": "REQ-001",
  "requirement_title": "User must be able to reset password",
  "related_tests": [
    {"testrail_id": "C123", "title": "...", "similarity_score": 0.85},
    {"testrail_id": "C456", "title": "...", "similarity_score": 0.72}
  ]
}
```

**Implementation:**
- New service: `RequirementAnalysisService` (or extend `RAGService`).
- Method: `find_related_tests(requirement_text: str) -> List[dict]`.
- Reuse existing retriever; parse `source_documents` for TestRail IDs and metadata.

---

## Phase 3: Suggest Which Tests Need Updating

### 3.1 Update Detection Logic

For each requirement with related tests:

1. **Input:** Requirement text + related test case content (title, steps, expected result, preconditions).
2. **LLM prompt:** "Given this requirement and these existing tests, determine:
   - Does the test still align with the requirement? (Yes / No / Partial)
   - If No or Partial: what specific changes are suggested? (steps, expected results, preconditions)
   - Confidence: High / Medium / Low"
3. **Output:** Structured response per test: `{testrail_id, status: "ok"|"needs_update"|"partial", suggested_changes: [...], confidence}`.

**Status meanings:**
- **ok:** Test fully covers the requirement; no changes needed.
- **needs_update:** Test is missing significant coverage or is misaligned; substantial changes needed (e.g. wrong scope, missing scenarios, or requirement has evolved).
- **partial:** Test covers only part of the requirement (e.g. one scenario or a minor aspect); it can be extended or refined rather than rewritten. Use when the test is valid but incomplete for the full requirement.

### 3.2 Prompt Design

Use a dedicated system/user prompt for update analysis (separate from general RAG chat):

```
You are a test analyst. Given:
- Requirement: [requirement text]
- Existing test: [test case title, steps, expected result]

Analyze if the test fully covers the requirement or needs updates.
Return JSON: {"status": "ok"|"needs_update"|"partial", "suggested_changes": ["..."], "reason": "..."}
```

### 3.3 Output Format

```json
{
  "requirement_id": "REQ-001",
  "tests_needing_update": [
    {
      "testrail_id": "C123",
      "title": "...",
      "status": "needs_update",
      "suggested_changes": ["Add step: verify email sent", "Update expected result: ..."],
      "reason": "Test omits email verification step per updated requirement"
    }
  ],
  "tests_ok": ["C456"]
}
```

---

## When We Generate New Test Cases vs When We Don't

**Generate new tests when (either):**
1. **No related tests** – RAG returns no test-case chunks above the similarity threshold for that requirement (**uncovered**), or  
2. **Related tests exist but coverage is insufficient** – An LLM check (`_is_coverage_sufficient`) decides that the existing tests, taken together, do **not** fully cover the requirement (e.g. they only cover a partial flow, or only non-critical/edge cases, or miss key positive/negative E2E scenarios). Then we treat the requirement as under-covered and also generate additional tests.

**Do not generate new tests when:** The requirement has at least one related test **and** the LLM judges that coverage is **sufficient** (existing tests collectively cover acceptance criteria and key positive/negative flows). In that case we only classify related tests as OK or needing update.

**Decision flow (implemented in `RequirementAnalysisService.analyze()`):**
1. For each requirement: `find_related_tests(req_text, k=10)` with `source_type=testcase` and `REQUIREMENT_RETRIEVAL_SIMILARITY_THRESHOLD` (default 60%).
2. **No related tests** → add to `uncovered_requirements`; if `generate_new_tests=True`, call `_generate_tests_for_requirement()`.
3. **Related tests exist** → run `_assess_updates()` (OK vs needing update), then `_is_coverage_sufficient(requirement_text, related_tests)`. If **not sufficient** → add to `uncovered_requirements` and generate; if sufficient → do not generate.
4. Result: we generate for requirements with zero related tests **or** with related tests that only give partial/non-critical coverage.

**Config:** `REQUIREMENT_RETRIEVAL_SIMILARITY_THRESHOLD` and `REQUIREMENT_RETRIEVAL_K` control how strict “related” is. The coverage-sufficiency check uses a dedicated LLM call so that 1–2 tests covering only partial or non-important flows still trigger generation.

---

## E2E Test Generation: Approach (Aspire Context)

Generated tests must be **end-to-end** only: complete user journeys and real user stories in the **Aspire system context**, not unit-level or single-step tests.

**Principles:**
1. **E2E only** – Each test is a full flow from user entry point to outcome (e.g. login → navigate → perform action → verify result). No isolated validations or unit-style checks.
2. **User journeys** – Scenarios reflect how a real user would use the system (roles, steps, data, and expected results aligned with the product).
3. **Positive and negative flows** – Cover both:
   - **Positive:** Happy path (valid inputs, expected success, correct state).
   - **Negative:** Error paths (invalid input, permissions, edge cases, lockout, validation messages) where the requirement implies them.
4. **Aspire context** – Tests are written for the Aspire product: flows, terminology, and behaviour should match the current Aspire system; no generic or hypothetical product details.

**Implementation:** The LLM prompt in `_generate_tests_for_requirement()` instructs the model to produce multiple E2E test cases per requirement, cover every acceptance point, include both positive and negative scenarios where relevant, and to avoid unit-level or single-step tests. Prior Confluence/spec context and example TestRail tests provide feature and structural guidance.

---

## Phase 4: Generate New Test Cases for Uncovered Requirements

### 4.1 Gap Identification

- **Uncovered:** Requirements with no related tests (RAG returns empty after retrieval). Covered = has at least one related test; retrieval threshold only (no separate coverage threshold).

### 4.2 New Test Generation

For each uncovered requirement:

1. **Context:** Requirement text + prior Confluence/spec context (feature) + a few similar existing tests from TestRail (structure/style).
2. **LLM prompt:** E2E-only, user-journey and Aspire-context rules (see section **E2E Test Generation: Approach** above); output JSON array with Title, Priority, Preconditions, Steps, Expected Result.
3. **Output:** Structured test case(s) in the same format as existing tests (3–8 E2E tests per requirement, priority-ordered).

### 4.3 Push to TestRail (Optional)

**TestRail API:** `POST /api/v2/add_case/{section_id}` to create a case.

**Implementation:**
- Extend `TestRailConnector` with `add_case(section_id, case_data) -> dict`.
- **Section mapping:** User must specify target section (or we derive from requirement section/tag).
- **Section:** User selects Project → Suite → Section in customer portal (or “Use same section as related tests”).
- **Safety:** Generated tests are suggested first; "Push to TestRail" as explicit user action (UI or API flag).

---

## Phase 5: Orchestrator & API

### 5.1 Requirement Analysis Orchestrator

Single entry point that runs the full pipeline:

```
analyze_requirements(spec_text_or_path) ->
  1. Extract requirements (RequirementExtractor)
  2. For each requirement:
     a. find_related_tests(requirement)
     b. If related_tests: assess_updates(requirement, related_tests)
     c. If no related_tests or all "needs_update" with low coverage: generate_new_tests(requirement)
  3. Aggregate and return report
```

### 5.2 API Design

**New endpoint:** `POST /api/customer/requirement-analysis`

**Request:**
```json
{
  "requirement_spec": "Raw text or path to uploaded doc",
  "options": {
    "generate_new_tests": true,
    "push_to_testrail": false,
    "target_section_id": null
  }
}
```

**Response:**
```json
{
  "success": true,
  "requirements_analyzed": 5,
  "related_tests": { "REQ-001": [...], "REQ-002": [...] },
  "tests_needing_update": { "REQ-001": [...], "REQ-003": [...] },
  "uncovered_requirements": ["REQ-004", "REQ-005"],
  "generated_tests": { "REQ-004": [...], "REQ-005": [...] },
  "summary": { ... }
}
```

### 5.3 UI Flow

- **Customer portal:** New "Requirement Analysis" section.
- **Input:** Upload requirement spec (PDF/DOCX) or paste text.
- **Output:** Tabbed or accordion view:
  - Related tests (with TestRail links)
  - Tests needing update (with suggested changes)
  - Generated new tests (with "Copy" / "Push to TestRail" if enabled)

---

## Implementation Order

| Phase | Scope | Dependency | Effort (Est.) |
|-------|-------|------------|---------------|
| **1** | Requirement ingestion + doc_type metadata | None | 1–2 days |
| **1b** | Requirement extraction (regex/LLM) | Phase 1 | 1 day |
| **2** | Find related tests per requirement | Phase 1 | 1 day |
| **3** | Update suggestion (LLM comparison) | Phase 2 | 1–2 days |
| **4a** | Generate new tests for uncovered reqs | Phase 2, 3 | 1–2 days |
| **4b** | TestRail add_case (optional push) | Phase 4a | 1 day |
| **5** | Orchestrator + API + UI | All above | 2–3 days |

**Total:** ~8–12 days for full flow.

---

## Configuration Additions

```env
# Requirement analysis (covered = has ≥1 related test; retrieval threshold only)
REQUIREMENT_NEEDS_UPDATE_CONFIDENCE_THRESHOLD=70   # 0-100 percentage or 0-1 decimal; only suggest needs_update if confidence >= this
TESTRAIL_PUSH_ENABLED=false                   # Allow push to TestRail (section chosen in UI)
```

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| LLM hallucination in update suggestions | Use structured output, confidence scores; human review before applying |
| Requirement extraction quality | Start with regex; add LLM extraction as enhancement |
| TestRail write permissions | Push optional, behind config flag; require explicit user action |
| Large specs (100+ requirements) | Batch processing; async job + progress polling |
| Cost (LLM calls per requirement) | Cache; batch similar requirements; use cheaper model for triage |

---

## Success Criteria

1. **Related tests:** For a given requirement spec, the tool returns TestRail test IDs with relevance scores.
2. **Update suggestions:** For tests that no longer match the requirement, the tool suggests specific changes.
3. **New tests:** For requirements with no coverage, the tool generates test cases in the same format as existing tests.
4. **End-to-end:** User can upload a requirement spec and get a single report with all three outputs.
5. **Optional:** Generated tests can be pushed to TestRail with user confirmation.

---

## Roadmap (Requirement Analysis & TestRail UX) — completed

**E2E workflow:** For each requirement: (1) Confluence context for E2E flow, (2) TestRail related tests, (3) Produce one E2E set = reuse + update + **generate only gaps** (P0/P1 by default; optional P2/P3). Gaps addressed: unified "recommended E2E set" per requirement (reuse_as_is, use_after_update, create_new); generation "only fill gaps"; default P0/P1, optional P2/P3; Push → ingest into ChromaDB; Confluence as E2E flow context (optional). See **SELF_TESTING_REQUIREMENT_ANALYSIS.md** for config.

| # | Topic | Status |
|---|--------|--------|
| 1 | Generate only P0–P1, E2E, automation-friendly | Done |
| 2 | Fix push modal content cut on left | Done |
| 3 | Push modal: load dropdowns once, prefill next time | Done |
| 4 | After push: uncheck/remove checkbox, show TestRail link | Done |
| 5 | Push progress: "x out of y done…" | Done |
| 6 | Update with AI: preserve existing content in suggestion | Done |
| 7 | Remove "Generate with AI"; add Update / Insert as new in popup | Done |
| 8 | After update/insert: remove "Update with AI", show link | Done |
| 9 | Full E2E testing via UI | Done |
