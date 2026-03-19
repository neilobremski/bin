"""FSRS-inspired stability tracking: retrievability, decay, and tier reassignment."""
import math
from datetime import datetime

from config import HOT_TIER_SIZE, log


def fsrs_retrievability(accessed_at_str, stability_days, now):
    """FSRS v4 power-law forgetting curve.

    R(t, S) = (1 + t/(9*S))^(-1)

    At t=0: R=1.0 (just accessed)
    At t=S: R=0.9 (stability is the 90% threshold)
    At t=9*S: R=0.5
    """
    if isinstance(accessed_at_str, str):
        accessed_at = datetime.fromisoformat(accessed_at_str.replace("Z", "+00:00"))
    else:
        accessed_at = accessed_at_str
    elapsed = max((now - accessed_at).total_seconds() / 86400.0, 0.0)
    return 1.0 / (1.0 + elapsed / (9.0 * max(stability_days, 0.1)))


def fsrs_decay(db, now):
    """Update tier/active status based purely on FSRS retrievability.

    When R drops below 0.3 and access_count is low: deactivate.
    When R drops below 0.5: demote to cold.
    No category exemptions — decay is universal.

    Uses batch SQL to avoid loading all memories into Python.
    The FSRS power-law formula R = 1/(1 + t/(9*S)) is computed inline.
    R < threshold becomes: t/(9*S) > (1/threshold - 1), i.e. t > 9*S*(1/threshold - 1).
    For R < 0.3: t > 9*S*(1/0.3 - 1) = 9*S*7/3 = 21*S
    For R < 0.5: t > 9*S*(1/0.5 - 1) = 9*S*1 = 9*S
    """
    now_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Deactivate: R < 0.3 AND access_count < 5
    cursor = db.execute("""
        UPDATE memories SET is_active=0, tier='cold'
        WHERE is_active = 1 AND access_count < 5
          AND (julianday(?) - julianday(
                REPLACE(REPLACE(accessed_at, 'Z', '+00:00'), 'T', ' ')
              )) > 21.0 * MAX(stability_days, 0.1)
    """, (now_str,))
    decayed = cursor.rowcount

    # Demote to cold: R < 0.5 (but still active)
    db.execute("""
        UPDATE memories SET tier='cold'
        WHERE is_active = 1 AND tier='hot'
          AND (julianday(?) - julianday(
                REPLACE(REPLACE(accessed_at, 'Z', '+00:00'), 'T', ' ')
              )) > 9.0 * MAX(stability_days, 0.1)
    """, (now_str,))

    return decayed


def retier_memories(db, now):
    """Reassign memories to hot/cold tiers based on utility score.

    Hot tier target: ~HOT_TIER_SIZE memories.

    Scoring still requires Python (log function), but tier UPDATEs are
    batched into two SQL statements instead of one per row.
    """
    rows = db.execute("""
        SELECT id, importance, accessed_at, stability_days, access_count
        FROM memories WHERE is_active = 1
    """).fetchall()

    scored = []
    for mid, imp, accessed_at, stability, access_count in rows:
        last_access = datetime.fromisoformat(accessed_at.replace("Z", "+00:00"))
        age = (now - last_access).total_seconds() / 86400.0
        r = 1.0 / (1.0 + age / (9.0 * max(stability, 0.1)))

        utility = (imp / 10.0) * r * (1.0 + math.log(access_count + 1))
        scored.append((utility, mid))

    scored.sort(key=lambda x: -x[0])

    hot_ids = [mid for _, mid in scored[:HOT_TIER_SIZE]]
    cold_ids = [mid for _, mid in scored[HOT_TIER_SIZE:]]

    retiered = 0

    # Batch promote to hot
    if hot_ids:
        placeholders = ",".join("?" for _ in hot_ids)
        retiered += db.execute(
            f"UPDATE memories SET tier='hot' WHERE id IN ({placeholders}) AND tier!='hot'",
            hot_ids
        ).rowcount

    # Batch demote to cold
    if cold_ids:
        placeholders = ",".join("?" for _ in cold_ids)
        retiered += db.execute(
            f"UPDATE memories SET tier='cold' WHERE id IN ({placeholders}) AND tier!='cold'",
            cold_ids
        ).rowcount

    return retiered
