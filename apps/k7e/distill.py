"""k7e distillation — extract knowledge from raw experience.

Scans raw files (journals, transcripts, command output, images, audio, video).
Extracts knowledge candidates. Diffs against existing store. Stores genuine deltas.

Text files: pattern extraction + LLM extraction.
Media files: multimodal LLM (describe/transcribe) + asset storage.

Uses agy/claude/codex CLI or ollama for LLM extraction. Falls back to
pattern-based extraction if neither is available.
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

MEDIA_EXTENSIONS = {
    "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".svg"},
    "audio": {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".wma"},
    "video": {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"},
}
ALL_MEDIA_EXTENSIONS = set().union(*MEDIA_EXTENSIONS.values())

MIN_CONTENT_LENGTH = 20
REJECT_PATTERNS = [
    r"^(ok|okay|sure|yes|no|got it|thanks|thank you|hi|hello|hey)\.?$",
    r"^.{0,10}$",  # anything under 10 chars
]

GENERIC_CAPABILITY_PATTERNS = [
    r"^the (agent|system|bot) (is equipped with|has|can use|can|has access to)",
    r"^(this system|the system|we) (have|has|can|support)",
    r"(is equipped with|equipped with .* capabilities|available tools|available commands)",
]


def _should_reject(text):
    """Reject trivial content that isn't worth storing."""
    text = text.strip()
    if len(text) < MIN_CONTENT_LENGTH:
        return True
    for pattern in REJECT_PATTERNS:
        if re.match(pattern, text, re.IGNORECASE):
            return True
    # Reject generic capability descriptions
    for pattern in GENERIC_CAPABILITY_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
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
            text_files = sorted(p.rglob("*.md")) + sorted(p.rglob("*.txt"))
            media_files = [
                f for f in sorted(p.rglob("*"))
                if f.suffix.lower() in ALL_MEDIA_EXTENSIONS
            ]
            files = text_files + media_files
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
                    # Store asset and embed link for media files
                    asset_ref = ""
                    if item.get("_asset_path"):
                        asset_rel = engine.store_asset(item["_asset_path"])
                        asset_ref = f"\n\n![{Path(item['_asset_path']).name}]({asset_rel})"
                    content = item["content"] + asset_ref

                    if item.get("_supersedes"):
                        node_id = engine.store_entry(
                            title=item["title"],
                            content=content,
                            tags=item.get("tags", []),
                            importance=importance,
                        )
                        engine.supersede(item["_supersedes"], node_id)
                        results.append({"action": "superseded", "id": node_id, "old_id": item["_supersedes"], "title": item["title"], "source": str(f)})
                    elif item.get("_append_to"):
                        engine.append_entry(item["_append_to"], "Edge Cases", content)
                        results.append({"action": "appended", "id": item["_append_to"], "title": item["title"], "source": str(f)})
                    else:
                        node_id = engine.store_entry(
                            title=item["title"],
                            content=content,
                            tags=item.get("tags", []),
                            importance=importance,
                        )
                        results.append({"action": "stored", "id": node_id, "title": item["title"], "source": str(f)})
    return results


def _media_type(path):
    ext = Path(path).suffix.lower()
    for kind, exts in MEDIA_EXTENSIONS.items():
        if ext in exts:
            return kind
    return None


def extract_from_file(path):
    if _media_type(path):
        return _multimodal_extract(path)
    text = Path(path).read_text(encoding="utf-8")
    candidates = _pattern_extract(text)
    llm_candidates = _llm_extract(text)
    if llm_candidates:
        candidates.extend(llm_candidates)
    return candidates


def _multimodal_extract(path):
    """Extract knowledge from media files via multimodal LLM."""
    import config

    cmd = config.resolve_llm_command("test")
    if not cmd:
        print(f"  [distill] no LLM available — cannot process media file {path}", file=sys.stderr)
        return []

    kind = _media_type(path)
    abs_path = str(Path(path).resolve())

    if kind == "image":
        instruction = "Describe this image in detail."
    elif kind == "audio":
        instruction = "Transcribe this audio file completely. Include speaker identification if multiple speakers."
    elif kind == "video":
        instruction = "Transcribe the audio and describe key visual content of this video."
    else:
        return []

    prompt = (
        f"{instruction} File: {abs_path}\n\n"
        "Return a JSON object with:\n"
        '- "title": short descriptive title for this content\n'
        '- "content": the full transcription or description\n'
        '- "tags": list of topic keywords\n'
        "Return ONLY the JSON object, no markdown fencing."
    )

    real_cmd = config.resolve_llm_command(prompt)
    if not real_cmd:
        return []

    try:
        result = subprocess.run(
            real_cmd, capture_output=True, text=True, timeout=180,
            cwd=str(config._k7e_home()),
        )
        if result.returncode != 0:
            print(f"  [llm] non-zero exit ({result.returncode}) for {path}", file=sys.stderr)
            return []
        parsed = _parse_multimodal_response(result.stdout, path)
        if parsed:
            parsed["_asset_path"] = abs_path
            parsed["_media_type"] = kind
            return [parsed]
    except subprocess.TimeoutExpired:
        print(f"  [llm] timed out (180s) for {path}", file=sys.stderr)
    except OSError as e:
        print(f"  [llm] launch failed: {e}", file=sys.stderr)

    return []


