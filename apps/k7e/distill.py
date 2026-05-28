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
