"""
TestRail Sync Service
====================
Orchestrates the synchronization of test cases from TestRail to ChromaDB.
"""

import os
import re
import json
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
import tempfile

# If sync has been "in progress" longer than this, treat as stale and allow new sync
STALE_SYNC_MINUTES = 30
MAX_SYNC_LOG_LINES = 80

# Add parent directories to path for imports
import sys
_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from backend.connectors.testrail_connector import TestRailConnector
from backend.rag.settings import get_config


def _load_env_for_sync():
    """Load .env from config/.env so frontend sync uses the same config as the rest of the app."""
    env_path = _ROOT / "config" / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path)
        except Exception:
            pass


class TestRailSyncService:
    """Service for syncing test cases from TestRail."""
    
    def __init__(self):
        """Initialize sync service with configuration (same source as backend script)."""
        _load_env_for_sync()
        self.config = get_config()
        self.storage_dir = Path(os.getenv('STORAGE_DIR', 'storage'))
        self.metadata_file = self.storage_dir / 'testrail_sync_metadata.json'

        self.storage_dir.mkdir(parents=True, exist_ok=True)
        
        # Config resolution (config/.env) matches app and tests
        url = getattr(self.config, "testrail_url", None) or os.getenv("TESTRAIL_URL", "")
        email = getattr(self.config, "testrail_email", None) or os.getenv("TESTRAIL_EMAIL", "")
        api_key = getattr(self.config, "testrail_api_key", None) or os.getenv("TESTRAIL_API_KEY", "")
        
        self.connector = None
        if url and email and api_key:
            self.connector = TestRailConnector(url=url, email=email, api_key=api_key)
    
    def _load_sync_metadata(self) -> Dict[str, Any]:
        """
        Load sync metadata from storage.
        
        Returns:
            Sync metadata dictionary
        """
        if not self.metadata_file.exists():
            return {
                'last_sync': None,
                'is_syncing': False,
                'syncs': []
            }
        
        try:
            with open(self.metadata_file, 'r') as f:
                data = json.load(f)
                # Ensure is_syncing is reset on init/load if service restarted crash
                if 'is_syncing' not in data:
                    data['is_syncing'] = False
                return data
        except Exception as e:
            print(f"⚠️  Failed to load sync metadata: {e}")
            return {
                'last_sync': None,
                'is_syncing': False,
                'syncs': []
            }
    
    def _save_sync_metadata(self, metadata: Dict[str, Any]):
        """
        Save sync metadata to storage.
        
        Args:
            metadata: Sync metadata to save
        """
        try:
            with open(self.metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
        except Exception as e:
            print(f"⚠️  Failed to save sync metadata: {e}")

    def _append_sync_log(self, message: str):
        """Append a timestamped line to sync_log (for UI running log)."""
        try:
            metadata = self._load_sync_metadata()
            log = metadata.get('sync_log', [])
            ts = datetime.now().strftime('%H:%M:%S')
            log.append(f"[{ts}] {message}")
            metadata['sync_log'] = log[-MAX_SYNC_LOG_LINES:]
            self._save_sync_metadata(metadata)
        except Exception as e:
            print(f"⚠️  Failed to append sync log: {e}")

    def _clear_stale_sync_if_needed(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        If is_syncing is True but sync_started_at is older than STALE_SYNC_MINUTES (or missing/future), clear it
        so the user can start a new sync (e.g. after server restart or stuck thread).
        """
        if not metadata.get('is_syncing'):
            return metadata
        started = metadata.get('sync_started_at')
        # Clear if no timestamp (stuck state) or timestamp is in the future (invalid)
        if not started:
            metadata['is_syncing'] = False
            metadata.pop('sync_started_at', None)
            metadata.pop('current_sync', None)
            self._save_sync_metadata(metadata)
            print("⚠️  Cleared stale sync (no sync_started_at)")
            return metadata
        try:
            s = str(started).replace('Z', '').split('+')[0].strip()
            started_dt = datetime.fromisoformat(s)
            age = datetime.now() - started_dt
            # Clear if too old OR if timestamp is in the future (invalid)
            if age > timedelta(minutes=STALE_SYNC_MINUTES) or age.total_seconds() < 0:
                metadata['is_syncing'] = False
                metadata.pop('sync_started_at', None)
                metadata.pop('current_sync', None)
                self._save_sync_metadata(metadata)
                print(f"⚠️  Cleared stale sync (was in progress for {age.total_seconds() / 60:.0f} min)")
        except Exception as e:
            print(f"⚠️  Could not check stale sync: {e}")
        return metadata

    def _validate_testcase_data(self, df) -> tuple[bool, str]:
        """
        Validate if DataFrame has required testcase structure.
        
        Args:
            df: DataFrame to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if df.empty:
            return False, "No data to sync"
        
        # Check for required columns
        target_headers = {
            'id', 'title', 'execution mode', 'expected result', 
            'platform', 'preconditions', 'priority', 
            'section hierarchy', 'steps', 'type'
        }
        
        columns = {str(col).lower().strip() for col in df.columns}
        matched = columns.intersection(target_headers)
        
        # Must have at least 70% of target headers
        if len(matched) < 7:
            missing = target_headers - columns
            return False, f"Missing required columns: {', '.join(list(missing)[:5])}..."
        
        return True, ""
    
    def sync_from_testrail(self, rag_service: Optional[Any] = None) -> Dict[str, Any]:
        """
        Main sync entry point - fetch and sync test cases from TestRail.
        
        Args:
            rag_service: Optional shared RAGService (e.g. from app). If provided, documents
                are added to this instance so the customer portal can query them immediately.
        
        Returns:
            Sync result dictionary with status, counts, and errors
        """
        start_time = datetime.now()
        
        # Validate configuration
        if not self.config.testrail_url:
            return {
                'success': False,
                'error': 'TestRail URL not configured',
                'message': 'Set TESTRAIL_URL in .env'
            }
        
        if not self.config.testrail_project_ids:
            return {
                'success': False,
                'error': 'No projects configured',
                'message': 'Set TESTRAIL_PROJECT_IDS in .env (comma-separated)'
            }
        
        if not self.connector:
            return {
                'success': False,
                'error': 'TestRail connector not initialized',
                'message': 'Check TestRail configuration'
            }
        
        # Mark as syncing
        metadata = self._load_sync_metadata()
        metadata = self._clear_stale_sync_if_needed(metadata)
        if metadata.get('is_syncing', False):
             return {'success': False, 'message': 'Sync already in progress'}

        project_ids = self.config.testrail_project_ids
        projects_total = len(project_ids) if isinstance(project_ids, list) else 0

        first_log_line = f"[{datetime.now().strftime('%H:%M:%S')}] Sync started. Projects: {project_ids}, delta_days: {self.config.testrail_delta_days}"
        metadata['is_syncing'] = True
        metadata['sync_started_at'] = datetime.now().isoformat()
        metadata['current_sync'] = {
            'phase': 'starting',
            'projects_done': 0,
            'projects_total': projects_total,
            'test_cases_so_far': 0,
            'message': 'Starting sync...'
        }
        metadata['sync_log'] = [first_log_line]
        self._save_sync_metadata(metadata)

        def progress_callback(projects_done: int, projects_total: int, test_cases_so_far: int, message: str):
            m = self._load_sync_metadata()
            if not m.get('is_syncing'):
                return
            m['current_sync'] = {
                'phase': 'fetching',
                'projects_done': projects_done,
                'projects_total': projects_total,
                'test_cases_so_far': test_cases_so_far,
                'message': message
            }
            self._save_sync_metadata(m)

        def log_callback(msg: str):
            self._append_sync_log(msg)

        try:
            self._append_sync_log("Connecting to TestRail and fetching test cases...")
            print("\n" + "="*60)
            print("🚀 Starting TestRail Sync")
            print("="*60)
            print(f"Projects: {project_ids}")
            print(f"Delta days: {self.config.testrail_delta_days}")
            print("")
            
            # Fetch and transform data from TestRail (with progress and log callbacks)
            df = self.connector.fetch_and_transform(
                project_ids=project_ids,
                delta_days=self.config.testrail_delta_days,
                progress_callback=progress_callback,
                log_callback=log_callback
            )
            self._append_sync_log(f"Fetched {len(df)} test cases from TestRail")

            # Update progress: validating (so UI can show live phase)
            metadata = self._load_sync_metadata()
            if metadata.get('is_syncing'):
                metadata['current_sync'] = {
                    'phase': 'validating',
                    'projects_done': projects_total,
                    'projects_total': projects_total,
                    'test_cases_so_far': len(df),
                    'message': 'Validating test case structure...'
                }
                self._save_sync_metadata(metadata)

            # Validate data structure
            is_valid, error_msg = self._validate_testcase_data(df)
            if not is_valid:
                self._append_sync_log(f"Validation failed: {error_msg}")
                return {
                    'success': False,
                    'error': error_msg,
                    'message': f'Data validation failed: {error_msg}'
                }
            
            print(f"\n✅ Validation passed. Processing {len(df)} test cases (one file per suite)...")

            if rag_service is None:
                from backend.services.rag_service import RAGService
                rag_service = RAGService()

            # Full sync (delta_days=0): wipe all TestRail docs and re-ingest everything.
            # Delta sync (delta_days>0): only re-ingest changed suites — keep existing docs
            # so tests older than the delta window are NOT lost.
            _is_full_sync = (self.config.testrail_delta_days or 0) == 0
            metadata = self._load_sync_metadata()
            if _is_full_sync:
                if metadata.get('is_syncing') and 'current_sync' in metadata:
                    metadata['current_sync'] = {
                        'phase': 'chromadb',
                        'projects_done': projects_total,
                        'projects_total': projects_total,
                        'test_cases_so_far': len(df),
                        'message': 'Removing previous TestRail docs (full sync)...'
                    }
                    self._save_sync_metadata(metadata)
                del_result = rag_service.delete_documents_by_name_prefix("testrail_")
                if del_result.get('deleted_count', 0) > 0:
                    print(f"🗑️  Removed {del_result['deleted_count']} previous TestRail doc(s)")
                    self._append_sync_log(f"Removed {del_result['deleted_count']} previous TestRail doc(s)")
            else:
                print(f"📦 Delta sync (delta_days={self.config.testrail_delta_days}) — keeping existing docs, updating changed suites only")
                self._append_sync_log(f"Delta sync — updating changed suites only (keeping existing docs)")
            if del_result.get('errors'):
                for e in del_result['errors'][:5]:
                    print(f"⚠️  {e}")
                if len(del_result['errors']) > 5:
                    print(f"⚠️  ... and {len(del_result['errors']) - 5} more")

            # One CSV per suite: scalable (many suites = many small files instead of one heavy file)
            suite_col = 'Suite' if 'Suite' in df.columns else None
            if suite_col is None:
                groups = [('Default', df)]
            else:
                groups = [(name, grp) for name, grp in df.groupby(suite_col, sort=False)]

            def slug(s: str) -> str:
                s = (s or 'default').strip() or 'default'
                s = re.sub(r'[^\w\-]', '_', s)
                s = re.sub(r'_+', '_', s).strip('_')
                return s[:80] or 'default'

            added_suites = 0
            failed = []
            for suite_name, group_df in groups:
                if metadata.get('is_syncing') and added_suites > 0 and added_suites % 10 == 0:
                    metadata = self._load_sync_metadata()
                    if metadata.get('is_syncing') and 'current_sync' in metadata:
                        metadata['current_sync'] = {
                            'phase': 'chromadb',
                            'projects_done': projects_total,
                            'projects_total': projects_total,
                            'test_cases_so_far': len(df),
                            'message': f'Adding suite {added_suites + 1}/{len(groups)}...'
                        }
                        self._save_sync_metadata(metadata)
                safe_name = slug(str(suite_name))
                file_name = f"testrail_{safe_name}.csv"
                temp_csv = Path(tempfile.gettempdir()) / f"testrail_{safe_name}_{datetime.now().strftime('%H%M%S')}.csv"
                try:
                    group_df.to_csv(temp_csv, index=False)
                    result = rag_service.upload_document(
                        file_path=temp_csv,
                        file_name=file_name,
                        subdir="testrail",
                    )
                    if result.get('success'):
                        added_suites += 1
                    else:
                        failed.append(f"{suite_name}: {result.get('error', 'Unknown error')}")
                except Exception as e:
                    failed.append(f"{suite_name}: {e}")
                finally:
                    try:
                        temp_csv.unlink()
                    except FileNotFoundError:
                        pass

            if failed:
                err = "; ".join(failed[:3]) + ("..." if len(failed) > 3 else "")
                self._append_sync_log(f"ChromaDB update had failures: {len(failed)} suite(s). {err}")
                return {
                    'success': False,
                    'error': f"{len(failed)} suite(s) failed to add",
                    'message': f'Added {added_suites} suite(s); {len(failed)} failed: {err}',
                }
            self._append_sync_log("ChromaDB update completed successfully")

            # Cleanup: remove orphaned tests from ChromaDB that no longer exist in TestRail.
            # For delta sync this is essential (wipe doesn't happen); for full sync it's a safety net.
            try:
                self._append_sync_log("Checking for deleted tests in TestRail...")
                # Get all case IDs currently in ChromaDB
                _col = rag_service.rag.vectorstore._collection if rag_service.rag and rag_service.rag.vectorstore else None
                if _col:
                    _chroma_results = _col.get(where={"source_type": "testcase"}, include=["metadatas"])
                    _chroma_ids = set()
                    for _m in (_chroma_results.get("metadatas") or []):
                        _tid = (_m.get("testrail_id") or "").replace("C", "")
                        if _tid:
                            _chroma_ids.add(_tid)
                    # Get all case IDs from TestRail (all projects, no delta filter)
                    _testrail_ids = set()
                    for _pid in project_ids:
                        try:
                            _suites = self.connector.get_suites(_pid) or []
                            for _s in _suites:
                                _cases = self.connector.get_test_cases(_pid, suite_id=_s.get("id")) or []
                                for _c in _cases:
                                    _testrail_ids.add(str(_c.get("id", "")))
                        except Exception:
                            pass
                    # Find orphans: in ChromaDB but not in TestRail
                    _orphans = _chroma_ids - _testrail_ids
                    if _orphans:
                        _orphan_cids = [f"C{oid}" for oid in _orphans]
                        # Delete orphaned chunks from ChromaDB by testrail_id metadata
                        _deleted_count = 0
                        for _oid in _orphan_cids:
                            try:
                                _col.delete(where={"testrail_id": _oid})
                                _deleted_count += 1
                            except Exception:
                                pass
                        print(f"🗑️  Removed {_deleted_count} deleted test(s) from ChromaDB")
                        self._append_sync_log(f"Removed {_deleted_count} deleted test(s) from ChromaDB (no longer in TestRail)")
                    else:
                        print("✅ No orphaned tests found")
            except Exception as _cleanup_err:
                print(f"⚠️  Orphan cleanup failed (non-fatal): {_cleanup_err}")

            # Calculate sync statistics
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            
            # Update sync metadata
            metadata = self._load_sync_metadata()
            sync_record = {
                'timestamp': start_time.isoformat(),
                'project_ids': project_ids,
                'projects_count': projects_total,
                'test_cases_fetched': len(df),
                'test_cases_updated': len(df),
                'duration_seconds': duration,
                'status': 'success',
                'errors': []
            }
            metadata['last_sync'] = start_time.isoformat()
            metadata['syncs'].append(sync_record)
            metadata['syncs'] = metadata['syncs'][-100:]
            metadata.pop('current_sync', None)
            self._save_sync_metadata(metadata)

            print("\n" + "="*60)
            print("✅ TestRail Sync Complete!")
            print("="*60)
            print(f"Duration: {duration:.2f}s")
            print(f"Test cases synced: {len(df)}")
            print("")
            self._append_sync_log(f"Sync completed. {len(df)} test cases synced from {projects_total} project(s).")

            return {
                'success': True,
                'test_cases_fetched': len(df),
                'test_cases_updated': len(df),
                'duration_seconds': duration,
                'last_sync': start_time.isoformat(),
                'message': f'Successfully synced {len(df)} test cases from TestRail'
            }
            
        except Exception as e:
            error_msg = str(e)
            self._append_sync_log(f"Error: {error_msg}")
            print(f"\n❌ Sync failed: {error_msg}")
            metadata = self._load_sync_metadata()
            error_record = {
                'timestamp': start_time.isoformat(),
                'project_ids': project_ids,
                'projects_count': projects_total,
                'status': 'failed',
                'error': error_msg,
                'test_cases_fetched': 0
            }
            metadata['syncs'].append(error_record)
            metadata['syncs'] = metadata['syncs'][-100:]
            metadata.pop('current_sync', None)
            self._save_sync_metadata(metadata)
            return {
                'success': False,
                'error': error_msg,
                'message': f'Sync failed: {error_msg}'
            }
        finally:
            # Always clear syncing flag so "Sync already in progress" cannot get stuck
            metadata = self._load_sync_metadata()
            metadata['is_syncing'] = False
            metadata.pop('sync_started_at', None)
            metadata.pop('current_sync', None)
            self._save_sync_metadata(metadata)
    
    def get_sync_status(self) -> Dict[str, Any]:
        """
        Get current sync status and metadata.
        Clears stale sync (is_syncing True for > 30 min) so UI can show correct state.
        
        Returns:
            Sync status dictionary
        """
        metadata = self._load_sync_metadata()
        metadata = self._clear_stale_sync_if_needed(metadata)

        # Get latest sync info
        latest_sync = metadata['syncs'][-1] if metadata['syncs'] else None
        
        # Always return sync_log as a list so the UI can show running log (during sync and after refresh)
        sync_log = metadata.get('sync_log')
        if not isinstance(sync_log, list):
            sync_log = []

        return {
            'last_sync': metadata.get('last_sync'),
            'is_syncing': metadata.get('is_syncing', False),
            'current_sync': metadata.get('current_sync'),
            'latest_sync_record': latest_sync,
            'sync_log': sync_log,
            'total_syncs': len(metadata['syncs']),
            'configured_projects': self.config.testrail_project_ids,
            'delta_days': self.config.testrail_delta_days
        }
