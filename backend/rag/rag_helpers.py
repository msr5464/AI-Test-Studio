"""
RAG Helper Utilities
====================

Utility functions and classes for RAG operations.
"""

import sys
import re
import hashlib
from pathlib import Path
from typing import List, Optional, Any
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, AIMessage


def debug_log(message: str, debug_mode: bool = False):
    """
    Print debug message only if debug_mode is enabled.
    
    Args:
        message: Message to print
        debug_mode: If True, print the message. Default: False
    """
    if debug_mode:
        print(message)


def import_chromadb_helper():
    """Import ChromaDBHelper with fallback for direct script execution."""
    try:
        from .chromadb_helper import ChromaDBHelper
        return ChromaDBHelper
    except ImportError:
        from backend.rag.chromadb_helper import ChromaDBHelper
        return ChromaDBHelper


class SimpleMemory:
    """Simple in-memory conversation history."""
    
    def __init__(self, memory_type='buffer', max_messages=None):
        self.memory_type = memory_type
        self.max_messages = max_messages
        self.chat_history = []
        self.memory_key = "chat_history"
    
    def save_context(self, inputs: dict, outputs: dict):
        """Save a conversation turn."""
        question = inputs.get('input', '')
        answer = outputs.get('output', '')
        
        self.chat_history.append(HumanMessage(content=question))
        self.chat_history.append(AIMessage(content=answer))
        
        if self.memory_type == 'window' and self.max_messages:
            if len(self.chat_history) > self.max_messages * 2:
                self.chat_history = self.chat_history[-self.max_messages * 2:]
    
    def clear(self):
        """Clear conversation history."""
        self.chat_history = []
    
    @property
    def messages(self):
        """Get conversation messages."""
        return self.chat_history


def calculate_similarity(distance: float) -> tuple:
    """
    Convert cosine distance to similarity score.
    
    Args:
        distance: Cosine distance (0-2, where 0 = identical, 2 = opposite)
    
    Returns:
        Tuple of (similarity, similarity_percent)
    """
    similarity = 1 - (distance / 2)
    return similarity, similarity * 100


def sanitize_documents(documents: List[Document]) -> List[Document]:
    """
    Ensure every document has page_content as a string (never None).
    ChromaDB/retrievers can sometimes return docs with None content, which breaks Pydantic validation.
    """
    out = []
    for doc in documents:
        content = doc.page_content if doc.page_content is not None else ""
        out.append(Document(page_content=content, metadata=doc.metadata or {}))
    return out


def deduplicate_documents(documents: List[Document]) -> List[Document]:
    """
    Remove duplicate documents based on content hash.
    
    Args:
        documents: List of documents
    
    Returns:
        List of unique documents
    """
    seen = set()
    unique_docs = []
    
    for doc in documents:
        content = doc.page_content or ""
        content_hash = hashlib.md5(content[:200].encode("utf-8", errors="replace"), usedforsecurity=False).hexdigest()
        if content_hash not in seen:
            seen.add(content_hash)
            unique_docs.append(doc)
    
    return unique_docs


def calculate_file_hash(file_path: Path) -> str:
    """
    Calculate SHA256 hash of a file.
    
    Args:
        file_path: Path to file
    
    Returns:
        Hexadecimal hash string
    """
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def calculate_content_hash(content: str) -> str:
    """
    Calculate SHA256 hash of content string.
    
    Args:
        content: Content string
    
    Returns:
        Hexadecimal hash string
    """
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def add_file_metadata_to_documents(documents: List[Document], file_path: Optional[Path] = None, 
                                   file_hash: Optional[str] = None) -> List[Document]:
    """
    Add file metadata to documents for tracking and deduplication.
    
    Args:
        documents: List of Document objects
        file_path: Path to source file
        file_hash: Hash of the file/content
    
    Returns:
        List of Document objects with metadata
    """
    import time
    
    for doc in documents:
        if not doc.metadata:
            doc.metadata = {}
        
        if file_path:
            doc.metadata['file_path'] = str(file_path.absolute())
            doc.metadata['file_name'] = file_path.name
        
        if file_hash:
            doc.metadata['file_hash'] = file_hash
        
        doc.metadata['upload_timestamp'] = time.time()
    
    return documents


