"""
RAG Engine
==========

Core RAG engine: base class with embeddings, LLM, retrieval, and query
functionality shared across different document types.
"""

import hashlib
import os
import time
from typing import List, Optional, Dict, Any, Tuple
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ===== CENTRALIZED IMPORT HANDLING WITH FALLBACKS =====
# (Merged from rag_imports.py for better organization)
try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from langchain_openai import OpenAIEmbeddings, ChatOpenAI
    from langchain_core.documents import Document
    from langchain_core.prompts import ChatPromptTemplate
    
    # ChromaDB import with fallback
    try:
        from langchain_chroma import Chroma
    except ImportError:
        from langchain_community.vectorstores import Chroma
        import warnings
        warnings.filterwarnings("ignore", category=DeprecationWarning, module="langchain_community")
    
    # BM25 and Ensemble retrievers
    try:
        from langchain_community.retrievers import BM25Retriever
        BM25_AVAILABLE = True
        try:
            from langchain.retrievers import EnsembleRetriever
            ENSEMBLE_AVAILABLE = True
        except ImportError:
            ENSEMBLE_AVAILABLE = False
    except ImportError:
        BM25_AVAILABLE = False
        ENSEMBLE_AVAILABLE = False
    
    # CrossEncoder for re-ranking
    try:
        from sentence_transformers import CrossEncoder
        RERANKER_AVAILABLE = True
    except ImportError:
        RERANKER_AVAILABLE = False
        CrossEncoder = None  # Make it available even if import fails
    
    # Ollama support
    try:
        from langchain_ollama import ChatOllama
        OLLAMA_AVAILABLE = True
        USE_NEW_OLLAMA = True
    except ImportError:
        try:
            from langchain_community.chat_models import ChatOllama
            OLLAMA_AVAILABLE = True
            USE_NEW_OLLAMA = False
        except ImportError:
            OLLAMA_AVAILABLE = False
            USE_NEW_OLLAMA = False
    
    LANGCHAIN_AVAILABLE = True
except ImportError as e:
    LANGCHAIN_AVAILABLE = False
    OLLAMA_AVAILABLE = False
    BM25_AVAILABLE = False
    ENSEMBLE_AVAILABLE = False
    RERANKER_AVAILABLE = False
    USE_NEW_OLLAMA = False
    print(f"⚠️  Import error: {e}")

try:
    from .rag_helper import (
        import_chromadb_helper, SimpleMemory, calculate_similarity, deduplicate_documents,
        sanitize_documents, calculate_file_hash, calculate_content_hash, add_file_metadata_to_documents,
        debug_log, RAG_SYSTEM_MESSAGE, extract_answer_from_llm_result, expand_query, extract_cited_sources,
        rerank_documents
    )
except ImportError:
    from backend.rag.rag_helper import (
        import_chromadb_helper, SimpleMemory, calculate_similarity, deduplicate_documents,
        sanitize_documents, calculate_file_hash, calculate_content_hash, add_file_metadata_to_documents,
        debug_log, RAG_SYSTEM_MESSAGE, extract_answer_from_llm_result, expand_query, extract_cited_sources,
        rerank_documents
    )

# Try to import caching modules
try:
    from .rag_caching import QueryCache, EmbeddingCache, CachedEmbeddings
    CACHING_AVAILABLE = True
except ImportError:
    from backend.rag.rag_caching import QueryCache, EmbeddingCache, CachedEmbeddings
    CACHING_AVAILABLE = True


