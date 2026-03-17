#!/bin/bash
# Run Script for macOS/Linux
# Bash script to start the RAG system

set -e  # Exit on error

echo "Starting RAG System..."

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "❌ Virtual environment not found. Please run install.sh first"
    exit 1
fi

# Activate virtual environment
source venv/bin/activate

# Check if config file exists
if [ ! -f "config/.env" ]; then
    echo "⚠️  Configuration file not found. Creating from example..."
    cp config/env.example config/.env
    echo "⚠️  Please edit config/.env with your settings"
fi

# Load PORT from config/.env for port check
# Flask's app.py uses dotenv which loads .env automatically
if [ -f "config/.env" ]; then
    # Extract PORT value from .env file (handle various formats)
    PORT_FROM_ENV=$(grep "^PORT=" config/.env 2>/dev/null | head -1 | sed 's/^PORT=//' | sed 's/^"//' | sed 's/"$//' | sed "s/^'//" | sed "s/'$//" | tr -d ' ')
    if [ -n "$PORT_FROM_ENV" ] && [ "$PORT_FROM_ENV" != "" ]; then
        export PORT=$PORT_FROM_ENV
    fi
fi

# Check LLM_PROVIDER and verify Ollama is running if needed
if [ -f "config/.env" ]; then
    # Extract LLM_PROVIDER value from .env file (handle various formats)
    LLM_PROVIDER_FROM_ENV=$(grep "^LLM_PROVIDER=" config/.env 2>/dev/null | head -1 | sed 's/^LLM_PROVIDER=//' | sed 's/^"//' | sed 's/"$//' | sed "s/^'//" | sed "s/'$//" | tr -d ' ' | tr '[:upper:]' '[:lower:]')
    if [ -n "$LLM_PROVIDER_FROM_ENV" ] && [ "$LLM_PROVIDER_FROM_ENV" = "ollama" ]; then
        echo "🔍 Checking if Ollama is running..."
        # Check if ollama is running by trying to connect to it
        OLLAMA_URL=$(grep "^OLLAMA_BASE_URL=" config/.env 2>/dev/null | head -1 | sed 's/^OLLAMA_BASE_URL=//' | sed 's/^"//' | sed 's/"$//' | sed "s/^'//" | sed "s/'$//" | tr -d ' ')
        OLLAMA_URL=${OLLAMA_URL:-http://localhost:11434}
        
        # Extract host and port from URL
        if [[ $OLLAMA_URL =~ http://([^:]+):([0-9]+) ]]; then
            OLLAMA_HOST=${BASH_REMATCH[1]}
            OLLAMA_PORT=${BASH_REMATCH[2]}
        else
            OLLAMA_HOST="localhost"
            OLLAMA_PORT="11434"
        fi
        
        # Check if Ollama is running by checking if the port is listening or by making a curl request
        if command -v curl > /dev/null 2>&1; then
            if curl -s --connect-timeout 2 "$OLLAMA_URL/api/tags" > /dev/null 2>&1; then
                echo "✅ Ollama is running at $OLLAMA_URL"
            else
                echo "❌ Ollama is not running or not accessible at $OLLAMA_URL"
                echo ""
                echo "Please start Ollama before running this script:"
                echo "  - On macOS/Linux: Run 'ollama serve' in a terminal"
                echo "  - Or ensure Ollama service is running"
                echo ""
                echo "You can also change LLM_PROVIDER in config/.env to use a different provider"
                exit 1
            fi
        elif command -v lsof > /dev/null 2>&1; then
            if lsof -Pi :$OLLAMA_PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
                echo "✅ Ollama appears to be running on port $OLLAMA_PORT"
            else
                echo "❌ Ollama is not running on port $OLLAMA_PORT"
                echo ""
                echo "Please start Ollama before running this script:"
                echo "  - On macOS/Linux: Run 'ollama serve' in a terminal"
                echo "  - Or ensure Ollama service is running"
                echo ""
                echo "You can also change LLM_PROVIDER in config/.env to use a different provider"
                exit 1
            fi
        else
            echo "⚠️  Cannot check Ollama status (curl and lsof not available)"
            echo "   Please ensure Ollama is running at $OLLAMA_URL"
        fi
    fi
fi

# Check if port is in use (use PORT from .env or default to 5001)
PORT=${PORT:-5001}
if command -v lsof > /dev/null 2>&1; then
    if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null 2>&1 ; then
        echo "⚠️  Port $PORT is already in use!"
        echo ""
        echo "Options:"
        echo "1. Change port in config/.env (set PORT=5002)"
        echo "2. Kill the process using port $PORT"
        echo "3. On macOS: Disable AirPlay Receiver (System Preferences -> General -> AirDrop & Handoff)"
        echo ""
        read -p "Do you want to try a different port? (y/n) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            NEW_PORT=$((PORT + 1))
            echo "Using port $NEW_PORT instead..."
            export PORT=$NEW_PORT
        else
            echo "Exiting. Please free port $PORT or change it in config/.env"
            exit 1
        fi
    fi
fi

# Run the application
echo ""
echo "Starting Flask application on port ${PORT:-5001}..."
echo "Tip: After changing backend code (e.g. connectors), restart this script to pick up changes."
python backend/app.py

