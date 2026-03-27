"""FSRS-inspired stability tracking: retrievability, decay, and tier reassignment.

Timestamps are stored as 'YYYY-MM-DD HH:MM:SS' (UTC). julianday() works
directly on this format -- no REPLACE chains needed.
"""
import math
from datetime import datetime, timezone

from constants import HOT_TIER_SIZE, TS_FMT, log


def fsrs_retrievability(accessed_at_str, stability_days, now):
    """FSRS v4 power-law forgetting curve: R(t, S) = (1 + t/(9*S))^(-1)"""
    accessed_at = datetime.strptime(accessed_at_str, TS_FMT).replace(
        tzinfo=timezone.utc)
    elapsed = max((now - accessed_at).total_seconds() / 86400.0, 0.0)
    return 1.0 / (1.0 + elapsed / (9.0 * max(stability_days, 0.1)))


def fsrs_decay(db, now):
    """Deactivate/demote memories based on FSRS retrievability.

    R < 0.3 AND access_count < 5: deactivate
    R < 0.5 AND tier='hot': demote to cold

    Uses julianday() directly on clean timestamps (no REPLACE needed).
    """
    now_str = now.strftime(TS_FMT)

    # Deactivate: R < 0.3 means t > 21*S
    cursor = db.execute("""
        UPDATE memories SET is_active=0, tier='cold'
        WHERE is_active = 1 AND access_count < 5
          AND (julianday(?) - julianday(accessed_at)) > 21.0 * MAX(stability_days, 0.1)
    """, (now_str,))
    decayed = cursor.rowcount

    # Demote to cold: R < 0.5 means t > 9*S
    db.execute("""
        UPDATE memories SET tier='cold'
        WHERE is_active = 1 AND tier='hot'
          AND (julianday(?) - julianday(accessed_at)) > 9.0 * MAX(stability_days, 0.1)
    """, (now_str,))

    return decayed


def retier_memories(db, now):
    """Reassign memories to hot/cold tiers based on utility score.

    Hot tier target: ~HOT_TIER_SIZE memories.
    Loads active memories, scores in Python (log() not available in base SQLite),
    then batch-updates tiers.
    """
    rows = db.execute("""
        SELECT id, importance, accessed_at, stability_days, access_count
        FROM memories WHERE is_active = 1
        LIMIT 10000
    """).fetchall()

    scored = []
    for mid, imp, accessed_at, stability, access_count in rows:
        last_access = datetime.strptime(accessed_at, TS_FMT).replace(
            tzinfo=timezone.utc)
        age = max((now - last_access).total_seconds() / 86400.0, 0.01)
        r = 1.0 / (1.0 + age / (9.0 * max(stability, 0.1)))
        utility = (imp / 10.0) * r * (1.0 + math.log(access_count + 1))
        scored.append((utility, mid))

    scored.sort(key=lambda x: -x[0])

    hot_ids = [mid for _, mid in scored[:HOT_TIER_SIZE]]
    cold_ids = [mid for _, mid in scored[HOT_TIER_SIZE:]]

    retiered = 0
    if hot_ids:
        placeholders = ",".join("?" for _ in hot_ids)
        retiered += db.execute(
            f"UPDATE memories SET tier='hot' WHERE id IN ({placeholders}) "
            "AND tier!='hot'", hot_ids
        ).rowcount
    if cold_ids:
        placeholders = ",".join("?" for _ in cold_ids)
        retiered += db.execute(
            f"UPDATE memories SET tier='cold' WHERE id IN ({placeholders}) "
            "AND tier!='cold'", cold_ids
        ).rowcount

    return retiered
