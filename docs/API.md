# API Documentation

Complete REST API reference for AI Test Studio.

## Base URL

```
http://localhost:5001/api
```

Default port is `5001` (configurable via `PORT` in `config/.env`).

---

## Authentication

### Session-Based Authentication

The system uses session-based authentication with username and password. All authenticated requests require session cookies.

**Default Admin Credentials:**
- **Username**: `admin`
- **Password**: `admin123`

⚠️ **IMPORTANT**: Change the default admin password immediately after first login!

### Admin Endpoints

All admin endpoints require an authenticated admin session. Login via `/api/auth/login` first to establish a session.

**Login Example:**
```bash
curl -X POST http://localhost:5001/api/auth/login \
  -H "Content-Type: application/json" \
  -c cookies.txt \
  -d '{"username": "admin", "password": "admin123"}'
```

**Using Session:**
```bash
# Include cookies in subsequent requests
curl -X GET http://localhost:5001/api/admin/documents \
  -b cookies.txt
```

### Customer Endpoints

Customer endpoints are publicly accessible (no authentication required).

---

## Authentication & User Management Endpoints

### Login

Authenticate and create a session.

**Endpoint:** `POST /api/auth/login`

**Body:**
```json
{
  "username": "admin",
  "password": "admin123"
}
```

**Response:**
```json
{
  "success": true,
  "user": {
    "user_id": "uuid-here",
    "username": "admin",
    "role": "admin"
  }
}
```

**Example:**
```bash
curl -X POST http://localhost:5001/api/auth/login \
  -H "Content-Type: application/json" \
  -c cookies.txt \
  -d '{"username": "admin", "password": "admin123"}'
```

---

### Logout

End the current session.

**Endpoint:** `POST /api/auth/logout`

**Response:**
```json
{
  "success": true,
  "message": "Logged out successfully"
}
```

**Example:**
```bash
curl -X POST http://localhost:5001/api/auth/logout \
  -b cookies.txt
```

---

### Get Current User

Get information about the currently authenticated user.

**Endpoint:** `GET /api/auth/me`

**Response:**
```json
{
  "success": true,
  "user": {
    "user_id": "uuid-here",
    "username": "admin",
    "role": "admin",
    "created_at": "2024-01-01T12:00:00",
    "last_login": "2024-01-01T12:00:00"
  }
}
```

**Example:**
```bash
curl -X GET http://localhost:5001/api/auth/me \
  -b cookies.txt
```

---

### List Users (Admin Only)

Get list of all users in the system.

**Endpoint:** `GET /api/auth/users`

**Response:**
```json
{
  "success": true,
  "users": [
    {
      "user_id": "uuid-here",
      "username": "admin",
      "role": "admin",
      "created_at": "2024-01-01T12:00:00",
      "last_login": "2024-01-01T12:00:00"
    }
  ],
  "count": 1
}
```

**Example:**
```bash
curl -X GET http://localhost:5001/api/auth/users \
  -b cookies.txt
```

---

### Create User (Admin Only)

Create a new user account.

**Endpoint:** `POST /api/auth/users`

**Body:**
```json
{
  "username": "newuser",
  "password": "securepassword",
  "role": "customer"
}
```

**Response:**
```json
{
  "success": true,
  "user": {
    "user_id": "uuid-here",
    "username": "newuser",
    "role": "customer",
    "created_at": "2024-01-01T12:00:00"
  }
}
```

**Example:**
```bash
curl -X POST http://localhost:5001/api/auth/users \
  -H "Content-Type: application/json" \
  -b cookies.txt \
  -d '{"username": "newuser", "password": "securepassword", "role": "customer"}'
```

---

### Update User Role (Admin Only)

Change a user's role (admin/customer).

**Endpoint:** `PUT /api/auth/users/<user_id>`

**Body:**
```json
{
  "role": "admin"
}
```

**Response:**
```json
{
  "success": true,
  "user": {
    "user_id": "uuid-here",
    "username": "user",
    "role": "admin"
  }
}
```

**Example:**
```bash
curl -X PUT http://localhost:5001/api/auth/users/uuid-here \
  -H "Content-Type: application/json" \
  -b cookies.txt \
  -d '{"role": "admin"}'
```

