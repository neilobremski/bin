#!/usr/bin/env python3
"""Benchmark: 10K memories — store, consolidate, retier, search.

Creates a fresh DB at /tmp/benchmark-10k.db, stores 10,000 synthetic memories,
runs a full hippocampus cycle (consolidate + retier), then runs 10 search queries.
Reports timing for each phase. Target: full cycle under 60 seconds.
"""
import hashlib
import os
import random
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta

# Add hippocampus to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["MEMORY_DB"] = "/tmp/benchmark-10k.db"
os.environ["MAX_MEMORIES"] = "15000"
os.environ["HOT_TIER_SIZE"] = "500"

from schema import init_db, migrate, backfill_stability
from consolidation import consolidate
from stability import retier_memories
from retrieval import search
from entities import seed_entities

DB_PATH = "/tmp/benchmark-10k.db"

# Vocabulary pools for synthetic memories
SUBJECTS = [
    "Neil", "the tadpole", "Knobert", "the hippocampus", "the brainstem",
    "memory system", "MQTT broker", "GAS bridge", "the dashboard",
    "Python script", "Docker container", "AWS instance", "the phone",
    "SQLite database", "FTS5 index", "FSRS algorithm", "the ganglion",
    "stimulus queue", "consolidation", "the lymph node",
]

VERBS = [
    "processed", "stored", "retrieved", "updated", "consolidated",
    "decayed", "merged", "superseded", "routed", "triggered",
    "analyzed", "optimized", "deployed", "configured", "monitored",
    "debugged", "refactored", "tested", "benchmarked", "scheduled",
]

OBJECTS = [
    "a new memory", "the health check", "stability scores", "tier assignments",
    "entity links", "search results", "composite scores", "admission rules",
    "dedup hashes", "rolling windows", "Jaccard similarity", "BM25 rankings",
    "category filters", "importance ratings", "access counts",
    "the exploration bonus", "retrievability values", "decay thresholds",
    "merge candidates", "pruning targets",
]

CONTEXTS = [
    "during the morning ritual", "after a long silence", "in response to stimulus",
    "as part of consolidation", "during tier reassignment", "on first boot",
    "after a crash recovery", "following Neil's instruction", "at sunset",
    "during the weekly long run", "in the background", "via nervous system",
    "through the circulatory system", "with high importance", "at low stability",
    "under memory pressure", "with LLM assistance", "in cold tier",
    "from hot tier", "across body parts",
]

CATEGORIES = ["general", "system", "decision", "insight", "health", "communication",
              "research", "project", "personal", "technical"]


def generate_memory(i):
    """Generate a synthetic memory with varied content."""
    subj = random.choice(SUBJECTS)
    verb = random.choice(VERBS)
    obj = random.choice(OBJECTS)
    ctx = random.choice(CONTEXTS)
    extra = f" (iteration {i}, timestamp {random.randint(1000, 9999)})"
    return f"{subj} {verb} {obj} {ctx}{extra}"


