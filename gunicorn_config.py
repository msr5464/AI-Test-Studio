"""
Gunicorn Configuration for Production Deployment
================================================

Production WSGI server configuration for Thanos Knowledge Dashboard.
"""

import multiprocessing
import os
from pathlib import Path

# Set working directory to project root
# This ensures relative paths (like storage/) resolve correctly
project_root = Path(__file__).parent.absolute()
os.chdir(str(project_root))

# Server socket
bind = f"0.0.0.0:{os.getenv('PORT', '5001')}"
backlog = 2048

# Worker processes
workers = int(os.getenv('GUNICORN_WORKERS', 1))
worker_class = 'sync'
worker_connections = 1000
timeout = 120
keepalive = 5

# Logging
accesslog = os.getenv('GUNICORN_ACCESS_LOG', '-')  # '-' means stdout
errorlog = os.getenv('GUNICORN_ERROR_LOG', '-')   # '-' means stderr
loglevel = os.getenv('GUNICORN_LOG_LEVEL', 'info')
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = 'rag-system'

# Server mechanics
daemon = False
pidfile = os.getenv('GUNICORN_PIDFILE', None)
umask = 0
user = None
group = None
tmp_upload_dir = None

# SSL (if needed, uncomment and configure)
# keyfile = '/path/to/keyfile'
# certfile = '/path/to/certfile'

# Performance tuning
max_requests = 1000
max_requests_jitter = 50
preload_app = False  # Set to False to avoid issues with ChromaDB and path resolution

# Graceful timeout for worker restart
graceful_timeout = 30