def _parse_multimodal_response(text, path):
    """Parse LLM response for a single media file. Returns one candidate dict or None."""
    # Try to extract a JSON object
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        # Fallback: use entire response as content, filename as title
        if len(text.strip()) > 20:
            return {
                "title": Path(path).stem.replace("-", " ").replace("_", " "),
                "content": text.strip(),
                "tags": [_media_type(path)],
            }
        return None
    try:
        item = json.loads(match.group())
        if isinstance(item, dict) and "content" in item:
            return {
                "title": item.get("title") or Path(path).stem.replace("-", " ").replace("_", " "),
                "content": item["content"],
                "tags": item.get("tags", [_media_type(path)]),
            }
    except (json.JSONDecodeError, TypeError):
        # Fallback: use raw text
        if len(text.strip()) > 20:
            return {
                "title": Path(path).stem.replace("-", " ").replace("_", " "),
                "content": text.strip(),
                "tags": [_media_type(path)],
            }
    return None


_TITLE_STOPWORDS = {"the", "a", "an", "via", "with", "using", "from", "to", "for", "and", "or", "of", "in", "on", "by"}


def _normalize_title(title):
    """Normalize title for comparison: lowercase, stem, strip stopwords, sort."""
    t = title.lower().strip()
    t = re.sub(r"[^a-z0-9\s]", "", t)
    t = re.sub(r"^(how to)\s+", "", t)
    words = t.split()
    normalized = []
    for w in words:
        if w in _TITLE_STOPWORDS:
            continue
        # Strip trailing 's' for plurals (simple)
        if w.endswith("s") and len(w) > 3 and not w.endswith("ss"):
            w = w[:-1]
        # Normalize gerunds: "sending" → "send", "capturing" → "capture"
        if w.endswith("ing") and len(w) > 5:
            stem = w[:-3]
            if stem.endswith("t") or stem.endswith("n") or stem.endswith("d"):
                w = stem
            elif stem.endswith("e"):
                w = stem
            elif stem + "e" != w:  # avoid "e" → "ee"
                w = stem + "e"
        normalized.append(w)
    return " ".join(sorted(normalized))


def _title_similarity(a, b):
    """Jaccard similarity on normalized title words."""
    words_a = set(_normalize_title(a).split())
    words_b = set(_normalize_title(b).split())
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


def diff_against_store(candidates):
    new = []
    for candidate in candidates:
        # Stage 0: title-based dedup — catches paraphrases with same topic
        title_results = engine.search(candidate["title"], limit=8)
        if _is_title_duplicate(candidate, title_results):
            continue

        # Stage 1: search by content keywords
        content_terms = " ".join(
            w for w in candidate["content"].split()[:20]
            if len(w) > 3
        )
        search_query = content_terms or candidate["title"]
        results = engine.search(search_query, limit=8)

        if not results:
            new.append(candidate)
            continue

        # Stage 2: content overlap with normalized terms
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
            # Bidirectional overlap: max of either direction
            forward = len(candidate_terms & existing_terms) / len(candidate_terms)
            backward = len(candidate_terms & existing_terms) / len(existing_terms)
            overlap = max(forward, backward)
            if overlap > best_overlap:
                best_overlap = overlap
                best_match_id = result["id"]

        if best_overlap >= 0.7:
            # High overlap — this is a restatement, skip entirely
            continue
        elif best_overlap >= 0.45:
            # Moderate overlap — check for novel content worth appending
            existing_text = engine.get(best_match_id)
            existing_terms_full = set(
                w.lower() for w in re.findall(r"\b\w{4,}\b", existing_text)
            )
            novel_terms = candidate_terms - existing_terms_full
            if len(novel_terms) > len(candidate_terms) * 0.4:
                candidate["_append_to"] = best_match_id
                new.append(candidate)
            # else: mostly covered, skip
        else:
            new.append(candidate)

    return new


def _is_title_duplicate(candidate, search_results):
    """Check if candidate's title matches an existing node closely enough to skip."""
    if not search_results:
        return False
    for result in search_results:
        sim = _title_similarity(candidate["title"], result["title"])
        if sim >= 0.6:
            return True
    return False


