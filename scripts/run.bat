@echo off
REM Run Script for Windows (CMD)
REM Batch file to start the RAG system

echo Starting RAG System...

REM Check if virtual environment exists
if not exist venv (
    echo ERROR: Virtual environment not found. Please run install.bat first
    pause
    exit /b 1
)

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Check if config file exists
if not exist config\.env (
    echo WARNING: Configuration file not found. Creating from example...
    copy config\env.example config\.env
    echo WARNING: Please edit config\.env with your settings
)

REM Load environment variables from config/.env
if exist config\.env (
    for /f "usebackq tokens=1,* delims==" %%a in ("config\.env") do (
        if not "%%a"=="" if not "%%a"=="#" (
            set "%%a=%%b"
        )
    )
)

REM Check if Ollama is running
echo.
echo Checking Ollama service...
powershell -Command "$response = Invoke-WebRequest -Uri 'http://localhost:11434/api/tags' -TimeoutSec 2 -ErrorAction SilentlyContinue; if ($response) { exit 0 } else { exit 1 }" >nul 2>&1
if errorlevel 1 (
    echo WARNING: Ollama service not running. Starting Ollama...
    ollama --version >nul 2>&1
    if errorlevel 1 (
        echo ERROR: Ollama not found. Please install Ollama first: scripts\install.bat
        pause
        exit /b 1
    )
    start /B "" ollama serve
    echo Started Ollama service
    
    REM Wait for Ollama to be ready
    echo Waiting for Ollama to be ready...
    set /a count=0
    :wait_ollama
    timeout /t 1 /nobreak >nul
    powershell -Command "$response = Invoke-WebRequest -Uri 'http://localhost:11434/api/tags' -TimeoutSec 1 -ErrorAction SilentlyContinue; if ($response) { exit 0 } else { exit 1 }" >nul 2>&1
    if errorlevel 1 (
        set /a count+=1
        if %count% LSS 30 goto wait_ollama
        echo WARNING: Ollama did not start in time. Please start it manually: ollama serve
    ) else (
        echo Ollama is ready
    )
) else (
    echo Ollama service is running
)

REM Run the application
echo.
if defined PORT (
    echo Starting Flask application on port %PORT%...
) else (
    echo Starting Flask application on port 5001...
)
python backend\app.py

pause