# RAG System Message Template
RAG_SYSTEM_MESSAGE = """
You are a Testcase Intelligence Assistant, a specialized AI helper for test case management, test design, and test suite analysis.

Your primary goal is to answer questions about test cases using the provided RAG context, and to generate improved or new test cases when explicitly requested.

========================
CORE RESPONSIBILITIES
========================
You have two responsibilities:

A) Improve and standardize existing test cases using retrieved context.
B) Generate new test cases ONLY when the user explicitly asks to create new ones, and ONLY by extrapolating from patterns in the retrieved context.

========================
CONTEXT USAGE RULES
========================
- Read the context carefully and extract relevant information from ALL provided sources.
- Use the retrieved context as the primary source of truth for product behavior, steps, assertions, and validations.
- If information is partially available, provide the best possible answer using what is available.
- If information is missing, clearly mention what is missing.
- You may infer missing formatting or structure (e.g., clearer steps, expected results, preconditions).
- You may use general QA expertise to improve clarity, testability, and completeness.
- Never introduce new product rules, limits, screens, API fields, or compliance requirements unless supported by context.
- If assumptions are unavoidable (only when generating new test cases), keep them minimal and clearly label them as assumptions.

========================
STRICT BEHAVIOR RULES
========================
- Do NOT include source citations like [Source 1] or [Source 2].
- Be direct, confident, and clear-cut.
- Avoid overly cautious language.
- Be thorough and extract all relevant details from the context.
- Rephrase test case content for clarity. Do not copy-paste raw text.
- Preserve meaning while improving readability.

========================
TEST CASE SPECIALIZATION
========================
- Always include the **Testrail Id** and **Priority** when listing or describing test cases.
- Treat the **Testrail Id** as the primary identifier for any test case.
- Preserve **Testrail Id** and **Priority** exactly as they appear in context.

Priority Ordering Rules:
- When asked for "top" test cases or lists, strictly sort them by priority:
  **P0 > P1 > P2 > P3**
- Show all P0 cases first, then P1, then P2, then P3.

========================
TEST CASE GENERATION RULES (ONLY IF USER ASKS)
========================
When the user explicitly requests creation of new test cases:
- Generate new test cases by extrapolating patterns, flows, and validations seen in the retrieved context.
- Reuse the same structure, tone, and validation style from existing test cases.
- If the user requests a new market/location suite (e.g., SG -> US):
    * Reuse the same workflow and checks.
    * Modify only market-specific parameters if the context supports such differences.
    * If market-specific rules are not present, mark those fields as TBD and list them under Assumptions.
- Never fabricate compliance rules, legal validations, country-specific requirements, or system limits unless context supports them.

Generated tests must always include a "Generated" label.

========================
OUTPUT FORMAT RULES (STRICT)
========================
For all testcase-related output, follow this structure.

IMPORTANT: When listing multiple test cases, start EACH test case with exactly ONE title line: a level-3 markdown heading. Put the Testrail Id at the START of the line, then the title. Use only this pattern (nothing else for the title):

### (Testrail Id or NA) [Test Case Title]

Then continue with the fields below. Do NOT add a second title line such as "Test Case 1: ..." or "Test Scenario 1: ..."—the ### line is the only title. The Testrail Id must be first in that ### line (e.g. "### (C19) Verify login lockout duration").

* Priority: [Priority]
* Module/Feature: [Module or NA]  (include only if available)
* Type: [Functional/Regression/Smoke/API/UI/Integration or NA] (include only if available)

* Preconditions:  (ONLY if available; if empty or 'nan', omit this entire section)
  + <precondition 1>
  + <precondition 2>

* Test Data: (ONLY if available; if empty omit)
  + <field>: <value>
  + <field>: <value>

* Steps: (ONLY if available; if empty or 'nan', omit this entire section)
  1. <step>
  2. <step>

* Expected Result: (ONLY if available; if empty or 'nan', omit this entire line)
  + <expected result>

* Notes / Checks: (ONLY if relevant; omit if empty)
  + <edge case / validation / logging / audit / localization>

* Traceability: (ONLY if test is generated)
  + Derived from: <reference test case ids/titles>

* Generated: Yes/No

========================
FORMATTING REQUIREMENTS
========================
- Use proper line breaks and spacing between sections.
- Format lists using markdown bullet points (* or -).
- Use numbered lists (1., 2., 3.) for sequential items.
- Add blank lines between different items or sections.
- Ensure readability with clear separation.
- Do NOT merge everything into a single paragraph.
- If formatting is provided by the user, follow the user's formatting instructions strictly.

========================
FOLLOW-UP QUESTIONS
========================
- If this is a follow-up question, use conversation history to maintain context.
- Provide complete answers without truncation.
"""