---

### Delete User (Admin Only)

Delete a user account.

**Endpoint:** `DELETE /api/auth/users/<user_id>`

**Response:**
```json
{
  "success": true,
  "message": "User deleted successfully"
}
```

**Example:**
```bash
curl -X DELETE http://localhost:5001/api/auth/users/uuid-here \
  -b cookies.txt
```

---

### Reset User Password (Admin Only)

Reset a user's password (admin can reset any user's password).

**Endpoint:** `POST /api/auth/users/<user_id>/reset-password`

**Body:**
```json
{
  "new_password": "newsecurepassword"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Password reset successfully"
}
```

**Example:**
```bash
curl -X POST http://localhost:5001/api/auth/users/uuid-here/reset-password \
  -H "Content-Type: application/json" \
  -b cookies.txt \
  -d '{"new_password": "newsecurepassword"}'
```

---

### Change Password

Change your own password.

**Endpoint:** `POST /api/auth/change-password`

**Body:**
```json
{
  "old_password": "oldpassword",
  "new_password": "newpassword"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Password changed successfully"
}
```

**Example:**
```bash
curl -X POST http://localhost:5001/api/auth/change-password \
  -H "Content-Type: application/json" \
  -b cookies.txt \
  -d '{"old_password": "oldpassword", "new_password": "newpassword"}'
```

---

## Admin Endpoints

### Upload Document

Upload and process a document.

**Endpoint:** `POST /api/admin/upload`

**Authentication:** Requires admin session (login first)

**Body:** `multipart/form-data`
- `file`: Document file (PDF, CSV, Excel, Text)

**Response:**
```json
{
  "success": true,
  "document_id": "uuid-here",
  "message": "Document uploaded and processed successfully",
  "replaced": false
}
```

**Example:**
```bash
curl -X POST http://localhost:5001/api/admin/upload \
  -b cookies.txt \
  -F "file=@document.pdf"
```

---

### List Documents

Get list of all uploaded documents.

**Endpoint:** `GET /api/admin/documents`

**Authentication:** Requires admin session (login first)

**Response:**
```json
{
  "success": true,
  "documents": [
    {
      "id": "uuid-here",
      "name": "document.pdf",
      "path": "/path/to/document",
      "uploaded_at": "2024-01-01T12:00:00",
      "status": "processed"
    }
  ],
  "count": 1
}
```

**Example:**
```bash
curl -X GET http://localhost:5001/api/admin/documents \
  -b cookies.txt
```

---

### Delete Document

Delete a document by ID.

**Endpoint:** `DELETE /api/admin/documents/<document_id>`

**Authentication:** Requires admin session (login first)

**Response:**
```json
{
  "success": true,
  "message": "Document deleted successfully"
}
```

**Example:**
```bash
curl -X DELETE http://localhost:5001/api/admin/documents/uuid-here \
  -b cookies.txt
```

---

### Download Document

Download a document file.

**Endpoint:** `GET /api/admin/documents/<document_id>/download`

**Authentication:** Requires admin session (login first)

**Response:** File download

**Example:**
```bash
curl -X GET http://localhost:5001/api/admin/documents/uuid-here/download \
  -b cookies.txt \
  -o downloaded-file.pdf
```

---

### Get System Statistics

Get system statistics (document count, chunk count, etc.).

**Endpoint:** `GET /api/admin/stats`

**Authentication:** Requires admin session (login first)

**Response:**
```json
{
  "success": true,
  "total_documents": 5,
  "total_chunks": 42,
  "rag_config": {
    "use_hybrid_search": false,
    "use_reranking": false,
    "enable_query_cache": true,
    "enable_embedding_cache": true
  }
}
```

**Example:**
```bash
curl -X GET http://localhost:5001/api/admin/stats \
  -b cookies.txt
```

---

### Get ChromaDB Contents

View all chunks stored in ChromaDB.

**Endpoint:** `GET /api/admin/chromadb`

**Authentication:** Requires admin session (login first)

