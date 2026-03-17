"""
Pytest fixtures for Knowledge-AI tests.
Ensures project root is on path and provides common fixtures.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Project root
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def temp_storage_dir():
    """Temporary directory for sync metadata (no real storage)."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def app():
    """Flask app for API tests."""
    from backend.app import create_app
    return create_app()
