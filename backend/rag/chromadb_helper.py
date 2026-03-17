"""
ChromaDB Helper Module
======================

Helper functions for ChromaDB operations including:
- Creating/loading vectorstores
- Inspecting collections
- Deleting collections
"""

import shutil
from typing import List, Optional
from pathlib import Path


class ChromaDBHelper:
    """Helper class for ChromaDB operations."""
    
    @staticmethod
    def check_documents_exist_by_file_path(vectorstore, file_path: str) -> bool:
        """
        Check if documents from a specific file path already exist in ChromaDB.
        
        Args:
            vectorstore: ChromaDB vectorstore instance
            file_path: File path to check (absolute path)
            
        Returns:
            bool: True if documents exist, False otherwise
        """
        if not vectorstore:
            return False
        
        try:
            # Normalize file path for comparison
            file_path_normalized = str(Path(file_path).absolute())
            
            # Get all documents from collection
            collection = vectorstore._collection
            results = collection.get()
            
            # Check if any document matches the file path
            if results.get('metadatas'):
                for metadata in results['metadatas']:
                    if metadata:
                        stored_path = metadata.get('file_path')
                        if stored_path:
                            # Normalize stored path for comparison
                            stored_path_normalized = str(Path(stored_path).absolute())
                            if stored_path_normalized == file_path_normalized:
                                return True
            return False
        except Exception:
            # If there's an error, assume documents don't exist to be safe
            return False
    
    @staticmethod
    def remove_documents_by_file_path(vectorstore, file_path: str, return_count: bool = False):
        """
        Remove documents from vectorstore that match a specific file path.
        
        Args:
            vectorstore: ChromaDB vectorstore instance
            file_path: File path to match (absolute path)
            return_count: If True, return count of removed documents. Default: False
            
        Returns:
            int: Count of removed documents (if return_count=True)
        """
        if not vectorstore:
            return 0 if return_count else None
        
        try:
            # Normalize file path for comparison
            file_path_normalized = str(Path(file_path).absolute())
            
            # Get all documents from collection
            collection = vectorstore._collection
            results = collection.get()
            
            # Find document IDs that match the file path
            ids_to_delete = []
            if results.get('ids') and results.get('metadatas'):
                for idx, metadata in enumerate(results['metadatas']):
                    if metadata:
                        stored_path = metadata.get('file_path')
                        if stored_path:
                            # Normalize stored path for comparison
                            stored_path_normalized = str(Path(stored_path).absolute())
                            if stored_path_normalized == file_path_normalized:
                                ids_to_delete.append(results['ids'][idx])
            
            # Delete matching documents
            if ids_to_delete:
                collection.delete(ids=ids_to_delete)
                removed_count = len(ids_to_delete)
                if not return_count:
                    print(f"🗑️  Removed {removed_count} existing document(s) for: {Path(file_path).name}")
                return removed_count if return_count else None
            return 0 if return_count else None
        except Exception as e:
            print(f"⚠️  Error removing documents: {e}")
            import traceback
            traceback.print_exc()
            return 0 if return_count else None
    
    @staticmethod
    def get_or_create_vectorstore(documents, embeddings, persist_directory: str, collection_name: str, show_log: bool = True, _retried: bool = False):
        """
        Get existing ChromaDB collection or create a new one with persistent storage.
        On ChromaDB compaction/corruption errors, removes the persist directory and retries once.

        Args:
            documents: List of Document objects to add
            embeddings: Embedding function/model
            persist_directory: Directory to persist ChromaDB data
            collection_name: Name of ChromaDB collection
            show_log: If True, print collection status. Default: True
            _retried: Internal flag to avoid infinite retry (do not set manually)

        Returns:
            ChromaDB vectorstore instance
        """
        # Import here to avoid circular dependencies
        try:
            from langchain_chroma import Chroma
        except ImportError:
            from langchain_community.vectorstores import Chroma
            import warnings
            warnings.filterwarnings("ignore", category=DeprecationWarning, module="langchain_community")

        persist_path = Path(persist_directory)
        persist_path.mkdir(parents=True, exist_ok=True, mode=0o755)

        def _is_chromadb_corruption(exc: BaseException) -> bool:
            try:
                import chromadb.errors
                if isinstance(exc, chromadb.errors.InternalError):
                    return True
            except Exception:
                pass
            return "compaction" in str(exc).lower() or "hnsw" in str(exc).lower()

        def _delete_persist_and_retry():
            if _retried:
                return None
            if show_log:
                print("⚠️ ChromaDB data appears corrupted (compaction error). Removing persist directory and retrying once...")
            try:
                if persist_path.exists():
                    shutil.rmtree(persist_path)
                persist_path.mkdir(parents=True, exist_ok=True, mode=0o755)
            except Exception as e:
                if show_log:
                    print(f"⚠️ Could not remove ChromaDB directory: {e}")
                return None
            return ChromaDBHelper.get_or_create_vectorstore(
                documents, embeddings, persist_directory, collection_name, show_log=show_log, _retried=True
            )

        try:
            existing_vectorstore = Chroma(
                persist_directory=str(persist_path),
                collection_name=collection_name,
                embedding_function=embeddings
            )
            count = existing_vectorstore._collection.count()
            if show_log:
                print(f"📂 Loaded existing ChromaDB collection '{collection_name}' ({count} documents)")
            if documents and len(documents) > 0:
                existing_vectorstore.add_documents(documents)
            return existing_vectorstore
        except Exception as e:
            if _is_chromadb_corruption(e):
                ret = _delete_persist_and_retry()
                if ret is not None:
                    return ret
            # Collection doesn't exist or create failed; create new one
            if show_log:
                print(f"📂 Creating new ChromaDB collection '{collection_name}'")
            try:
                if documents and len(documents) > 0:
                    return Chroma.from_documents(
                        documents=documents,
                        embedding=embeddings,
                        persist_directory=str(persist_path),
                        collection_name=collection_name
                    )
                return Chroma(
                    persist_directory=str(persist_path),
                    collection_name=collection_name,
                    embedding_function=embeddings
                )
            except Exception as e2:
                if _is_chromadb_corruption(e2):
                    ret = _delete_persist_and_retry()
                    if ret is not None:
                        return ret
                raise
    
    @staticmethod
    def inspect_collection(vectorstore, show_data: bool = True):
        """
        Inspect ChromaDB collection contents.
        
        Args:
            vectorstore: ChromaDB vectorstore instance
            show_data: If True, display detailed contents
        """
        if not vectorstore:
            print("❌ No documents added yet. Add documents first.")
            return
        
        if not show_data:
            return
        
        print("\n" + "=" * 80)
        print("ChromaDB Contents Inspection")
        print("=" * 80)
        
        collection = vectorstore._collection
        print(f"\n📚 Collection Name: {collection.name}")
        print(f"📊 Total Documents: {collection.count()}")
        
        results = collection.get()
        
        print("\n" + "-" * 80)
        print("📄 Stored Documents:")
        print("-" * 80)
        
        for i, (doc_id, doc_text) in enumerate(zip(results['ids'], results['documents']), 1):
            print(f"\n[{i}] Document ID: {doc_id}")
            print(f"    Content: {doc_text[:200]}...")  # Truncate long content
            
            if results.get('metadatas') and results['metadatas'][i-1]:
                print(f"    Metadata: {results['metadatas'][i-1]}")
        
        if results.get('embeddings'):
            print("\n" + "-" * 80)
            print("🔢 Embeddings Information:")
            print("-" * 80)
            print(f"    Embedding Dimension: {len(results['embeddings'][0])}")
            print(f"    Total Embeddings: {len(results['embeddings'])}")
            print(f"    Sample (first 5 values): {results['embeddings'][0][:5]}")
        else:
            print("\n💡 Note: Embeddings computed on-the-fly (not stored)")
        
        print("\n" + "=" * 80)
    
    @staticmethod
    def get_collection_data(vectorstore, limit: int = None) -> dict:
        """
        Get ChromaDB collection data as dictionary.
        
        Args:
            vectorstore: ChromaDB vectorstore instance
            limit: If set, return only this many documents (for faster initial load).
                   Total count is always returned via total_documents.
            
        Returns:
            Dictionary with collection info, documents, IDs, etc.
        """
        if not vectorstore:
            raise ValueError("No documents added yet")
        
        collection = vectorstore._collection
        total_documents = collection.count()
        if limit is not None and limit > 0:
            results = collection.get(limit=limit)
        else:
            results = collection.get()
        
        return {
            "collection_name": collection.name,
            "total_documents": total_documents,
            "document_ids": results.get('ids', []),
            "documents": results.get('documents', []),
            "metadatas": results.get('metadatas', []),
            "has_embeddings": bool(results.get('embeddings')),
            "embedding_dimension": len(results['embeddings'][0]) if results.get('embeddings') else None
        }
    
    @staticmethod
    def delete_collection(collection_name: str = None, 
                         persist_directory: str = "./chroma_db", delete_all: bool = False):
        """
        Delete ChromaDB collection(s).
        
        Args:
            collection_name: Specific collection name to delete (optional)
            persist_directory: Directory where ChromaDB persists data
            delete_all: If True, delete all collections
        """
        import chromadb
        
        if delete_all:
            try:
                persist_path = Path(persist_directory)
                if persist_path.exists():
                    client = chromadb.PersistentClient(path=str(persist_path))
                else:
                    client = chromadb.Client()
                
                collections = client.list_collections()
                
                if collections:
                    collection_names = [coll.name for coll in collections]
                    print(f"🔍 Found {len(collections)} ChromaDB collection(s): {collection_names}")
                    
                    for coll in collections:
                        try:
                            coll_name = coll.name
                            doc_count = coll.count()
                            client.delete_collection(coll_name)
                            print(f"✅ Deleted ChromaDB collection: {coll_name} ({doc_count} documents)")
                        except Exception as e:
                            print(f"⚠️  Error deleting collection {coll.name}: {e}")
                else:
                    print("ℹ️  No ChromaDB collections found to delete")
                    if persist_path.exists():
                        print(f"💡 ChromaDB directory exists at: {persist_path}")
                        print("   But no collections found. This might be a fresh installation.")
            except Exception as e:
                print(f"⚠️  Error accessing ChromaDB: {e}")
        
        elif collection_name:
            try:
                persist_path = Path(persist_directory)
                if persist_path.exists():
                    client = chromadb.PersistentClient(path=str(persist_path))
                else:
                    client = chromadb.Client()
                
                client.delete_collection(collection_name)
                print(f"✅ Deleted ChromaDB collection: {collection_name}")
            except Exception as e:
                print(f"⚠️  Error deleting collection {collection_name}: {e}")

