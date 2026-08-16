# How This Repository Works

A full-stack **RAG (Retrieval-Augmented Generation)** system that turns documents (especially test-case CSVs/Excel) into a queryable knowledge base. Users upload files, ask questions in natural language, and get answers grounded in those documents.

---

## 1. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              USER INTERFACES                                 │
├─────────────────────────────┬───────────────────────────────────────────────┤
│  Customer Portal (/customer)│  Admin Portal (/admin)                        │
│  - Ask questions            │  - Login (session)                             │
│  - RAG or Direct LLM mode   │  - Upload/delete documents                     │
│  - No auth required         │  - User management, ChromaDB browse, reset     │
└──────────────┬──────────────┴───────────────────┬───────────────────────────┘
               │                                   │
               ▼                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         FLASK APP (backend/app.py)                           │
│  - Serves frontend HTML + static files                                       │
│  - Registers blueprints: /api/auth, /api/admin, /api/customer                 │
│  - Holds RAG_SERVICE and AUTH_SERVICE in app.config                          │
└──────────────┬──────────────────────────────────┬────────────────────────────┘
               │                                  │
               ▼                                  ▼
┌──────────────────────────────┐    ┌──────────────────────────────────────────┐
│  Customer API                │    │  Admin API + Auth API                    │
│  POST /api/customer/query    │    │  - /api/auth/login, /users, etc.         │
│  GET  /api/customer/health   │    │  - /api/admin/upload, documents, stats   │
└──────────────┬───────────────┘    │  - require_auth(admin_only=True)         │
               │                     └──────────────────┬───────────────────────┘
               ▼                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  RAGService (backend/services/rag_service.py)                                │
│  - Single entry point for RAG: upload_document(), query(), list_documents()   │
│  - Manages storage dirs, documents_metadata.json, ChromaDB/embedding paths    │
│  - Wraps MultiFormatRAG and AuthService for users                             │
└──────────────┬───────────────────────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  MultiFormatRAG (backend/rag/rag_document_loader.py) extends BaseRAG          │
│  - Loads PDF, CSV, Excel, Word, PowerPoint, text                            │
│  - Validates testcase files (required columns), chunks, adds to vectorstore   │
│  - add_files() → load by type → split → embed → ChromaDB                     │
│  - query() → retrieve → (optional rerank) → prompt LLM → answer              │
└──────────────┬───────────────────────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  BaseRAG (backend/rag/rag_engine.py)                                          │
│  - Embeddings (HuggingFace or OpenAI), LLM (Ollama or OpenAI)               │
│  - Text splitter, ChromaDB vectorstore, retriever, optional hybrid/rerank   │
│  - Query cache, embedding cache, _query_impl() with similarity threshold     │
└──────────────┬───────────────────────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  ChromaDB (storage/chroma_db), storage/documents, storage/embedding_cache   │
│  config/.env (from config/env.example)                                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Directory Layout

