"""k7e distillation — extract knowledge from raw experience.

Scans raw files (journals, transcripts, command output). Extracts knowledge
candidates. Diffs against existing store. Stores genuine deltas.

Uses ollama or gemini CLI for LLM extraction. Falls back to pattern-based
extraction if neither is available.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import engine

MIN_CONTENT_LENGTH = 20
REJECT_PATTERNS = [
    r"^(ok|okay|sure|yes|no|got it|thanks|thank you|hi|hello|hey)\.?$",
    r"^.{0,10}$",  # anything under 10 chars
]


def _should_reject(text):
    """Reject trivial content that isn't worth storing."""
    text = text.strip()
    if len(text) < MIN_CONTENT_LENGTH:
        return True
    for pattern in REJECT_PATTERNS:
        if re.match(pattern, text, re.IGNORECASE):
            return True
    return False


def _score_importance(title, content):
    """Score 1-10 based on content patterns. Higher = more operationally important."""
    score = 5  # default
    text = (title + " " + content).lower()
    # Boost patterns
    if any(w in text for w in ["error", "fix", "bug", "crash", "failure"]):
        score += 2
    if any(w in text for w in ["security", "credential", "secret", "auth"]):
        score += 2
    if any(w in text for w in ["never", "always", "must", "critical"]):
        score += 1
    if any(w in text for w in ["prefer", "suggestion", "might", "could"]):
        score -= 1
    if any(w in text for w in ["til", "today i learned", "interesting"]):
        score -= 1
    return max(1, min(10, score))


def distill(paths, dry_run=False):
    results = []
    for path in paths:
        p = Path(path)
        if p.is_dir():
            files = sorted(p.rglob("*.md"))
        else:
            files = [p]
        for f in files:
            candidates = extract_from_file(f)
            candidates = [c for c in candidates if not _should_reject(c["content"])]
            new_knowledge = diff_against_store(candidates)
            if dry_run:
                for item in new_knowledge:
                    results.append({"action": "would_store", "title": item["title"], "source": str(f)})
            else:
                for item in new_knowledge:
                    importance = _score_importance(item["title"], item["content"])
                    if item.get("_supersedes"):
                        # Almost identical — store new and supersede old
                        node_id = engine.store_entry(
                            title=item["title"],
                            content=item["content"],
                            tags=item.get("tags", []),
                            importance=importance,
                        )
                        engine.supersede(item["_supersedes"], node_id)
                        results.append({"action": "superseded", "id": node_id, "old_id": item["_supersedes"], "title": item["title"], "source": str(f)})
                    elif item.get("_append_to"):
                        # Append to existing node instead of creating new
                        engine.append_entry(item["_append_to"], "Edge Cases", item["content"])
                        results.append({"action": "appended", "id": item["_append_to"], "title": item["title"], "source": str(f)})
                    else:
                        node_id = engine.store_entry(
                            title=item["title"],
                            content=item["content"],
                            tags=item.get("tags", []),
                            importance=importance,
                        )
                        results.append({"action": "stored", "id": node_id, "title": item["title"], "source": str(f)})
    return results


def extract_from_file(path):
    text = Path(path).read_text(encoding="utf-8")
    candidates = _pattern_extract(text)
    llm_candidates = _llm_extract(text)
    if llm_candidates:
        candidates.extend(llm_candidates)
    return candidates


def diff_against_store(candidates):
    new = []
    for candidate in candidates:
        # Two-stage dedup: broad search, then content overlap check
        # Stage 1: search by content keywords (not title — titles often differ)
        content_terms = " ".join(
            w for w in candidate["content"].split()[:20]
            if len(w) > 3
        )
        search_query = content_terms or candidate["title"]
        results = engine.search(search_query, limit=5)

        if not results:
            new.append(candidate)
            continue

        # Stage 2: check content overlap against top results
        candidate_terms = set(
            w.lower() for w in re.findall(r"\b\w{4,}\b", candidate["content"])
        )
        if not candidate_terms:
            new.append(candidate)
            continue

        best_overlap = 0.0
        best_match_id = None
        for result in results:
            try:
                existing_text = engine.get(result["id"])
            except FileNotFoundError:
                continue
            existing_terms = set(
                w.lower() for w in re.findall(r"\b\w{4,}\b", existing_text)
            )
            if not existing_terms:
                continue
            overlap = len(candidate_terms & existing_terms) / len(candidate_terms)
            if overlap > best_overlap:
                best_overlap = overlap
                best_match_id = result["id"]

        if best_overlap >= 0.85:
            # Almost identical — supersede the old entry
            candidate["_supersedes"] = best_match_id
            new.append(candidate)
        elif best_overlap >= 0.6:
            # Existing node covers 60%+ of candidate's content
            novel_terms = [t for t in candidate_terms if t not in engine.get(best_match_id).lower()]
            if len(novel_terms) > len(candidate_terms) * 0.3:
                # >30% novel — append the new bits
                candidate["_append_to"] = best_match_id
                new.append(candidate)
            # else: fully covered, skip
        else:
            # No sufficient overlap — genuinely new
            new.append(candidate)

    return new


