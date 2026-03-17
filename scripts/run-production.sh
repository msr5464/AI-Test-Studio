#!/bin/bash
# Production Run Script for macOS/Linux
# Uses Gunicorn WSGI server for production deployment

set -e  # Exit on error

echo "🚀 Starting RAG System in Production Mode..."

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "❌ Virtual environment not found. Please run install.sh first"
    exit 1
fi

# Activate virtual environment
source venv/bin/activate

# Check if Gunicorn is installed
if ! python -c "import gunicorn" 2>/dev/null; then
    echo "⚠️  Gunicorn not found. Installing..."
    pip install gunicorn
fi

# Check if config file exists
if [ ! -f "config/.env" ]; then
    echo "⚠️  Configuration file not found. Creating from example..."
    cp config/env.example config/.env
    echo "⚠️  Please edit config/.env with your production settings"
    echo "⚠️  IMPORTANT: Change SECRET_KEY for production!"
    exit 1
fi

# Load environment variables
export $(grep -v '^#' config/.env | xargs)

# Check if SECRET_KEY is still default
if grep -q "your-secret-key-here-change-in-production" config/.env 2>/dev/null; then
    echo "⚠️  WARNING: SECRET_KEY is still set to default value!"
    echo "⚠️  Please change SECRET_KEY in config/.env before running in production"
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Check if FLASK_DEBUG is enabled
if grep -q "FLASK_DEBUG=True" config/.env 2>/dev/null; then
    echo "⚠️  WARNING: FLASK_DEBUG is enabled. This should be False in production!"
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Check if port is in use
PORT=${PORT:-5001}
if command -v lsof > /dev/null 2>&1; then
    if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null 2>&1 ; then
        echo "⚠️  Port $PORT is already in use!"
        echo "Please free the port or change PORT in config/.env"
        exit 1
    fi
fi

# Get the script directory and change to project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"
cd "$PROJECT_ROOT" || exit 1

echo "   Working directory: $PROJECT_ROOT"

# Run with Gunicorn
echo ""
echo "Starting Gunicorn WSGI server..."
echo "   Workers: ${GUNICORN_WORKERS:-auto}"
echo "   Port: $PORT"
echo "   Access: http://0.0.0.0:$PORT"
echo ""

# Use gunicorn_config.py if it exists, otherwise use command line args
if [ -f "gunicorn_config.py" ]; then
    gunicorn -c gunicorn_config.py "backend.app:create_app()"
else
    WORKERS=${GUNICORN_WORKERS:-$(($(nproc) * 2 + 1))}
    gunicorn \
        --chdir "$PROJECT_ROOT" \
        --bind "0.0.0.0:$PORT" \
        --workers "$WORKERS" \
        --worker-class sync \
        --timeout 120 \
        --access-logfile - \
        --error-logfile - \
        --log-level info \
        "backend.app:create_app()"
fi