| Path | Purpose |
|------|--------|
| **backend/** | Flask app and API layer |
| **backend/app.py** | Creates Flask app, loads `.env`, mounts RAG + Auth services, registers blueprints, serves frontend and `/health` |
| **backend/api/** | Blueprints: `auth` (login, users, sessions), `admin` (upload, documents, stats, ChromaDB, reset), `customer` (query, health) |
| **backend/services/rag_service.py** | Document lifecycle, query, list, delete, stats, ChromaDB reset; uses `MultiFormatRAG` and settings |
| **backend/services/auth_service.py** | Login/logout, session, user CRUD, default admin; uses `UserStorage` |
| **backend/models/user.py** | User model and file-based `UserStorage` (e.g. `storage/users.json`) |
| **backend/rag/** | RAG core (no Flask) |
| **backend/rag/rag_settings.py** | `RAGConfig` (Pydantic) loaded from `config/.env`; used by RAG layer |
| **backend/rag/rag_engine.py** | Embeddings, LLM, splitter, vectorstore, retriever, caching, `query()` / `_query_impl()` |
| **backend/rag/rag_document_loader.py** | Format-specific loaders, `add_files()`, testcase validation; subclasses `BaseRAG` |
| **backend/rag/rag_helper.py** | Similarity, dedup, hashing, prompts, query expansion, rerank, citation parsing, ChromaDB helpers |
| **backend/rag/rag_caching.py** | Query cache and embedding cache for faster repeated requests |
| **config/** | `env.example` template; copy to `config/.env` (created by install or run scripts) |
| **frontend/** | Static HTML/JS/CSS: `admin/` (login, dashboard), `customer/` (chat UI) |
| **scripts/** | `install.sh` (venv, deps, Ollama, storage, `.env`), `run.sh` (venv, optional Ollama check, start Flask) |
| **storage/** | Created at runtime: `documents/`, `chroma_db/`, `embedding_cache/`, `documents_metadata.json`, `users.json` |
| **tests/evaluation/** | RAG evaluation scripts and test set (e.g. RAGAS-style eval) |

---

## 3. Request Flows

### 3.1 Document upload (Admin)

1. User logs in via **POST /api/auth/login** → session cookie set.
2. **POST /api/admin/upload** (with session) → `require_auth(admin_only=True)` → `RAGService.upload_document(temp_path, filename)`.
3. **RAGService**:
   - Validates file as testcase (CSV/Excel with required headers).
   - If same filename exists: delete old doc (ChromaDB + file + metadata).
   - Saves file under `storage/documents/{uuid}_{filename}`.
   - Calls `self.rag.add_files([saved_path])`.
4. **MultiFormatRAG.add_files()**:
   - Detects type (e.g. CSV/Excel), loads with pandas/langchain, cleans and normalizes (e.g. priority, platform).
   - Builds `Document` objects with metadata, adds file_path.
   - Splits with `RecursiveCharacterTextSplitter` (chunk_size, chunk_overlap from config).
   - Embeds (with optional embedding cache) and adds to ChromaDB (create or append collection).
   - Sets `retriever` (and optional hybrid retriever).
5. **RAGService** writes `documents_metadata.json` with id, name, path, uploaded_at, status.
6. Response: success, document_id, message.

### 3.2 Query (Customer or API)

1. **POST /api/customer/query** with `{ "question": "...", "use_rag": true/false, "bypass_cache": false }`.
2. No auth required for this endpoint.
3. **RAGService.query(question, session_id, bypass_cache, use_rag)**:
   - If **use_rag=False**: build a simple prompt, call `self.rag.llm` only, return answer (no retrieval).
   - If **use_rag=True**: call `self.rag.query(question, bypass_cache=bypass_cache)`.
4. **BaseRAG.query()**:
   - If query cache enabled and not bypass: check cache (keyed by question and retrieval params); on hit return cached answer + metadata.
   - Else: ** _query_impl(question)**:
     - Optional dynamic params from question (e.g. “top 10”, “P0”, “iOS”) → `search_k`, `metadata_filter`.
     - Optional query expansion → multiple queries.
     - Retrieve: vectorstore similarity_search (with optional k and filter) or hybrid retriever; merge and dedupe.
     - Optional rerank with CrossEncoder.
     - Optional similarity threshold: keep only chunks above `min_similarity_threshold`.
     - Build context string from chunks, build prompt (system + context + question), call LLM.
     - Parse answer, strip citations, build `sources` / `source_documents`.
     - If cache enabled, store result; return answer, sources, query_time_ms, cache_hit.
5. Response: `{ success, answer, sources, source_documents, query_time_ms, cache_hit, mode }`.

### 3.3 Auth and admin-only actions

- **Auth**: Login stores `user_id`, `username`, `role` in Flask session (server-side). `AuthService` uses `UserStorage` (JSON file).
- **Admin routes** (upload, list/delete documents, stats, ChromaDB contents, reset) use `@require_auth(admin_only=True)`; they read session and require `role == 'admin'`.

---

## 4. Configuration (config/.env)

- **Flask**: `SECRET_KEY`, `HOST`, `PORT`, `FLASK_DEBUG`, `ADMIN_UPLOAD_MAX_SIZE_MB`.
- **Paths**: `STORAGE_DIR`, `DOCUMENTS_DIR`, `CHROMA_DB_DIR`, `EMBEDDING_CACHE_DIR`.
- **RAG**: `COLLECTION_NAME`, `CHUNK_SIZE`, `CHUNK_OVERLAP`, `RETRIEVAL_K`, `MIN_SIMILARITY_THRESHOLD`, `SHOW_MATCHING_SOURCES`.
- **LLM**: `LLM_PROVIDER` (ollama | openai | gemini), `OLLAMA_*`, `OPENAI_API_KEY`, `OPENAI_MODEL`, or `GOOGLE_API_KEY`/`GEMINI_API_KEY`, `GEMINI_MODEL`.
- **Embeddings**: Typically follow LLM provider (e.g. local HuggingFace when using Ollama; can use OpenAI embeddings when using OpenAI).
- **Features**: `USE_HYBRID_SEARCH`, `USE_RERANKING`, `USE_QUERY_EXPANSION`, `ENABLE_QUERY_CACHE`, `ENABLE_EMBEDDING_CACHE`, etc.

Settings are loaded in `backend/rag/rag_settings.py` (RAGConfig) and from env in `backend/app.py` for Flask. Install script copies `config/env.example` → `config/.env` if missing.

---

## 5. Data and State

- **Documents**: Stored under `storage/documents/` as `{uuid}_{original_filename}`. Metadata in `storage/documents_metadata.json` (id, name, path, uploaded_at, status).
- **Vectors**: ChromaDB under `storage/chroma_db/` (persistent). Each chunk has embedding and metadata (e.g. file_path, priority, platform for testcases).
- **Users**: `storage/users.json` (created by AuthService); default admin created if no admin exists.
- **Caches**: Query cache in memory (optional); embedding cache on disk under `storage/embedding_cache/`.

---

## 6. Frontend (High Level)

- **Customer** (`frontend/customer/index.html`): Single-page chat; POST to `/api/customer/query` with question and `use_rag`; displays answer and optional sources.
- **Admin** (`frontend/admin/login.html`, `index.html`): Login form → session; dashboard with upload, document list, user management, ChromaDB browser, reset. All write operations go through the admin API with session cookie.

---

## 7. Scripts and Run

- **install.sh** (or .bat / .ps1): Python 3.9+ check, venv, `pip install -r requirements.txt`, init storage, optional Ollama install/check, pull default model, copy `config/env.example` → `config/.env`.
- **run.sh**: Activate venv, ensure `config/.env` exists, optionally check Ollama if `LLM_PROVIDER=ollama`, check port, run `python backend/app.py` (Flask dev server).
- **gunicorn_config.py**: For production (e.g. Gunicorn); app entrypoint is the same Flask `create_app()`.

---

## 8. TestRail: fetch, store, and retrieval

- **Fetch:** `TestRailConnector.fetch_and_transform()` in `backend/connectors/testrail_connector.py` calls TestRail `GET /api/v2/get_cases/{project_id}`; after aggregating, sorts cases by ID **descending** (newest first).
- **Store:** `SyncService.sync_from_testrail()` groups by suite, writes one CSV per suite (`testrail_{suite_slug}.csv`), uploads via `rag_service.upload_document()`. MultiFormatRAG loads CSV and adds documents to ChromaDB in **newest-first** order.
- **Retrieval:** Requirement analysis uses `RAGService.find_related_tests(requirement_text, k=10)` → `rag.retrieve_documents_with_scores(..., metadata_filter={"source_type": "testcase"})`. Results are ordered by **similarity**; `find_related_tests()` and chat `_query_impl()` sort by `testrail_id` descending for display so newest-first is shown to the user.

---

## 9. Summary Table

| Concern | Where it lives |
|--------|-----------------|
| HTTP and routing | `backend/app.py`, `backend/api/*/routes.py` |
| Document upload and lifecycle | `RAGService.upload_document`, `MultiFormatRAG.add_files` |
| Document validation (testcase) | `RAGService._validate_testcase_file`, column checks in MultiFormatRAG |
| Chunking and embedding | `BaseRAG` (splitter, embeddings), ChromaDB in `rag_helper` |
| Retrieval and optional rerank/expansion | `BaseRAG._query_impl`, `rag_helper` |
| LLM call and answer formatting | `BaseRAG._query_impl`, `rag_helper` (prompt, extract answer) |
| Caching | `BaseRAG` + `backend/rag/rag_caching.py` (query + embedding cache) |
| Configuration | `config/.env`, `backend/rag/rag_settings.py` (RAGConfig) |
| Auth and users | `backend/services/auth_service.py`, `backend/models/user.py`, auth routes |

End-to-end: **Upload** → files and metadata on disk, chunks in ChromaDB. **Query** → retrieve chunks → optional rerank/filter → LLM with context → answer and sources. **Auth** → session + role for admin-only operations.
