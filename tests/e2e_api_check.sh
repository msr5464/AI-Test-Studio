#!/usr/bin/env bash
# E2E API check: verifies customer and admin APIs when server is running on PORT.
# Usage: ./tests/e2e_api_check.sh [PORT]
# Requires: server running (e.g. ./scripts/run.sh), curl

set -e
PORT="${1:-5001}"
BASE="http://localhost:${PORT}"
COOKIES="/tmp/rag_e2e_cookies_$$"

cleanup() { rm -f "$COOKIES"; }
trap cleanup EXIT

echo "=== E2E API check (port $PORT) ==="

# Customer
echo -n "GET / ... "
code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/")
[ "$code" = "200" ] && echo "OK ($code)" || { echo "FAIL ($code)"; exit 1; }

echo -n "GET /health ... "
code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/health")
[ "$code" = "200" ] && echo "OK ($code)" || { echo "FAIL ($code)"; exit 1; }

echo -n "POST /api/customer/query (direct LLM) ... "
resp=$(curl -s -X POST "$BASE/api/customer/query" -H "Content-Type: application/json" -d '{"question":"2+2?","use_rag":false}')
if echo "$resp" | grep -q '"success":true'; then echo "OK"; else echo "FAIL"; echo "$resp" | head -c 200; exit 1; fi

echo -n "POST /api/customer/requirement-analysis (paste) ... "
resp=$(curl -s -X POST "$BASE/api/customer/requirement-analysis" -H "Content-Type: application/json" -d '{"requirement_spec":"REQ-001: User must reset password.","generate_new_tests":false}')
if echo "$resp" | grep -q '"success":true'; then echo "OK"; else echo "FAIL"; echo "$resp" | head -c 200; exit 1; fi

# Admin (login then protected routes)
echo -n "POST /api/auth/login ... "
code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/api/auth/login" -H "Content-Type: application/json" -d '{"username":"admin","password":"admin123"}' -c "$COOKIES")
[ "$code" = "200" ] && echo "OK ($code)" || { echo "FAIL ($code)"; exit 1; }

echo -n "GET /api/admin/sync/status ... "
code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/admin/sync/status" -b "$COOKIES")
[ "$code" = "200" ] && echo "OK ($code)" || { echo "FAIL ($code)"; exit 1; }

echo -n "GET /api/admin/documents ... "
code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/admin/documents" -b "$COOKIES")
[ "$code" = "200" ] && echo "OK ($code)" || { echo "FAIL ($code)"; exit 1; }

echo -n "GET /api/admin/chromadb ... "
code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/admin/chromadb?limit=5" -b "$COOKIES")
[ "$code" = "200" ] && echo "OK ($code)" || { echo "FAIL ($code)"; exit 1; }

echo -n "GET /admin (page) ... "
code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/admin")
[ "$code" = "200" ] && echo "OK ($code)" || { echo "FAIL ($code)"; exit 1; }

echo "=== All E2E API checks passed ==="