**Response:**
```json
{
  "success": true,
  "collection_name": "rag_collection",
  "total_chunks": 42,
  "has_embeddings": true,
  "embedding_dimension": 384,
  "chunks": [
    {
      "id": "chunk-id",
      "index": 1,
      "content": "Chunk content preview...",
      "content_full": "Full chunk content...",
      "content_length": 500,
      "metadata": {...},
      "file_path": "/path/to/document.pdf",
      "source": "document.pdf",
      "document_id": "uuid-here"
    }
  ]
}
```

**Example:**
```bash
curl -X GET http://localhost:5001/api/admin/chromadb \
  -b cookies.txt
```

---

### Reset ChromaDB

Reset the entire database (deletes all documents, chunks, and cache).

**⚠️ Warning:** This action cannot be undone!

**Endpoint:** `POST /api/admin/chromadb/reset`

**Authentication:** Requires admin session (login first)
- `Content-Type`: application/json

**Body:**
```json
{
  "delete_all": true
}
```

**Response:**
```json
{
  "success": true,
  "message": "Database reset successfully. Deleted 5 document file(s), 10 ChromaDB file(s), 15 embedding cache file(s) and all vector data.",
  "deleted_files": 30,
  "details": {
    "documents": 5,
    "chroma_db_files": 10,
    "embedding_cache_files": 15
  }
}
```

**Example:**
```bash
curl -X POST http://localhost:5001/api/admin/chromadb/reset \
  -H "Content-Type: application/json" \
  -b cookies.txt \
  -d '{"delete_all": true}'
```

---

### Trigger TestRail Sync

Start a background sync that pulls all TestRail test cases into the vector store.

**Endpoint:** `POST /api/admin/sync/testrail`

**Authentication:** Requires admin session

**Response (202 Accepted):**
```json
{
  "success": true,
  "message": "TestRail sync started in background",
  "status": "started"
}
```

**Returns 409** if a sync is already in progress.

**Example:**
```bash
curl -X POST http://localhost:5001/api/admin/sync/testrail \
  -b cookies.txt
```

---

### Trigger Confluence Sync

Start a background sync that pulls Confluence pages (via CQL) into the vector store.

**Endpoint:** `POST /api/admin/sync/confluence`

**Authentication:** Requires admin session

**Response (202 Accepted):**
```json
{
  "success": true,
  "message": "Confluence sync started in background (CQL)",
  "status": "started"
}
```

**Example:**
```bash
curl -X POST http://localhost:5001/api/admin/sync/confluence \
  -b cookies.txt
```

---

### Get Sync Status

Get the current sync status for both TestRail and Confluence.

**Endpoint:** `GET /api/admin/sync/status`

**Authentication:** Requires admin session

**Response:**
```json
{
  "success": true,
  "status": {
    "is_syncing": false,
    "last_sync": "2024-01-01T12:00:00",
    "last_sync_count": 42,
    "confluence": {
      "is_syncing": false,
      "last_sync": "2024-01-01T11:00:00",
      "last_sync_count": 10
    }
  }
}
```

**Example:**
```bash
curl -X GET http://localhost:5001/api/admin/sync/status \
  -b cookies.txt
```

---

### Get Sync Schedule

Get next scheduled run times for automatic TestRail and Confluence syncs.

**Endpoint:** `GET /api/admin/sync/schedule`

**Authentication:** Requires admin session

**Example:**
```bash
curl -X GET http://localhost:5001/api/admin/sync/schedule \
  -b cookies.txt
```

---

### Confluence Diagnose

Run a connectivity diagnostic to troubleshoot Confluence configuration (credentials, API path, CQL query).

**Endpoint:** `GET /api/admin/confluence-diagnose`

**Authentication:** Requires admin session

**Example:**
```bash
curl -X GET http://localhost:5001/api/admin/confluence-diagnose \
  -b cookies.txt
```

---

### Get Settings (Public)

Return non-sensitive public settings (e.g. default theme). No authentication required.

**Endpoint:** `GET /api/admin/settings/public`

**Response:**
```json
{
  "success": true,
  "default_theme": "dark"
}
```

**Example:**
```bash
curl http://localhost:5001/api/admin/settings/public
```

---

### Get Settings

Return full settings schema with current values. Sensitive values are masked as `****`.

