# Run Script for Windows
# PowerShell script to start the RAG system

Write-Host "Starting RAG System..." -ForegroundColor Green

# Check if virtual environment exists
if (-not (Test-Path "venv")) {
    Write-Host "❌ Virtual environment not found. Please run install.ps1 first" -ForegroundColor Red
    exit 1
}

# Activate virtual environment
& ".\venv\Scripts\Activate.ps1"

# Check if config file exists
if (-not (Test-Path "config\.env")) {
    Write-Host "⚠️  Configuration file not found. Creating from example..." -ForegroundColor Yellow
    Copy-Item "config\env.example" "config\.env"
    Write-Host "⚠️  Please edit config\.env with your settings" -ForegroundColor Yellow
}

# Load environment variables from config/.env
if (Test-Path "config\.env") {
    Get-Content "config\.env" | ForEach-Object {
        if ($_ -match '^([^#][^=]+)=(.*)$') {
            $name = $matches[1].Trim()
            $value = $matches[2].Trim()
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

# Check if Ollama is running
Write-Host ""
Write-Host "Checking Ollama service..." -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -TimeoutSec 2 -ErrorAction Stop
    Write-Host "✅ Ollama service is running" -ForegroundColor Green
} catch {
    Write-Host "⚠️  Ollama service not running. Starting Ollama..." -ForegroundColor Yellow
    try {
        $ollamaVersion = ollama --version 2>&1
        Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden
        Write-Host "✅ Started Ollama service" -ForegroundColor Green
        
        # Wait for Ollama to be ready
        Write-Host "Waiting for Ollama to be ready..." -ForegroundColor Yellow
        $ready = $false
        for ($i = 1; $i -le 30; $i++) {
            Start-Sleep -Seconds 1
            try {
                $response = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -TimeoutSec 1 -ErrorAction Stop
                Write-Host "✅ Ollama is ready" -ForegroundColor Green
                $ready = $true
                break
            } catch {
                continue
            }
        }
        if (-not $ready) {
            Write-Host "⚠️  Ollama did not start in time. Please start it manually: ollama serve" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "❌ Ollama not found. Please install Ollama first: .\scripts\install.ps1" -ForegroundColor Red
        exit 1
    }
}

# Run the application
Write-Host ""
$port = [Environment]::GetEnvironmentVariable("PORT", "Process")
if (-not $port) { $port = "5001" }
Write-Host "Starting Flask application on port $port..." -ForegroundColor Cyan
python backend\app.py

