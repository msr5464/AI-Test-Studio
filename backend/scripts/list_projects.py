"""
Script to list available TestRail projects.
"""
import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend.connectors.testrail_connector import TestRailConnector
from backend.rag.settings import get_config
from dotenv import load_dotenv

def list_projects():
    # Load env vars
    load_dotenv(Path(__file__).parent.parent.parent / 'config' / '.env')
    
    config = get_config()
    
    if not config.testrail_url:
        print("❌ TestRail URL not configured in .env")
        return
        
    print(f"🔌 Connecting to {config.testrail_url}...")
    
    try:
        connector = TestRailConnector(
            url=config.testrail_url,
            email=config.testrail_email,
            api_key=config.testrail_api_key
        )
        
        projects = connector.get_projects()
        
        print("\n📋 Available Projects:")
        print("="*60)
        print(f"{'ID':<10} | {'Name':<30} | {'Suite Mode':<10}")
        print("-" * 60)
        
        for p in projects:
            print(f"{p.get('id', '?'):<10} | {p.get('name', 'Unknown'):<30} | {p.get('suite_mode', '?'):<10}")
            
        print("="*60)
        
    except Exception as e:
        print(f"\n❌ Failed to list projects: {e}")

if __name__ == "__main__":
    list_projects()