def run_benchmark():
    # Clean up any previous run
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    results = {}
    total_start = time.time()

    # Phase 1: Create DB and schema
    t0 = time.time()
    db = sqlite3.connect(DB_PATH)
    init_db(db)
    migrate(db)
    seed_entities(db)
    db.commit()
    results["schema_init"] = time.time() - t0

    # Phase 2: Store 10,000 memories
    t0 = time.time()
    random.seed(42)  # Reproducible
    now = datetime.now(timezone.utc)

    for i in range(10000):
        content = generate_memory(i)
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:32]
        importance = random.randint(1, 10)
        category = random.choice(CATEGORIES)
        # Spread creation times across 90 days
        created = (now - timedelta(days=random.uniform(0, 90)))
        created_str = created.strftime("%Y-%m-%dT%H:%M:%SZ")
        accessed = (created + timedelta(days=random.uniform(0, 30)))
        if accessed > now:
            accessed = now
        accessed_str = accessed.strftime("%Y-%m-%dT%H:%M:%SZ")
        access_count = random.randint(0, 20)
        stability = random.uniform(0.5, 30.0)
        difficulty = max(1.0, min(10.0, 11.0 - importance))
        tier = "hot" if random.random() < 0.3 else "cold"

        db.execute(
            "INSERT OR IGNORE INTO memories(content, importance, category, source, "
            "created_at, accessed_at, access_count, content_hash, stability_days, "
            "difficulty, tier, is_active) "
            "VALUES (?, ?, ?, 'benchmark', ?, ?, ?, ?, ?, ?, ?, 1)",
            (content, importance, category, created_str, accessed_str,
             access_count, content_hash, stability, difficulty, tier)
        )

        # Batch commit every 1000
        if (i + 1) % 1000 == 0:
            db.commit()

    db.commit()
    results["store_10k"] = time.time() - t0

    # Verify count
    count = db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    results["memory_count"] = count

    # Phase 3: Consolidation cycle
    t0 = time.time()
    decayed, pruned = consolidate(db)
    db.commit()
    results["consolidate"] = time.time() - t0
    results["decayed"] = decayed
    results["pruned"] = pruned

    # Phase 4: Retier
    t0 = time.time()
    retiered = retier_memories(db, now)
    db.commit()
    results["retier"] = time.time() - t0
    results["retiered"] = retiered

    # Phase 5: Search queries
    queries = [
        "FSRS algorithm stability",
        "Neil instruction decision",
        "hippocampus memory consolidation",
        "MQTT stimulus routing",
        "Docker container deployment",
        "search ranking composite score",
        "entity linking tags",
        "tier assignment hot cold",
        "admission control importance",
        "supersession merge prune",
    ]

    search_times = []
    search_result_counts = []
    for q in queries:
        t0 = time.time()
        results_q = search(db, q, limit=10)
        elapsed = time.time() - t0
        search_times.append(elapsed)
        search_result_counts.append(len(results_q))

    results["search_times"] = search_times
    results["search_counts"] = search_result_counts
    results["search_avg"] = sum(search_times) / len(search_times)
    results["search_max"] = max(search_times)
    results["search_queries"] = queries

    total_elapsed = time.time() - total_start
    results["total"] = total_elapsed

    db.close()
    return results


def format_results(r):
    """Format results as markdown."""
    lines = []
    lines.append("# Benchmark: 10K Memories")
    lines.append("")
    lines.append(f"**Total time: {r['total']:.2f}s** (target: <60s)")
    passed = r["total"] < 60
    lines.append(f"**Result: {'PASS' if passed else 'FAIL'}**")
    lines.append("")
    lines.append("## Phase Breakdown")
    lines.append("")
    lines.append(f"| Phase | Time (s) |")
    lines.append(f"|-------|----------|")
    lines.append(f"| Schema init | {r['schema_init']:.3f} |")
    lines.append(f"| Store 10K memories | {r['store_10k']:.3f} |")
    lines.append(f"| Consolidation (decay+merge+prune) | {r['consolidate']:.3f} |")
    lines.append(f"| Retier | {r['retier']:.3f} |")
    lines.append(f"| Search (10 queries) | {sum(r['search_times']):.3f} |")
    lines.append("")
    lines.append(f"## Stats")
    lines.append("")
    lines.append(f"- Memories stored: {r['memory_count']}")
    lines.append(f"- Decayed: {r['decayed']}")
    lines.append(f"- Pruned: {r['pruned']}")
    lines.append(f"- Retiered: {r['retiered']}")
    lines.append("")
    lines.append("## Search Latency")
    lines.append("")
    lines.append(f"| Query | Results | Time (ms) |")
    lines.append(f"|-------|---------|-----------|")
    for i, q in enumerate(r["search_queries"]):
        lines.append(f"| {q} | {r['search_counts'][i]} | {r['search_times'][i]*1000:.1f} |")
    lines.append("")
    lines.append(f"- **Average**: {r['search_avg']*1000:.1f}ms")
    lines.append(f"- **Max**: {r['search_max']*1000:.1f}ms")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    print("Running 10K memory benchmark...", file=sys.stderr)
    r = run_benchmark()
    report = format_results(r)
    print(report)

    # Write to /tmp
    with open("/tmp/benchmark-results.md", "w") as f:
        f.write(report)
    print(f"\nResults written to /tmp/benchmark-results.md", file=sys.stderr)
