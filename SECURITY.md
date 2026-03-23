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
- Cross-site scripting (XSS) and CSRF

## Best Practices for Deployment

- Never commit `config/.env` with real API keys
- Set `CORS_ALLOWED_ORIGINS` to your frontend domain(s) in production
- Set a strong `SECRET_KEY` environment variable
- Change the default admin password immediately after first login
- Run behind a reverse proxy (nginx) with HTTPS in production
