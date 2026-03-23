"""
Requirement Extractor
====================
Parses requirement spec text into structured requirements: List[{id, title, description}].
Uses regex for common patterns: REQ-001, Requirement 1, R-1, ## 1., US #1, User Story #2, etc.

Optionally enriches requirements with document context using LLM (enrich_requirements_with_context).
"""

import json
import re
from typing import List, Dict, Any, Optional


# Combined pattern to find requirement headers: REQ-001, Requirement 1, R-1, ## 1., US #1, User Story #2, etc.
REQ_HEADER = re.compile(
    r'(?:(REQ[-_]?\d+)|Requirement\s+(\d+)|\bR[-_](\d+)|^#{1,4}\s+(\d+)[.)]|\b(?:US|User\s+Story)\s*#?\s*(\d+))\s*[:\-]?\s*',
    re.IGNORECASE | re.MULTILINE
)
SECTION_HEADING = re.compile(r'^(#{1,4})\s+(.+)$', re.MULTILINE)
CONFLUENCE_HEADING = re.compile(r'^(h[1-6])\.\s+(.+)$', re.IGNORECASE | re.MULTILINE)
NUMBERED_SECTION = re.compile(r'^(\d+)[.)]\s+(.+)$', re.MULTILINE)
USER_STORY_LINE = re.compile(
    r'^(User\s+story|Scenario|Feature|As\s+a\s+user|Given|When|Acceptance\s+criteria)\s*[:\-]?\s*(.*)$',
    re.IGNORECASE | re.MULTILINE
)
MAX_FALLBACK_REQUIREMENTS = 30

# Pattern to extract user story statement: "As a/an X, I should/want/would/can/need..."
USER_STORY_STATEMENT = re.compile(
    r'As\s+an?\s+[^,]+,?\s+I\s+(?:should|want|would|can|need|will|must|am able to)\s+.+',
    re.IGNORECASE
)

# Lines to strip from descriptions (status/metadata noise)
STATUS_LINE_PATTERNS = [
    # Alpha status : BE done, FE to be prio'd...
    re.compile(r'^.*Alpha\s+(?:status|dev\s+status)\s*:\s*.{0,100}$', re.IGNORECASE | re.MULTILINE),
    # BE done, FE to be prio'd post-hibernation
    re.compile(r'^.*(?:BE|FE|Backend|Frontend)\s*(?:done|to be|prio|status|:)\b.{0,100}$', re.IGNORECASE | re.MULTILINE),
    # Prio post-hibernation, TxM : Prio post-hibernation
    re.compile(r'^.*(?:Prio|Priority)\s*(?:post-hibernation|:|done)\b.*$', re.IGNORECASE | re.MULTILINE),
    # TxM : ..., DE : ..., NPP : ...
    re.compile(r'^(?:TxM|DE|NPP)\s*:\s*.{0,100}$', re.IGNORECASE | re.MULTILINE),
    # Standalone status words
    re.compile(r'^\s*(?:IN PROGRESS|Complete|Green|Yellow|Red)\s*$', re.IGNORECASE | re.MULTILINE),
    # Connectivity established for dev env / Prod connectivity to be done...
    re.compile(r'^.*Connectivity\s+(?:established|to be done)\b.*$', re.IGNORECASE | re.MULTILINE),
]

# Template placeholder markers (skip requirements that are just templates)
TEMPLATE_MARKERS = ['[user]', '[action]', '[goal]', '[objective]', '[feature]']


def _norm_id(m: re.Match) -> str:
    """Normalize match to REQ-XXX id."""
    for g in m.groups():
        if g:
            if 'REQ' in str(g).upper():
                return str(g).upper().replace('_', '-')
            return f"REQ-{g}"
    return "REQ-1"


def _extract_user_story_title(content: str) -> str:
    """Extract 'As a/an X, I should/want...' statement from content as the requirement title.
    Falls back to first line if no user story pattern found.
    """
    match = USER_STORY_STATEMENT.search(content)
    if match:
        story = match.group().strip()
        # Truncate at sentence end or 200 chars
        for end_char in ['.', '\n']:
            idx = story.find(end_char)
            if idx > 20:
                story = story[:idx]
                break
        return story[:200].strip()
    # Fallback: first non-empty line
    for line in content.split('\n'):
        line = line.strip()
        if line and len(line) >= 5:
            return line[:200]
    return content[:200]