def extract_answer_from_llm_result(result: Any) -> str:
    """
    Extract answer text from LLM result object.
    
    Args:
        result: LLM result object (can be various types)
    
    Returns:
        Answer string
    """
    if hasattr(result, 'content'):
        return result.content
    elif isinstance(result, str):
        return result
    else:
        return str(result)


def expand_query(query: str, use_query_expansion: bool = False, llm=None) -> List[str]:
    """
    Expand query with alternative phrasings using LLM when available.

    Args:
        query: Original query string
        use_query_expansion: If True, expand the query
        llm: Optional LLM instance. When provided, generates 2 semantic reformulations.
             When None, falls back to basic punctuation-stripping expansion.

    Returns:
        List of query strings (original + expansions)
    """
    if not use_query_expansion:
        return [query]

    if llm is not None:
        try:
            prompt = (
                "Generate 2 alternative phrasings for the following test-case search query. "
                "Return only the phrasings, one per line, no numbering or extra text.\n"
                f"Query: {query}"
            )
            result = llm.invoke(prompt)
            raw = result.content if hasattr(result, 'content') else str(result)
            variants = [line.strip() for line in raw.strip().split('\n') if line.strip()]
            return [query] + variants[:2]
        except Exception:
            pass  # Fall through to basic expansion on LLM failure

    # Basic fallback: strip trailing punctuation for a cleaner keyword variant
    expanded = [query]
    stripped = query.replace("?", "").strip()
    if stripped and stripped != query:
        expanded.append(stripped)
    return expanded


def extract_cited_sources(answer: str) -> set:
    """
    Extract cited source numbers from answer text.
    
    Args:
        answer: Answer text containing source citations like [Source 1], [Source 2]
    
    Returns:
        Set of cited source numbers (1-indexed). Empty set if no citations found.
    """
    source_pattern = r'\[Source\s+(\d+)\]'
    matches = re.findall(source_pattern, answer, re.IGNORECASE)
    cited_source_numbers = {int(num) for num in matches}
    
    return cited_source_numbers


def rerank_documents(query: str, documents: List[Document], reranker: Any, top_k: int) -> List[tuple]:
    """
    Re-rank documents using CrossEncoder.
    
    Args:
        query: Query string
        documents: List of documents to re-rank
        reranker: CrossEncoder instance (or None)
        top_k: Number of top documents to return
    
    Returns:
        List of tuples (score, document) sorted by score descending
    """
    if not reranker:
        return [(0.0, doc) for doc in documents[:top_k]]
    
    try:
        pairs = [[query, doc.page_content] for doc in documents]
        scores = reranker.predict(pairs)
        scored_docs = list(zip(scores, documents))
        scored_docs.sort(key=lambda x: x[0], reverse=True)
        return scored_docs[:top_k]
    except Exception as e:
        print(f"⚠️  Error re-ranking documents: {e}")
        return [(0.0, doc) for doc in documents[:top_k]]

