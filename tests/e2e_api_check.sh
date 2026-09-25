#!/usr/bin/env bash
# E2E API check: verifies customer and admin APIs when the server is running on PORT.
#
# Usage:
#   E2E_USERNAME=admin E2E_PASSWORD='<password>' ./tests/e2e_api_check.sh [PORT]
#
# Needs an ACTIVE admin account (the admin password is printed once on first
# start), a running server (./scripts/run.sh) and curl. The query and
# requirement-analysis checks make real LLM calls.

set -e
PORT="${1:-5001}"
BASE="http://localhost:${PORT}"
COOKIES="$(mktemp -t rag_e2e_cookies.XXXXXX)"

cleanup() { rm -f "$COOKIES"; }
trap cleanup EXIT

if [ -z "${E2E_USERNAME:-}" ] || [ -z "${E2E_PASSWORD:-}" ]; then
  echo "Set E2E_USERNAME and E2E_PASSWORD to an active admin account." >&2
  exit 2
fi

expect_code() {   # expect_code <label> <expected> <curl args...>
  local label="$1" want="$2"; shift 2
  echo -n "$label ... "
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" "$@")
  [ "$code" = "$want" ] && echo "OK ($code)" || { echo "FAIL ($code, expected $want)"; exit 1; }
}

expect_success() {   # expect_success <label> <curl args...>
  local label="$1"; shift
  echo -n "$label ... "
  local resp
  resp=$(curl -s "$@")
  if echo "$resp" | grep -q '"success": *true'; then echo "OK"; else echo "FAIL"; echo "$resp" | head -c 200; echo; exit 1; fi
}

echo "=== E2E API check (port $PORT) ==="

expect_code "GET /" 200 "$BASE/"
expect_code "GET /health" 200 "$BASE/health"
expect_code "GET /admin (page)" 200 "$BASE/admin"
expect_code "POST /api/customer/query without a session is refused" 401 \
  -X POST "$BASE/api/customer/query" -H "Content-Type: application/json" -d '{"question":"x"}'

LOGIN_BODY=$(printf '{"username":"%s","password":"%s"}' "$E2E_USERNAME" "$E2E_PASSWORD")
expect_code "POST /api/auth/login" 200 \
  -X POST "$BASE/api/auth/login" -H "Content-Type: application/json" -d "$LOGIN_BODY" -c "$COOKIES"

# Customer (session required)
expect_success "POST /api/customer/query (direct LLM)" \
  -b "$COOKIES" -X POST "$BASE/api/customer/query" -H "Content-Type: application/json" \
  -d '{"question":"2+2?","use_rag":false}'
expect_success "POST /api/customer/requirement-analysis (paste)" \
  -b "$COOKIES" -X POST "$BASE/api/customer/requirement-analysis" -H "Content-Type: application/json" \
  -d '{"requirement_spec":"REQ-001: User must reset password.","generate_new_tests":false}'

# Admin
expect_code "GET /api/admin/sync/status" 200 -b "$COOKIES" "$BASE/api/admin/sync/status"
expect_code "GET /api/admin/documents" 200 -b "$COOKIES" "$BASE/api/admin/documents"
expect_code "GET /api/admin/chromadb" 200 -b "$COOKIES" "$BASE/api/admin/chromadb?limit=5"
expect_code "GET /api/admin/analytics" 200 -b "$COOKIES" "$BASE/api/admin/analytics"

echo "=== All E2E API checks passed ==="