def _clean_description(content: str) -> str:
    """Remove status/metadata noise lines and user story line from requirement description.
    The user story line becomes the title, so we remove it from description to avoid redundancy.
    Only removes user story if there's other content remaining.
    """
    result = content
    for pattern in STATUS_LINE_PATTERNS:
        result = pattern.sub('', result)
    # Remove leading bracket labels like [Tech onboarding]-, [Account management] -
    result = re.sub(r'^\s*\[[^\]]+\]\s*[-–—]?\s*', '', result, flags=re.MULTILINE)
    # Remove user story line (it's used as title, so redundant in description)
    # But only if there's other content remaining
    candidate = USER_STORY_STATEMENT.sub('', result, count=1).strip()
    if candidate and len(candidate) >= 10:
        result = candidate
    # Clean up multiple blank lines
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result.strip()


def _is_template_placeholder(content: str) -> bool:
    """Check if content is a template placeholder (e.g. 'As a [user], I want to [action]')."""
    content_lower = content.lower()
    marker_count = sum(1 for m in TEMPLATE_MARKERS if m in content_lower)
    return marker_count >= 2  # At least 2 markers suggests it's a template


def _ensure_unique_ids(requirements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ensure all requirement IDs are unique by appending suffix when duplicates found."""
    seen: Dict[str, int] = {}
    result = []
    for req in requirements:
        orig_id = req["id"]
        if orig_id in seen:
            seen[orig_id] += 1
            req["id"] = f"{orig_id}-{seen[orig_id]}"
        else:
            seen[orig_id] = 1
        result.append(req)
    return result


def _split_by_sections(text: str) -> List[tuple]:
    """Split text into (title, content) chunks by section patterns. Returns list of (title, content)."""
    chunks = []
    for m in SECTION_HEADING.finditer(text):
        title = m.group(2).strip()[:200]
        start = m.end()
        next_m = SECTION_HEADING.search(text, start)
        end = next_m.start() if next_m else len(text)
        content = text[start:end].strip()
        if content and len(content) >= 20:
            chunks.append((title, content))
    if chunks:
        return chunks[:MAX_FALLBACK_REQUIREMENTS]
    for m in CONFLUENCE_HEADING.finditer(text):
        title = m.group(2).strip()[:200]
        start = m.end()
        next_m = CONFLUENCE_HEADING.search(text, start)
        end = next_m.start() if next_m else len(text)
        content = text[start:end].strip()
        if content and len(content) >= 20:
            chunks.append((title, content))
    if chunks:
        return chunks[:MAX_FALLBACK_REQUIREMENTS]
    for m in NUMBERED_SECTION.finditer(text):
        rest = m.group(2).strip()
        title = rest[:200] if len(rest) < 500 else (rest[:197] + "...")
        start = m.end()
        next_m = NUMBERED_SECTION.search(text, start)
        end = next_m.start() if next_m else len(text)
        content = text[start:end].strip()
        if content and len(content) >= 15:
            chunks.append((title, content))
    if chunks:
        return chunks[:MAX_FALLBACK_REQUIREMENTS]
    for m in USER_STORY_LINE.finditer(text):
        label = m.group(1)
        rest = (m.group(2) or "").strip()
        title = f"{label}: {rest[:150]}" if rest else label
        start = m.end()
        next_m = USER_STORY_LINE.search(text, start)
        end = next_m.start() if next_m else len(text)
        content = text[start:end].strip()
        if content and len(content) >= 15:
            chunks.append((title[:200], content))
    return chunks[:MAX_FALLBACK_REQUIREMENTS] if chunks else []


def extract_requirements(text: str) -> List[Dict[str, Any]]:
    """
    Extract structured requirements from spec text.

    Args:
        text: Raw requirement spec text (from file, Confluence, or paste).

    Returns:
        List of dicts: [{"id": "REQ-001", "title": "...", "description": "..."}, ...]
    """
    if not text or not text.strip():
        return []

    text = text.strip()
    requirements = []
    matches = list(REQ_HEADER.finditer(text))

    if matches:
        for i, m in enumerate(matches):
            req_id = _norm_id(m)
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            content = text[start:end].strip()
            if not content:
                continue
            # Skip template placeholders (e.g. "As a [user], I want to [action]...")
            if _is_template_placeholder(content):
                continue
            # Extract user story as title instead of first line
            title = _extract_user_story_title(content)
            # Clean status/metadata noise from description
            description = _clean_description(content)
            requirements.append({
                "id": req_id,
                "title": title,
                "description": description,
            })
        # Ensure unique IDs (handle duplicate US #1, US #1 etc.)
        return _ensure_unique_ids(requirements)

    # No REQ-XXX headers: try section-based split (Confluence / markdown / user stories)
    chunks = _split_by_sections(text)
    if chunks:
        for i, (section_title, content) in enumerate(chunks):
            # Skip template placeholders
            if _is_template_placeholder(content):
                continue
            req_id = f"REQ-{i + 1:03d}"
            # Try to extract user story as title; fall back to section title
            user_story_title = _extract_user_story_title(content)
            # If user story found, use it; otherwise use section title
            if USER_STORY_STATEMENT.search(content):
                title = user_story_title
            else:
                title = section_title
            description = _clean_description(content)
            requirements.append({
                "id": req_id,
                "title": title,
                "description": description,
            })
        return _ensure_unique_ids(requirements)

    # Fallback: no structured headers found — use first line or first 200 chars as title
    first_line = text.split("\n")[0].strip() if text else ""
    if first_line and len(first_line) >= 10:
        title = first_line[:200].strip()
    else:
        title = (text[:200].strip() + ("..." if len(text) > 200 else "")) or "Requirement (extracted from document)"
    return [{
        "id": "REQ-1",
        "title": title,
        "description": _clean_description(text[:8000]),
    }]


def enrich_requirements_with_context(
    requirements: List[Dict[str, Any]],
    document_text: str,
    llm: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """
    Use LLM to enrich requirement titles with document context.
    
    The document may describe a broader initiative (e.g. "AU market rollout", "AUD accounts via ASL").
    This function rewrites each requirement title to include that context, making requirements
    self-contained and meaningful.
    
    Args:
        requirements: List of requirements from extract_requirements()
        document_text: Full document text for context extraction
        llm: LangChain LLM instance (if None, returns requirements unchanged)
    
    Returns:
        Requirements with enriched titles (original titles preserved in 'original_title')
    """
    if not llm or not requirements:
        return requirements
    
    # Extract document context (objective, key terms) from first part of document
    context_text = document_text[:3000]
    
    # Build a single prompt to rewrite all requirement titles with context
    req_list_str = "\n".join([
        f"{i+1}. [{r['id']}] {r['title']}"
        for i, r in enumerate(requirements)
    ])
    
    from langchain_core.prompts import ChatPromptTemplate
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", """You are a requirements analyst. Given a document's context and a list of extracted requirements, rewrite each requirement title to be self-contained and include the broader context.

Rules:
1. Keep the "As a [role], I should be able to..." format (fix grammar if needed, e.g. "As an Tech team" → "As a Tech team")
2. Add context about:
   - The END GOAL (e.g. "to create AUD bank accounts", "to enable payments in AU")
   - The PRODUCT/MARKET (e.g. "for AU market", "for AUD accounts")
   - Key integrations mentioned (e.g. "via ASL", "using NPP/BECS rails")
3. Make each title self-explanatory without reading the full document
4. Focus on WHAT the user achieves, not just the technical step
5. Keep titles concise (under 150 chars)

Example transformations:
- "As a Tech team, I should establish connectivity with sandbox" → "As a Tech team, I should be able to connect with the sandbox to enable bank account creation"
- "As a user, I would like to create counterparties" → "As a user, I should be able to create counterparties for my account to send funds"

Return ONLY valid JSON array (no markdown):
[
  {{"id": "REQ-1", "title": "rewritten title with context"}},
  {{"id": "REQ-2", "title": "rewritten title with context"}}
]"""),
        ("human", """Document context:
{context}

Requirements to enrich:
{requirements}

JSON array:"""),
    ])
    
    try:
        chain = prompt | llm
        result = chain.invoke({
            "context": context_text,
            "requirements": req_list_str,
        })
        raw = result.content if hasattr(result, "content") else str(result)
        
        # Parse JSON response
        match = re.search(r'\[[\s\S]*\]', raw)
        if match:
            enriched_list = json.loads(match.group())
            enriched_map = {item["id"]: item["title"] for item in enriched_list}
            
            # Update requirements with enriched titles
            for req in requirements:
                if req["id"] in enriched_map:
                    req["original_title"] = req["title"]
                    req["title"] = enriched_map[req["id"]][:200]
    except Exception as e:
        print(f"⚠️  enrich_requirements_with_context failed: {e}")
        # Return original requirements unchanged
    
    return requirements


def clean_descriptions_with_llm(
    requirements: List[Dict[str, Any]],
    llm: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """
    Use LLM to clean up requirement descriptions into readable summaries.
    
    Removes:
    - Raw HTML tags (<aside>, etc.)
    - API documentation and sample payloads
    - Internal references ("refer here", "flows here", URLs)
    - Technical implementation details
    
    Keeps:
    - Key acceptance criteria
    - Business rules and workflows
    - Important user-facing details
    
    Args:
        requirements: List of requirements with raw descriptions
        llm: LangChain LLM instance (if None, returns requirements unchanged)
    
    Returns:
        Requirements with cleaned descriptions (original in 'raw_description')
    """
    if not llm or not requirements:
        return requirements
    
    from langchain_core.prompts import ChatPromptTemplate

    # Only clean requirements with non-trivial descriptions
    to_clean = [(i, req) for i, req in enumerate(requirements)
                if req.get("description", "") and len(req.get("description", "")) >= 50]
    if not to_clean:
        return requirements

    # Build numbered blocks for batch call
    blocks = []
    for idx, (_, req) in enumerate(to_clean):
        raw_desc = req.get("description", "")[:3000]
        blocks.append(f"[{idx + 1}]\n{raw_desc}")
    batch_text = "\n\n---\n\n".join(blocks)

    batch_prompt = ChatPromptTemplate.from_messages([
        ("system", """You are a technical writer. Clean up raw requirement descriptions into readable, structured summaries.

For EACH numbered requirement block, produce a clean description.

REMOVE: HTML tags, API endpoints/payloads, JSON, internal URLs, code snippets, technical configs, status updates.
KEEP: What user/system can do, key acceptance criteria, business rules, workflow steps (simplified).
FORMAT: Brief summary sentence + key bullet points (* for bullets). Keep 200-400 chars each.

Return ONLY a valid JSON array in the same order:
[
  {{"id": 1, "cleaned": "cleaned text here"}},
  {{"id": 2, "cleaned": "cleaned text here"}}
]"""),
        ("human", """{descriptions}

JSON array:"""),
    ])

    try:
        chain = batch_prompt | llm
        result = chain.invoke({"descriptions": batch_text})
        raw = result.content if hasattr(result, "content") else str(result)
        raw = raw.strip()
        match = re.search(r"\[[\s\S]*\]", raw)
        if match:
            cleaned_list = json.loads(match.group())
            cleaned_map = {item["id"]: item["cleaned"] for item in cleaned_list
                           if isinstance(item, dict) and "id" in item and "cleaned" in item}
            for idx, (_, req) in enumerate(to_clean):
                cleaned = cleaned_map.get(idx + 1, "").strip()
                if cleaned and len(cleaned) >= 20:
                    req["raw_description"] = req.get("description", "")
                    req["description"] = cleaned[:1000]
            print(f"[clean_descriptions] Batch cleaned {len(cleaned_map)}/{len(to_clean)} requirements in one call")
            return requirements
        else:
            print("⚠️  clean_descriptions_with_llm batch: no JSON array found, falling back to sequential")
    except Exception as e:
        print(f"⚠️  clean_descriptions_with_llm batch failed: {e}, falling back to sequential")

    # Sequential fallback
    single_prompt = ChatPromptTemplate.from_messages([
        ("system", """You are a technical writer. Clean up the raw requirement description into a readable, structured summary.

REMOVE: HTML tags, API documentation, endpoints, sample payloads, JSON, internal references, URLs, code snippets, technical configs, status updates.
KEEP: What the user/system will be able to do, key acceptance criteria, business rules, constraints, workflow steps (simplified).
FORMAT: Brief summary sentence + key points as bullet items (* for bullets). Keep concise (200-400 chars).

Return ONLY the cleaned description text, nothing else."""),
        ("human", """Raw description:
{description}

Cleaned description:"""),
    ])
    for _, req in to_clean:
        raw_desc = req.get("description", "")
        try:
            chain = single_prompt | llm
            result = chain.invoke({"description": raw_desc[:3000]})
            cleaned = result.content if hasattr(result, "content") else str(result)
            cleaned = cleaned.strip()
            if cleaned and len(cleaned) >= 20:
                req["raw_description"] = raw_desc
                req["description"] = cleaned[:1000]
        except Exception as e:
            print(f"⚠️  clean_descriptions_with_llm failed for {req.get('id')}: {e}")

    return requirements