class BaseRAG:
    """Base RAG class with common functionality."""
    
    def __init__(self, use_local_embeddings: bool = True, 
                 show_matching_sources: bool = True,
                 min_similarity_threshold: float = 30.0,
                 chunk_size: int = 1000, chunk_overlap: int = 200, retrieval_k: int = 3,
                 enable_memory: bool = False, memory_type: str = 'buffer',
                 use_hybrid_search: bool = False, use_reranking: bool = False,
                 use_query_expansion: bool = False, hybrid_weights: List[float] = None,
                 reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
                 persist_directory: str = "./chroma_db", collection_name: str = "rag_collection",
                 debug_mode: bool = False, config: Optional[object] = None,
                 enable_query_cache: bool = False, query_cache_size: int = 1000,
                 enable_embedding_cache: bool = False, embedding_cache_dir: str = "./embedding_cache"):
        """
        Initialize RAG system.
        
        Args:
            use_local_embeddings: Use local HuggingFace embeddings instead of OpenAI
            show_matching_sources: If True, print matching sources after each query
            min_similarity_threshold: Minimum similarity percentage (0-100) to include a source
            chunk_size: Size of text chunks for splitting documents
            chunk_overlap: Overlap between chunks
            retrieval_k: Number of documents to retrieve
            enable_memory: If True, enable conversation memory for multi-turn conversations
            memory_type: Type of memory to use ('buffer', 'window', 'summary')
            use_hybrid_search: Enable hybrid search (semantic + BM25)
            use_reranking: Enable re-ranking with CrossEncoder
            use_query_expansion: Enable query expansion
            hybrid_weights: Weights for hybrid search [semantic_weight, keyword_weight]
            reranker_model: CrossEncoder model for re-ranking
            persist_directory: Directory to persist ChromaDB data
            collection_name: Name of ChromaDB collection
            debug_mode: If True, print detailed debug logs. Default: False
            config: Optional RAGConfig object. If provided, overrides individual parameters.
            enable_query_cache: Enable query result caching. Default: False
            query_cache_size: Maximum number of cached queries. Default: 1000
            enable_embedding_cache: Enable embedding caching. Default: False
            embedding_cache_dir: Directory for embedding cache. Default: "./embedding_cache"
        """
        if not LANGCHAIN_AVAILABLE:
            raise ImportError("LangChain required")
        
        # If config object is provided, use it to override parameters
        if config:
            try:
                # Try relative import first (when used as module)
                try:
                    from .rag_settings import RAGConfig
                except ImportError:
                    # Fallback to absolute import (when run as script)
                    from backend.rag.rag_settings import RAGConfig
                if isinstance(config, RAGConfig):
                    config_dict = config.to_dict()
                    use_local_embeddings = config_dict.get('use_local_embeddings', use_local_embeddings)
                    show_matching_sources = config_dict.get('show_matching_sources', show_matching_sources)
                    min_similarity_threshold = config_dict.get('min_similarity_threshold', min_similarity_threshold)
                    chunk_size = config_dict.get('chunk_size', chunk_size)
                    chunk_overlap = config_dict.get('chunk_overlap', chunk_overlap)
                    retrieval_k = config_dict.get('retrieval_k', retrieval_k)
                    enable_memory = config_dict.get('enable_memory', enable_memory)
                    memory_type = config_dict.get('memory_type', memory_type)
                    use_hybrid_search = config_dict.get('use_hybrid_search', use_hybrid_search)
                    use_reranking = config_dict.get('use_reranking', use_reranking)
                    use_query_expansion = config_dict.get('use_query_expansion', use_query_expansion)
                    hybrid_weights = config_dict.get('hybrid_weights', hybrid_weights)
                    reranker_model = config_dict.get('reranker_model', reranker_model)
                    persist_directory = config_dict.get('persist_directory', persist_directory)
                    collection_name = config_dict.get('collection_name', collection_name)
                    debug_mode = config_dict.get('debug_mode', debug_mode)
                    enable_query_cache = config_dict.get('enable_query_cache', enable_query_cache)
                    query_cache_size = config_dict.get('query_cache_size', query_cache_size)
                    enable_embedding_cache = config_dict.get('enable_embedding_cache', enable_embedding_cache)
                    embedding_cache_dir = config_dict.get('embedding_cache_dir', embedding_cache_dir)
            except ImportError:
                pass  # If config module not available, use individual parameters
        
        self.show_matching_sources = show_matching_sources
        self.min_similarity_threshold = min_similarity_threshold
        self.retrieval_k = retrieval_k
        self.persist_directory = persist_directory
        self.collection_name = collection_name
        self.debug_mode = debug_mode
        
        self._init_embeddings(use_local_embeddings)
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap
        )
        
        self.vectorstore = None
        self.llm = None
        self.retriever = None
        self.memory = None
        self.enable_memory = enable_memory
        # In-memory cache for exact embedding scans (keyed by filter repr → (E_norm, docs, metas))
        # Cleared when documents are added to the collection.
        self._exact_emb_cache: dict = {}
        self._exact_emb_cache_lock = __import__('threading').Lock()
        
        self.use_hybrid_search = use_hybrid_search and BM25_AVAILABLE
        self.use_reranking = use_reranking and RERANKER_AVAILABLE
        self.use_query_expansion = use_query_expansion
        self.hybrid_weights = hybrid_weights or [0.6, 0.4]
        self.reranker_model = reranker_model
        
        self.keyword_retriever = None
        self.hybrid_retriever = None
        self.reranker = None
        self.all_documents = []
        self.ensemble_available = ENSEMBLE_AVAILABLE
        
        if self.use_reranking:
            try:
                self.reranker = CrossEncoder(self.reranker_model)
                debug_log(f"✅ Re-ranker initialized: {self.reranker_model}", self.debug_mode)
            except Exception as e:
                if self.debug_mode:
                    print(f"⚠️  Failed to initialize re-ranker: {e}")
                self.use_reranking = False
        
        self._init_llm()
        
        if self.enable_memory:
            self._init_memory(memory_type)
        
        # Initialize caching if available
        self.enable_query_cache = enable_query_cache and CACHING_AVAILABLE
        self.enable_embedding_cache = enable_embedding_cache and CACHING_AVAILABLE
        
        if CACHING_AVAILABLE:
            # Initialize query cache
            self.query_cache = QueryCache(
                max_size=query_cache_size,
                enabled=self.enable_query_cache
            )
            
            # Initialize embedding cache
            self.embedding_cache = EmbeddingCache(
                cache_dir=embedding_cache_dir,
                enabled=self.enable_embedding_cache
            )
            
            # Wrap embeddings with caching if enabled
            if self.enable_embedding_cache:
                original_embeddings = self.embeddings
                self.embeddings = CachedEmbeddings(original_embeddings, self.embedding_cache)
                self._original_embeddings = original_embeddings
        else:
            self.query_cache = None
            self.embedding_cache = None
        
        # Print caching status if enabled (always print, not just in debug mode)
        if self.enable_query_cache or self.enable_embedding_cache:
            print("✅ Caching enabled:")
            if self.enable_query_cache:
                print(f"   - Query cache: enabled (max size: {query_cache_size})")
            if self.enable_embedding_cache:
                print(f"   - Embedding cache: enabled (dir: {embedding_cache_dir})")
    
    def _init_embeddings(self, use_local_embeddings: bool):
        """Initialize embeddings. OpenAI key is used only when LLM_PROVIDER=openai and no local embeddings:
        it converts document/query text to vectors for RAG retrieval. With ollama/gemini we keep local embeddings by default."""
        # Check for LLM provider preference
        llm_provider = os.getenv("LLM_PROVIDER", "").lower()
        
        # Use OpenAI embeddings only when explicitly using OpenAI (not for Gemini/Ollama)
        if llm_provider == "openai":
            use_local_embeddings = False
            debug_log("ℹ️  LLM_PROVIDER=openai detected: Using OpenAI embeddings", self.debug_mode)

        if use_local_embeddings or not os.getenv("OPENAI_API_KEY"):
            # Force CPU device: the default (MPS on Apple Silicon) is not thread-safe —
            # concurrent embed_query() calls from multiple threads crash the Metal compiler
            # service with SIGABRT. CPU is thread-safe and the embedding cache means
            # this only matters for cache misses, so the performance impact is minimal.
            _embed_kwargs = {"device": "cpu"}
            try:
                from langchain_huggingface import HuggingFaceEmbeddings
                self.embeddings = HuggingFaceEmbeddings(
                    model_name="sentence-transformers/all-MiniLM-L6-v2",
                    model_kwargs=_embed_kwargs,
                )
                debug_log("✅ Using local embeddings (langchain-huggingface)", self.debug_mode)
            except ImportError:
                from langchain_community.embeddings import HuggingFaceEmbeddings
                import warnings
                warnings.filterwarnings("ignore", category=DeprecationWarning, module="langchain_community")
                self.embeddings = HuggingFaceEmbeddings(
                    model_name="sentence-transformers/all-MiniLM-L6-v2",
                    model_kwargs=_embed_kwargs,
                )
                debug_log("✅ Using local embeddings (langchain_community - deprecated)", self.debug_mode)
                debug_log("💡 Tip: Install 'langchain-huggingface' to remove deprecation warning", self.debug_mode)
        else:
            self.embeddings = OpenAIEmbeddings()
            debug_log("✅ Using OpenAI embeddings", self.debug_mode)
    
    def _init_llm(self):
        """Initialize LLM. OpenAI key is used only when LLM_PROVIDER=openai to call GPT for generating answers from RAG context (and direct-QA)."""
        # Check for explicit LLM preference from environment
        llm_provider = os.getenv("LLM_PROVIDER", "").lower()
        
        # Check if OpenAI API key is set and valid (not a placeholder)
        openai_key = os.getenv("OPENAI_API_KEY", "")
        has_valid_openai_key = (
            openai_key and 
            openai_key.strip() != "" and 
            not openai_key.startswith("your-") and
            "api-key" not in openai_key.lower()
        )
        
        # Gemini: GOOGLE_API_KEY or GEMINI_API_KEY
        gemini_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or ""
        has_valid_gemini_key = (
            gemini_key and
            gemini_key.strip() != "" and
            not gemini_key.startswith("your-") and
            "api-key" not in gemini_key.lower()
        )
        
        # Use OpenAI only if explicitly requested or if valid key is provided and other providers not chosen
        if llm_provider == "openai" or (has_valid_openai_key and llm_provider not in ("ollama", "gemini")):
            openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            self.llm = ChatOpenAI(temperature=0, model=openai_model)
            debug_log(f"✅ Using OpenAI LLM ({openai_model})", self.debug_mode)
        elif llm_provider == "gemini" and has_valid_gemini_key:
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI
                gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
                if gemini_model in ("gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-flash"):
                    gemini_model = "gemini-2.5-flash"  # older models deprecated, use 2.5
                # Configurable retries for 429 Resource Exhausted (rate limit / quota)
                gemini_max_retries = 6
                try:
                    _r = os.getenv("GEMINI_MAX_RETRIES", "").strip()
                    if _r:
                        gemini_max_retries = max(2, min(20, int(_r)))
                except ValueError:
                    pass
                self.llm = ChatGoogleGenerativeAI(
                    model=gemini_model,
                    temperature=0,
                    google_api_key=gemini_key.strip(),
                    max_retries=gemini_max_retries,
                )
                debug_log(f"✅ Using Gemini LLM ({gemini_model}, max_retries={gemini_max_retries})", self.debug_mode)
            except ImportError:
                if self.debug_mode:
                    print("⚠️  langchain-google-genai not installed. Run: pip install langchain-google-genai")
                self.llm = None
            except Exception as e:
                if self.debug_mode:
                    print(f"⚠️  Failed to initialize Gemini: {e}")
                self.llm = None
        elif OLLAMA_AVAILABLE or llm_provider == "ollama":
            if USE_NEW_OLLAMA:
                from langchain_ollama import ChatOllama
            else:
                from langchain_community.chat_models import ChatOllama
            
            ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
            ollama_model = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
            try:
                self.llm = ChatOllama(
                    model=ollama_model,
                    base_url=ollama_base_url,
                    temperature=0
                )
                debug_log(f"✅ Using Ollama LLM ({ollama_model})", self.debug_mode)
            except Exception as e:
                if self.debug_mode:
                    print(f"⚠️  Failed to initialize Ollama: {e}")
                    print("⚠️  Make sure Ollama is running: ollama serve")
                self.llm = None
        else:
            if self.debug_mode:
                print("⚠️  No LLM available (set LLM_PROVIDER to ollama, openai, or gemini and provide the required API key or run Ollama).")
                print("⚠️  Using simple template-based responses.")
            self.llm = None
    
    def _init_memory(self, memory_type: str = 'buffer'):
        """Initialize conversation memory."""
        if memory_type == 'buffer':
            self.memory = SimpleMemory(memory_type='buffer')
            debug_log("✅ Using SimpleMemory (stores all messages)", self.debug_mode)
        elif memory_type == 'window':
            self.memory = SimpleMemory(memory_type='window', max_messages=5)
            debug_log("✅ Using SimpleMemory with window (stores last 5 exchanges)", self.debug_mode)
        elif memory_type == 'summary':
            self.memory = SimpleMemory(memory_type='buffer')
            debug_log("✅ Using SimpleMemory (summary mode not yet implemented, using buffer)", self.debug_mode)
        else:
            self.memory = SimpleMemory(memory_type='buffer')
            debug_log(f"✅ Using SimpleMemory (unknown type '{memory_type}', using buffer)", self.debug_mode)
    
    def _get_or_create_vectorstore(self, documents: List[Document], show_log: bool = True):
        """Get existing ChromaDB collection or create a new one with persistent storage."""
        ChromaDBHelper = import_chromadb_helper()
        return ChromaDBHelper.get_or_create_vectorstore(
            documents=documents,
            embeddings=self.embeddings,
            persist_directory=self.persist_directory,
            collection_name=self.collection_name,
            show_log=show_log
        )
    
    def _load_vectorstore_if_needed(self):
        """Load vectorstore if it doesn't exist (needed for duplicate checking)."""
        if self.vectorstore is None:
            from langchain_core.documents import Document
            empty_docs = [Document(page_content="temp")]
            self.vectorstore = self._get_or_create_vectorstore(empty_docs, show_log=True)
            # Remove the temp document if it was added
            try:
                results = self.vectorstore._collection.get()
                if results.get('ids') and results.get('documents'):
                    for idx, doc in enumerate(results.get('documents', [])):
                        if doc == "temp":
                            self.vectorstore._collection.delete(ids=[results['ids'][idx]])
                            break
            except:
                pass
    
    def _remove_existing_documents(self, file_paths: List[Path], replace_if_exists: bool) -> int:
        """
        Remove existing documents from the same files if replacing.
        
        Args:
            file_paths: List of file paths to check
            replace_if_exists: If True, remove existing documents
            
        Returns:
            Total count of removed documents
        """
        if not replace_if_exists or not self.vectorstore:
            return 0
        
        ChromaDBHelper = import_chromadb_helper()
        removed_count = 0
        
        for file_path in file_paths:
            removed = ChromaDBHelper.remove_documents_by_file_path(
                self.vectorstore, str(file_path), return_count=True
            )
            removed_count += removed
            # Also remove from all_documents list
            self.all_documents = [doc for doc in self.all_documents 
                                if doc.metadata.get('file_path') != str(file_path)]
        
        return removed_count
    
    def _add_documents_to_vectorstore(self, splits: List[Document], success_message: str = "Documents indexed!"):
        """
        Add documents to vectorstore and update retrievers.
        
        Args:
            splits: List of Document objects to add
            success_message: Success message to display
        """
        # Store documents for BM25 (needed for hybrid search)
        self.all_documents.extend(splits)
        
        # Add documents to vectorstore
        if self.vectorstore is None:
            # If vectorstore doesn't exist, create new one
            self.vectorstore = self._get_or_create_vectorstore(splits, show_log=False)
        else:
            try:
                # Try to check if collection exists and has documents
                count = self.vectorstore._collection.count()
                if count == 0:
                    # If collection is empty, recreate with new documents
                    self.vectorstore = self._get_or_create_vectorstore(splits, show_log=False)
                else:
                    # Add to existing vectorstore
                    self.vectorstore.add_documents(splits)
            except Exception:
                # Collection doesn't exist or error accessing it, create new one
                self.vectorstore = self._get_or_create_vectorstore(splits, show_log=False)
        
        self.retriever = self.vectorstore.as_retriever(search_kwargs={"k": self.retrieval_k})
        # Invalidate exact-scan embedding cache — collection contents have changed
        with self._exact_emb_cache_lock:
            self._exact_emb_cache.clear()

        # Re-initialize keyword retriever with all documents
        if self.use_hybrid_search:
            self._init_keyword_retriever()

        final_count = self.vectorstore._collection.count()
        print(f"✅ {success_message} (Total: {final_count} documents)")
    
    def add_documents_from_texts(self, documents: List[str]):
        """Add documents from text strings."""
        print(f"📚 Adding {len(documents)} documents...")
        
        docs = [Document(page_content=text.strip()) for text in documents]
        splits = self.text_splitter.split_documents(docs)
        print(f"📄 Split into {len(splits)} chunks")
        
        self.all_documents.extend(splits)
        self.vectorstore = self._get_or_create_vectorstore(splits)
        self.retriever = self.vectorstore.as_retriever(search_kwargs={"k": self.retrieval_k})
        
        if self.use_hybrid_search:
            self._init_keyword_retriever()
        
        print("✅ Documents indexed!")
    
    def query(self, question: str, bypass_cache: bool = False) -> Dict[str, Any]:
        """
        Query the RAG system with caching and performance tracking.
        
        Args:
            question: User question
            bypass_cache: If True, skip cache and force fresh LLM query
            
        Returns:
            Query result dictionary with answer, sources, and metadata
        """
        start_time = time.time()
        cache_hit = False
        
        # Check query cache if enabled and not bypassed
        if self.enable_query_cache and self.query_cache and not bypass_cache:
            cached_result = self.query_cache.get(
                question,
                retrieval_k=self.retrieval_k,
                use_hybrid_search=self.use_hybrid_search,
                use_reranking=self.use_reranking,
                min_similarity_threshold=self.min_similarity_threshold
            )
            
            if cached_result:
                cache_hit = True
                elapsed_time = (time.time() - start_time) * 1000
                
                # Add cache metadata
                result = cached_result.copy()
                result['cache_hit'] = True
                result['query_time_ms'] = elapsed_time
                
                # Clean cached answer: remove source citations but preserve formatting
                import re
                answer = result.get('answer', '')
                if answer:
                    # Remove all source citations
                    answer = re.sub(r'\[Source\s+\d+\]', '', answer)
                    # Remove "Based on the retrieved context" prefix
                    answer = re.sub(r'Based on the retrieved context\s*:?\s*', '', answer, flags=re.IGNORECASE)
                    # Remove any remaining source references
                    answer = re.sub(r'\[Source\s+\d+\]\s*', '', answer)
                    # Preserve formatting: normalize whitespace but keep line breaks
                    answer = re.sub(r'[ \t]+', ' ', answer)  # Multiple spaces/tabs to single space
                    answer = re.sub(r'\n{3,}', '\n\n', answer)  # 3+ newlines to 2
                    answer = re.sub(r' *\n *', '\n', answer)  # Clean up spaces around newlines
                    answer = answer.strip()
                    result['answer'] = answer
                    print(f"✅ Answer: {answer}")
                else:
                    # Fallback: try to extract answer from other fields
                    if 'source_documents' in result and result['source_documents']:
                        # If no answer but we have sources, at least show we have cached data
                        print("✅ Answer: (Cached - see sources below)")
                
                # Print source information if available (matching base class format)
                if self.show_matching_sources:
                    sources = result.get('sources', [])
                    source_documents = result.get('source_documents', [])
                    
                    if sources or source_documents:
                        # Use source_documents if available (more complete), otherwise use sources
                        if source_documents:
                            cited_count = len(source_documents)
                            if self.min_similarity_threshold > 0.0:
                                print(f"📄 Retrieved {cited_count} relevant chunk(s) above {self.min_similarity_threshold}% threshold")
                            else:
                                print(f"📄 Retrieved {cited_count} relevant chunk(s)")
                            print("\n📚 Sources:")
                            print("-" * 60)
                            for doc_info in source_documents:
                                source_id = doc_info.get('source_id', 0)
                                content = doc_info.get('content', '')
                                content_preview = content[:100] if len(content) > 100 else content
                                print(f"[Source {source_id}] {content_preview}...")
                        elif sources:
                            cited_count = len(sources)
                            if self.min_similarity_threshold > 0.0:
                                print(f"📄 Retrieved {cited_count} relevant chunk(s) above {self.min_similarity_threshold}% threshold")
                            else:
                                print(f"📄 Retrieved {cited_count} relevant chunk(s)")
                            print("\n📚 Sources:")
                            print("-" * 60)
                            for i, source in enumerate(sources, 1):
                                if isinstance(source, dict):
                                    content = source.get('content', '')
                                    similarity = source.get('similarity_percent')
                                    content_preview = content[:100] if len(content) > 100 else content
                                    if similarity is not None:
                                        print(f"[Source {i}] Similarity: {similarity:.2f}% - {content_preview}...")
                                    else:
                                        print(f"[Source {i}] {content_preview}...")
                                else:
                                    print(f"[Source {i}] {str(source)[:100]}...")
                
                print(f"⏱️  Query time: {elapsed_time:.2f}ms")
                return result
        
        # Cache miss - execute query
        # Call the actual query implementation
        result = self._query_impl(question)
        
        # Calculate query time
        elapsed_time = (time.time() - start_time) * 1000
        
        # Add performance metadata
        result['cache_hit'] = False
        result['query_time_ms'] = elapsed_time
        
        # Clean answer before caching: remove source citations but preserve formatting
        import re
        answer = result.get('answer', '')
        if answer:
            # Remove all source citations
            answer = re.sub(r'\[Source\s+\d+\]', '', answer)
            # Remove "Based on the retrieved context" prefix
            answer = re.sub(r'Based on the retrieved context\s*:?\s*', '', answer, flags=re.IGNORECASE)
            # Remove any remaining source references
            answer = re.sub(r'\[Source\s+\d+\]\s*', '', answer)
            # Preserve formatting: normalize whitespace but keep line breaks
            answer = re.sub(r'[ \t]+', ' ', answer)  # Multiple spaces/tabs to single space
            answer = re.sub(r'\n{3,}', '\n\n', answer)  # 3+ newlines to 2
            answer = re.sub(r' *\n *', '\n', answer)  # Clean up spaces around newlines
            answer = answer.strip()
            result['answer'] = answer
        
        # Cache the result if enabled (with cleaned answer)
        if self.enable_query_cache and self.query_cache:
            self.query_cache.set(
                question,
                result,
                retrieval_k=self.retrieval_k,
                use_hybrid_search=self.use_hybrid_search,
                use_reranking=self.use_reranking,
                min_similarity_threshold=self.min_similarity_threshold
            )
        
        
        print(f"⏱️  Query time: {elapsed_time:.2f}ms")
        
        # Show performance warning if query took more than 30 seconds
        if elapsed_time > 30000:  # 30 seconds in milliseconds
            print("\n⚠️  Performance Warning:")
            print("   Query took longer than 30 seconds. Consider:")
            print("   1. Using a machine with higher specifications for better Ollama performance")
            print("   2. Upgrading to a more powerful Ollama model")
            print("   3. Switching to OpenAI or Gemini (set LLM_PROVIDER=openai or gemini in .env and add API key)")
        
        return result
    
    def _get_dynamic_retrieval_params(self, question: str) -> Dict[str, Any]:
        """
        Dynamically extract retrieval parameters (k, filters) from the question.
        When user asks for "N test cases" or "top N critical", we over-fetch and sort by P0>P1>P2>P3.
        """
        import re
        params = {"k": self.retrieval_k, "filter": {}, "requested_k": None, "sort_by_priority": False}
        
        # 1. Dynamic K extraction (e.g., "top 20", "list 15", "give me 10", "10 critical testcases")
        count_match = re.search(r'(?:top|list|give me|show me|return|provide)\s+(\d+)', question, re.IGNORECASE)
        if not count_match:
            count_match = re.search(r'(\d+)\s+(?:test\s*cases|results|items|rows|critical|high|medium)?', question, re.IGNORECASE)
        if not count_match:
            count_match = re.search(r'(\d+)\s+(?:test\s*cases|critical)', question, re.IGNORECASE)
        
        is_testcase_list = bool(re.search(r'test\s*case|testcase', question, re.IGNORECASE))
        wants_priority_order = is_testcase_list and (count_match or re.search(r'\b(critical|high|priority|top)\b', question, re.IGNORECASE))
        
        if count_match:
            requested_k = int(count_match.group(1))
            params["requested_k"] = requested_k
            # Cap at 50 to avoid overwhelming context
            params["k"] = min(max(requested_k, self.retrieval_k), 50)
            # When user wants test cases in priority order (e.g. "give me 10 critical testcases"),
            # over-fetch so we can sort by P0>P1>P2>P3 and then take requested_k
            if wants_priority_order:
                params["sort_by_priority"] = True
                params["k"] = min(requested_k * 3, 50)  # fetch more to sort by priority
                params["requested_k"] = requested_k
        elif wants_priority_order:
            params["sort_by_priority"] = True
            params["requested_k"] = min(self.retrieval_k, 20)
            params["k"] = min(params["requested_k"] * 3, 50)
            
        # 2. Metadata filtering (Priority: P0, P1, P2, P3) - only when user asks for a specific P-level, not "critical" list
        if not params.get("sort_by_priority"):
            priority_match = re.search(r'\b(P[0-3])\b', question, re.IGNORECASE)
            if priority_match:
                params["filter"]["priority"] = priority_match.group(1).upper()
            
        # 3. Metadata filtering (Platform: iOS, Android, etc.)
        platforms = ["ios", "android", "web", "api", "backend"]
        for p in platforms:
            if re.search(f'\\b{p}\\b', question, re.IGNORECASE):
                params["filter"]["platform"] = p
                break

        return params

    @staticmethod
    def _testrail_id_numeric_from_doc(doc: Any) -> int:
        """Extract numeric part of TestRail ID from document (metadata or page_content) for sorting. Newest-first = descending."""
        tid = (doc.metadata or {}).get("testrail_id") or (doc.metadata or {}).get("id") or ""
        if not tid and getattr(doc, "page_content", None):
            for line in doc.page_content.split("\n"):
                if line.strip().lower().startswith("testrail id:"):
                    tid = line.split(":", 1)[-1].strip()
                    break
        tid = (tid or "").strip().upper()
        if not tid or tid == "N/A":
            return -1
        digits = "".join(c for c in tid if c.isdigit())
        return int(digits) if digits else -1

    @staticmethod
    def _sort_docs_by_priority(docs: List, limit: Optional[int] = None) -> List:
        """Sort documents by priority P0 > P1 > P2 > P3 (metadata 'priority'), then optionally take first `limit`."""
        priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}

        def key(d):
            p = (d.metadata or {}).get("priority") or ""
            return (priority_order.get(str(p).upper(), 99), (d.page_content or ""))

        sorted_docs = sorted(docs, key=key)
        if limit is not None:
            return sorted_docs[:limit]
        return sorted_docs

    def retrieve_documents_with_scores(
        self,
        query: str,
        k: int,
        metadata_filter: Optional[Dict[str, Any]] = None,
        min_similarity_threshold_override: Optional[float] = None,
        use_hybrid_search_override: Optional[bool] = None,
        use_reranking_override: Optional[bool] = None,
    ) -> List[Tuple[Any, Optional[float]]]:
        """
        Retrieve documents using the same pipeline as Chat: optional hybrid search,
        reranking, and min_similarity_threshold. Used by find_related_tests and find_related_specs.
        Pass *_override for requirement analysis (REQUIREMENT_* env vars).

        Args:
            query: Search query (e.g. requirement text).
            k: Max number of documents to return.
            metadata_filter: Optional ChromaDB metadata filter (e.g. {"source_type": "testcase"}).
            min_similarity_threshold_override: Override threshold (0-100); use REQUIREMENT_TESTS_SIMILARITY_THRESHOLD.
            use_hybrid_search_override: Override hybrid search; use REQUIREMENT_USE_HYBRID_SEARCH.
            use_reranking_override: Override reranking; use REQUIREMENT_USE_RERANKING.

        Returns:
            List of (Document, similarity_score) where similarity_score is 0.0-1.0 or None.
            Only documents passing min_similarity_threshold are included (or top few if none pass).
        """
        if not self.vectorstore:
            return []
        # Over-fetch aggressively so the full set of threshold-passing docs is in the candidate pool.
        # K is applied last (after threshold), so the multiplier only affects ChromaDB query size.
        if metadata_filter:
            search_k = min(k * 15, 500) if k < 50 else k
        else:
            search_k = min(k * 10, 300) if k < 50 else k
        use_hybrid = use_hybrid_search_override if use_hybrid_search_override is not None else self.use_hybrid_search
        use_rerank = use_reranking_override if use_reranking_override is not None else self.use_reranking
        threshold_pct = min_similarity_threshold_override if min_similarity_threshold_override is not None else self.min_similarity_threshold

        # Full exact-cosine scan for metadata-filtered path (testcases).
        # Deterministic: fetches ALL matching embeddings from ChromaDB, computes exact cosine
        # via vectorised numpy matmul — no HNSW approximation involved.
        # Speed: embeddings are cached in self._exact_emb_cache after the first call so
        # subsequent calls for the same filter are near-instantaneous.
        raw_scored: List[Tuple[Any, Optional[float]]] = []
        _exact_search_done = False
        if metadata_filter:
            try:
                import numpy as _np
                _cache_key = repr(sorted(metadata_filter.items()) if isinstance(metadata_filter, dict) else metadata_filter)
                _q_emb = _np.array(self.embeddings.embed_query(query), dtype=float)
                _q_norm = _q_emb / (_np.linalg.norm(_q_emb) + 1e-10)
                # Use cached embedding matrix if available; otherwise load from ChromaDB
                with self._exact_emb_cache_lock:
                    _cache_hit = _cache_key in self._exact_emb_cache
                    if _cache_hit:
                        _E_norm, _docs_raw, _metas_raw = self._exact_emb_cache[_cache_key]
                if not _cache_hit:
                    _col = self.vectorstore._collection
                    _col_results = _col.get(
                        where=metadata_filter,
                        include=["documents", "metadatas", "embeddings"]
                    )
                    _all_embs = _col_results.get("embeddings")
                    if _all_embs is None or len(_all_embs) == 0:
                        raise ValueError("No embeddings stored in collection")
                    _docs_raw = _col_results.get("documents") or []
                    _metas_raw = _col_results.get("metadatas") or []
                    _E = _np.array(_all_embs, dtype=float)          # (N, D)
                    _norms = _np.linalg.norm(_E, axis=1, keepdims=True)
                    _E_norm = _E / (_norms + 1e-10)                  # (N, D)
                    with self._exact_emb_cache_lock:
                        self._exact_emb_cache[_cache_key] = (_E_norm, _docs_raw, _metas_raw)
                    print(f"[retrieve] Exact-scan cache built: {len(_docs_raw)} docs for filter {_cache_key}")
                # Batch cosine similarity: single matmul → (N,) array — fully deterministic
                _sims = _E_norm @ _q_norm                            # (N,)
                _cos_dists = 1.0 - _sims                             # cosine distance [0, 2]
                _exact: List[Tuple[Any, float]] = []
                for _i in range(len(_docs_raw)):
                    _doc = Document(
                        page_content=_docs_raw[_i] or "",
                        metadata=_metas_raw[_i] or {}
                    )
                    _exact.append((_doc, float(_cos_dists[_i])))
                _exact.sort(key=lambda x: x[1])   # ascending dist = descending similarity
                raw_scored = _exact
                _exact_search_done = True
            except Exception as _e:
                print(f"[retrieve] Exact scan failed ({_e}), falling back to HNSW")

        if not _exact_search_done:
            try:
                if metadata_filter:
                    raw_scored = self.vectorstore.similarity_search_with_score(
                        query, k=search_k, filter=metadata_filter
                    )
                elif use_hybrid and self.hybrid_retriever:
                    # Hybrid retriever doesn't return scores; fetch scores for its docs separately
                    hybrid_docs = sanitize_documents(list(self.hybrid_retriever.get_relevant_documents(query)))
                    hybrid_docs = deduplicate_documents(hybrid_docs)[:search_k]
                    if hybrid_docs:
                        # Use a wider window than search_k so BM25-only docs (not in top-N semantic)
                        # still get their similarity scores recovered.
                        scored_all = self.vectorstore.similarity_search_with_score(query, k=min(search_k * 3, 500))
                        score_map = {d.page_content: dist for d, dist in scored_all}
                        # Only keep hybrid docs that exist in the scored set (have a similarity score)
                        raw_scored = [(doc, score_map.get(doc.page_content)) for doc in hybrid_docs]
                        # Filter out docs that scored None due to not being in the semantic results
                        raw_scored = [(doc, dist) for doc, dist in raw_scored if dist is not None]
                    else:
                        raw_scored = []
                else:
                    raw_scored = self.vectorstore.similarity_search_with_score(query, k=search_k)
            except Exception:
                return []

        # Sanitize (handle None page_content) while keeping scores paired
        all_scored: List[Tuple[Any, Optional[float]]] = []
        for doc, dist in raw_scored:
            content = doc.page_content if doc.page_content is not None else ""
            sanitized = Document(page_content=content, metadata=doc.metadata or {})
            all_scored.append((sanitized, dist))

        # Deduplicate by first-200-char hash while preserving scores.
        # Use hashlib (not Python's built-in hash()) — hash() is randomized per process
        # (PYTHONHASHSEED) which causes non-deterministic deduplication across runs.
        seen_hashes: set = set()
        unique_scored: List[Tuple[Any, Optional[float]]] = []
        for doc, dist in all_scored:
            h = hashlib.md5((doc.page_content or "")[:200].encode("utf-8", errors="replace"), usedforsecurity=False).hexdigest()
            if h not in seen_hashes:
                seen_hashes.add(h)
                unique_scored.append((doc, dist))

        if not unique_scored:
            return []

        # Apply threshold to the full deduplicated candidate set BEFORE any K slicing.
        # This ensures K is the final limit, not an intermediate cut — so the same docs
        # pass the threshold on every run regardless of HNSW ordering variance.
        if threshold_pct > 0:
            candidates: List[Tuple[Any, Optional[float]]] = []
            for doc, dist in unique_scored:
                if dist is not None:
                    _, sim_pct = calculate_similarity(dist)
                    if sim_pct >= threshold_pct:
                        candidates.append((doc, dist))
                # docs with no score dropped — no reliable similarity to compare
        else:
            candidates = list(unique_scored)

        if not candidates:
            return []

        # Rerank the threshold-filtered candidates, then slice to K
        if use_rerank and self.reranker:
            docs_only = [doc for doc, _ in candidates]
            reranked = rerank_documents(query, docs_only, self.reranker, top_k=k)
            content_to_dist = {doc.page_content: dist for doc, dist in candidates}
            final_scored = [(doc, content_to_dist.get(doc.page_content)) for _, doc in reranked]
        else:
            final_scored = candidates

        # Convert distance → 0-1 score and return top K
        results: List[Tuple[Any, Optional[float]]] = []
        for doc, distance in final_scored:
            if distance is not None:
                _, similarity_percent = calculate_similarity(distance)
                results.append((doc, similarity_percent / 100.0))

        return results[:k]

    def _query_impl(self, question: str) -> Dict[str, Any]:
        """Internal query implementation (moved from query method)."""
        if not self.retriever:
            self._load_vectorstore_if_needed()
            if self.vectorstore:
                self.retriever = self.vectorstore.as_retriever(search_kwargs={"k": self.retrieval_k})
            if not self.retriever:
                raise ValueError("No documents added. Please upload documents or run TestRail/Confluence sync first.")
        print(f"❓ Question: {question}")
        
        # Extract dynamic parameters
        dynamic_params = self._get_dynamic_retrieval_params(question)
        search_k = dynamic_params["k"]
        metadata_filter = dynamic_params["filter"]
        
        if search_k != self.retrieval_k or metadata_filter:
            print(f"🎯 Dynamic Retrieval: k={search_k}, filters={metadata_filter}")

        expanded_queries = expand_query(question, self.use_query_expansion, llm=self.llm)
        if len(expanded_queries) > 1:
            print(f"🔍 Expanded queries: {len(expanded_queries)}")
        
        all_docs = []
        for query in expanded_queries:
            if self.use_hybrid_search and self.hybrid_retriever:
                # Note: Hybrid search might not fully respect filters yet depending on implementation
                docs = self.hybrid_retriever.get_relevant_documents(query)
            else:
                if metadata_filter:
                    # Direct vector store search for filtered results
                    docs = self.vectorstore.similarity_search(query, k=search_k, filter=metadata_filter)
                elif search_k != self.retrieval_k:
                    # Custom k for this query
                    docs = self.vectorstore.similarity_search(query, k=search_k)
                else:
                    docs = self.retriever.invoke(query)
            all_docs.extend(docs)
        
        # Ensure no doc has page_content=None (ChromaDB can return None and break Pydantic Document validation)
        all_docs = sanitize_documents(all_docs)
        initial_retrieval_count = len(all_docs)
        unique_docs = deduplicate_documents(all_docs)
        if len(unique_docs) < len(all_docs):
            print(f"🔄 Deduplicated: {len(all_docs)} → {len(unique_docs)} documents")
        
        rerank_scores = []
        if self.use_reranking:
            reranked = rerank_documents(question, unique_docs, self.reranker, top_k=search_k)
            final_docs = [doc for _, doc in reranked]
            rerank_scores = [float(score) for score, _ in reranked]
            if rerank_scores:
                print(f"📊 Re-ranked top score: {rerank_scores[0]:.4f}")
        else:
            final_docs = unique_docs[:search_k]
        
        # When user asked for N test cases in priority order (e.g. "give me 10 critical testcases"),
        # sort by P0 > P1 > P2 > P3 and take exactly requested_k
        if dynamic_params.get("sort_by_priority") and dynamic_params.get("requested_k"):
            final_docs = self._sort_docs_by_priority(final_docs, limit=dynamic_params["requested_k"])
            if len(final_docs) < dynamic_params["requested_k"]:
                print(f"📋 Priority-sorted: {len(final_docs)} test cases (requested {dynamic_params['requested_k']})")
            else:
                print(f"📋 Priority order applied: P0>P1>P2>P3, returning {dynamic_params['requested_k']} test cases")
        
        docs = final_docs
        initial_count = len(docs)
        
        needs_scores = self.min_similarity_threshold > 0.0 and len(docs) > 0
        sources_with_scores = []
        filtered_docs = []
        sources_above_threshold = 0
        
        if needs_scores:
            # Use a wider k to capture BM25-matched docs that may fall outside the semantic top-k window
            score_lookup_k = min(max(len(docs) * 3, 30), 200)
            docs_with_scores = self.vectorstore.similarity_search_with_score(question, k=score_lookup_k)
            score_map = {doc.page_content: (doc, score) for doc, score in docs_with_scores}

            for i, doc in enumerate(docs, 1):
                doc_with_score = score_map.get(doc.page_content)

                if doc_with_score:
                    original_doc, distance = doc_with_score
                    similarity, similarity_percent = calculate_similarity(distance)

                    if similarity_percent >= self.min_similarity_threshold:
                        filtered_docs.append(doc)
                        sources_above_threshold += 1
                        sources_with_scores.append({
                            "content": doc.page_content[:150],
                            "similarity": similarity,
                            "similarity_percent": similarity_percent,
                            "distance": distance,
                            "doc_id": doc.metadata.get('id', '') if doc.metadata else ''
                        })
                else:
                    # Doc is BM25-only (no semantic match in widened window).
                    # BM25 selected it as keyword-relevant, so include it but assign a floor score
                    # so it passes threshold filtering and sorts after all semantically-scored docs.
                    floor_pct = self.min_similarity_threshold
                    filtered_docs.append(doc)
                    sources_above_threshold += 1
                    sources_with_scores.append({
                        "content": doc.page_content[:150],
                        "similarity": floor_pct / 100.0,
                        "similarity_percent": floor_pct,
                        "distance": None,
                        "doc_id": doc.metadata.get('id', '') if doc.metadata else ''
                    })
        else:
            filtered_docs = docs
            sources_above_threshold = len(docs)
            for doc in docs:
                sources_with_scores.append({
                    "content": doc.page_content[:150],
                    "similarity": None,
                    "similarity_percent": None,
                    "distance": None,
                    "doc_id": doc.metadata.get('id', '') if doc.metadata else ''
                })
        
        if not filtered_docs and docs:
            print(f"⚠️  No documents passed {self.min_similarity_threshold}% threshold, using top {min(3, len(docs))} retrieved documents")
            filtered_docs = docs[:min(3, len(docs))]
            sources_above_threshold = len(filtered_docs)
            for i, doc in enumerate(filtered_docs, 1):
                if i > len(sources_with_scores):
                    sources_with_scores.append({
                        "content": doc.page_content[:150],
                        "similarity": None,
                        "similarity_percent": None,
                        "distance": None,
                        "doc_id": doc.metadata.get('id', '') if doc.metadata else ''
                    })
        
        docs = filtered_docs

        # Sort by similarity score DESC (best match first), then by newest TestRail ID as tiebreaker
        if docs and sources_with_scores and len(sources_with_scores) == len(docs):
            paired = list(zip(docs, sources_with_scores))
            paired.sort(key=lambda p: (
                -(p[1].get('similarity_percent') or 0.0),
                -self._testrail_id_numeric_from_doc(p[0])
            ))
            docs = [p[0] for p in paired]
            sources_with_scores = [p[1] for p in paired]

        context_parts = []
        for i, doc in enumerate(docs, 1):
            sim_pct = sources_with_scores[i - 1].get('similarity_percent') if sources_with_scores else None
            prefix = f"### Source {i} (relevance: {sim_pct:.0f}%)\n" if sim_pct is not None else f"### Source {i}\n"
            context_parts.append(prefix + (doc.page_content or ""))

        context = "\n\n".join(context_parts)
        
        if self.llm:
            try:
                if self.enable_memory and self.memory:
                    from langchain_core.prompts import SystemMessagePromptTemplate, HumanMessagePromptTemplate, MessagesPlaceholder
                    
                    prompt = ChatPromptTemplate.from_messages([
                        SystemMessagePromptTemplate.from_template(RAG_SYSTEM_MESSAGE),
                        MessagesPlaceholder(variable_name="chat_history"),
                        HumanMessagePromptTemplate.from_template("Context:\n{context}\n\nQuestion: {question}\n\nAnswer:")
                    ])
                    
                    chat_history = self.memory.messages if hasattr(self.memory, 'messages') else []
                    
                    chain = prompt | self.llm
                    result = chain.invoke({
                        "context": context,
                        "question": question,
                        "chat_history": chat_history
                    })
                    try:
                        from backend.cost_tracker import record_from_langchain_result
                        record_from_langchain_result("rag.query", result)
                    except Exception:
                        pass
                    answer = extract_answer_from_llm_result(result)
                    
                    self.memory.save_context(
                        {"input": question},
                        {"output": answer}
                    )
                else:
                    prompt = ChatPromptTemplate.from_messages([
                        ("system", RAG_SYSTEM_MESSAGE),
                        ("human", "Context:\n{context}\n\nQuestion: {question}\n\nAnswer:")
                    ])
                    
                    chain = prompt | self.llm
                    result = chain.invoke({"context": context, "question": question})
                    try:
                        from backend.cost_tracker import record_from_langchain_result
                        record_from_langchain_result("rag.query", result)
                    except Exception:
                        pass
                    answer = extract_answer_from_llm_result(result)
            except Exception as e:
                print(f"⚠️  Error generating answer with LLM: {e}")
                # Fallback: use full context without source citations
                answer = context
        else:
            # Fallback when no LLM: provide full context without source citations
            answer = context
        
        # Clean answer: remove source citations but preserve formatting
        import re
        # Remove all source citations
        answer = re.sub(r'\[Source\s+\d+\]', '', answer)
        # Remove "Based on the retrieved context" prefix
        answer = re.sub(r'Based on the retrieved context\s*:?\s*', '', answer, flags=re.IGNORECASE)
        # Remove any remaining source references
        answer = re.sub(r'\[Source\s+\d+\]\s*', '', answer)
        # Preserve formatting: normalize whitespace but keep line breaks
        answer = re.sub(r'[ \t]+', ' ', answer)  # Multiple spaces/tabs to single space
        answer = re.sub(r'\n{3,}', '\n\n', answer)  # 3+ newlines to 2
        answer = re.sub(r' *\n *', '\n', answer)  # Clean up spaces around newlines
        answer = answer.strip()
        
        print(f"✅ Answer: {answer}")
        
        # Extract cited sources for metadata (but don't include in answer text)
        cited_source_numbers = extract_cited_sources(answer)
        
        cited_count = len(cited_source_numbers)
        
        if self.show_matching_sources:
            if self.min_similarity_threshold > 0.0:
                if sources_above_threshold > cited_count:
                    print(f"📄 {sources_above_threshold} source(s) passed {self.min_similarity_threshold}% threshold, {cited_count} cited in answer (out of {initial_count} retrieved)")
                else:
                    print(f"📄 Using {cited_count} source(s) above {self.min_similarity_threshold}% threshold out of {initial_count} retrieved")
            else:
                print(f"📄 Retrieved {cited_count} relevant chunks")
        
        if self.show_matching_sources:
            print("\n📚 Sources:")
            print("-" * 60)
            # Show all retrieved sources, not just cited ones
            sources_to_show = cited_source_numbers if cited_source_numbers else set(range(1, len(docs) + 1))
            for i, doc in enumerate(docs, 1):
                if i in sources_to_show:
                    content_preview = doc.page_content[:100]
                    print(f"[Source {i}] {content_preview}...")
        
        cited_docs = [doc for i, doc in enumerate(docs, 1) if i in cited_source_numbers]
        cited_sources_with_scores = [
            sources_with_scores[i-1] for i in cited_source_numbers if i <= len(sources_with_scores)
        ]
        
        # When show_matching_sources is True, include all retrieved sources in response
        # Otherwise, only include cited sources
        if self.show_matching_sources:
            # Include all retrieved sources
            all_sources_with_scores = sources_with_scores if sources_with_scores else [
                {
                    "content": doc.page_content[:150],
                    "similarity": None,
                    "similarity_percent": None,
                    "distance": None,
                    "doc_id": doc.metadata.get('id', '') if doc.metadata else ''
                }
                for doc in docs
            ]
            all_source_documents = [
                {
                    "source_id": i,
                    "content": doc.page_content,
                    "metadata": doc.metadata if doc.metadata else {}
                }
                for i, doc in enumerate(docs, 1)
            ]
        else:
            # Only include cited sources
            all_sources_with_scores = cited_sources_with_scores
            all_source_documents = [
                {
                    "source_id": i,
                    "content": doc.page_content,
                    "metadata": doc.metadata if doc.metadata else {}
                }
                for i, doc in enumerate(docs, 1)
                if i in cited_source_numbers
            ]
        
        result = {
            "answer": answer,
            "sources": all_sources_with_scores,
            "source_documents": all_source_documents,
            "all_retrieved_documents": len(docs)
        }
        
        if self.use_hybrid_search or self.use_reranking or len(expanded_queries) > 1:
            result['advanced_retrieval'] = {
                'initial_count': initial_retrieval_count,
                'after_deduplication': len(unique_docs),
                'final_count': len(final_docs),
                'used_hybrid_search': self.use_hybrid_search and self.hybrid_retriever is not None,
                'used_reranking': self.use_reranking and len(rerank_scores) > 0,
                'used_query_expansion': len(expanded_queries) > 1
            }
            
            if rerank_scores:
                result['rerank_scores'] = rerank_scores[:len(cited_source_numbers)]
        
        return result
    
    def clear_memory(self):
        """Clear conversation memory."""
        if self.memory:
            try:
                self.memory.clear()
                print("✅ Conversation memory cleared")
            except Exception as e:
                print(f"⚠️  Error clearing memory: {e}")
    
    def get_memory_summary(self) -> str:
        """Get a summary of the conversation history."""
        if not self.memory or not self.enable_memory:
            return "No memory enabled"
        
        try:
            messages = self.memory.messages if hasattr(self.memory, 'messages') else []
            if messages:
                return f"Conversation has {len(messages)} messages ({len(messages)//2} exchanges)"
            else:
                return "No conversation history"
        except Exception as e:
            return f"Error accessing memory: {e}"
    
    def inspect_chromadb(self):
        """Inspect ChromaDB contents."""
        ChromaDBHelper = import_chromadb_helper()
        ChromaDBHelper.inspect_collection(self.vectorstore, show_data=True)
    
    def get_chromadb_data(self, limit: int = None) -> dict:
        """Get ChromaDB data as dictionary. If limit is set, only that many documents are fetched (faster)."""
        ChromaDBHelper = import_chromadb_helper()
        return ChromaDBHelper.get_collection_data(self.vectorstore, limit=limit)
    
    def delete_chromadb(self, collection_name: str = None, delete_all: bool = False):
        """
        Delete ChromaDB collection entirely.
        
        Args:
            collection_name: Specific collection name to delete
            delete_all: If True, deletes all ChromaDB collections
        """
        ChromaDBHelper = import_chromadb_helper()
        
        ChromaDBHelper.delete_collection(
            collection_name=collection_name,
            persist_directory=self.persist_directory,
            delete_all=delete_all
        )
        
        self.vectorstore = None
        self.retriever = None
        self.all_documents = []
        self.keyword_retriever = None
        self.hybrid_retriever = None
    
    def _init_keyword_retriever(self):
        """Initialize BM25 keyword retriever for hybrid search."""
        if not BM25_AVAILABLE or not self.use_hybrid_search or not self.all_documents:
            return
        
        self.keyword_retriever = BM25Retriever.from_documents(self.all_documents)
        self.keyword_retriever.k = self.retrieval_k
        
        if self.ensemble_available:
            from langchain.retrievers import EnsembleRetriever
            self.hybrid_retriever = EnsembleRetriever(
                retrievers=[self.retriever, self.keyword_retriever],
                weights=self.hybrid_weights
            )
    
