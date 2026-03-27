"""Shared constants, configuration, and utilities for the hippocampus organ."""
import os
import sys
import subprocess
from pathlib import Path

DIR = Path(__file__).resolve().parent
DB_PATH = os.environ.get("MEMORY_DB", str(DIR / "memory.db"))
CONF_DIR = os.environ.get("CONF_DIR", str(DIR.parent))

# Thresholds (configurable via environment)
MAX_MEMORIES = int(os.environ.get("MAX_MEMORIES", "10000"))
SIMILAR_THRESHOLD = float(os.environ.get("SIMILAR_THRESHOLD", "0.85"))
STALE_DAYS = int(os.environ.get("STALE_DAYS", "30"))
HOT_TIER_SIZE = int(os.environ.get("HOT_TIER_SIZE", "500"))

# Optional LLM integration (off by default)
USE_LLM = os.environ.get("HIPPOCAMPUS_USE_LLM", "") == "1"

# Track total queries this session (for UCB exploration bonus)
_total_queries = 0


def log(msg):
    print(f"hippocampus: {msg}", file=sys.stderr)



# =========================================================================
#  Small-LLM integration (optional, controlled by HIPPOCAMPUS_USE_LLM=1)
# =========================================================================

def _call_small_llm(system_prompt, user_prompt, timeout=30):
    """Call small-llm CLI and return its output, or None on failure."""
    try:
        result = subprocess.run(
            ["small-llm", "-s", system_prompt, user_prompt],
            capture_output=True, text=True, timeout=timeout
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def score_importance(content):
    """Ask small-llm to rate a memory's importance 1-10.

    Returns an integer 1-10, or 5 (default) if the LLM is unavailable
    or returns an unparseable response.
    """
    if not USE_LLM:
        return 5

    system = (
        "You are a memory importance scorer. Rate the following memory on a "
        "scale of 1-10 where 1 is trivial noise and 10 is critical information "
        "that must never be forgotten. Respond with ONLY a single integer."
    )
    response = _call_small_llm(system, content)
    if response:
        for token in response.split():
            try:
                score = int(token)
                if 1 <= score <= 10:
                    return score
            except ValueError:
                continue
    return 5


def check_similar(content, candidates):
    """Ask small-llm if content is similar to any candidate memories.

    Args:
        content: the new memory text
        candidates: list of (id, existing_content) tuples

    Returns:
        The id of the most similar memory, or None if no match.
    """
    if not USE_LLM or not candidates:
        return None

    numbered = []
    for i, (mid, text) in enumerate(candidates, 1):
        numbered.append(f"{i}. {text[:200]}")
    candidate_text = "\n".join(numbered)

    system = (
        "You compare memories for similarity. Given a NEW memory and a numbered "
        "list of EXISTING memories, respond with ONLY the number of the existing "
        "memory that says the same thing as the new one. If none are similar, "
        "respond with 0."
    )
    user = f"NEW: {content[:300]}\n\nEXISTING:\n{candidate_text}"

    response = _call_small_llm(system, user)
    if response:
        for token in response.split():
            try:
                idx = int(token)
                if 1 <= idx <= len(candidates):
                    return candidates[idx - 1][0]
                if idx == 0:
                    return None
            except ValueError:
                continue
    return None


# =========================================================================
#  Admission Rules
# =========================================================================

ADMISSION_RULES = [
    # (pattern, action, importance_override)
    (r"^(ok|got it|sure|yes|no|thanks)$", "reject", None),
    (r"^health check", "accept_ttl", 3),
    (r"(decided|decision|commit)", "accept", 7),
    (r"neil (said|told|asked|wants|prefers)", "accept", 8),
    (r"(error|fail|crash|exception)", "accept", 6),
    (r"(supersedes|replaces|overrides)", "accept", 7),
]

# Dedup window: reject memories with identical hash within this many seconds
DEDUP_WINDOW_SECONDS = 60
MAX_STORE_RATE = 100  # max memories per cycle

# Rolling window patterns for auto-supersession
ROLLING_PATTERNS = {
    "session_reflection": 5,
    "health_check": 3,
    "morning_ritual": 3,
    "evening_ritual": 3,
}
