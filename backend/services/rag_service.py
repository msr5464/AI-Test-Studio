"""
RAG Service
===========
Service layer for RAG operations.
"""

import os
import json
import hashlib
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
import uuid

# Add parent directory to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend.rag.rag_document_loader import MultiFormatRAG
from backend.rag.rag_helper import ChromaDBHelper
from backend.rag.rag_settings import get_config
from backend.cost_tracker import record_from_langchain_result


class RAGService:
    """Service for managing RAG operations."""
    
    # Project root: backend/services/rag_service.py -> backend -> project root (same as app.py)
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

    def __init__(self):
        """Initialize RAG service with configuration."""
        # Resolve storage paths relative to project root so they are correct regardless of cwd (e.g. gunicorn)
        def _abs_path(env_key: str, default: str) -> Path:
            raw = os.getenv(env_key, default)
            p = Path(raw)
            if p.is_absolute():
                return p.resolve()
            return (self._PROJECT_ROOT / raw).resolve()
        self.storage_dir = _abs_path("STORAGE_DIR", "storage")
        self.documents_dir = _abs_path("DOCUMENTS_DIR", "storage/documents")
        self.chroma_db_dir = _abs_path("CHROMA_DB_DIR", "storage/chroma_db")
        self.embedding_cache_dir = _abs_path("EMBEDDING_CACHE_DIR", "storage/embedding_cache")
        self.metadata_file = self.storage_dir / 'documents_metadata.json'
        
        # Create directories with explicit writable mode so they're never read-only (avoids umask
        # or pre-existing dirs causing "attempt to write a readonly database" during Confluence/sync)
        _dir_mode = 0o755
        self.storage_dir.mkdir(parents=True, exist_ok=True, mode=_dir_mode)
        self.documents_dir.mkdir(parents=True, exist_ok=True, mode=_dir_mode)
        self.chroma_db_dir.mkdir(parents=True, exist_ok=True, mode=_dir_mode)
        self.embedding_cache_dir.mkdir(parents=True, exist_ok=True, mode=_dir_mode)
        # Ensure chroma_db and its contents are writable (fix if dir/files existed with restrictive permissions)
        try:
            os.chmod(self.chroma_db_dir, _dir_mode)
            for f in self.chroma_db_dir.iterdir():
                try:
                    os.chmod(f, 0o644 if f.is_file() else _dir_mode)
                except OSError:
                    pass
        except OSError:
            pass
        self._ensure_chroma_db_writable()
        
        # Get RAG configuration
        config = get_config()
        
        # Override paths in config (already absolute, project-root-relative)
        config.persist_directory = str(self.chroma_db_dir)
        config.embedding_cache_dir = str(self.embedding_cache_dir)
        
        # Initialize RAG system
        self.rag = MultiFormatRAG(config=config)
        # Lock so reload in find_related_tests does not race with concurrent RAG use (chat, etc.)
        self._vectorstore_reload_lock = threading.Lock()

        # Load existing document metadata
        self.documents: Dict[str, Dict[str, Any]] = {}
        self._load_document_metadata()
        
        # Load existing documents into RAG system
        self._load_existing_documents()
    
    def _ensure_chroma_db_writable(self):
        """Check that ChromaDB directory is writable; otherwise Confluence/sync will fail with 'readonly database' (SQLite 1032)."""
        probe = self.chroma_db_dir / ".write_probe"
        try:
            probe.write_text("")
            probe.unlink(missing_ok=True)
        except OSError as e:
            path = self.chroma_db_dir.resolve()
            raise RuntimeError(
                f"ChromaDB directory is not writable: {path}\n"
                f"Confluence/TestRail sync will fail with 'attempt to write a readonly database' (SQLite 1032).\n"
                f"Fix: ensure the process has write access, e.g. chmod -R u+rwX '{path.parent}' or fix ownership (chown).\n"
                f"Original error: {e}"
            ) from e
    
    def _load_document_metadata(self):
        """Load document metadata from persistent storage."""
        if self.metadata_file.exists():
            try:
                with open(self.metadata_file, 'r') as f:
                    self.documents = json.load(f)
                print(f"✅ Loaded {len(self.documents)} document(s) from metadata file")
            except Exception as e:
                print(f"⚠️  Failed to load document metadata: {e}")
                self.documents = {}
        else:
            self.documents = {}
    
    def _save_document_metadata(self):
        """Save document metadata to persistent storage."""
        try:
            with open(self.metadata_file, 'w') as f:
                json.dump(self.documents, f, indent=2)
        except Exception as e:
            print(f"⚠️  Failed to save document metadata: {e}")
    
    def _load_existing_documents(self):
        """Load existing documents from storage directory into RAG system."""
        from backend.rag.rag_helper import ChromaDBHelper
        
        # Ensure vectorstore is loaded for checking
        self.rag._load_vectorstore_if_needed()
        
        existing_files = []
        skipped_files = []
        
        for doc_info in self.documents.values():
            doc_path = Path(doc_info['path'])
            if doc_path.exists():
                # Check if documents from this file already exist in ChromaDB
                if self.rag.vectorstore:
                    file_exists = ChromaDBHelper.check_documents_exist_by_file_path(
                        self.rag.vectorstore, str(doc_path)
                    )
                    if file_exists:
                        skipped_files.append(doc_path)
                        continue
                existing_files.append(doc_path)
        
        if skipped_files:
            print(f"⏭️  Skipping {len(skipped_files)} document(s) already in ChromaDB:")
            for skipped_file in skipped_files:
                print(f"   - {skipped_file.name}")
        
        if existing_files:
            print(f"📚 Loading {len(existing_files)} existing document(s) into RAG system...")
            try:
                self.rag.add_files(existing_files, replace_if_exists=False)
                print(f"✅ Loaded {len(existing_files)} document(s) into RAG system")
            except Exception as e:
                print(f"⚠️  Failed to load some documents: {e}")
        elif not skipped_files:
            print("ℹ️  No existing documents to load")
        
        # Ensure retriever is initialized if vectorstore exists (so Ask works even with 0 docs)
        if self.rag.vectorstore and not self.rag.retriever:
            try:
                count = self.rag.vectorstore._collection.count()
                self.rag.retriever = self.rag.vectorstore.as_retriever(
                    search_kwargs={"k": self.rag.retrieval_k}
                )
                if count > 0 and self.rag.use_hybrid_search:
                    self.rag._init_keyword_retriever()
                print(f"✅ Initialized retriever from ChromaDB ({count} documents)")
            except Exception as e:
                print(f"⚠️  Failed to initialize retriever: {e}")
    
    def _find_existing_document(self, file_name: str) -> Optional[str]:
        """Find existing document by filename (case-insensitive)."""
        file_name_lower = file_name.lower()
        for doc_id, doc_info in self.documents.items():
            if doc_info['name'].lower() == file_name_lower:
                return doc_id
        return None
    
    def _validate_testcase_file(self, file_path: Path) -> tuple[bool, str]:
        """
        Validate if the file has the required testcase structure.
        
        Args:
            file_path: Path to the file to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            import pandas as pd
            
            # Only validate CSV and Excel files
            file_ext = file_path.suffix.lower()
            if file_ext not in ['.csv', '.xlsx', '.xls']:
                return False, f"Only CSV and Excel files are supported. Got: {file_ext}"
            
            # Load the file
            if file_ext == '.csv':
                df = pd.read_csv(file_path)
            else:
                df = pd.read_excel(file_path)
            
            # Check for required testcase headers
            target_headers = {
                'id', 'title', 'execution mode', 'expected result', 
                'platform', 'preconditions', 'priority', 
                'section hierarchy', 'steps', 'type'
            }
            
            columns = {str(col).lower().strip() for col in df.columns}
            matched = columns.intersection(target_headers)
            
            # Must have at least 70% of target headers (7 out of 10)
            if len(matched) < 7:
                missing = target_headers - columns
                return False, f"File does not match testcase structure. Missing or incorrect headers. Expected headers like: {', '.join(list(target_headers)[:5])}..."
            
            return True, ""
            
        except Exception as e:
            return False, f"Failed to validate file: {str(e)}"

    def _validate_specs_file(self, file_path: Path) -> tuple[bool, str]:
        """
        Validate if the file has the required specs structure (page_id, title, body).

        Args:
            file_path: Path to the file to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            import pandas as pd

            file_ext = file_path.suffix.lower()
            if file_ext != '.csv':
                return False, f"Specs documents must be CSV. Got: {file_ext}"

            df = pd.read_csv(file_path)
            required_headers = {'page_id', 'title', 'body'}
            columns = {str(col).lower().strip() for col in df.columns}
            if not required_headers.issubset(columns):
                missing = required_headers - columns
                return False, f"Specs CSV missing required columns: {', '.join(missing)}"

            return True, ""

        except Exception as e:
            return False, f"Failed to validate specs file: {str(e)}"

    def upload_specs_document(self, file_path: Path, file_name: str, subdir: Optional[str] = None) -> Dict[str, Any]:
        """
        Upload and process a specs document (e.g., Confluence sync output).
        Uses label "specs" in ChromaDB metadata. Replaces existing document with same filename.

        Args:
            file_path: Path to uploaded file
            file_name: Original file name
            subdir: Optional subdirectory under documents_dir (e.g. "confluence") for segregation

        Returns:
            Document metadata
        """
        is_valid, error_msg = self._validate_specs_file(file_path)
        if not is_valid:
            return {
                'success': False,
                'error': error_msg,
                'message': f'Specs validation failed: {error_msg}'
            }

        existing_doc_id = self._find_existing_document(file_name)

        if existing_doc_id:
            print(f"🔄 Replacing existing specs document: {file_name}")
            delete_result = self.delete_document(existing_doc_id)
            if not delete_result['success']:
                return {
                    'success': False,
                    'error': f"Failed to delete existing document: {delete_result.get('error', 'Unknown error')}",
                    'message': f'Failed to replace document {file_name}'
                }

        doc_id = str(uuid.uuid4())
        if subdir:
            target_dir = self.documents_dir / subdir.strip().strip('/')
            target_dir.mkdir(parents=True, exist_ok=True)
            saved_path = target_dir / f"{doc_id}_{file_name}"
        else:
            saved_path = self.documents_dir / f"{doc_id}_{file_name}"

        import shutil
        shutil.copy2(file_path, saved_path)

        try:
            self.rag.add_files([saved_path], replace_if_exists=True)

            self.documents[doc_id] = {
                'id': doc_id,
                'name': file_name,
                'path': str(saved_path),
                'uploaded_at': datetime.now().isoformat(),
                'status': 'processed',
                'doc_type': 'specs'
            }

            self._save_document_metadata()

            return {
                'success': True,
                'document_id': doc_id,
                'message': f'Specs document {file_name} uploaded successfully',
                'replaced': existing_doc_id is not None
            }
        except Exception as e:
            if saved_path.exists():
                saved_path.unlink()
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to process specs document {file_name}'
            }

    def upload_document(self, file_path: Path, file_name: str, subdir: Optional[str] = None) -> Dict[str, Any]:
        """
        Upload and process a document.
        If a document with the same filename exists, it will be replaced.

        Args:
            file_path: Path to uploaded file
            file_name: Original file name
            subdir: Optional subdirectory under documents_dir (e.g. "testrail") for segregation

        Returns:
            Document metadata
        """
        # Validate file structure first
        is_valid, error_msg = self._validate_testcase_file(file_path)
        if not is_valid:
            return {
                'success': False,
                'error': error_msg,
                'message': f'File validation failed: {error_msg}'
            }
        
        # Check if document with same filename already exists
        existing_doc_id = self._find_existing_document(file_name)
        
        if existing_doc_id:
            # Delete existing document first
            print(f"🔄 Replacing existing document: {file_name}")
            delete_result = self.delete_document(existing_doc_id)
            if not delete_result['success']:
                return {
                    'success': False,
                    'error': f"Failed to delete existing document: {delete_result.get('error', 'Unknown error')}",
                    'message': f'Failed to replace document {file_name}'
                }
        
        # Generate new document ID
        doc_id = str(uuid.uuid4())
        
        # Save file to documents directory (optionally under subdir for connector segregation)
        if subdir:
            target_dir = self.documents_dir / subdir.strip().strip('/')
            target_dir.mkdir(parents=True, exist_ok=True)
            saved_path = target_dir / f"{doc_id}_{file_name}"
        else:
            saved_path = self.documents_dir / f"{doc_id}_{file_name}"
        
        # Copy file
        import shutil
        shutil.copy2(file_path, saved_path)
        
        # Add to RAG system
        try:
            self.rag.add_files([saved_path], replace_if_exists=True)
            
            # Track document
            self.documents[doc_id] = {
                'id': doc_id,
                'name': file_name,
                'path': str(saved_path),
                'uploaded_at': datetime.now().isoformat(),
                'status': 'processed'
            }
            
            # Save metadata persistently
            self._save_document_metadata()
            self._clear_all_query_caches()

            message = f'Document {file_name} uploaded and processed successfully'
            if existing_doc_id:
                message = f'Document {file_name} replaced successfully'
            
            return {
                'success': True,
                'document_id': doc_id,
                'message': message,
                'replaced': existing_doc_id is not None
            }
        except Exception as e:
            # Remove file if processing failed
            if saved_path.exists():
                saved_path.unlink()
            
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to process document {file_name}'
            }

    def add_document_file(
        self, file_path: Path, file_name: str, subdir: Optional[str] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Add a raw file (e.g. .md, .txt) to the knowledge base without CSV/testcase validation.
        Used for Confluence sync (Markdown per page or per batch).

        Args:
            file_path: Path to the file
            file_name: Logical name (e.g. confluence_123.md, confluence_batch_0.md)
            subdir: Optional subdirectory (e.g. "confluence") for segregation
            extra_metadata: Extra key/value pairs to store on every chunk (e.g. {"source_type": "specs"})

        Returns:
            Dict with success, document_id, message
        """
        existing_doc_id = self._find_existing_document(file_name)
        if existing_doc_id:
            delete_result = self.delete_document(existing_doc_id)
            if not delete_result['success']:
                return {
                    'success': False,
                    'error': delete_result.get('error', 'Failed to replace existing'),
                    'message': f'Failed to replace existing document {file_name}',
                }
        doc_id = str(uuid.uuid4())
        if subdir:
            target_dir = self.documents_dir / subdir.strip().strip('/')
            target_dir.mkdir(parents=True, exist_ok=True)
            saved_path = target_dir / f"{doc_id}_{file_name}"
        else:
            saved_path = self.documents_dir / f"{doc_id}_{file_name}"
        import shutil
        shutil.copy2(file_path, saved_path)
        try:
            self.rag.add_files([saved_path], replace_if_exists=True)
            # Retroactively tag all chunks from this file with extra_metadata (e.g. source_type: specs)
            if extra_metadata and self.rag.vectorstore:
                try:
                    col = self.rag.vectorstore._collection
                    res = col.get(where={"file_path": str(saved_path)}, include=["metadatas"])
                    ids = res.get("ids") or []
                    if ids:
                        metas = res.get("metadatas") or [{}] * len(ids)
                        updated = [{**(m or {}), **extra_metadata} for m in metas]
                        col.update(ids=ids, metadatas=updated)
                except Exception as _me:
                    print(f"⚠️  add_document_file: failed to apply extra_metadata: {_me}")
            self.documents[doc_id] = {
                'id': doc_id,
                'name': file_name,
                'path': str(saved_path),
                'uploaded_at': datetime.now().isoformat(),
                'status': 'processed',
                'doc_type': 'file',
            }
            self._save_document_metadata()
            self._clear_all_query_caches()
            return {
                'success': True,
                'document_id': doc_id,
                'message': f'Added {file_name}',
                'replaced': existing_doc_id is not None,
            }
        except Exception as e:
            if saved_path.exists():
                saved_path.unlink()
            return {
                'success': False,
                'error': str(e),
                'message': f'Failed to add file {file_name}',
            }

    def _get_fresh_vectorstore_from_disk(self):
        """
        Create a new Chroma connection to the persisted collection (no temp doc added).
        Ensures we see the latest data after another process/worker has ingested.
        Uses the same absolute path as init so ingest and retrieval share the same ChromaDB.
        Returns the vectorstore or None if not available.
        """
        try:
            persist_dir = str(self.chroma_db_dir)
            collection_name = getattr(self.rag, "collection_name", None) or "rag_collection"
            embeddings = getattr(self.rag, "embeddings", None)
            if not embeddings:
                return None
            return ChromaDBHelper.get_or_create_vectorstore(
                [], embeddings, persist_dir, collection_name, show_log=False
            )
        except Exception as e:
            print(f"⚠️  Fresh vectorstore load failed: {e}")
            return None

    def invalidate_vectorstore_for_reload(self):
        """
        Clear in-memory vectorstore/retriever so the next read (e.g. find_related_tests)
        loads fresh from disk. Call after ingesting a new document (e.g. pushed test case).
        """
        with self._vectorstore_reload_lock:
            self.rag.vectorstore = None
            self.rag.retriever = None

    def load_fresh_vectorstore_once(self):
        """
        Load and return a fresh vectorstore from disk once.
        Callers can pass the returned object to find_related_tests() to avoid
        repeated disk reads when iterating over many requirements.
        Returns None if unavailable.
        """
        with self._vectorstore_reload_lock:
            vs = self._get_fresh_vectorstore_from_disk()
            if vs:
                return vs
            self.rag._load_vectorstore_if_needed()
            return self.rag.vectorstore

    def _clear_all_query_caches(self):
        """Clear query caches in the main RAG and all child RAGs to prevent stale answers."""
        if hasattr(self.rag, 'query_cache') and self.rag.query_cache is not None:
            self.rag.query_cache.clear()
        for child_attr in ('_pdf_rag', '_csv_excel_rag', '_text_rag'):
            child = getattr(self.rag, child_attr, None)
            if child and hasattr(child, 'query_cache') and child.query_cache is not None:
                child.query_cache.clear()

    def find_related_tests(self, requirement_text: str, k: int = 10, vectorstore=None) -> List[Dict[str, Any]]:
        """
        Find test cases in ChromaDB that are semantically related to a requirement.
        Uses the same retrieval pipeline as Chat (hybrid, reranking, MIN_SIMILARITY_THRESHOLD).
        Restricts retrieval to testcase chunks (source_type == "testcase").
        Always uses a fresh Chroma connection to disk so recently ingested (pushed) tests are visible.

        Args:
            requirement_text: Requirement description/title to search for
            k: Max number of chunks to retrieve
            vectorstore: Optional pre-loaded vectorstore to reuse (avoids repeated disk reads when
                         calling in a loop over many requirements — see load_fresh_vectorstore_once()).

        Returns:
            List of dicts: [{"testrail_id": "C123", "title": "...", "content": "...", "similarity_score": 0.85}, ...]
        """
        # Hold lock only for vectorstore swap (microseconds), not during retrieval.
        # This allows concurrent find_related_tests calls from parallel requirement threads.
        with self._vectorstore_reload_lock:
            if vectorstore is not None:
                self.rag.vectorstore = vectorstore
                self.rag.retriever = None
            else:
                fresh_vs = self._get_fresh_vectorstore_from_disk()
                if not fresh_vs:
                    self.rag._load_vectorstore_if_needed()
                else:
                    self.rag.vectorstore = fresh_vs
                    self.rag.retriever = None
            if not self.rag.vectorstore:
                return []

        # Retrieval runs outside the lock — read-only operations are thread-safe
        try:
            config = get_config()
            metadata_filter = {"source_type": "testcase"}
            doc_score_pairs = self.rag.retrieve_documents_with_scores(
                requirement_text,
                k=config.requirement_retrieval_k,
                metadata_filter=metadata_filter,
                min_similarity_threshold_override=config.requirement_tests_similarity_threshold,
                use_hybrid_search_override=config.requirement_use_hybrid_search,
                use_reranking_override=config.requirement_use_reranking,
            )
        except Exception as e:
            print(f"⚠️  find_related_tests failed: {e}")
            return []

        results = []
        seen_ids = set()
        for doc, similarity_score in doc_score_pairs:
            meta = doc.metadata or {}
            testrail_id = meta.get("testrail_id") or meta.get("id") or ""
            if not testrail_id and "Testrail Id:" in doc.page_content:
                for line in doc.page_content.split("\n"):
                    if line.strip().lower().startswith("testrail id:"):
                        testrail_id = line.split(":", 1)[-1].strip()
                        break
            if testrail_id and testrail_id in seen_ids:
                continue
            if testrail_id:
                seen_ids.add(testrail_id)
            title = meta.get("title", "")
            if not title and "Title:" in doc.page_content:
                for line in doc.page_content.split("\n"):
                    if line.strip().startswith("Title:"):
                        title = line.split(":", 1)[-1].strip()
                        break
            if not title:
                title = doc.page_content[:100].replace("\n", " ")
            priority = (meta.get("priority") or "").strip().upper()
            if not priority and doc.page_content:
                for line in doc.page_content.split("\n"):
                    if line.strip().lower().startswith("priority:"):
                        priority = line.split(":", 1)[-1].strip().upper()
                        break
            if priority and priority not in ("P0", "P1", "P2", "P3"):
                priority = ""  # normalize to P0/P1/P2/P3 or empty
            item = {
                "testrail_id": testrail_id or "N/A",
                "title": title,
                "content": doc.page_content,
                "similarity_score": round(similarity_score, 4) if similarity_score is not None else None,
                "priority": priority or None,
                "case_type": meta.get("case_type", ""),
            }
            if meta.get("preconditions") is not None:
                item["preconditions"] = meta.get("preconditions") or ""
            if meta.get("steps") is not None:
                item["steps"] = meta.get("steps") or ""
            if meta.get("expected_result") is not None:
                item["expected_result"] = meta.get("expected_result") or ""
            results.append(item)

        # Order by score (high at top, low at bottom); None scores last; tie-break by TestRail ID descending
        def _testrail_id_numeric(tid: str) -> int:
            tid = (tid or "").strip().upper()
            if not tid or tid == "N/A":
                return -1
            digits = "".join(c for c in tid if c.isdigit())
            return int(digits) if digits else -1

        def _score_sort_key(item: Dict[str, Any]) -> tuple:
            s = item.get("similarity_score")
            # (False, -score) for real scores so non-None first and higher score first; (True, 0) for None last
            none_last = (1 if s is None else 0, -(s if s is not None else 0))
            tid_num = _testrail_id_numeric(item.get("testrail_id") or "")
            return (none_last[0], none_last[1], -tid_num)

        results.sort(key=_score_sort_key)
        return results

    def find_related_specs(self, spec_or_requirement_text: str, k: int = 10) -> List[Dict[str, Any]]:
        """
        Find Confluence/specs chunks in ChromaDB that are semantically related to the given text.
        Uses the same retrieval pipeline as Chat (hybrid, reranking, MIN_SIMILARITY_THRESHOLD).
        Used as prior requirement-doc context for requirement analysis (feature/spec context).
        Restricts retrieval to specs chunks (source_type == "specs").

        Args:
            spec_or_requirement_text: New requirement text or full spec to find related prior docs for
            k: Max number of chunks to retrieve

        Returns:
            List of dicts: [{"title": "...", "content": "...", "url": "...", "similarity_score": 0.85}, ...]
        """
        self.rag._load_vectorstore_if_needed()
        if not self.rag.vectorstore:
            return []

        try:
            config = get_config()
            metadata_filter = {"source_type": "specs"}
            # Confluence spec pages use broader business language, so use a lower similarity threshold
            # than for TestRail test cases (requirement_specs_similarity_threshold, default 50%).
            specs_threshold = getattr(config, "requirement_specs_similarity_threshold", 50.0)
            doc_score_pairs = self.rag.retrieve_documents_with_scores(
                spec_or_requirement_text,
                k=config.requirement_retrieval_k,
                metadata_filter=metadata_filter,
                min_similarity_threshold_override=specs_threshold,
                use_hybrid_search_override=config.requirement_use_hybrid_search,
                use_reranking_override=config.requirement_use_reranking,
            )
        except Exception as e:
            print(f"⚠️  find_related_specs failed: {e}")
            return []

        results = []
        for doc, similarity_score in doc_score_pairs:
            meta = doc.metadata or {}
            title = meta.get("title", "")
            if not title and ("Title:" in doc.page_content or "Spec (" in doc.page_content):
                for line in doc.page_content.split("\n"):
                    if line.strip().startswith("Title:"):
                        title = line.split(":", 1)[-1].strip()
                        break
                    if "Spec (Page ID:" in line:
                        title = line.replace("Spec (Page ID:", "").strip().rstrip(")")
                        break
            if not title:
                title = doc.page_content[:80].replace("\n", " ").strip()
            url = meta.get("url", "")
            results.append({
                "title": title,
                "content": doc.page_content,
                "url": url,
                "page_id": meta.get("page_id", ""),
                "similarity_score": round(similarity_score, 4) if similarity_score is not None else None,
            })
        return results

    def query(self, question: str, session_id: Optional[str] = None, bypass_cache: bool = False, use_rag: bool = True) -> Dict[str, Any]:
        """
        Query the RAG system or LLM directly.
        
        Args:
            question: User question
            session_id: Optional session ID for conversation context
            bypass_cache: If True, skip cache and force fresh LLM query
            use_rag: If True, use RAG with documents; If False, query LLM directly
            
        Returns:
            Query result
        """
        try:
            if use_rag:
                result = self.rag.query(question, bypass_cache=bypass_cache)
                
                return {
                    'success': True,
                    'answer': result.get('answer', ''),
                    'sources': result.get('sources', []),
                    'source_documents': result.get('source_documents', []),
                    'query_time_ms': result.get('query_time_ms', 0),
                    'cache_hit': result.get('cache_hit', False),
                    'mode': 'rag'
                }
            else:
                # Direct LLM query without RAG
                import time
                start_time = time.time()
                
                from langchain_core.prompts import ChatPromptTemplate
                from backend.rag.rag_helper import extract_answer_from_llm_result
                
                # Direct LLM system message (no context)
                DIRECT_LLM_MESSAGE = """You are a helpful AI assistant. Answer questions directly and clearly based on your knowledge.

Instructions:
- Provide direct, clear, and accurate answers
- Use markdown formatting for better readability
- Format lists using numbered lists (1., 2., 3.) or bullet points
- Be concise but thorough
- If you don't know something, say so directly without disclaimers"""
                
                llm = self.rag.llm
                if not llm:
                    return {
                        'success': False,
                        'error': 'LLM not available',
                        'message': 'LLM is not configured'
                    }
                
                prompt = ChatPromptTemplate.from_messages([
                    ("system", DIRECT_LLM_MESSAGE),
                    ("human", "{question}")
                ])
                
                chain = prompt | llm
                result = chain.invoke({"question": question})
                record_from_langchain_result("rag.direct_query", result)
                answer = extract_answer_from_llm_result(result)
                
                query_time_ms = int((time.time() - start_time) * 1000)
                
                return {
                    'success': True,
                    'answer': answer,
                    'sources': [],
                    'source_documents': [],
                    'query_time_ms': query_time_ms,
                    'cache_hit': False,
                    'mode': 'direct_llm'
                }
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'message': 'Failed to process query'
            }
    
    def list_documents(self) -> List[Dict[str, Any]]:
        """List all uploaded documents."""
        return list(self.documents.values())
    
    def delete_documents_by_name_prefix(self, prefix: str) -> Dict[str, Any]:
        """
        Delete all documents whose name starts with the given prefix (e.g. "confluence_").
        Used to clear all Confluence sync docs before re-sync (one file per page).

        Args:
            prefix: Filename prefix (case-sensitive; e.g. "confluence_")

        Returns:
            Dict with success, deleted_count, errors list
        """
        to_delete = [
            doc_id for doc_id, info in self.documents.items()
            if info.get('name', '').startswith(prefix)
        ]
        deleted = 0
        errors = []
        for doc_id in to_delete:
            result = self.delete_document(doc_id)
            if result.get('success'):
                deleted += 1
            else:
                errors.append(f"{doc_id}: {result.get('error', 'Unknown error')}")
        return {
            'success': len(errors) == 0,
            'deleted_count': deleted,
            'errors': errors,
        }

    def delete_document(self, doc_id: str) -> Dict[str, Any]:
        """
        Delete a document.
        
        Args:
            doc_id: Document ID
            
        Returns:
            Deletion result
        """
        if doc_id not in self.documents:
            return {
                'success': False,
                'error': 'Document not found'
            }
        
        doc_info = self.documents[doc_id]
        doc_path = Path(doc_info['path'])
        
        try:
            # Remove from RAG system (remove documents by file path)
            if doc_path.exists() and self.rag.vectorstore:
                try:
                    from backend.rag.rag_helper import ChromaDBHelper
                    removed_count = ChromaDBHelper.remove_documents_by_file_path(
                        self.rag.vectorstore, 
                        str(doc_path),
                        return_count=True
                    )
                    if removed_count > 0:
                        print(f"🗑️  Removed {removed_count} document chunk(s) from ChromaDB")
                except Exception as e:
                    print(f"⚠️  Failed to remove from ChromaDB: {e}")
            
            # Remove file
            if doc_path.exists():
                doc_path.unlink()
            
            # Remove from tracking
            del self.documents[doc_id]
            
            # Save metadata persistently
            self._save_document_metadata()
            self._clear_all_query_caches()

            return {
                'success': True,
                'message': f'Document {doc_info["name"]} deleted successfully'
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'message': 'Failed to delete document'
            }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get system statistics."""
        try:
            doc_count = self.rag.vectorstore._collection.count() if self.rag.vectorstore else 0
            
            return {
                'success': True,
                'total_documents': len(self.documents),
                'total_chunks': doc_count,
                'rag_config': {
                    'use_hybrid_search': self.rag.use_hybrid_search,
                    'use_reranking': self.rag.use_reranking,
                    'enable_query_cache': self.rag.enable_query_cache,
                    'enable_embedding_cache': self.rag.enable_embedding_cache
                }
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_chromadb_contents(self, limit: int = None) -> Dict[str, Any]:
        """
        Get ChromaDB collection contents.
        
        Args:
            limit: If set, only fetch this many chunks (for faster initial load).
                   total_chunks still reflects full count.
        
        Returns:
            ChromaDB data including documents, metadata, and collection info
        """
        try:
            if not self.rag.vectorstore:
                return {
                    'success': True,
                    'collection_name': None,
                    'total_chunks': 0,
                    'chunks': [],
                    'message': 'No documents in ChromaDB yet'
                }
            
            chromadb_data = self.rag.get_chromadb_data(limit=limit)
            
            # Format chunks for display
            chunks = []
            for idx, (doc_id, doc_text, metadata) in enumerate(zip(
                chromadb_data.get('document_ids', []),
                chromadb_data.get('documents', []),
                chromadb_data.get('metadatas', [])
            )):
                file_path = metadata.get('file_path', 'N/A') if metadata else 'N/A'
                # Extract document ID from file path (format: {doc_id}_{filename})
                doc_id_from_path = None
                if file_path != 'N/A':
                    file_name = Path(file_path).name
                    if '_' in file_name:
                        doc_id_from_path = file_name.split('_')[0]
                    # Also try to find document by matching file path
                    for stored_doc_id, doc_info in self.documents.items():
                        if doc_info.get('path') == file_path or doc_info.get('name') in file_name:
                            doc_id_from_path = stored_doc_id
                            break
                
                chunks.append({
                    'id': doc_id,
                    'index': idx + 1,
                    'content': doc_text[:500] + '...' if len(doc_text) > 500 else doc_text,
                    'content_full': doc_text,
                    'content_length': len(doc_text),
                    'metadata': metadata or {},
                    'file_path': file_path,
                    'source': metadata.get('source', 'N/A') if metadata else 'N/A',
                    'document_id': doc_id_from_path  # Document ID for download link
                })
            
            return {
                'success': True,
                'collection_name': chromadb_data.get('collection_name', 'N/A'),
                'total_chunks': chromadb_data.get('total_documents', 0),
                'has_embeddings': chromadb_data.get('has_embeddings', False),
                'embedding_dimension': chromadb_data.get('embedding_dimension'),
                'chunks': chunks
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'message': 'Failed to retrieve ChromaDB contents'
            }
    
    def reset_chromadb(self, delete_all: bool = True) -> Dict[str, Any]:
        """
        Reset ChromaDB by deleting all collections and storage files.
        
        Deletes:
        - All ChromaDB collections (vector data)
        - All files in storage/documents
        - All files in storage/chroma_db (except chroma.sqlite3 related files)
        - All files in storage/embedding_cache
        - Document metadata file
        
        Args:
            delete_all: If True, deletes all collections. Default: True
            
        Returns:
            Reset result with counts of deleted items
        """
        try:
            import shutil
            
            # Delete ChromaDB collections
            self.rag.delete_chromadb(delete_all=delete_all)
            
            # Clear query cache to prevent cached answers from being returned after reset
            if hasattr(self.rag, 'query_cache') and self.rag.query_cache is not None:
                self.rag.query_cache.clear()
                print("✅ Cleared query cache")
            
            # Ensure vectorstore is completely cleared in MultiFormatRAG and child RAGs
            if hasattr(self.rag, '_pdf_rag'):
                self.rag._pdf_rag.vectorstore = None
                self.rag._pdf_rag.retriever = None
                self.rag._pdf_rag.all_documents = []
                # Clear query cache in child RAGs if they have it
                if hasattr(self.rag._pdf_rag, 'query_cache') and self.rag._pdf_rag.query_cache is not None:
                    self.rag._pdf_rag.query_cache.clear()
            if hasattr(self.rag, '_csv_excel_rag'):
                self.rag._csv_excel_rag.vectorstore = None
                self.rag._csv_excel_rag.retriever = None
                self.rag._csv_excel_rag.all_documents = []
                # Clear query cache in child RAGs if they have it
                if hasattr(self.rag._csv_excel_rag, 'query_cache') and self.rag._csv_excel_rag.query_cache is not None:
                    self.rag._csv_excel_rag.query_cache.clear()
            if hasattr(self.rag, '_text_rag'):
                self.rag._text_rag.vectorstore = None
                self.rag._text_rag.retriever = None
                self.rag._text_rag.all_documents = []
                # Clear query cache in child RAGs if they have it
                if hasattr(self.rag._text_rag, 'query_cache') and self.rag._text_rag.query_cache is not None:
                    self.rag._text_rag.query_cache.clear()
            
            deleted_counts = {
                'documents': 0,
                'chroma_db_files': 0,
                'embedding_cache_files': 0
            }
            
            # Delete all files in documents directory (including subdirs e.g. testrail/, confluence/)
            if self.documents_dir.exists():
                for item in self.documents_dir.iterdir():
                    try:
                        if item.is_file():
                            item.unlink()
                            deleted_counts['documents'] += 1
                        elif item.is_dir():
                            for f in item.rglob('*'):
                                if f.is_file():
                                    f.unlink()
                                    deleted_counts['documents'] += 1
                            shutil.rmtree(item)
                    except Exception as e:
                        print(f"⚠️  Failed to delete {item}: {e}")
            
            # Delete files in chroma_db directory, but preserve chroma.sqlite3 related files
            # ChromaDB uses chroma.sqlite3 as its main database file - we should NOT delete it
            # However, we can delete collection-specific data files if they exist
            if self.chroma_db_dir.exists():
                for item in self.chroma_db_dir.iterdir():
                    if item.is_file():
                        # Preserve chroma.sqlite3 and related files (chroma.sqlite3-shm, chroma.sqlite3-wal)
                        if 'chroma.sqlite3' in item.name:
                            continue  # Skip chroma.sqlite3 related files
                        try:
                            item.unlink()
                            deleted_counts['chroma_db_files'] += 1
                        except Exception as e:
                            print(f"⚠️  Failed to delete {item}: {e}")
                    elif item.is_dir():
                        # Delete subdirectories (collections)
                        try:
                            shutil.rmtree(item)
                            deleted_counts['chroma_db_files'] += 1
                        except Exception as e:
                            print(f"⚠️  Failed to delete directory {item}: {e}")
            
            # Delete all files in embedding_cache directory
            if self.embedding_cache_dir.exists():
                for item in self.embedding_cache_dir.iterdir():
                    try:
                        if item.is_file():
                            item.unlink()
                            deleted_counts['embedding_cache_files'] += 1
                        elif item.is_dir():
                            shutil.rmtree(item)
                            deleted_counts['embedding_cache_files'] += 1
                    except Exception as e:
                        print(f"⚠️  Failed to delete {item}: {e}")
            
            # Clear document metadata
            self.documents = {}
            if self.metadata_file.exists():
                try:
                    self.metadata_file.unlink()
                except Exception as e:
                    print(f"⚠️  Failed to delete metadata file: {e}")
            
            # Build success message
            message_parts = []
            if deleted_counts['documents'] > 0:
                message_parts.append(f"{deleted_counts['documents']} document file(s)")
            if deleted_counts['chroma_db_files'] > 0:
                message_parts.append(f"{deleted_counts['chroma_db_files']} ChromaDB file(s)")
            if deleted_counts['embedding_cache_files'] > 0:
                message_parts.append(f"{deleted_counts['embedding_cache_files']} embedding cache file(s)")
            
            # Add query cache clearing to message
            cache_cleared = hasattr(self.rag, 'query_cache') and self.rag.query_cache is not None
            if cache_cleared:
                message_parts.append("query cache")
            
            if message_parts:
                message = f'Database reset successfully. Deleted {", ".join(message_parts)} and all vector data.'
            else:
                message = 'Database reset successfully. All storage cleared.'
            
            total_deleted = deleted_counts['documents'] + deleted_counts['chroma_db_files'] + deleted_counts['embedding_cache_files']
            
            return {
                'success': True,
                'message': message,
                'deleted_files': total_deleted,
                'details': deleted_counts
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'message': 'Failed to reset database'
            }

