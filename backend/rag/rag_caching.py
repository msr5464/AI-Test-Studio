"""
Caching Module for RAG Systems
===============================

Provides query result caching and embedding caching to improve performance.
"""

import os
import json
import hashlib
import pickle
import time
from typing import Optional, Dict, Any, Tuple, List
from pathlib import Path

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False
    # Create a dummy numpy-like class for type hints
    class np:
        @staticmethod
        def array(x):
            return x
        @staticmethod
        def load(path):
            return None


class QueryCache:
    """
    In-memory thread-safe LRU cache for query results.

    Caches query results to avoid redundant LLM calls and retrieval operations.
    """

    def __init__(self, max_size: int = 1000, enabled: bool = True):
        """
        Initialize query cache.

        Args:
            max_size: Maximum number of cached queries. Default: 1000
            enabled: Whether caching is enabled. Default: True
        """
        import threading
        self.cache: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.max_size = max_size
        self.enabled = enabled
        self.hits = 0
        self.misses = 0
    
    def _get_cache_key(self, query: str, **kwargs) -> str:
        """
        Generate cache key from query and parameters.
        
        Args:
            query: User query string
            **kwargs: Additional parameters (e.g., retrieval_k, use_hybrid_search)
            
        Returns:
            Cache key string
        """
        # Normalize query (lowercase, strip whitespace)
        normalized_query = query.lower().strip()
        
        # Create key data dict
        key_data = {"query": normalized_query}
        # Only include relevant kwargs that affect results
        relevant_kwargs = {
            k: v for k, v in kwargs.items() 
            if k in ['retrieval_k', 'use_hybrid_search', 'use_reranking', 'min_similarity_threshold']
        }
        key_data.update(relevant_kwargs)
        
        # Generate hash
        key_str = json.dumps(key_data, sort_keys=True)
        return hashlib.md5(key_str.encode()).hexdigest()
    
    def get(self, query: str, **kwargs) -> Optional[Dict[str, Any]]:
        """
        Get cached result.
        
        Args:
            query: User query string
            **kwargs: Additional parameters
            
        Returns:
            Cached result or None if not found
        """
        if not self.enabled:
            return None
        
        cache_key = self._get_cache_key(query, **kwargs)
        with self._lock:
            result = self.cache.get(cache_key)
            if result:
                # Move to end (LRU: most recently used)
                del self.cache[cache_key]
                self.cache[cache_key] = result
                self.hits += 1
                return result
            else:
                self.misses += 1
                return None

    def set(self, query: str, result: Dict[str, Any], **kwargs):
        """
        Cache result.

        Args:
            query: User query string
            result: Query result dictionary
            **kwargs: Additional parameters
        """
        if not self.enabled:
            return

        cache_key = self._get_cache_key(query, **kwargs)
        with self._lock:
            if cache_key in self.cache:
                del self.cache[cache_key]  # re-insert at end
            elif len(self.cache) >= self.max_size:
                oldest_key = next(iter(self.cache))
                del self.cache[oldest_key]
            self.cache[cache_key] = result

    def clear(self):
        """Clear cache."""
        with self._lock:
            self.cache.clear()
            self.hits = 0
            self.misses = 0
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.
        
        Returns:
            Dictionary with cache statistics
        """
        total_requests = self.hits + self.misses
        hit_rate = (self.hits / total_requests * 100) if total_requests > 0 else 0.0
        
        return {
            "enabled": self.enabled,
            "cache_size": len(self.cache),
            "max_size": self.max_size,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate_percent": round(hit_rate, 2),
            "total_requests": total_requests
        }


class EmbeddingCache:
    """
    File-based cache for embeddings.
    
    Caches document and query embeddings to avoid recomputation.
    """
    
    def __init__(self, cache_dir: str = "./embedding_cache", enabled: bool = True):
        """
        Initialize embedding cache.
        
        Args:
            cache_dir: Directory to store cached embeddings. Default: "./embedding_cache"
            enabled: Whether caching is enabled. Default: True
        """
        self.cache_dir = Path(cache_dir)
        self.enabled = enabled
        self.hits = 0
        self.misses = 0
        
        if self.enabled:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_cache_key(self, text: str) -> str:
        """
        Generate cache key from text.
        
        Args:
            text: Text to embed
            
        Returns:
            Cache key string
        """
        # Normalize text (lowercase, strip whitespace)
        normalized_text = text.lower().strip()
        return hashlib.md5(normalized_text.encode()).hexdigest()
    
    def _get_cache_path(self, cache_key: str) -> Path:
        """
        Get cache file path for a cache key.
        
        Args:
            cache_key: Cache key string
            
        Returns:
            Path to cache file
        """
        return self.cache_dir / f"{cache_key}.pkl"
    
    def get_embedding(self, text: str) -> Optional[np.ndarray]:
        """
        Get cached embedding.
        
        Args:
            text: Text that was embedded
            
        Returns:
            Cached embedding array or None if not found
        """
        if not self.enabled:
            return None
        
        cache_key = self._get_cache_key(text)
        cache_path = self._get_cache_path(cache_key)
        
        if cache_path.exists():
            try:
                with open(cache_path, 'rb') as f:
                    embedding = pickle.load(f)
                self.hits += 1
                if NUMPY_AVAILABLE:
                    return np.array(embedding) if not isinstance(embedding, np.ndarray) else embedding
                else:
                    return embedding
            except Exception as e:
                # If cache file is corrupted, delete it
                cache_path.unlink(missing_ok=True)
                return None
        
        self.misses += 1
        return None
    
    def save_embedding(self, text: str, embedding: np.ndarray):
        """
        Save embedding to cache.
        
        Args:
            text: Text that was embedded
            embedding: Embedding array
        """
        if not self.enabled:
            return
        
        cache_key = self._get_cache_key(text)
        cache_path = self._get_cache_path(cache_key)
        
        try:
            # Convert to list for better compatibility
            if NUMPY_AVAILABLE and isinstance(embedding, np.ndarray):
                embedding_list = embedding.tolist()
            else:
                embedding_list = embedding
            with open(cache_path, 'wb') as f:
                pickle.dump(embedding_list, f)
        except Exception as e:
            # Silently fail if cache write fails
            pass
    
    def get_embeddings_batch(self, texts: list) -> Tuple[Dict[int, Any], List[str], List[int]]:
        """
        Get cached embeddings for a batch of texts.
        
        Args:
            texts: List of texts to embed
            
        Returns:
            Tuple of (cached_embeddings_dict, uncached_texts, uncached_indices)
            cached_embeddings_dict: Dict mapping index to embedding
            uncached_texts: List of texts not in cache
            uncached_indices: List of indices for uncached texts
        """
        cached_embeddings = {}
        uncached_texts = []
        uncached_indices = []
        
        for i, text in enumerate(texts):
            embedding = self.get_embedding(text)
            if embedding is not None:
                cached_embeddings[i] = embedding
            else:
                uncached_texts.append(text)
                uncached_indices.append(i)
        
        return cached_embeddings, uncached_texts, uncached_indices
    
    def save_embeddings_batch(self, texts: list, embeddings: list):
        """
        Save embeddings for a batch of texts.
        
        Args:
            texts: List of texts that were embedded
            embeddings: List of embedding arrays
        """
        for text, embedding in zip(texts, embeddings):
            self.save_embedding(text, embedding)
    
    def clear(self):
        """Clear all cached embeddings."""
        if self.cache_dir.exists():
            for cache_file in self.cache_dir.glob("*.pkl"):
                cache_file.unlink(missing_ok=True)
        self.hits = 0
        self.misses = 0
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.
        
        Returns:
            Dictionary with cache statistics
        """
        cache_size = len(list(self.cache_dir.glob("*.pkl"))) if self.cache_dir.exists() else 0
        total_requests = self.hits + self.misses
        hit_rate = (self.hits / total_requests * 100) if total_requests > 0 else 0.0
        
        return {
            "enabled": self.enabled,
            "cache_size": cache_size,
            "cache_dir": str(self.cache_dir),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate_percent": round(hit_rate, 2),
            "total_requests": total_requests
        }