**Endpoint:** `GET /api/admin/settings`

**Authentication:** Requires admin session

**Example:**
```bash
curl -X GET http://localhost:5001/api/admin/settings \
  -b cookies.txt
```

---

### Update Settings

Save one or more settings. Sensitive fields submitted as `****` are not overwritten.

**Endpoint:** `PUT /api/admin/settings`

**Authentication:** Requires admin session

**Body:** Key-value map of settings to update.
```json
{
  "default_theme": "light",
  "CHAT_RETRIEVAL_K": 10
}
```

**Response:**
```json
{
  "success": true,
  "message": "Settings saved and applied successfully"
}
```

**Example:**
```bash
curl -X PUT http://localhost:5001/api/admin/settings \
  -H "Content-Type: application/json" \
  -b cookies.txt \
  -d '{"default_theme": "light"}'
```

---

## Customer Endpoints

### Customer Config

Get public customer configuration (e.g. TestRail push enabled, available features).

**Endpoint:** `GET /api/customer/config`

**Response:**
```json
{
  "success": true,
  "testrail_push_enabled": true
}
```

**Example:**
```bash
curl http://localhost:5001/api/customer/config
```

---

### Query the System

Ask a question and get an AI-powered answer based on uploaded documents.

**Endpoint:** `POST /api/customer/query`

**Body:**
```json
{
  "question": "What is the main topic?",
  "session_id": "optional-session-id",
  "bypass_cache": false
}
```

**Parameters:**
- `question` (required): Your question in natural language
- `session_id` (optional): Session ID for conversation context
- `bypass_cache` (optional): If `true`, skip cache and force fresh LLM query

**Response:**
```json
{
  "success": true,
  "answer": "The main topic is...",
  "sources": [
    {
      "content": "Source content...",
      "similarity_percent": 85.5
    }
  ],
  "source_documents": [
    {
      "content": "Document chunk content...",
      "metadata": {...}
    }
  ],
  "query_time_ms": 1234.56,
  "cache_hit": false
}
```

**Example:**
```bash
curl -X POST http://localhost:5001/api/customer/query \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What is the main topic?",
    "bypass_cache": false
  }'
```

---

### Requirement Analysis

Analyze a requirement spec: find related test cases, identify uncovered requirements, generate new tests.

**Endpoint:** `POST /api/customer/requirement-analysis`

**Input (provide exactly one):**
- `requirement_spec`: Pasted text (JSON body)
- `confluence_url`: Confluence page URL (JSON body)
- `file`: Uploaded file (PDF, DOCX, TXT) via multipart/form-data

**Options (JSON body or form):**
- `generate_new_tests`: bool (default: true) – generate tests for uncovered requirements
- `push_to_testrail`: bool (default: false) – push generated tests to TestRail (requires TESTRAIL_PUSH_ENABLED and credentials)
- `use_section_of_related`: bool (default: false) – when true, push each requirement’s generated tests into the **same section as its first related test** (fallback: `target_section_id`)
- `target_section_id`: int (optional) – TestRail section ID for push when not using “same as related”; chosen in UI (Project → Suite → Section) or passed in request

**Request (JSON with pasted text):**
```json
{
  "requirement_spec": "REQ-001: User must reset password via email.\nREQ-002: System shall send verification email within 60 seconds.",
  "generate_new_tests": true
}
```

**Request (JSON with Confluence URL):**
```json
{
  "confluence_url": "https://company.atlassian.net/wiki/spaces/DEV/pages/123456/Requirements",
  "generate_new_tests": true
}
```

**Request (multipart with file):**
```bash
curl -X POST http://localhost:5001/api/customer/requirement-analysis \
  -F "file=@requirements.pdf" \
  -F "generate_new_tests=true"
```

