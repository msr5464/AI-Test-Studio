# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| latest  | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do NOT** open a public GitHub issue for security vulnerabilities.
2. Email the maintainer or use [GitHub Security Advisories](https://github.com/msr5464/AI-Test-Studio/security/advisories/new) to report privately.
3. Include a description of the vulnerability, steps to reproduce, and potential impact.

## Expected Response

- Acknowledgment within **48 hours**
- Status update within **7 days**
- Fix or mitigation within **30 days** for critical issues

## Scope

The following are in scope:
- Authentication and session management
- API input validation and injection vulnerabilities
- File upload and path traversal
- Secret/credential exposure
- Cross-site scripting (XSS) and CSRF (there is no CSRF token; protection rests on
  `SameSite=Lax` session cookies and the CORS allowlist, so both matter)
- The proxy to QA Agent Network (identity headers, shared secret)

## Best Practices for Deployment

- **`SECRET_KEY`** — set a strong, unique value. It signs the session cookie, which
  is the only thing separating a visitor from an admin; the app refuses to start
  with a known placeholder unless `FLASK_DEBUG=true`, which must never be used in
  production.
- **HTTPS** — run behind a reverse proxy (e.g. Nginx) with TLS, and set
  `SESSION_COOKIE_SECURE=true` so the cookie is never sent in clear.
- **CORS** — set `CORS_ALLOWED_ORIGINS` to your frontend origin(s). Never `*`:
  credentials are enabled.
- **Admin account** — the default `admin` password is random and printed once at
  first start; store it safely or change it in Admin → Users. Approve sign-ups
  deliberately — an active account can spend LLM budget and push to TestRail.
- **QA Agent Network** — its server trusts the identity headers this app's proxy
  injects. Keep it bound to `127.0.0.1`, or set the same `QA_AGENT_PROXY_SECRET`
  in both repos' `config/.env` before exposing it on a network.
- **Secrets** — never commit `config/.env`. Admin → Agent Settings can write
  `GITHUB_TOKEN` into QA Agent Network's config, so treat admin access accordingly.
