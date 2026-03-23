"""
RAG Configuration with Pydantic Settings
=========================================

Get RAG configuration from environment variables (.env file).
The .env file is the single source of truth for all RAG configurations.
Configuration is loaded once and cached for performance.
To change any setting, modify the corresponding value in config/.env file
and restart your application.
"""

from pydantic import Field, field_validator, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List
from pathlib import Path


class RAGConfig(BaseSettings):
    """
    RAG Configuration with automatic validation.
    Loads from config/.env file automatically with type checking.
    All RAG classes accept this config object.
    """
    
    # Embedding settings
    use_local_embeddings: bool = Field(
        default=True,
        description="Use local HuggingFace embeddings instead of OpenAI"
    )
    
    # Display settings
    show_matching_sources: bool = Field(
        default=False,
        description="Show matching sources after each query"
    )
    
    # Retrieval settings (shared: chunk_size, chunk_overlap, hybrid_weights, reranker_model)
    chunk_size: int = Field(
        default=1000,
        ge=100,
        le=10000,
        description="Size of text chunks for splitting documents"
    )
    chunk_overlap: int = Field(
        default=200,
        ge=0,
        description="Overlap between chunks"
    )
    retrieval_k: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Number of documents to retrieve (legacy; prefer CHAT_RETRIEVAL_K)"
    )

    # Chat retrieval (CHAT_* prefix)
    chat_min_similarity_threshold: float = Field(
        default=15.0,
        ge=0.0,
        le=100.0,
        validation_alias="CHAT_MIN_SIMILARITY_THRESHOLD",
        description="Chat: minimum similarity percentage (0-100) to include a source"
    )
    chat_retrieval_k: int = Field(
        default=10,
        ge=1,
        le=50,
        validation_alias="CHAT_RETRIEVAL_K",
        description="Chat: number of documents to retrieve"
    )
    chat_use_hybrid_search: bool = Field(
        default=False,
        validation_alias="CHAT_USE_HYBRID_SEARCH",
        description="Chat: enable hybrid search (semantic + BM25)"
    )
    chat_use_reranking: bool = Field(
        default=False,
        validation_alias="CHAT_USE_RERANKING",
        description="Chat: enable re-ranking with CrossEncoder"
    )
    chat_use_query_expansion: bool = Field(
        default=False,
        validation_alias="CHAT_USE_QUERY_EXPANSION",
        description="Chat: enable query expansion"
    )

    # Requirement analysis retrieval (REQUIREMENT_* prefix)
    requirement_tests_similarity_threshold: float = Field(
        default=50.0,
        ge=0.0,
        le=100.0,
        validation_alias="REQUIREMENT_TESTS_SIMILARITY_THRESHOLD",
        description="Requirement: minimum similarity (0-100) for retrieving related tests; lower = more inclusive so generated tests match"
    )
    requirement_specs_similarity_threshold: float = Field(
        default=50.0,
        ge=0.0,
        le=100.0,
        validation_alias="REQUIREMENT_SPECS_SIMILARITY_THRESHOLD",
        description="Requirement: minimum similarity (0-100) for retrieving related Confluence specs. Lower than test threshold because spec pages use broader business language."
    )
    requirement_retrieval_k: int = Field(
        default=10,
        ge=1,
        le=50,
        validation_alias="REQUIREMENT_RETRIEVAL_K",
        description="Requirement: number of related tests/specs to retrieve"
    )
    requirement_use_hybrid_search: bool = Field(
        default=False,
        validation_alias="REQUIREMENT_USE_HYBRID_SEARCH",
        description="Requirement: enable hybrid search for find_related_tests/specs"
    )
    requirement_use_reranking: bool = Field(
        default=False,
        validation_alias="REQUIREMENT_USE_RERANKING",
        description="Requirement: enable re-ranking for find_related_tests/specs"
    )
    
    # Advanced retrieval features (shared; Chat uses chat_*, Requirement uses requirement_*)
    hybrid_weights: List[float] = Field(
        default=[0.6, 0.4],
        description="Weights for hybrid search [semantic_weight, keyword_weight]"
    )
    reranker_model: str = Field(
        default="cross-encoder/ms-marco-MiniLM-L-6-v2",
        description="CrossEncoder model for re-ranking"
    )
    
    # ChromaDB settings
    persist_directory: str = Field(
        default="./chroma_db",
        alias="CHROMA_DB_DIR",
        description="Directory to persist ChromaDB data"
    )
    collection_name: str = Field(
        default="rag_collection",
        description="Name of ChromaDB collection"
    )
    
    # Caching settings
    enable_query_cache: bool = Field(
        default=True,
        description="Enable query result caching"
    )
    query_cache_size: int = Field(
        default=1000,
        ge=1,
        description="Maximum number of cached queries"
    )
    enable_embedding_cache: bool = Field(
        default=True,
        description="Enable embedding caching"
    )
    embedding_cache_dir: str = Field(
        default="./embedding_cache",
        description="Directory for embedding cache"
    )
    
    # Pydantic Settings configuration
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).parent.parent.parent / "config" / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,  # CHUNK_SIZE or chunk_size both work
        extra="ignore",  # Ignore extra fields in .env
    )
    
    @field_validator("chunk_overlap")
    @classmethod
    def validate_chunk_overlap(cls, v, info):
        """Ensure chunk_overlap is less than chunk_size."""
        chunk_size = info.data.get("chunk_size", 1000)
        if v >= chunk_size:
            raise ValueError(
                f"chunk_overlap ({v}) must be less than chunk_size ({chunk_size})"
            )
        return v
    
    @field_validator("hybrid_weights")
    @classmethod
    def validate_hybrid_weights(cls, v):
        """Ensure hybrid_weights has exactly 2 values and they sum to ~1.0."""
        if len(v) != 2:
            raise ValueError("hybrid_weights must have exactly 2 values")
        if abs(sum(v) - 1.0) > 0.01:
            raise ValueError(
                f"hybrid_weights must sum to 1.0, got {sum(v):.2f}"
            )
        return v
    
    # TestRail API Configuration
    testrail_url: str = Field(
        default="",
        description="TestRail instance URL"
    )
    testrail_email: str = Field(
        default="",
        description="Email for TestRail API authentication"
    )
    testrail_api_key: str = Field(
        default="",
        description="API key for TestRail"
    )
    # Stored as str so .env can use comma-separated values (e.g. 8 or 1,2,3); pydantic-settings
    # would otherwise try to parse List[int] as JSON and fail on "8,9".
    testrail_project_ids_str: str = Field(
        default="",
        alias="testrail_project_ids",
        description="Comma-separated TestRail project IDs to sync"
    )
    testrail_delta_days: int = Field(
        default=0,
        ge=0,
        description="Days to look back for updated test cases (0 = fetch all, no date filter)"
    )

    # Confluence API Configuration
    confluence_url: str = Field(
        default="",
        description="Confluence instance URL (e.g. https://company.atlassian.net/wiki)"
    )
    confluence_email: str = Field(
        default="",
        description="Email for Confluence API authentication"
    )
    confluence_api_token: str = Field(
        default="",
        description="API token for Confluence (Atlassian API tokens)"
    )
    confluence_cql: str = Field(
        default="type=page",
        description="Confluence Query Language (CQL) to filter pages; e.g. type=page, or type=page AND space=DEV"
    )
    confluence_delta_days: int = Field(
        default=0,
        ge=0,
        description="Days to look back for updated Confluence pages (0 = fetch all, no date filter)"
    )

    # Requirement analysis
    requirement_needs_update_confidence_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        validation_alias="REQUIREMENT_NEEDS_UPDATE_CONFIDENCE_THRESHOLD",
        description="Only suggest test needs_update if LLM confidence >= this (use 0-100 percentage e.g. 70, or 0-1 decimal e.g. 0.7)"
    )

    @field_validator("requirement_needs_update_confidence_threshold", mode="before")
    @classmethod
    def _normalize_confidence_threshold_percentage(cls, v: object) -> float:
        """Accept 0-100 (percentage) or 0-1 (decimal); store as 0-1 for comparison."""
        if v is None:
            return 0.7
        x = float(v)
        if 1.0 < x <= 100.0:
            return x / 100.0
        return x

    testrail_push_enabled: bool = Field(
        default=False,
        description="Allow pushing generated tests to TestRail (explicit user action required)"
    )

    @computed_field
    @property
    def testrail_project_ids(self) -> List[int]:
        """Parse comma-separated project IDs from env string."""
        v = self.testrail_project_ids_str
        if isinstance(v, str):
            if not v or v.strip() == '':
                return []
            return [int(pid.strip()) for pid in v.split(',') if pid.strip()]
        return []

    def to_dict(self, exclude_testrail: bool = True) -> dict:
        """
        Convert config to dictionary.
        Maps chat_* to the keys BaseRAG expects (min_similarity_threshold, retrieval_k, etc.).
        
        Args:
            exclude_testrail: If True, exclude TestRail-specific fields
        """
        data = self.model_dump()
        # Map chat_* to keys BaseRAG expects for Chat retrieval
        data['min_similarity_threshold'] = data.get('chat_min_similarity_threshold', 15.0)
        data['retrieval_k'] = data.get('chat_retrieval_k', 10)
        data['use_hybrid_search'] = data.get('chat_use_hybrid_search', False)
        data['use_reranking'] = data.get('chat_use_reranking', False)
        data['use_query_expansion'] = data.get('chat_use_query_expansion', False)
        
        # Remove chat_* and requirement_* raw fields (BaseRAG expects min_similarity_threshold, retrieval_k, etc.)
        for key in list(data.keys()):
            if key.startswith('chat_') or key.startswith('requirement_'):
                data.pop(key, None)
        if exclude_testrail:
            connector_fields = [
                'testrail_url', 'testrail_email', 'testrail_api_key',
                'testrail_project_ids', 'testrail_project_ids_str', 'testrail_delta_days',
                'confluence_url', 'confluence_email', 'confluence_api_token',
                'confluence_cql', 'confluence_delta_days',
                'requirement_needs_update_confidence_threshold',
                'testrail_push_enabled',
            ]
            for field in connector_fields:
                data.pop(field, None)
        
        return data


# Global settings instance - automatically loads from .env
_settings = None


def get_config() -> RAGConfig:
    global _settings
    if _settings is None:
        _settings = RAGConfig()
    return _settings


def reset_config() -> None:
    """
    Clear the cached RAGConfig singleton so the next get_config() call re-reads os.environ.

    Called by SettingsService after saving new values to ensure changes take effect
    immediately for services that call get_config() at instantiation time (e.g. sync services).

    Note: modules that imported `settings` directly at module level (e.g.
    `from backend.rag.settings import settings`) hold a stale reference and will not
    see the update until restarted. Services that call get_config() freshly on each
    request (TestRailSyncService, ConfluenceSyncService) are unaffected by this limitation.
    """
    global _settings
    _settings = None


# For convenience, also export settings directly
settings = get_config()