**Response:**
```json
{
  "success": true,
  "requirements_analyzed": 2,
  "requirements": [
    {"id": "REQ-001", "title": "...", "description": "..."},
    {"id": "REQ-002", "title": "...", "description": "..."}
  ],
  "related_tests": {
    "REQ-001": [
      {"testrail_id": "C123", "title": "...", "content": "...", "similarity_score": 0.85}
    ],
    "REQ-002": []
  },
  "tests_needing_update": {
    "REQ-001": [
      {"testrail_id": "C123", "title": "...", "status": "needs_update", "suggested_changes": ["..."], "reason": "...", "confidence": 0.85}
    ]
  },
  "uncovered_requirements": ["REQ-002"],
  "generated_tests": {
    "REQ-002": [
      {"title": "...", "priority": "P1", "steps": "...", "expected_result": "...", "generated": true}
    ]
  },
  "pushed_to_testrail": [
    {"requirement_id": "REQ-002", "testrail_id": "C456", "success": true}
  ],
  "summary": {
    "total_requirements": 2,
    "requirements_with_coverage": 1,
    "needing_update_count": 1,
    "uncovered_count": 1,
    "generated_count": 1,
    "pushed_count": 1
  }
}
```

**Note:** Confluence URL requires CONFLUENCE_URL, CONFLUENCE_EMAIL, CONFLUENCE_API_TOKEN in .env.

---

### Requirement Analysis (Streaming)

Same as `requirement-analysis` but streams progress events as Server-Sent Events (SSE). Used by the UI to show a live progress bar.

**Endpoint:** `POST /api/customer/requirement-analysis/stream`

**Request:** Identical to `requirement-analysis`.

**Response:** `text/event-stream` — emits JSON events with fields `stage`, `message`, `progress` (0–100), and per-requirement results as they complete.

**Example:**
```bash
curl -X POST http://localhost:5001/api/customer/requirement-analysis/stream \
  -H "Content-Type: application/json" \
  -d '{"requirement_spec": "REQ-001: ...", "generate_new_tests": true}'
```

---

### Requirement Analysis — Push to TestRail

Push already-generated tests (from a prior analysis) to TestRail. Used when the user clicks "Push to TestRail" after reviewing results.

**Endpoint:** `POST /api/customer/requirement-analysis/push`

**Body:**
```json
{
  "generated_tests": {
    "REQ-001": [
      {"title": "...", "priority": "P1", "steps": "...", "expected_result": "..."}
    ]
  },
  "target_section_id": 123,
  "use_section_of_related": false
}
```

**Example:**
```bash
curl -X POST http://localhost:5001/api/customer/requirement-analysis/push \
  -H "Content-Type: application/json" \
  -d '{"generated_tests": {...}, "target_section_id": 123}'
```

---

### Requirement Analysis — Suggest Case Update

Given a requirement and an existing TestRail case, get an AI suggestion for how to update the case to match the requirement.

**Endpoint:** `POST /api/customer/requirement-analysis/suggest-case-update`

---

### Requirement Analysis — Update Case

Apply an AI-suggested update to an existing TestRail case.

**Endpoint:** `POST /api/customer/requirement-analysis/update-case`

---

### Requirement Analysis — Create Case

Create a new TestRail case from a generated test object.

**Endpoint:** `POST /api/customer/requirement-analysis/create-case`

---

### List Unautomated TestRail Cases

Fetch TestRail cases that are not yet automated (automation_type ≠ automated). Used by the automation improvement workflow.

**Endpoint:** `GET /api/customer/testrail/unautomated-cases`

**Query params:** `project_id` (required), `suite_id` (optional), `section_id` (optional)

**Example:**
```bash
curl "http://localhost:5001/api/customer/testrail/unautomated-cases?project_id=1"
```

---

### Improve Case for Automation

Given a manual TestRail test case, return an AI-rewritten version that is more suitable for automation (explicit steps, deterministic assertions, etc.).

**Endpoint:** `POST /api/customer/testrail/improve-for-automation`

**Body:**
```json
{
  "case_id": "C123",
  "title": "Login test",
  "steps": "1. Go to login page\n2. Enter credentials",
  "expected_result": "User is logged in"
}
```

**Example:**
```bash
curl -X POST http://localhost:5001/api/customer/testrail/improve-for-automation \
  -H "Content-Type: application/json" \
  -d '{"case_id": "C123", "title": "Login test", "steps": "...", "expected_result": "..."}'
```

---

### TestRail structure (for Requirement Analysis push)

