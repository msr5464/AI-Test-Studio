"""
Confluence Sync Service
======================
Orchestrates the synchronization of specs (pages) from Confluence to ChromaDB.
Syncs Confluence pages using CQL (Confluence Query Language).
"""

import os
import json
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
import tempfile

STALE_SYNC_MINUTES = 30
MAX_SYNC_LOG_LINES = 80

import sys
_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from backend.connectors.confluence_connector import ConfluenceConnector
from backend.rag.rag_settings import get_config


def _load_env_for_sync():
    """Load .env for sync configuration."""
    env_path = _ROOT / "config" / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path)
        except Exception:
            pass


class ConfluenceSyncService:
    """Service for syncing specs from Confluence (CQL)."""

    def __init__(self):
        """Initialize Confluence sync service."""
        _load_env_for_sync()
        self.config = get_config()
        self.storage_dir = Path(os.getenv('STORAGE_DIR', 'storage'))
        self.metadata_file = self.storage_dir / 'confluence_sync_metadata.json'
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        url = getattr(self.config, "confluence_url", None) or os.getenv("CONFLUENCE_URL", "")
        email = getattr(self.config, "confluence_email", None) or os.getenv("CONFLUENCE_EMAIL", "")
        api_token = getattr(self.config, "confluence_api_token", None) or os.getenv("CONFLUENCE_API_TOKEN", "")

        self.connector = None
        if url and email and api_token:
            self.connector = ConfluenceConnector(url=url, email=email, api_token=api_token)

    def _load_sync_metadata(self) -> Dict[str, Any]:
        """Load Confluence sync metadata."""
        if not self.metadata_file.exists():
            return {'last_sync': None, 'is_syncing': False, 'syncs': [], 'sync_log': []}
        try:
            with open(self.metadata_file, 'r') as f:
                data = json.load(f)
                if 'is_syncing' not in data:
                    data['is_syncing'] = False
                if 'sync_log' not in data:
                    data['sync_log'] = []
                return data
        except Exception as e:
            print(f"⚠️  Failed to load Confluence sync metadata: {e}")
            return {'last_sync': None, 'is_syncing': False, 'syncs': [], 'sync_log': []}

    def _save_sync_metadata(self, metadata: Dict[str, Any]):
        """Save Confluence sync metadata."""
        try:
            with open(self.metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
        except Exception as e:
            print(f"⚠️  Failed to save Confluence sync metadata: {e}")

    def _append_sync_log(self, message: str):
        """Append a timestamped log line."""
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
        """Clear stale sync flag if in progress too long."""
        if not metadata.get('is_syncing'):
            return metadata
        started = metadata.get('sync_started_at')
        if not started:
            metadata['is_syncing'] = False
            metadata.pop('sync_started_at', None)
            metadata.pop('current_sync', None)
            self._save_sync_metadata(metadata)
            return metadata
        try:
            s = str(started).replace('Z', '').split('+')[0].strip()
            started_dt = datetime.fromisoformat(s)
            age = datetime.now() - started_dt
            if age > timedelta(minutes=STALE_SYNC_MINUTES) or age.total_seconds() < 0:
                metadata['is_syncing'] = False
                metadata.pop('sync_started_at', None)
                metadata.pop('current_sync', None)
                self._save_sync_metadata(metadata)
        except Exception:
            pass
        return metadata

    def _validate_specs_data(self, df) -> tuple[bool, str]:
        """Validate DataFrame has specs structure: page_id, title, body."""
        if df.empty:
            return False, "No data to sync"
        required = {'page_id', 'title', 'body'}
        columns = {str(col).lower().strip() for col in df.columns}
        if not required.issubset(columns):
            missing = required - columns
            return False, f"Missing required columns: {', '.join(missing)}"
        return True, ""

    def sync_from_confluence(self, rag_service: Optional[Any] = None) -> Dict[str, Any]:
        """
        Main sync entry point - fetch specs from Confluence and sync to ChromaDB.
        Uses CONFLUENCE_CQL (default type=page). Body is converted to Markdown (HTML→MD) for structure.
        Stores one .md file per page (confluence_{page_id}.md).
        To sync only one folder use CQL: type=page AND space=Product AND ancestor=<page_id>.
        """
        start_time = datetime.now()

        if not self.connector:
            return {
                'success': False,
                'error': 'Confluence connector not initialized',
                'message': 'Check CONFLUENCE_URL, CONFLUENCE_EMAIL, CONFLUENCE_API_TOKEN in .env',
            }

        metadata = self._load_sync_metadata()
        metadata = self._clear_stale_sync_if_needed(metadata)
        if metadata.get('is_syncing', False):
            return {'success': False, 'message': 'Confluence sync already in progress'}

        cql = getattr(self.config, "confluence_cql", None) or os.getenv("CONFLUENCE_CQL", "type=page")
        cql = (cql or "").strip() or "type=page"
        delta_days = int(getattr(self.config, "confluence_delta_days", 0) or os.getenv("CONFLUENCE_DELTA_DAYS", "0"))

        # Apply date filter when delta_days > 0 (fetch only recently updated pages)
        if delta_days > 0:
            cutoff = (datetime.now() - timedelta(days=delta_days)).strftime('%Y-%m-%d')
            cql = f"({cql}) AND lastModified >= '{cutoff}'"

        metadata['is_syncing'] = True
        metadata['sync_started_at'] = datetime.now().isoformat()
        metadata['current_sync'] = {
            'phase': 'starting',
            'message': f'Fetching Confluence pages (CQL)...',
        }
        delta_info = f", delta_days: {delta_days}" if delta_days > 0 else ""
        metadata['sync_log'] = [f"[{datetime.now().strftime('%H:%M:%S')}] Confluence sync started. CQL: {cql[:50]}...{delta_info}"]
        self._save_sync_metadata(metadata)

        def progress_callback(current: int, total: int, message: str):
            m = self._load_sync_metadata()
            if not m.get('is_syncing'):
                return
            m['current_sync'] = {'phase': 'fetching', 'current': current, 'total': total, 'message': message}
            self._save_sync_metadata(m)

        def log_callback(msg: str):
            self._append_sync_log(msg)

        try:
            self._append_sync_log("Connecting to Confluence and fetching pages...")
            print("\n" + "=" * 60)
            print("🚀 Starting Confluence Sync (CQL)")
            print("=" * 60)
            print(f"CQL: {cql[:80]}{'...' if len(cql) > 80 else ''}")
            if delta_days > 0:
                print(f"Delta days: {delta_days} (pages updated since {(datetime.now() - timedelta(days=delta_days)).strftime('%Y-%m-%d')})")
            print("")

            df = self.connector.fetch_and_transform(
                cql=cql,
                progress_callback=progress_callback,
                log_callback=log_callback,
            )

            self._append_sync_log(f"Fetched {len(df)} pages from Confluence")

            metadata = self._load_sync_metadata()
            if metadata.get('is_syncing'):
                metadata['current_sync'] = {'phase': 'validating', 'message': 'Validating specs structure...'}
                self._save_sync_metadata(metadata)

            is_valid, error_msg = self._validate_specs_data(df)
            if not is_valid:
                self._append_sync_log(f"Validation failed: {error_msg}")
                return {
                    'success': False,
                    'error': error_msg,
                    'message': f'Data validation failed: {error_msg}',
                }

            self._append_sync_log(f"Updating ChromaDB with {len(df)} specs (one Markdown file per page)...")

            if rag_service is None:
                from backend.services.rag_service import RAGService
                rag_service = RAGService()

            # Full sync (delta_days=0): wipe all Confluence docs and re-ingest everything.
            # Delta sync (delta_days>0): only re-ingest changed pages — keep existing docs.
            _is_full_sync = delta_days == 0
            metadata = self._load_sync_metadata()
            if _is_full_sync:
                if metadata.get('is_syncing'):
                    metadata['current_sync'] = {'phase': 'chromadb', 'message': 'Removing previous Confluence docs (full sync)...'}
                    self._save_sync_metadata(metadata)
                del_result = rag_service.delete_documents_by_name_prefix("confluence_")
                if del_result.get('deleted_count', 0) > 0:
                    print(f"🗑️  Removed {del_result['deleted_count']} previous Confluence doc(s)")
                    self._append_sync_log(f"Removed {del_result['deleted_count']} previous Confluence doc(s)")
            else:
                print(f"📦 Delta sync (delta_days={delta_days}) — keeping existing docs, updating changed pages only")
                self._append_sync_log(f"Delta sync — updating changed pages only (keeping existing docs)")
            if del_result.get('errors'):
                for e in del_result['errors'][:5]:
                    print(f"⚠️  {e}")
                if len(del_result['errors']) > 5:
                    print(f"⚠️  ... and {len(del_result['errors']) - 5} more")

            def md_section(row) -> str:
                title = (row.get('title') or '').replace('\n', ' ')
                url = row.get('url') or ''
                body = row.get('body') or ''
                parts = [f"# {title}"]
                if url:
                    parts.append(f"Source: {url}")
                parts.append("")
                parts.append(body)
                return "\n".join(parts)

            added = 0
            failed = []
            for idx, row in df.iterrows():
                    page_id = str(row.get('page_id', '')).strip() or f"row_{idx}"
                    if metadata.get('is_syncing') and added > 0 and added % 50 == 0:
                        metadata = self._load_sync_metadata()
                        if metadata.get('is_syncing'):
                            metadata['current_sync'] = {'phase': 'chromadb', 'message': f'Adding page {added + 1}/{len(df)}...'}
                            self._save_sync_metadata(metadata)
                    content = md_section(row)
                    temp_md = Path(tempfile.gettempdir()) / f"confluence_{page_id}_{datetime.now().strftime('%H%M%S')}.md"
                    try:
                        temp_md.write_text(content, encoding='utf-8')
                        result = rag_service.add_document_file(
                            file_path=temp_md,
                            file_name=f"confluence_{page_id}.md",
                            subdir="confluence",
                            extra_metadata={"source_type": "specs"},
                        )
                        if result.get('success'):
                            added += 1
                        else:
                            failed.append(f"{page_id}: {result.get('error', 'Unknown error')}")
                    except Exception as e:
                        failed.append(f"{page_id}: {e}")
                    finally:
                        try:
                            temp_md.unlink()
                        except FileNotFoundError:
                            pass

            if failed:
                err = "; ".join(failed[:3]) + ("..." if len(failed) > 3 else "")
                self._append_sync_log(f"ChromaDB update had failures: {len(failed)} page(s). {err}")
                return {
                    'success': False,
                    'error': f"{len(failed)} page(s) failed to add",
                    'message': f'Added {added} specs; {len(failed)} failed: {err}',
                }
            result = {'success': True}

            self._append_sync_log("ChromaDB update completed successfully")

            # Cleanup: remove Confluence pages from ChromaDB that no longer match the CQL query.
            # Handles pages deleted/moved in Confluence since the last sync.
            try:
                # Get all confluence page IDs currently in our documents
                _existing_doc_ids = {
                    doc_id: info.get('name', '')
                    for doc_id, info in rag_service.documents.items()
                    if info.get('name', '').startswith('confluence_') and info.get('name', '').endswith('.md')
                }
                # Extract page IDs from doc names: "confluence_12345.md" → "12345"
                import re as _re
                _chroma_page_ids = {}
                for _doc_id, _name in _existing_doc_ids.items():
                    _m = _re.search(r'confluence_(\d+)\.md', _name)
                    if _m:
                        _chroma_page_ids[_m.group(1)] = _doc_id
                # Get all valid page IDs from the current Confluence CQL (full query, no date filter)
                _valid_page_ids = set(str(row.get('page_id', '')) for _, row in df.iterrows() if row.get('page_id'))
                if not _is_full_sync and _chroma_page_ids:
                    # For delta sync, also fetch full page list to detect deletions
                    try:
                        _base_cql = (cql or "type=page").split(" AND lastmodified")[0].split(" AND last-modified")[0]
                        _full_df = self.connector.fetch_and_transform(cql=_base_cql)
                        _valid_page_ids = set(str(row.get('page_id', '')) for _, row in _full_df.iterrows() if row.get('page_id'))
                    except Exception:
                        _valid_page_ids = None  # can't verify, skip cleanup
                if _valid_page_ids is not None:
                    _orphan_pages = set(_chroma_page_ids.keys()) - _valid_page_ids
                    if _orphan_pages:
                        for _pid in _orphan_pages:
                            _doc_id = _chroma_page_ids[_pid]
                            try:
                                rag_service.delete_document(_doc_id)
                            except Exception:
                                pass
                        print(f"🗑️  Removed {len(_orphan_pages)} deleted Confluence page(s) from ChromaDB")
                        self._append_sync_log(f"Removed {len(_orphan_pages)} deleted Confluence page(s)")
                    else:
                        print("✅ No orphaned Confluence pages found")
            except Exception as _cleanup_err:
                print(f"⚠️  Confluence orphan cleanup failed (non-fatal): {_cleanup_err}")

            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()

            metadata = self._load_sync_metadata()
            sync_record = {
                'timestamp': start_time.isoformat(),
                'cql': cql[:80],
                'pages_fetched': len(df),
                'duration_seconds': duration,
                'status': 'success',
                'errors': [],
            }
            metadata['last_sync'] = start_time.isoformat()
            metadata['syncs'].append(sync_record)
            metadata['syncs'] = metadata['syncs'][-100:]
            metadata.pop('current_sync', None)
            self._save_sync_metadata(metadata)

            print("\n" + "=" * 60)
            print("✅ Confluence Sync Complete!")
            print("=" * 60)
            print(f"Duration: {duration:.2f}s")
            print(f"Specs synced: {len(df)}")
            print("")
            self._append_sync_log(f"Sync completed. {len(df)} specs synced.")

            return {
                'success': True,
                'pages_fetched': len(df),
                'duration_seconds': duration,
                'last_sync': start_time.isoformat(),
                'message': f'Successfully synced {len(df)} specs from Confluence',
            }

        except Exception as e:
            error_msg = str(e)
            self._append_sync_log(f"Error: {error_msg}")
            print(f"\n❌ Confluence sync failed: {error_msg}")
            metadata = self._load_sync_metadata()
            metadata['syncs'].append({
                'timestamp': start_time.isoformat(),
                'status': 'failed',
                'error': error_msg,
                'pages_fetched': 0,
            })
            metadata['syncs'] = metadata['syncs'][-100:]
            metadata.pop('current_sync', None)
            self._save_sync_metadata(metadata)
            return {
                'success': False,
                'error': error_msg,
                'message': f'Confluence sync failed: {error_msg}',
            }
        finally:
            metadata = self._load_sync_metadata()
            metadata['is_syncing'] = False
            metadata.pop('sync_started_at', None)
            metadata.pop('current_sync', None)
            self._save_sync_metadata(metadata)

    def get_sync_status(self) -> Dict[str, Any]:
        """Get Confluence sync status and metadata."""
        metadata = self._load_sync_metadata()
        metadata = self._clear_stale_sync_if_needed(metadata)
        latest_sync = metadata['syncs'][-1] if metadata.get('syncs') else None
        sync_log = metadata.get('sync_log', [])
        if not isinstance(sync_log, list):
            sync_log = []

        return {
            'last_sync': metadata.get('last_sync'),
            'is_syncing': metadata.get('is_syncing', False),
            'current_sync': metadata.get('current_sync'),
            'latest_sync_record': latest_sync,
            'sync_log': sync_log,
            'total_syncs': len(metadata.get('syncs', [])),
            'cql': (getattr(self.config, "confluence_cql", None) or os.getenv("CONFLUENCE_CQL", "type=page") or "type=page").strip() or "type=page",
        }
