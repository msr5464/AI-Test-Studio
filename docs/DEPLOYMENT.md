# Deployment Guide

Complete guide for deploying InHouse Knowledge AI on Windows, macOS, and Linux.

## Table of Contents

- [Installation](#installation)
  - [Windows](#windows-installation)
  - [macOS](#macos-installation)
  - [Linux](#linux-installation)
- [Configuration](#configuration)
- [Running the System](#running-the-system)
- [Production Deployment (Detailed)](#production-deployment-detailed)
- [Scripts Reference](#scripts-reference)
- [Project Structure](#project-structure)
- [API Usage](#api-usage)
- [Troubleshooting](#troubleshooting)
- [Additional Resources](#additional-resources)
- [Platform-Specific Notes](#platform-specific-notes)

---

## Installation

### Windows Installation

#### Prerequisites
- Python 3.9+ installed
- PowerShell 5.1+ or Command Prompt
- Git (optional)
- Ollama (will be installed automatically by install script)
- **LibreOffice** (optional, but required for `.doc` and `.ppt` files):
  - Download from [LibreOffice website](https://www.libreoffice.org/download/)
  - Install the standard version
  - After installation, ensure `soffice` command is available in PATH

#### Installation Steps

**Option 1: PowerShell (Recommended)**
```powershell
# Navigate to deployment folder
cd C:\path\to\rag_deploy

# Run installation script
.\scripts\install.ps1
```

**Option 2: Command Prompt**
```cmd
cd C:\path\to\rag_deploy
scripts\install.bat
```

The installation script will:
1. Check Python installation
2. Create virtual environment
3. Install Python dependencies
4. Install Ollama (if not present)
5. Start Ollama service
6. Pull default model (llama3.2:3b)
7. Initialize storage directories
8. Create configuration file

#### Manual Installation (if scripts fail)

```cmd
# 1. Create virtual environment
python -m venv venv

# 2. Activate virtual environment
venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create storage directories
mkdir storage\documents
mkdir storage\chroma_db
mkdir storage\embedding_cache
mkdir logs

# 5. Copy and edit configuration
copy config\env.example config\.env
REM Edit config\.env with your settings

# 6. Install Ollama manually from https://ollama.ai/download/windows
# 7. Pull model: ollama pull llama3.2:3b
```

---

### macOS Installation

#### Prerequisites
- Python 3.9+ (usually pre-installed, or install via Homebrew)
- Terminal access
- Git (optional)
- Ollama (will be installed automatically by install script)
- **LibreOffice** (optional, but required for `.doc` and `.ppt` files):
  - Install via Homebrew: `brew install --cask libreoffice`
  - Or download from [LibreOffice website](https://www.libreoffice.org/download/)

#### Installation Steps

```bash
# Navigate to deployment folder
cd /path/to/rag_deploy

# Make scripts executable (if needed)
chmod +x scripts/*.sh

# Run installation script
bash scripts/install.sh
```

The installation script will:
1. Check Python installation
2. Create virtual environment
3. Install Python dependencies
4. Install Ollama via Homebrew (if available) or prompt manual install
5. Start Ollama service
6. Pull default model (llama3.2:3b)
7. Initialize storage directories
8. Create configuration file

#### Using Homebrew (if Python not installed)

```bash
# Install Python via Homebrew
brew install python@3.11

# Then follow installation steps above
```

#### Manual Installation (if scripts fail)

```bash
# 1. Create virtual environment
python3 -m venv venv

# 2. Activate virtual environment
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create storage directories
mkdir -p storage/documents
mkdir -p storage/chroma_db
mkdir -p storage/embedding_cache
mkdir -p logs

# 5. Copy and edit configuration
cp config/env.example config/.env
# Edit config/.env with your settings

# 6. Install Ollama: brew install ollama
# 7. Pull model: ollama pull llama3.2:3b
```

---

### Linux Installation

#### Prerequisites
- Python 3.9+ (check with `python3 --version`)
- Terminal access
- pip (usually comes with Python)
- Git (optional)
- Ollama (will be installed automatically by install script)
- **LibreOffice** (optional, but required for `.doc` and `.ppt` files):
  - **Debian/Ubuntu**: `sudo apt-get install libreoffice`
  - **RHEL/CentOS/Fedora**: `sudo yum install libreoffice` or `sudo dnf install libreoffice`
  - **Arch Linux**: `sudo pacman -S libreoffice-fresh`

#### Installation Steps

**Ubuntu/Debian:**
```bash
# Install Python and pip if not installed
sudo apt-get update
sudo apt-get install python3 python3-pip python3-venv

# Navigate to deployment folder
cd /path/to/rag_deploy

# Make scripts executable
chmod +x scripts/*.sh

# Run installation script
bash scripts/install.sh
```

**CentOS/RHEL/Fedora:**
```bash
# Install Python and pip if not installed
sudo yum install python3 python3-pip
# OR for newer versions:
sudo dnf install python3 python3-pip

# Navigate to deployment folder
cd /path/to/rag_deploy

# Make scripts executable
chmod +x scripts/*.sh

# Run installation script
bash scripts/install.sh
```

The installation script will:
1. Check Python installation
2. Create virtual environment
3. Install Python dependencies
4. Install Ollama via official install script
5. Start Ollama service
6. Pull default model (llama3.2:3b)
7. Initialize storage directories
8. Create configuration file

#### Manual Installation (if scripts fail)

```bash
# 1. Create virtual environment
python3 -m venv venv

# 2. Activate virtual environment
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create storage directories
mkdir -p storage/documents
mkdir -p storage/chroma_db
mkdir -p storage/embedding_cache
mkdir -p logs

# 5. Copy and edit configuration
cp config/env.example config/.env
# Edit config/.env with your settings

# 6. Install Ollama: curl -fsSL https://ollama.ai/install.sh | sh
# 7. Pull model: ollama pull llama3.2:3b
```

---

## Configuration

### Environment Variables

Edit `config/.env` (created from `config/env.example`) to configure:

#### Required Settings
```bash
# Flask Configuration
SECRET_KEY=your-secret-key-here-change-in-production
PORT=5001
HOST=0.0.0.0
```

#### LLM Configuration

**Option 1: Ollama (Default - Recommended)**
```bash
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2:3b
USE_LOCAL_EMBEDDINGS=True
```

**Option 2: OpenAI**
```bash
LLM_PROVIDER=openai
OPENAI_API_KEY=your-openai-api-key-here
USE_LOCAL_EMBEDDINGS=False
```

**Option 3: Google Gemini**
```bash
LLM_PROVIDER=gemini
GOOGLE_API_KEY=your-google-api-key-here
# Or: GEMINI_API_KEY=...
# Optional: GEMINI_MODEL=gemini-2.5-flash (default) or gemini-2.5-pro
```

#### Storage Paths

**Windows:**
```bash
STORAGE_DIR=storage
DOCUMENTS_DIR=storage\documents
CHROMA_DB_DIR=storage\chroma_db
EMBEDDING_CACHE_DIR=storage\embedding_cache
```

**macOS/Linux:**
```bash
STORAGE_DIR=storage
DOCUMENTS_DIR=storage/documents
CHROMA_DB_DIR=storage/chroma_db
EMBEDDING_CACHE_DIR=storage/embedding_cache
```

#### RAG Settings
```bash
COLLECTION_NAME=rag_collection
CHUNK_SIZE=1000
CHUNK_OVERLAP=200
RETRIEVAL_K=3
MIN_SIMILARITY_THRESHOLD=15.0
```

#### Advanced Features
```bash
USE_HYBRID_SEARCH=False
USE_RERANKING=False
USE_QUERY_EXPANSION=False
ENABLE_QUERY_CACHE=True
QUERY_CACHE_SIZE=1000
ENABLE_EMBEDDING_CACHE=True
```

---

## Running the System

### Windows

**PowerShell:**
```powershell
.\scripts\run.ps1
```

**Command Prompt:**
```cmd
scripts\run.bat
```

### macOS / Linux

```bash
bash scripts/run.sh
```

The run scripts will:
1. Check if virtual environment exists
2. Activate virtual environment
3. Check if Ollama is running (start if needed)
4. Load configuration from `config/.env`
5. Start Flask application

### Manual Run

```bash
# Activate virtual environment
# Windows: venv\Scripts\activate
# macOS/Linux: source venv/bin/activate

# Run application
python backend/app.py
```

---

## Production Deployment

### Windows Production

#### Option 1: Windows Service (NSSM)

1. Download NSSM from https://nssm.cc/
2. Extract and run:
```cmd
nssm install RAGSystem "C:\path\to\python.exe" "C:\path\to\rag_deploy\backend\app.py"
nssm set RAGSystem AppDirectory "C:\path\to\rag_deploy"
nssm start RAGSystem
```

#### Option 2: IIS with wfastcgi

1. Install IIS and wfastcgi
2. Configure application pool
3. Set up wfastcgi handler
4. Configure `web.config`

#### Option 3: Docker

```dockerfile
FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "backend/app.py"]
```

Build and run:
```cmd
docker build -t rag-system .
docker run -p 5001:5001 rag-system
```

### macOS Production

#### Option 1: Launchd (macOS Service)

Create `~/Library/LaunchAgents/com.ragsystem.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ragsystem</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/rag_deploy/venv/bin/python</string>
        <string>/path/to/rag_deploy/backend/app.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/path/to/rag_deploy</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
```

Load service:
```bash
launchctl load ~/Library/LaunchAgents/com.ragsystem.plist
launchctl start com.ragsystem
```

#### Option 2: Docker

Same as Linux Docker instructions below.

### Linux Production

#### Option 1: Systemd Service

Create `/etc/systemd/system/rag-system.service`:

```ini
[Unit]
Description=RAG System Flask Application
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/rag_deploy
Environment="PATH=/path/to/rag_deploy/venv/bin"
ExecStart=/path/to/rag_deploy/venv/bin/python backend/app.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable rag-system
sudo systemctl start rag-system
sudo systemctl status rag-system
```

#### Option 2: Docker

Create `Dockerfile`:

```dockerfile
FROM python:3.9-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create storage directories
RUN mkdir -p storage/documents storage/chroma_db storage/embedding_cache logs

# Expose port
EXPOSE 5001

# Run application
CMD ["python", "backend/app.py"]
```

Build and run:
```bash
docker build -t rag-system .
docker run -d -p 5001:5001 \
  -v $(pwd)/storage:/app/storage \
  -v $(pwd)/config/.env:/app/config/.env \
  --name rag-system \
  rag-system
```

#### Option 3: Gunicorn (Production WSGI Server)

Install Gunicorn:
```bash
pip install gunicorn
```

Run with Gunicorn:
```bash
gunicorn -w 4 -b 0.0.0.0:5001 backend.app:create_app()
```

Or create `gunicorn_config.py`:
```python
bind = "0.0.0.0:5001"
workers = 4
timeout = 120
```

Run:
```bash
gunicorn -c gunicorn_config.py backend.app:create_app()
```

### Production Deployment (Detailed)

For production use, do not run Flask's development server. Use a WSGI server (e.g. Gunicorn) and follow the steps below.

#### Understanding the Development Server Warning

When you run `python backend/app.py` or `scripts/run.sh`, you may see a warning that the development server is not for production. Flask's built-in server is single-threaded, not optimized for performance, and not hardened for production.

#### Why Use a Production WSGI Server?

A production WSGI server like **Gunicorn** provides multi-worker architecture, process management, and better security and scalability.

#### Gunicorn Setup (Detailed)

1. **Install Gunicorn**: `pip install gunicorn` (often already in requirements.txt).
2. **Run**: `gunicorn -c gunicorn_config.py "backend.app:create_app()"` or use `./scripts/run-production.sh` if present.

Configuration (e.g. `gunicorn_config.py`): set `bind = "0.0.0.0:5001"`, `workers = 4` (or `(CPU cores * 2) + 1` for mixed workload), `timeout = 120` for long-running LLM queries.

#### Systemd Service (Linux)

Create `/etc/systemd/system/rag-system.service` with `Type=notify`, `ExecStart=.../venv/bin/gunicorn -c .../gunicorn_config.py "backend.app:create_app()"`, `Restart=always`. Then: `sudo systemctl daemon-reload`, `sudo systemctl enable rag-system`, `sudo systemctl start rag-system`.

#### Production Checklist

- Change **SECRET_KEY** in `config/.env`; set **FLASK_DEBUG=False**; change default admin password.
- Use **HTTPS** (reverse proxy such as Nginx with SSL).
- Configure **firewall**; set up **log rotation** and **backups**; tune **worker count** and **resource limits**.

#### Security

Use a reverse proxy (Nginx/Apache) with SSL for HTTPS. Restrict firewall to necessary ports. Never commit `.env`; use secure secret management. Keep dependencies updated.

#### Monitoring and Logging

Health check: `curl http://localhost:5001/health`. For production, redirect Gunicorn access/error logs to files and set up log rotation (e.g. logrotate). Consider Prometheus, Sentry, or APM tools.

#### Performance Tuning

Set workers to `(CPU_cores * 2) + 1` for mixed workload; use `timeout = 120` for LLM calls. Set resource limits in systemd or Docker. Enable ChromaDB persistence and embedding/query caches.

---

## Scripts Reference

Platform-specific scripts live in `scripts/`:

- **Installation:** `install.sh` (macOS/Linux), `install.ps1` / `install.bat` (Windows) — create venv, install deps, install/start Ollama, pull default model, init storage, create `config/.env` from `config/env.example`.
- **Run:** `run.sh` (macOS/Linux), `run.ps1` / `run.bat` (Windows) — activate venv, start Flask on configured port.
- **Storage init:** `init_storage.sh` / `init_storage.ps1` / `init_storage.bat` — create `storage/documents/`, `storage/chroma_db/`, `storage/embedding_cache/`, `logs/`.
**Scripts not executable (macOS/Linux):** `chmod +x scripts/*.sh`  
**PowerShell blocked (Windows):** `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`  
**Scripts not found:** Run from project root.  
**Manual run (if scripts fail):** Create venv, `pip install -r requirements.txt`, `bash scripts/init_storage.sh` (or Windows equivalent), copy `config/env.example` to `config/.env`, then `python backend/app.py`.

---

## Project Structure

### Directory Layout

```
rag_deploy/
│
├── backend/                    # Backend API server
│   ├── app.py                 # Main Flask application
│   ├── api/                   # API routes
│   │   ├── admin/             # Admin endpoints
│   │   │   ├── __init__.py
│   │   │   └── routes.py      # Document upload, management
│   │   └── customer/          # Customer endpoints
│   │       ├── __init__.py
│   │       └── routes.py      # Query endpoints
│   ├── services/              # Business logic
│   │   ├── __init__.py
│   │   └── rag_service.py     # RAG service layer
│   └── models/                # Data models
│       └── __init__.py
│
├── core/                      # Core RAG functionality
│   ├── __init__.py
│   ├── rag/                   # RAG classes
│   │   ├── __init__.py
│   │   ├── base_rag.py        # Base RAG class
│   │   ├── text_based_rag.py  # Text processing
│   │   ├── pdf_based_rag.py   # PDF processing
│   │   ├── csv_excel_based_rag.py  # CSV/Excel processing
│   │   ├── multi_format_rag.py    # Unified RAG
│   │   ├── rag_config.py      # Configuration
│   │   ├── rag_helpers.py     # Helper functions
│   │   ├── chromadb_helper.py # ChromaDB utilities
│   │   ├── caching.py         # Caching implementation
│   │   └── imports.py         # Centralized imports
│   └── utils/                 # General utilities
│
├── frontend/                  # Frontend interfaces
│   ├── admin/                 # Admin UI
│   │   └── index.html         # Admin dashboard
│   └── customer/              # Customer UI
│       └── index.html         # Query interface
│
├── config/                    # Configuration files
│   └── env.example           # Environment template
│
├── scripts/                   # Deployment scripts
│   ├── install.sh            # macOS/Linux installation
│   ├── install.ps1           # PowerShell installation
│   ├── install.bat           # CMD installation
│   ├── run.sh                # macOS/Linux run script
│   ├── run.ps1               # PowerShell run script
│   ├── run.bat               # CMD run script
│   ├── init_storage.sh       # macOS/Linux storage init
│   ├── init_storage.ps1      # PowerShell storage init
│   └── init_storage.bat      # CMD storage init
│
├── storage/                   # Data storage (created at runtime)
│   ├── documents/             # Uploaded documents
│   ├── chroma_db/              # Vector database
│   └── embedding_cache/       # Embedding cache
│
├── logs/                      # Application logs (created at runtime)
│
├── requirements.txt           # Python dependencies
├── README.md                  # Main documentation
├── docs/                     # Documentation files
│   ├── DEPLOYMENT.md         # This file
│   └── API.md                # API documentation
└── .gitignore                 # Git ignore rules
```

### Component Overview

**Backend (`backend/`)**:
- **Flask Application** (`app.py`): Main entry point, configures Flask, CORS, routes
- **API Routes** (`api/`): Admin and customer endpoints
- **Services** (`services/`): Business logic for RAG operations

**RAG core (`backend/rag/`)**:
- **Base RAG** (`base_rag.py`): Common functionality
- **Format-Specific RAG**: Text, PDF, CSV/Excel processors
- **Multi-Format RAG** (`multi_format_rag.py`): Unified interface
- **Supporting Files**: Configuration, helpers, caching, ChromaDB utilities

**Frontend (`frontend/`)**:
- **Admin Interface**: Document upload, management, ChromaDB viewing
- **Customer Interface**: Query interface with markdown formatting

### Data Flow

**Document Upload Flow:**
1. Admin uploads file via frontend
2. Frontend sends POST to `/api/admin/upload`
3. Admin route validates admin key
4. File saved temporarily
5. RAG service processes document
6. Document added to vectorstore
7. File moved to `storage/documents/`
8. Metadata saved to `storage/documents_metadata.json`
9. Response returned to frontend

**Query Flow:**
1. Customer enters question via frontend
2. Frontend sends POST to `/api/customer/query`
3. Customer route validates request
4. RAG service processes query
5. Query checked against cache
6. If cache miss, RAG system retrieves documents
7. LLM generates answer
8. Response returned to frontend

---

## API Usage

For complete API documentation with detailed examples, request/response formats, and code samples in multiple languages, see **[API.md](API.md)**.

### Quick Examples

**Login (get session):**
```bash
curl -X POST http://localhost:5001/api/auth/login \
  -H "Content-Type: application/json" \
  -c cookies.txt \
  -d '{"username": "admin", "password": "admin123"}'
```

**Upload Document:**
```bash
curl -X POST http://localhost:5001/api/admin/upload \
  -b cookies.txt \
  -F "file=@document.pdf"
```

**Query System:**
```bash
curl -X POST http://localhost:5001/api/customer/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the main topic?"}'
```

**List Documents:**
```bash
curl -X GET http://localhost:5001/api/admin/documents \
  -b cookies.txt
```

📖 **Full API Reference**: See [API.md](API.md) for:
- Complete endpoint documentation
- Request/response formats
- Authentication details
- Error handling
- Code examples (Python, JavaScript, cURL, PowerShell)
- Best practices

---

## Troubleshooting

### Common Issues

#### Port Already in Use

**All Platforms:**
Change `PORT` in `config/.env`:
```bash
PORT=5002  # or any other available port
```

**macOS Specific (Port 5000):**
macOS AirPlay Receiver often uses port 5000. The default port is set to 5001 to avoid conflicts. If you need to use a different port:

1. **Edit Configuration**:
   ```bash
   # Edit config/.env
   PORT=5002  # or any other available port
   ```

2. **Disable AirPlay Receiver** (if you want to use port 5000):
   - System Preferences → General → AirDrop & Handoff
   - Uncheck "AirPlay Receiver"

3. **Kill Process Using Port** (if needed):
   ```bash
   # Find process
   lsof -ti:5001
   
   # Kill process (replace PID)
   kill -9 <PID>
   ```

The run script (`scripts/run.sh`) will automatically detect port conflicts and prompt you to use a different port.

#### Python Not Found

**Windows:**
- Ensure Python is installed and added to PATH
- Reinstall Python with "Add to PATH" option checked

**macOS:**
```bash
# Install via Homebrew
brew install python@3.11

# Or use python3 explicitly
python3 -m venv venv
```

**Linux:**
```bash
# Ubuntu/Debian
sudo apt-get install python3 python3-pip python3-venv

# CentOS/RHEL
sudo yum install python3 python3-pip
```

#### Permission Denied (macOS/Linux)

Make scripts executable:
```bash
chmod +x scripts/*.sh
```

#### ChromaDB / Confluence sync: "attempt to write a readonly database" (SQLite 1032)

If Confluence or TestRail sync fails with:
```
Query error: Database error: (code: 1032) attempt to write a readonly database
```

the ChromaDB directory (or its files) is not writable by the process.

**Fix:** Ensure the app has write access to `storage/chroma_db` (and `storage/`):

**macOS/Linux:**
```bash
# From project root
chmod -R u+rwX storage/
# If the directory was created by another user (e.g. root), fix ownership:
chown -R $(whoami) storage/
```

**Windows:** Ensure the user running the app has write permission to the `storage` folder (Properties → Security).

On startup, the app checks that `storage/chroma_db` is writable; if not, it will fail with a clear message and the path to fix.

#### LibreOffice Required for .doc and .ppt Files

If you encounter errors when uploading `.doc` or `.ppt` files:

**Error Message:**
```
soffice command was not found. Please install libreoffice
```

**Solution:**

1. **macOS:**
   ```bash
   brew install --cask libreoffice
   ```

2. **Linux (Debian/Ubuntu):**
   ```bash
   sudo apt-get update
   sudo apt-get install libreoffice
   ```

3. **Linux (RHEL/CentOS/Fedora):**
   ```bash
   sudo yum install libreoffice
   # or
   sudo dnf install libreoffice
   ```

4. **Windows:**
   - Download and install from [LibreOffice website](https://www.libreoffice.org/download/)
   - Ensure LibreOffice is added to your system PATH

5. **Verify Installation:**
   ```bash
   soffice --version
   ```

**Note:** Modern formats (`.docx`, `.pptx`) work without LibreOffice. Only older formats (`.doc`, `.ppt`) require LibreOffice for conversion.

**Alternative:** Convert `.doc` files to `.docx` and `.ppt` files to `.pptx` before uploading to avoid the LibreOffice requirement.

#### Import Errors

Ensure virtual environment is activated:

**Windows:**
```cmd
venv\Scripts\activate
```

**macOS/Linux:**
```bash
source venv/bin/activate
```

#### ChromaDB Errors

Ensure storage directories exist and are writable:

**Windows:**
```cmd
mkdir storage\chroma_db
```

**macOS/Linux:**
```bash
mkdir -p storage/chroma_db
chmod 755 storage/chroma_db
```

#### Ollama Not Running

**Check if Ollama is running:**
```bash
curl http://localhost:11434/api/tags
```

**Start Ollama manually:**
```bash
ollama serve
```

**Pull model if missing:**
```bash
ollama pull llama3.2:3b
```

#### Virtual Environment Issues

**Windows:**
- If PowerShell execution policy blocks scripts:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

**macOS/Linux:**
- If `venv` module not found:
```bash
sudo apt-get install python3-venv  # Ubuntu/Debian
sudo yum install python3-venv      # CentOS/RHEL
```

### Platform-Specific Issues

#### Windows

**PowerShell Execution Policy:**
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

**Long Path Names:**
Enable long path support in Windows:
```powershell
New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name "LongPathsEnabled" -Value 1 -PropertyType DWORD -Force
```

#### macOS

**Homebrew Python:**
If using Homebrew Python, ensure it's in PATH:
```bash
echo 'export PATH="/opt/homebrew/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

**Permission Issues:**
```bash
sudo chown -R $(whoami) /path/to/rag_deploy
```

#### Linux

**Firewall:**
```bash
# Ubuntu/Debian
sudo ufw allow 5001/tcp

# CentOS/RHEL
sudo firewall-cmd --add-port=5001/tcp --permanent
sudo firewall-cmd --reload
```

**SELinux (if enabled):**
```bash
sudo setsebool -P httpd_can_network_connect 1
```

---

## Platform-Specific Notes

### Supported Platforms

- ✅ **Windows 10/11** (PowerShell 5.1+, Command Prompt)
- ✅ **macOS 10.15+** (Catalina and later)
- ✅ **Linux** (Ubuntu 18.04+, Debian 10+, CentOS 7+, RHEL 7+)

### Platform Comparison

| Feature | Windows | macOS | Linux |
|---------|---------|-------|-------|
| Python Installation | Python.org installer | Homebrew or system Python | Package manager |
| Script Format | `.bat` / `.ps1` | `.sh` | `.sh` |
| Path Separator | `\` or `/` | `/` | `/` |
| Service Manager | NSSM / IIS | launchd | systemd |
| Virtual Environment | `venv\Scripts\activate` | `venv/bin/activate` | `venv/bin/activate` |

### Cross-Platform Compatibility

**Path Handling:**
The application uses Python's `pathlib.Path` which automatically handles platform-specific path separators.

**Environment Variables:**
Environment variables work the same across platforms. Use forward slashes in paths (they work on Windows too).

**Script Execution:**
All platforms support:
- Virtual environments (`venv`)
- pip package management
- Environment variables (`.env` files)

### Platform-Specific Recommendations

**Windows:**
- Use PowerShell for better scripting
- Enable long path support
- Consider using WSL2 for Linux-like environment
- Use Docker Desktop for containerized deployment

**macOS:**
- Use Homebrew for package management
- Consider using pyenv for Python version management
- Use launchd for background services
- Test on both Intel and Apple Silicon Macs

**Linux:**
- Use systemd for production services
- Configure firewall rules
- Set up log rotation
- Consider using Docker for easier deployment
- Use Gunicorn for production WSGI server

---

## Security Considerations

1. **Change Default Keys**: Update `SECRET_KEY` in production
2. **Use HTTPS**: Deploy behind reverse proxy (nginx, Apache) with SSL
3. **Firewall**: Restrict access to admin endpoints
4. **File Validation**: Validate uploaded file types and sizes
5. **Rate Limiting**: Implement rate limiting for API endpoints
6. **Environment Variables**: Never commit `.env` file to version control

---

## Backup

Regularly backup:
- `storage/chroma_db/` - Vector database
- `storage/documents/` - Uploaded documents
- `config/.env` - Configuration (without secrets)

**Windows:**
```cmd
xcopy storage\chroma_db backup\chroma_db /E /I
```

**macOS/Linux:**
```bash
cp -r storage/chroma_db backup/
```

---

## Support

For issues specific to:
- **Windows**: Check PowerShell execution policy and PATH settings
- **macOS**: Verify Python installation via Homebrew or system Python
- **Linux**: Ensure Python 3.9+ and required system packages are installed

For detailed troubleshooting, refer to the [Troubleshooting](#troubleshooting) section above.

---

## Additional Resources

- **[README.md](../README.md)** - Main project documentation and quick start guide
- **[API.md](API.md)** - Complete REST API reference with examples
- **[frontend/BRANDING.md](frontend/BRANDING.md)** - Branding and logo information
