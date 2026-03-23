# Changelog

All notable changes to AI Test Studio are documented here.

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
