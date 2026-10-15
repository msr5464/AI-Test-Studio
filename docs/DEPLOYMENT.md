# Deployment Guide

Installing, configuring and running AI Test Studio, from a laptop to a
production server. For the five-minute version, see the
[README Quick Start](../README.md#quick-start).

## Table of Contents

- [Installation](#installation)
- [Configuration](#configuration)
- [Running](#running)
- [Production](#production)
- [Connecting QA Agent Network](#connecting-qa-agent-network)
- [Backup](#backup)
- [Troubleshooting](#troubleshooting)

---

## Installation

### Requirements

- Python 3.9+ ("Add to PATH" on Windows)
- An LLM: [Ollama](https://ollama.ai) locally (default provider), or an OpenAI or
  Google Gemini API key
- Optional: `antiword` for `.doc` requirement files; LibreOffice for legacy
  `.doc` / `.ppt` documents (`.docx` / `.pptx` need neither)

### Install script

| OS | Command |
|----|---------|
| macOS / Linux | `bash scripts/install.sh` |
| Windows PowerShell | `.\scripts\install.ps1` (first: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`) |
| Windows Command Prompt | `scripts\install.bat` |

Every script creates `venv/`, installs `requirements.txt`, creates `config/.env`
from `config/env.example` if missing, and initialises `storage/`. Ollama handling
differs:

- `install.sh` installs Ollama if missing (Homebrew on macOS, the official script
  on Linux) and `antiword`, then prints how to start Ollama and pull a model.
- `install.ps1` downloads and runs the Ollama installer if missing, then prints
  the same instructions.
- `install.bat` also starts Ollama and pulls `llama3.2:3b`.

To start Ollama yourself: `ollama serve`, then `ollama pull llama3.2:3b`.

### Manual install

```bash
python3 -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp config/env.example config/.env   # Windows: copy config\env.example config\.env
bash scripts/init_storage.sh        # Windows: scripts\init_storage.bat / .ps1
```

---

## Configuration

All settings live in `config/.env`. [`config/env.example`](../config/env.example)
documents every one of them and is the reference — this section covers only what
you must decide.

### Required

```bash
# The app refuses to start with the placeholder unless FLASK_DEBUG=true.
SECRET_KEY=<python3 -c "import secrets; print(secrets.token_urlsafe(48))">
FLASK_DEBUG=False
PORT=5001
```

### LLM provider

```bash
LLM_PROVIDER=ollama                 # ollama | openai | gemini
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2:3b

# LLM_PROVIDER=openai
# OPENAI_API_KEY=...  OPENAI_MODEL=gpt-4o-mini

# LLM_PROVIDER=gemini
# GOOGLE_API_KEY=...  GEMINI_MODEL=gemini-2.5-flash
```

- The LLM is created once at startup: after changing provider or model (in
  `config/.env` or Admin → Studio Settings) **restart the app**.
- With `openai` the embeddings switch to OpenAI's too, so the stored vectors no
  longer match: after switching to or from OpenAI, **reset the knowledge base and
  re-sync** (Admin → Knowledge Base, then Connectors).

### Knowledge sources

`TESTRAIL_*` and `CONFLUENCE_*` point the syncs at your instances.
`TESTRAIL_PUSH_ENABLED=true` allows pushing generated tests back to TestRail.
Daily auto-sync (`TESTRAIL_SCHEDULE_*`, `CONFLUENCE_SCHEDULE_*`) runs at a
**UTC** time.

### Settings in the admin UI

**Admin → Studio Settings** edits most settings at runtime and writes them back
into `config/.env` (comments and other keys are preserved), so the file stays the
single source. Keys sent to the settings API are the lowercase schema keys
(`chat_retrieval_k`), not the env names.

---

## Running

### Development

```bash
bash scripts/run.sh           # macOS / Linux
scripts\run.bat               # Windows (or .\scripts\run.ps1)
```

`run.sh` activates `venv`, creates `config/.env` if missing, **exits if
`LLM_PROVIDER=ollama` and Ollama is not reachable**, offers another port if 5001
is taken, then runs Flask's development server (`backend/app.py`, threaded,
binding `HOST`). Equivalent by hand: `source venv/bin/activate && python backend/app.py`.

On first start the console prints a random password for the `admin` user, once.

---

## Production

Use Gunicorn with the shipped configuration, not the development server:

```bash
bash scripts/run-production.sh
# or: venv/bin/gunicorn -c gunicorn_config.py "backend.app:create_app()"
```

`gunicorn_config.py` binds `0.0.0.0:$PORT` (it ignores `HOST`) and runs **one
worker** with many threads (`gthread`). Keep it at one worker: all threads share
the in-memory vector store and warmed caches, and every open page holds a thread
per live stream. Raise `GUNICORN_THREADS` (default `max(32, 4 × CPUs)`) for more
concurrent viewers; keep the 900 s `GUNICORN_TIMEOUT`, since analyses run long.
`run-production.sh` loads `config/.env` and warns about a placeholder
`SECRET_KEY` or `FLASK_DEBUG=True`.

### systemd

```ini
# /etc/systemd/system/ai-test-studio.service
[Unit]
Description=AI Test Studio
After=network-online.target

[Service]
User=studio
WorkingDirectory=/opt/AI-Test-Studio
# The app loads config/.env itself; no EnvironmentFile needed.
ExecStart=/opt/AI-Test-Studio/venv/bin/gunicorn -c gunicorn_config.py "backend.app:create_app()"
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now ai-test-studio
curl http://localhost:5001/health     # {"status": "healthy", ...}
```

### Checklist

- [ ] A real `SECRET_KEY`; `FLASK_DEBUG=False`.
- [ ] HTTPS in front (Nginx or similar) and `SESSION_COOKIE_SECURE=true`.
- [ ] `CORS_ALLOWED_ORIGINS` set to your public origin (never `*`).
- [ ] The `admin` password saved, or changed in Admin → Users.
- [ ] `storage/` writable by the service user, and backed up.
- [ ] If you use the agent pages, the agent server connected as below.

See [SECURITY.md](../SECURITY.md) for why each matters.

---

## Connecting QA Agent Network

The agent pages proxy to [QA Agent Network](https://github.com/msr5464/QA-AI-Agent)'s
server, which lives in its own repo (usually cloned next to this one):

```bash
cd ../QA-Agent-Network && bash scripts/run-server.sh    # 127.0.0.1:6001
```

| Setting (Studio `config/.env`) | |
|------|---|
| `QA_AGENT_NETWORK_URL` | Where the agent server is (default `http://localhost:6001`) |
| `QA_AGENT_NETWORK_TIMEOUT` | Seconds for non-streaming proxy calls (default 30) |
| `QA_AGENT_PROXY_SECRET` | Must equal the agent server's `QA_AGENT_PROXY_SECRET`. Required if the agent server listens on anything but localhost |

Studio authenticates the user and forwards their identity to the agent server;
the agent server trusts those headers, which is why it binds to localhost by
default and needs the shared secret otherwise. Team setup of the agent server:
[its DEPLOYMENT guide](https://github.com/msr5464/QA-AI-Agent/blob/main/docs/DEPLOYMENT.md).

---

## Backup

Everything stateful is under `storage/` (git-ignored):

| Path | Contents |
|------|----------|
| `storage/users.json` | Accounts and roles |
| `storage/chroma_db/` | Vector store (rebuildable by re-syncing, but slow) |
| `storage/documents/`, `documents_metadata.json` | Uploaded test-case files |
| `storage/requirement_sessions/`, `requirement_runs.jsonl` | Requirements → Tests run history |
| `storage/operation_costs.jsonl` | LLM cost log (Analytics) |
| `storage/*_sync_metadata.json` | Sync state |

Back up `config/.env` separately and securely — it holds secrets.

```bash
tar czf studio-backup-$(date +%F).tgz storage/
```

---

## Troubleshooting

### `RuntimeError: SECRET_KEY is unset or set to a publicly known placeholder`
Set a real `SECRET_KEY` in `config/.env`, or `FLASK_DEBUG=true` for local-only use.

### Lost the admin password
Another admin can reset it in Admin → Users. If there is no other admin: stop the
app, remove the `admin` user's entry from `storage/users.json`, and start it again
— when no admin exists, a new `admin` is created and its password printed once.

### Port already in use
Set `PORT` in `config/.env` (e.g. `5002`). On macOS, port 5000 is taken by AirPlay
Receiver; this app defaults to 5001 for that reason.

### Ollama not running
`ollama serve`; check with `curl http://localhost:11434/api/tags`; pull the model
with `ollama pull llama3.2:3b`. Or switch `LLM_PROVIDER` to `openai` / `gemini`.

### Sync fails: "attempt to write a readonly database" (SQLite 1032)
The process cannot write `storage/chroma_db`. Fix permissions/ownership:
```bash
chmod -R u+rwX storage/ && chown -R "$(whoami)" storage/
```
On Windows, give the service user write access to `storage\`.

### Upload rejected
The admin upload accepts **CSV/Excel test-case files** only (at least 7 of the 10
expected test-case columns). Other documents enter the knowledge base through
the Confluence sync.

### `.doc` / `.ppt` errors
Install `antiword` (`.doc` requirement files) or LibreOffice (legacy `.doc` /
`.ppt` documents), or convert to `.docx` / `.pptx`.

### Answers ignore recent content, or look wrong after switching LLM provider
Re-run the syncs. After switching to or from OpenAI, reset the knowledge base
first — the embedding dimensions differ.

### Agent pages show offline
The agent server is not reachable at `QA_AGENT_NETWORK_URL` — start it, and check
that `QA_AGENT_PROXY_SECRET` matches in both repos. Agent run errors are
explained in [QA Agent Network's troubleshooting guide](https://github.com/msr5464/QA-AI-Agent/blob/main/docs/TROUBLESHOOTING.md).

### Import errors or a broken venv
```bash
rm -rf venv && bash scripts/install.sh
```

### Windows: scripts will not run
`Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`, or run
`powershell -ExecutionPolicy Bypass -File .\scripts\run.ps1`.