def consolidate(dry_run=False):
    """Find and merge duplicate nodes. Returns list of actions taken."""
    engine.init()
    nodes = engine.list_nodes(status="active")
    if not nodes:
        return []

    # Group by normalized title
    groups = {}
    for node in nodes:
        key = _normalize_title(node["title"])
        groups.setdefault(key, []).append(node)

    # Also merge groups with high title similarity
    keys = list(groups.keys())
    merged_keys = {}  # maps key → canonical key
    for i, k1 in enumerate(keys):
        if k1 in merged_keys:
            continue
        for k2 in keys[i + 1:]:
            if k2 in merged_keys:
                continue
            sim = _title_similarity_raw(k1, k2)
            if sim >= 0.6:
                merged_keys[k2] = k1

    for old_key, canonical in merged_keys.items():
        groups.setdefault(canonical, []).extend(groups.pop(old_key, []))

    results = []
    for key, group in groups.items():
        if len(group) < 2:
            continue

        # Pick the best node: highest confidence, then most recently updated
        group.sort(key=lambda n: (n.get("confidence", 0), n["id"]), reverse=True)
        keeper = group[0]
        duplicates = group[1:]

        if dry_run:
            results.append({
                "action": "would_consolidate",
                "keeper": keeper["id"],
                "title": keeper["title"],
                "duplicates": [d["id"] for d in duplicates],
            })
        else:
            for dup in duplicates:
                engine.supersede(dup["id"], keeper["id"])
            results.append({
                "action": "consolidated",
                "keeper": keeper["id"],
                "title": keeper["title"],
                "superseded": [d["id"] for d in duplicates],
                "count": len(duplicates),
            })

    return results


def _title_similarity_raw(norm_a, norm_b):
    """Jaccard similarity on pre-normalized title strings."""
    words_a = set(norm_a.split())
    words_b = set(norm_b.split())
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


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

    if not config.resolve_llm_command("test"):
        ollama_url = config.get("ollama_url", "http://localhost:11434")
        try:
            urllib.request.urlopen(f"{ollama_url}/api/tags", timeout=2)
        except Exception:
            print("  [distill] no LLM available — pattern extraction only", file=sys.stderr)
            return []

    # Chunk the input and extract from each chunk independently
    chunks = _chunk_text(text, size=3000, overlap=200)
    all_candidates = []

    for chunk in chunks:
        prompt = (
            "Extract ONLY genuinely novel knowledge from this text. Be extremely selective.\n\n"
            "RULES:\n"
            "- Extract: specific facts, corrections, procedures with concrete details\n"
            "- Extract: user preferences, decisions, constraints that affect future behavior\n"
            "- SKIP: generic capability descriptions ('the system can...', 'the agent has...')\n"
            "- SKIP: command syntax that's already in documentation\n"
            "- SKIP: conversational noise, acknowledgments, planning without decisions\n"
            "- SKIP: anything that restates what a tool/system does in general terms\n"
            "- Maximum 3 items per chunk. If unsure, extract fewer.\n\n"
            "Return JSON array of objects with 'title' (specific, noun-phrase, max 6 words), "
            "'content' (the concrete factual detail — not a general description), "
            "and 'tags' (1-3 topic keywords). "
            "If nothing novel, return []. Text:\n\n" + chunk
        )

        candidates = _run_llm_prompt(prompt, config)
        if candidates:
            all_candidates.extend(candidates)

    # Deduplicate across chunks
    return _dedup_candidates(all_candidates)


def _run_llm_prompt(prompt, config):
    """Run a single LLM prompt and return parsed candidates."""
    cmd = config.resolve_llm_command(prompt)
    if cmd:
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=180,
                cwd=str(config._k7e_home()),
            )
            if result.returncode == 0:
                return _parse_llm_response(result.stdout)
            print(f"  [llm] non-zero exit ({result.returncode})", file=sys.stderr)
        except subprocess.TimeoutExpired:
            print("  [llm] timed out (180s)", file=sys.stderr)
        except OSError as e:
            print(f"  [llm] launch failed: {e}", file=sys.stderr)
        return []

    # Fallback: ollama HTTP API
    ollama_url = config.get("ollama_url", "http://localhost:11434")
    model = config.get("llm_model", "qwen3.5:latest")
    try:
        data = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
        req = urllib.request.Request(
            f"{ollama_url}/api/generate",
            data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            response = json.loads(resp.read())
            return _parse_llm_response(response.get("response", ""))
    except Exception as e:
        print(f"  [llm] ollama failed: {e}", file=sys.stderr)

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
