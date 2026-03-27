"""Pure configuration data for the hippocampus organ. No functions except log()."""
import os
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent
DB_PATH = str(DIR / ".memory" / "memories.db")

# Thresholds (configurable via environment)
MAX_MEMORIES = int(os.environ.get("MAX_MEMORIES", "10000"))
SIMILAR_THRESHOLD = float(os.environ.get("SIMILAR_THRESHOLD", "0.85"))
STALE_DAYS = int(os.environ.get("STALE_DAYS", "30"))
HOT_TIER_SIZE = int(os.environ.get("HOT_TIER_SIZE", "500"))

# Admission control
ADMISSION_RULES = [
    # (pattern, action, importance_override)
    (r"^(ok|got it|sure|yes|no|thanks)$", "reject", None),
    (r"(decided|decision|commit)", "accept", 7),
    (r"(error|fail|crash|exception)", "accept", 6),
    (r"(supersedes|replaces|overrides)", "accept", 7),
]

DEDUP_WINDOW_SECONDS = 60
MAX_STORE_RATE = 100  # max memories per cycle

# Timestamp format: SQLite-native, always UTC, no timezone suffix.
# julianday() and datetime() work directly on this format.
TS_FMT = "%Y-%m-%d %H:%M:%S"


def log(msg):
    print(f"hippocampus: {msg}", file=sys.stderr)
