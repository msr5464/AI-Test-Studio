"""
RAG Imports Module
==================

Centralized import handling with fallbacks.
"""

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