Used by the UI to show **Project → Suite → Section** and to create new suites/sections. Requires TestRail credentials in .env.

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/customer/testrail/projects` | List all projects |
| GET | `/api/customer/testrail/projects/<project_id>/suites` | List suites for a project |
| GET | `/api/customer/testrail/projects/<project_id>/sections?suite_id=<id>` | List sections (suite_id optional for single-suite projects) |
| POST | `/api/customer/testrail/projects/<project_id>/suites` | Create suite. Body: `{ "name": "...", "description": "..." }` |
| POST | `/api/customer/testrail/projects/<project_id>/sections` | Create section. Body: `{ "suite_id": <id>, "name": "...", "parent_id": <id>? }` |

---

### Health Check

Check if the API is running.

**Endpoint:** `GET /api/customer/health`

**Response:**
```json
{
  "status": "healthy",
  "service": "rag-system"
}
```

**Example:**
```bash
curl http://localhost:5001/api/customer/health
```

---

## Error Responses

All endpoints return errors in the following format:

```json
{
  "success": false,
  "error": "Error message here"
}
```

**HTTP Status Codes:**
- `200` - Success
- `400` - Bad Request (missing/invalid parameters)
- `401` - Unauthorized (invalid admin key)
- `404` - Not Found (document/resource not found)
- `500` - Internal Server Error

---

## Rate Limiting

Currently, there are no rate limits. However, for production deployments, consider implementing rate limiting based on your needs.

---

## Best Practices

1. **Store Admin Key Securely**: Never commit `config/.env` to version control
2. **Use HTTPS in Production**: Always use HTTPS for production deployments
3. **Validate Input**: Always validate file types and sizes before uploading
4. **Monitor Performance**: Use query time metrics to optimize performance
5. **Cache Strategically**: Use `bypass_cache` only when you need fresh results

---

## Examples

### Complete Workflow

```bash
# 1. Login to get session
curl -X POST http://localhost:5001/api/auth/login \
  -H "Content-Type: application/json" \
  -c cookies.txt \
  -d '{"username": "admin", "password": "admin123"}'

# 2. Upload a document
curl -X POST http://localhost:5001/api/admin/upload \
  -b cookies.txt \
  -F "file=@document.pdf"

# 3. List documents
curl -X GET http://localhost:5001/api/admin/documents \
  -b cookies.txt

# 4. Query the system (public endpoint, no auth needed)
curl -X POST http://localhost:5001/api/customer/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is this document about?"}'

# 5. Get statistics
curl -X GET http://localhost:5001/api/admin/stats \
  -b cookies.txt
```

### Python Example

```python
import requests

BASE_URL = "http://localhost:5001/api"
session = requests.Session()

# Login to get session
login_response = session.post(
    f"{BASE_URL}/auth/login",
    json={"username": "admin", "password": "admin123"}
)
print("Login:", login_response.json())

# Upload document
with open("document.pdf", "rb") as f:
    response = session.post(
        f"{BASE_URL}/admin/upload",
        files={"file": f}
    )
    print("Upload:", response.json())

# Query system (public endpoint, no auth needed)
response = requests.post(
    f"{BASE_URL}/customer/query",
    json={"question": "What is the main topic?"}
)
result = response.json()
print("Answer:", result["answer"])
```

### JavaScript Example

```javascript
const API_BASE = 'http://localhost:5001/api';

// Login to get session (cookies handled automatically by browser)
async function login() {
  const response = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include', // Important: include cookies
    body: JSON.stringify({ username: 'admin', password: 'admin123' })
  });
  return response.json();
}

// Upload document (after login)
async function uploadDocument(file) {
  const formData = new FormData();
  formData.append('file', file);

  const response = await fetch(`${API_BASE}/admin/upload`, {
    method: 'POST',
    credentials: 'include', // Include session cookies
    body: formData
})
.then(res => res.json())
.then(data => console.log(data));

// Query system
fetch(`${API_BASE}/customer/query`, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    question: 'What is the main topic?'
  })
})
.then(res => res.json())
.then(data => console.log(data.answer));
```

---

For more details, see [DEPLOYMENT.md](DEPLOYMENT.md) or the main [README.md](../README.md).

