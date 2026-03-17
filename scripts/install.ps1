# Installation Script for Windows
# PowerShell script to set up the RAG system

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "RAG System Installation Script" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check Python installation
Write-Host "Checking Python installation..." -ForegroundColor Yellow
try {
    $pythonVersion = python --version 2>&1
    Write-Host "✅ Python found: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "❌ Python not found. Please install Python 3.9+ from https://www.python.org/" -ForegroundColor Red
    exit 1
}

# Create virtual environment
Write-Host ""
Write-Host "Creating virtual environment..." -ForegroundColor Yellow
if (Test-Path "venv") {
    Write-Host "⚠️  Virtual environment already exists. Skipping..." -ForegroundColor Yellow
} else {
    python -m venv venv
    Write-Host "✅ Virtual environment created" -ForegroundColor Green
}

# Activate virtual environment
Write-Host ""
Write-Host "Activating virtual environment..." -ForegroundColor Yellow
& ".\venv\Scripts\Activate.ps1"

# Upgrade pip
Write-Host ""
Write-Host "Upgrading pip..." -ForegroundColor Yellow
python -m pip install --upgrade pip

# Install dependencies
Write-Host ""
Write-Host "Installing dependencies..." -ForegroundColor Yellow
pip install -r requirements.txt

# Initialize storage
Write-Host ""
Write-Host "Initializing storage directories..." -ForegroundColor Yellow
& ".\scripts\init_storage.ps1"

# Check and install Ollama
Write-Host ""
Write-Host "Checking Ollama installation..." -ForegroundColor Yellow
try {
    $ollamaVersion = ollama --version 2>&1
    Write-Host "✅ Ollama found: $ollamaVersion" -ForegroundColor Green
} catch {
    Write-Host "⚠️  Ollama not found. Installing Ollama..." -ForegroundColor Yellow
    Write-Host "Downloading Ollama installer..." -ForegroundColor Yellow
    
    # Download Ollama installer for Windows
    $installerUrl = "https://ollama.ai/download/windows"
    $installerPath = "$env:TEMP\ollama-installer.exe"
    
    try {
        Invoke-WebRequest -Uri "https://github.com/ollama/ollama/releases/latest/download/OllamaSetup.exe" -OutFile $installerPath -ErrorAction Stop
        Write-Host "✅ Download complete. Starting installer..." -ForegroundColor Green
        Write-Host "⚠️  Please complete the Ollama installation wizard, then press Enter to continue..." -ForegroundColor Yellow
        Start-Process $installerPath -Wait
        Remove-Item $installerPath -ErrorAction SilentlyContinue
        
        # Refresh PATH
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
        
        # Verify installation
        Start-Sleep -Seconds 2
        try {
            $ollamaVersion = ollama --version 2>&1
            Write-Host "✅ Ollama installed successfully" -ForegroundColor Green
        } catch {
            Write-Host "⚠️  Ollama installation may require a restart. Please restart your terminal and run this script again." -ForegroundColor Yellow
            Write-Host "   Or install manually from: https://ollama.ai/download/windows" -ForegroundColor Yellow
            Read-Host "Press Enter to continue anyway..."
        }
    } catch {
        Write-Host "❌ Failed to download Ollama installer." -ForegroundColor Red
        Write-Host "   Please install Ollama manually from: https://ollama.ai/download/windows" -ForegroundColor Yellow
        Read-Host "Press Enter after installing Ollama manually, or Ctrl+C to exit..."
    }
}

# Check for antiword (for .doc file support)
Write-Host ""
Write-Host "Checking antiword installation (for .doc file support)..." -ForegroundColor Yellow
try {
    $antiwordVersion = antiword 2>&1
    Write-Host "✅ antiword found" -ForegroundColor Green
} catch {
    Write-Host "⚠️  antiword not found." -ForegroundColor Yellow
    Write-Host "   Old .doc files (pre-2007 Word format) require antiword for text extraction." -ForegroundColor White
    Write-Host "   Options:" -ForegroundColor White
    Write-Host "   1. Convert .doc files to .docx (recommended)" -ForegroundColor Gray
    Write-Host "   2. Install antiword for Windows from: http://www.winfield.demon.nl/#antiword" -ForegroundColor Gray
    Write-Host "   Note: .docx, .pdf, and .txt files work without antiword." -ForegroundColor White
}

# Copy environment file
Write-Host ""
Write-Host "Setting up configuration..." -ForegroundColor Yellow
if (-not (Test-Path "config\.env")) {
    Copy-Item "config\env.example" "config\.env"
    Write-Host "✅ Configuration file created: config\.env" -ForegroundColor Green
    Write-Host ""
    Write-Host "📝 Configuration Setup:" -ForegroundColor Cyan
    Write-Host "   The .env file has been created from env.example" -ForegroundColor White
    Write-Host "   This file is hidden (not in git) and contains your personal settings" -ForegroundColor White
    Write-Host ""
    Write-Host "⚠️  IMPORTANT: Before running in production, edit config\.env and set:" -ForegroundColor Yellow
    Write-Host "   1. SECRET_KEY - Generate a secure random key" -ForegroundColor White
    Write-Host ""
    Write-Host "   To generate a secure SECRET_KEY, run:" -ForegroundColor White
    Write-Host "   python -c \"import secrets; print(secrets.token_hex(32))\"" -ForegroundColor Gray
    Write-Host ""
    Write-Host "   For development, the default values will work, but change SECRET_KEY for production!" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "   Default admin credentials: admin / admin123" -ForegroundColor White
    Write-Host "   ⚠️  Change the default admin password after first login!" -ForegroundColor Yellow
} else {
    Write-Host "⚠️  Configuration file already exists: config\.env" -ForegroundColor Yellow
    Write-Host "   Skipping creation. Edit it manually if needed." -ForegroundColor White
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Installation Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "📋 Next Steps:" -ForegroundColor Yellow
Write-Host ""
Write-Host "1. Start Ollama (if using local LLM):" -ForegroundColor White
Write-Host "   ollama serve" -ForegroundColor Gray
Write-Host "   ollama pull llama3.2:3b" -ForegroundColor Gray
Write-Host ""
Write-Host "2. Configure your environment (optional for development):" -ForegroundColor White
Write-Host "   - Edit config\.env to customize settings" -ForegroundColor Gray
Write-Host "   - For production: Change SECRET_KEY" -ForegroundColor Gray
Write-Host "   - Default admin credentials: admin / admin123 (change after first login!)" -ForegroundColor Gray
Write-Host ""
Write-Host "3. Start the application:" -ForegroundColor White
Write-Host "   .\scripts\run.ps1" -ForegroundColor Gray
Write-Host ""
Write-Host "4. Access the interfaces:" -ForegroundColor White
Write-Host "   - Admin Panel: http://localhost:5001/admin" -ForegroundColor Gray
Write-Host "   - Customer Panel: http://localhost:5001/customer" -ForegroundColor Gray
Write-Host "   - API Base: http://localhost:5001/api" -ForegroundColor Gray
Write-Host ""
Write-Host "📚 Documentation:" -ForegroundColor Yellow
Write-Host "   - README.md - Quick start guide" -ForegroundColor Gray
Write-Host "   - DEPLOYMENT.md - Detailed deployment guide" -ForegroundColor Gray
Write-Host "   - config\env.example - Configuration template with comments" -ForegroundColor Gray
Write-Host ""