class CachedEmbeddings:
    """
    Wrapper around embeddings that adds caching.
    
    Can be used as a drop-in replacement for any embeddings object.
    """
    
    def __init__(self, embeddings, embedding_cache: EmbeddingCache):
        """
        Initialize cached embeddings wrapper.
        
        Args:
            embeddings: Original embeddings object (e.g., HuggingFaceEmbeddings)
            embedding_cache: EmbeddingCache instance
        """
        self.embeddings = embeddings
        self.cache = embedding_cache
    
    def embed_query(self, text: str) -> list:
        """
        Embed query with caching.
        
        Args:
            text: Query text
            
        Returns:
            Embedding vector
        """
        # Check cache
        cached = self.cache.get_embedding(text)
        if cached is not None:
            if NUMPY_AVAILABLE and isinstance(cached, np.ndarray):
                return cached.tolist()
            return cached if isinstance(cached, list) else list(cached)
        
        # Generate embedding
        embedding = self.embeddings.embed_query(text)
        
        # Save to cache
        if NUMPY_AVAILABLE:
            self.cache.save_embedding(text, np.array(embedding))
        else:
            self.cache.save_embedding(text, embedding)
        
        return embedding
    
    def embed_documents(self, texts: list) -> list:
        """
        Embed documents with caching.
        
        Args:
            texts: List of document texts
            
        Returns:
            List of embedding vectors
        """
        # Get cached embeddings
        cached_embeddings, uncached_texts, uncached_indices = self.cache.get_embeddings_batch(texts)
        
        # Generate embeddings for uncached texts
        if uncached_texts:
            new_embeddings = self.embeddings.embed_documents(uncached_texts)
            # Save new embeddings to cache
            if NUMPY_AVAILABLE:
                self.cache.save_embeddings_batch(uncached_texts, [np.array(e) for e in new_embeddings])
            else:
                self.cache.save_embeddings_batch(uncached_texts, new_embeddings)
        else:
            new_embeddings = []
        
        # Combine cached and new embeddings in correct order
        all_embeddings = [None] * len(texts)
        for idx, emb in cached_embeddings.items():
            if NUMPY_AVAILABLE and isinstance(emb, np.ndarray):
                all_embeddings[idx] = emb.tolist()
            else:
                all_embeddings[idx] = emb if isinstance(emb, list) else list(emb)
        
        for idx, emb in zip(uncached_indices, new_embeddings):
            all_embeddings[idx] = emb
        
        return all_embeddings

