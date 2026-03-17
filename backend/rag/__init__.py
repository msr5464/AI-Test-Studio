"""
RAG Package
===========

RAG (Retrieval-Augmented Generation) system supporting multiple document formats.
"""

from .base_rag import BaseRAG
from .multi_format_rag import MultiFormatRAG

__all__ = ["BaseRAG", "MultiFormatRAG"]

