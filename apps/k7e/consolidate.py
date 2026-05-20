"""k7e consolidation — extract knowledge from raw experience.

Scans raw files (journals, transcripts, command output). Extracts knowledge
candidates. Diffs against existing store. Plants genuine deltas.

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
import engine as garden


def consolidate(paths, dry_run=False):
    results = []
    for path in paths:
        p = Path(path)
        if p.is_dir():
            files = sorted(p.rglob("*.md"))
        else:
            files = [p]
        for f in files:
            candidates = extract_from_file(f)
            new_knowledge = diff_against_garden(candidates)
            if dry_run:
                for item in new_knowledge:
                    results.append({"action": "would_plant", "title": item["title"], "source": str(f)})
            else:
                for item in new_knowledge:
                    node_id = garden.plant(
                        title=item["title"],
                        content=item["content"],
                        tags=item.get("tags", []),
                    )
                    results.append({"action": "planted", "id": node_id, "title": item["title"], "source": str(f)})
    return results


def extract_from_file(path):
    text = Path(path).read_text(encoding="utf-8")
    candidates = _pattern_extract(text)
    llm_candidates = _llm_extract(text)
    if llm_candidates:
        candidates.extend(llm_candidates)
    return candidates


def diff_against_garden(candidates):
    new = []
    for candidate in candidates:
        results = garden.search(candidate["title"], limit=3)
        if not results:
            new.append(candidate)
            continue
        top = results[0]
        if top["score"] < 0.02:
            new.append(candidate)
    return new


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

    prompt = (
        "Extract actionable knowledge from this text. Return JSON array of objects "
        "with 'title' (short descriptive), 'content' (the factual/procedural knowledge), "
        "and 'tags' (list of topic keywords). Only extract verified facts, procedures, "
        "corrections, or preferences — not opinions, greetings, or planning. "
        "If nothing extractable, return []. Text:\n\n" + text[:3000]
    )

    # Try gemini CLI first
    gemini_bin = shutil.which("gemini")
    if gemini_bin:
        try:
            result = subprocess.run(
                [gemini_bin, "--yolo", "-p", prompt, "-o", "text"],
                capture_output=True, text=True, timeout=60,
                cwd=os.getcwd(),
            )
            if result.returncode == 0:
                return _parse_llm_response(result.stdout)
        except (subprocess.TimeoutExpired, OSError):
            pass

    # Try ollama
    try:
        model = os.environ.get("INGEST_MODEL", "qwen3.5:latest")
        data = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
        url = f"{garden.OLLAMA_URL}/api/generate"
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        import urllib.request
        with urllib.request.urlopen(req, timeout=30) as resp:
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