def _chunk_text(text, size=3000, overlap=200):
    """Split text into overlapping chunks for processing."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks


def _dedup_candidates(candidates):
    """Deduplicate candidates by title similarity (lowercase first 40 chars)."""
    seen = set()
    deduped = []
    for c in candidates:
        key = c["title"].lower()[:40]
        if key not in seen:
            seen.add(key)
            deduped.append(c)
    return deduped


def _pattern_extract(text):
    candidates = []

    # Pattern: "TIL:" or "Today I learned:" lines
    for match in re.finditer(r"(?:TIL|Today I learned)[:\s]+(.+?)(?:\n\n|\n###|\Z)", text, re.DOTALL):
        content = match.group(1).strip()
        if len(content) > 20:
            title = content[:60].split("\n")[0].rstrip(".")
            candidates.append({"title": title, "content": content, "tags": ["learned"]})

    # Pattern: "The fix is:" or "Solution:" followed by content
    for match in re.finditer(r"(?:The fix is|Solution|Fix)[:\s]+(.+?)(?:\n\n|\n###|\Z)", text, re.DOTALL):
        content = match.group(1).strip()
        if len(content) > 15:
            title = f"Fix: {content[:50].split(chr(10))[0].rstrip('.')}"
            candidates.append({"title": title, "content": content, "tags": ["fix"]})

    # Pattern: Code blocks preceded by instructional context
    for match in re.finditer(r"(?:use|run|execute|command)[:\s]*\n```[^\n]*\n(.+?)```", text, re.DOTALL | re.IGNORECASE):
        content = match.group(1).strip()
        if len(content) > 10:
            title = f"Command: {content.split(chr(10))[0][:50]}"
            candidates.append({"title": title, "content": content, "tags": ["command"]})

    return candidates


def _llm_extract(text):
    if len(text) < 100:
        return []

    import config

    # Chunk the input and extract from each chunk independently
    chunks = _chunk_text(text, size=3000, overlap=200)
    all_candidates = []

    for chunk in chunks:
        prompt = (
            "Extract actionable knowledge from this text. Return JSON array of objects "
            "with 'title' (short descriptive), 'content' (the factual/procedural knowledge), "
            "and 'tags' (list of topic keywords). Only extract verified facts, procedures, "
            "corrections, or preferences — not opinions, greetings, or planning. "
            "If nothing extractable, return []. Text:\n\n" + chunk
        )

        candidates = _run_llm_prompt(prompt, config)
        if candidates:
            all_candidates.extend(candidates)

    # Deduplicate across chunks
    return _dedup_candidates(all_candidates)


def _run_llm_prompt(prompt, config):
    """Run a single LLM prompt and return parsed candidates."""
    # Try configured CLI (gemini, claude, codex — auto-detected if not set)
    cmd = config.resolve_llm_command(prompt)
    if cmd:
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60,
                cwd=os.getcwd(),
            )
            if result.returncode == 0:
                return _parse_llm_response(result.stdout)
        except (subprocess.TimeoutExpired, OSError):
            pass

    # Fallback: ollama HTTP API
    ollama_url = config.get("ollama_url", "http://localhost:11434")
    model = config.get("llm_model", "qwen3.5:latest")
    try:
        data = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
        req = urllib.request.Request(
            f"{ollama_url}/api/generate",
            data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            response = json.loads(resp.read())
            return _parse_llm_response(response.get("response", ""))
    except Exception:
        pass

    return []


def _parse_llm_response(text):
    # Extract JSON array from LLM response (may have surrounding text)
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        items = json.loads(match.group())
        valid = []
        for item in items:
            if isinstance(item, dict) and "title" in item and "content" in item:
                valid.append({
                    "title": item["title"],
                    "content": item["content"],
                    "tags": item.get("tags", []),
                })
        return valid
    except (json.JSONDecodeError, TypeError):
        return []
