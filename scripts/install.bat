@echo off
REM Installation Script for Windows (CMD)
REM Batch file to set up the RAG system

echo ========================================
echo RAG System Installation Script
echo ========================================
echo.

REM Check Python installation
echo Checking Python installation...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Please install Python 3.9+ from https://www.python.org/
    pause
    exit /b 1
)
python --version
echo.

REM Create virtual environment
echo Creating virtual environment...
if exist venv (
    echo WARNING: Virtual environment already exists. Skipping...
) else (
    python -m venv venv
    echo Virtual environment created
)
echo.

REM Activate virtual environment
echo Activating virtual environment...
call venv\Scripts\activate.bat

REM Upgrade pip
echo Upgrading pip...
python -m pip install --upgrade pip

REM Install dependencies
echo Installing dependencies...
pip install -r requirements.txt

REM Initialize storage
echo Initializing storage directories...
call scripts\init_storage.bat

REM Check and install Ollama
echo.
echo Checking Ollama installation...
ollama --version >nul 2>&1
if errorlevel 1 (
    echo WARNING: Ollama not found. Installing Ollama...
    echo Downloading Ollama installer...
    
    REM Download Ollama installer
    powershell -Command "Invoke-WebRequest -Uri 'https://github.com/ollama/ollama/releases/latest/download/OllamaSetup.exe' -OutFile '%TEMP%\OllamaSetup.exe'"
    
    if exist "%TEMP%\OllamaSetup.exe" (
        echo Download complete. Starting installer...
        echo WARNING: Please complete the Ollama installation wizard, then press Enter to continue...
        start /wait "" "%TEMP%\OllamaSetup.exe"
        del "%TEMP%\OllamaSetup.exe"
        
        REM Refresh PATH
        call refreshenv >nul 2>&1
        
        REM Verify installation
        timeout /t 2 /nobreak >nul
        ollama --version >nul 2>&1
        if errorlevel 1 (
            echo WARNING: Ollama installation may require a restart. Please restart your terminal and run this script again.
            echo Or install manually from: https://ollama.ai/download/windows
            pause
        ) else (
            echo Ollama installed successfully
        )
    ) else (
        echo ERROR: Failed to download Ollama installer.
        echo Please install Ollama manually from: https://ollama.ai/download/windows
        pause
    )
) else (
    ollama --version
    echo Ollama found
)

REM Start Ollama service if not running
echo.
echo Checking Ollama service...
powershell -Command "$response = Invoke-WebRequest -Uri 'http://localhost:11434/api/tags' -TimeoutSec 2 -ErrorAction SilentlyContinue; if ($response) { exit 0 } else { exit 1 }" >nul 2>&1
if errorlevel 1 (
    echo WARNING: Ollama service not running. Starting Ollama...
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

REM Pull default model if not available
echo.
echo Checking for Ollama model (llama3.2:3b)...
ollama list | findstr /C:"llama3.2:3b" >nul 2>&1
if errorlevel 1 (
    echo WARNING: Model llama3.2:3b not found. Pulling model (this may take a few minutes)...
    ollama pull llama3.2:3b
    if errorlevel 1 (
        echo WARNING: Failed to pull model. You can pull it manually later: ollama pull llama3.2:3b
    ) else (
        echo Model llama3.2:3b pulled successfully
    )
) else (
    echo Model llama3.2:3b is available
)

REM Copy environment file
echo.
echo Setting up configuration...
if not exist config\.env (
    copy config\env.example config\.env
    echo Configuration file created: config\.env
    echo.
    echo Configuration Setup:
    echo    The .env file has been created from env.example
    echo    This file is hidden (not in git) and contains your personal settings
    echo.
    echo IMPORTANT: Before running in production, edit config\.env and set:
    echo    1. SECRET_KEY - Generate a secure random key
    echo.
    echo    To generate a secure SECRET_KEY, run:
    echo    python -c "import secrets; print(secrets.token_hex(32))"
    echo.
    echo    For development, the default values will work, but change SECRET_KEY for production!
    echo.
    echo    Default admin credentials: admin / admin123
    echo    WARNING: Change the default admin password after first login!
) else (
    echo WARNING: Configuration file already exists: config\.env
    echo    Skipping creation. Edit it manually if needed.
)

echo.
echo ========================================
echo Installation Complete!
echo ========================================
echo.
echo Next Steps:
echo.
echo 1. Configure your environment (optional for development):
echo    - Edit config\.env to customize settings
echo    - For production: Change SECRET_KEY
echo    - Default admin credentials: admin / admin123 (change after first login!)
echo.
echo 2. Start the application:
echo    scripts\run.bat
echo.
echo 3. Access the interfaces:
echo    - Admin Panel: http://localhost:5001/admin
echo    - Customer Panel: http://localhost:5001/customer
echo    - API Base: http://localhost:5001/api
echo.
echo Documentation:
echo    - README.md - Quick start guide
echo    - DEPLOYMENT.md - Detailed deployment guide
echo    - config\env.example - Configuration template with comments
echo.
pause

