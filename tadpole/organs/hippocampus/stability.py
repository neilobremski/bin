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
    """
    decayed = 0
    rows = db.execute("""
        SELECT id, accessed_at, stability_days, importance, access_count
        FROM memories WHERE is_active = 1
    """).fetchall()

    for mid, accessed_at, stability, importance, access_count in rows:
        last_access = datetime.fromisoformat(accessed_at.replace("Z", "+00:00"))
        age_since_access = (now - last_access).total_seconds() / 86400.0

        r = 1.0 / (1.0 + age_since_access / (9.0 * max(stability, 0.1)))

        if r < 0.3 and access_count < 5:
            db.execute("UPDATE memories SET is_active=0, tier='cold' WHERE id=?", (mid,))
            decayed += 1
        elif r < 0.5:
            db.execute("UPDATE memories SET tier='cold' WHERE id=? AND tier='hot'", (mid,))

    return decayed


def retier_memories(db, now):
    """Reassign memories to hot/cold tiers based on utility score.

    Hot tier target: ~HOT_TIER_SIZE memories.
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

    hot_ids = {mid for _, mid in scored[:HOT_TIER_SIZE]}
    retiered = 0

    for _, mid in scored:
        new_tier = "hot" if mid in hot_ids else "cold"
        updated = db.execute(
            "UPDATE memories SET tier=? WHERE id=? AND tier!=?",
            (new_tier, mid, new_tier)
        ).rowcount
        retiered += updated

    return retiered
