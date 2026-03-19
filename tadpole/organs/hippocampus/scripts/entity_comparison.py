#!/usr/bin/env python3
"""Entity value comparison: courtroom test.

Creates a test DB with courtroom-themed memories and entities, then runs
8 queries with and without entity context to compare retrieval quality.
Scores each result set 1-10 based on relevance metrics.
"""
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DB_PATH = "/tmp/courtroom-test.db"
os.environ["MEMORY_DB"] = DB_PATH

from schema import init_db, migrate
from entities import (
    seed_entities, create_entity, extract_and_link_entities,
    get_entity_context, unconscious_entity_recall,
)
from retrieval import search, search_fts, composite_score
import config as _config


def setup_courtroom_db():
    """Create a courtroom-themed test DB with entities and memories."""
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    db = sqlite3.connect(DB_PATH)
    init_db(db)
    migrate(db)
    db.commit()

    # Create courtroom entities
    entities = [
        ("judge_martinez", "Judge Martinez", ["Judge Martinez", "Martinez", "the judge"],
         "person", "Presiding judge in the Robinson trial. Known for strict sentencing."),
        ("defendant_robinson", "James Robinson", ["Robinson", "James Robinson", "the defendant"],
         "person", "Defendant charged with embezzlement. Former CFO of Acme Corp."),
        ("prosecutor_chen", "Prosecutor Chen", ["Chen", "Prosecutor Chen", "the prosecution"],
         "person", "Lead prosecutor. Specializes in financial crimes."),
        ("defense_attorney", "Sarah Wells", ["Wells", "Sarah Wells", "defense counsel"],
         "person", "Defense attorney. Arguments focus on insufficient evidence."),
        ("acme_corp", "Acme Corporation", ["Acme", "Acme Corp", "the company"],
         "organization", "Victim corporation. Lost $2.3M in alleged embezzlement."),
        ("witness_park", "Dr. Park", ["Dr. Park", "Park", "the forensic accountant"],
         "person", "Expert witness. Forensic accountant who analyzed the financial records."),
        ("evidence_laptop", "Robinson's Laptop", ["the laptop", "Robinson's laptop"],
         "thing", "Key evidence. Contains financial records and encrypted files."),
        ("bank_first_national", "First National Bank", ["First National", "the bank"],
         "organization", "Bank where suspicious transfers were routed."),
    ]

    now = datetime.now(timezone.utc)
    for eid, name, aliases, etype, summary in entities:
        create_entity(db, eid, name, aliases=aliases, entity_type=etype, summary=summary)
    db.commit()

    # Create courtroom memories (varied, interconnected)
    memories = [
        ("Judge Martinez set the trial date for March 15th and warned both sides about courtroom conduct", 7, "legal"),
        ("Robinson was arrested on January 10th after Acme Corp filed a complaint about missing funds", 8, "legal"),
        ("Prosecutor Chen presented evidence showing 47 suspicious wire transfers from Acme Corp accounts", 9, "legal"),
        ("Sarah Wells argued that Robinson had authorization for all transfers as part of his CFO duties", 8, "legal"),
        ("Dr. Park testified that the financial records show a pattern consistent with embezzlement over 18 months", 9, "legal"),
        ("The laptop seized from Robinson's home contained encrypted files that forensics is still analyzing", 7, "legal"),
        ("First National Bank records show transfers totaling $2.3 million to offshore accounts", 9, "legal"),
        ("Robinson claims the transfers were legitimate business expenses approved by the board", 6, "legal"),
        ("Judge Martinez denied the defense motion to suppress the laptop evidence", 8, "legal"),
        ("Chen called three additional witnesses from Acme Corp's accounting department", 6, "legal"),
        ("Wells presented character witnesses who described Robinson as honest and dedicated to Acme", 5, "legal"),
        ("The jury consists of 12 members selected after two days of voir dire proceedings", 4, "legal"),
        ("Dr. Park found discrepancies between Robinson's expense reports and actual bank transactions", 8, "legal"),
        ("The prosecution rested its case after presenting 23 exhibits including the laptop and bank records", 7, "legal"),
        ("Robinson testified in his own defense claiming he was set up by a disgruntled colleague at Acme", 7, "legal"),
        ("First National Bank manager confirmed Robinson opened the offshore accounts using his Acme credentials", 8, "legal"),
        ("Judge Martinez instructed the jury on the legal standards for proving embezzlement beyond reasonable doubt", 6, "legal"),
        ("Wells made a closing argument focusing on reasonable doubt and the lack of direct evidence of intent", 7, "legal"),
        ("Chen's closing highlighted the pattern of transfers, Robinson's sole access, and the encrypted laptop files", 8, "legal"),
        ("The trial has generated significant media coverage affecting Acme Corp's stock price", 5, "legal"),
        ("Robinson's bail was set at $500,000 secured by his family home", 6, "legal"),
        ("Dr. Park compared Robinson's case to three similar financial fraud cases in the district", 5, "legal"),
        ("The encrypted files on the laptop were partially recovered showing personal investment records", 8, "legal"),
        ("Acme Corp's board voted to pursue civil damages regardless of the criminal trial outcome", 7, "legal"),
        ("Wells filed a motion for mistrial after a juror was seen talking to a reporter about the case", 7, "legal"),
        ("Judge Martinez denied the mistrial motion but dismissed the juror and seated an alternate", 8, "legal"),
        ("Robinson's former assistant testified that Robinson asked her to delete emails related to the transfers", 9, "legal"),
        ("The defense hired their own forensic accountant who disputes Dr. Park's methodology", 6, "legal"),
        ("Prosecutor Chen subpoenaed Robinson's personal banking records from three additional banks", 7, "legal"),
        ("The trial is expected to last another two weeks before going to jury deliberation", 4, "legal"),
    ]

    for i, (content, importance, category) in enumerate(memories):
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:32]
        created = (now - timedelta(days=30 - i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.execute(
            "INSERT INTO memories(content, importance, category, source, created_at, "
            "accessed_at, access_count, content_hash, stability_days, difficulty, tier, is_active) "
            "VALUES (?, ?, ?, 'test', ?, ?, 0, ?, 5.0, 5.0, 'hot', 1)",
            (content, importance, category, created, created, content_hash)
        )
        mid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        extract_and_link_entities(db, mid, content)

    db.commit()
    return db


# The 8 courtroom queries — keyword style for FTS5 compatibility
QUERIES = [
    "judge Martinez evidence motion",
    "Robinson embezzlement accused charges",
    "Dr Park forensic accountant testimony",
    "First National bank transfers offshore",
    "defense Wells reasonable doubt authorization",
    "laptop encrypted files evidence",
    "million transfers wire money amount",
    "jury mistrial juror voir dire",
]

# Expected key facts for scoring (what a good retrieval should surface)
EXPECTED_FACTS = [
    ["denied motion to suppress laptop", "instructed jury", "denied mistrial"],
    ["embezzlement", "2.3 million", "cfo", "acme"],
    ["pattern consistent with embezzlement", "discrepancies", "expense reports"],
    ["first national", "offshore accounts", "2.3 million", "wire transfers"],
    ["authorization", "reasonable doubt", "insufficient evidence", "character witnesses"],
    ["encrypted files", "personal investment records", "forensics"],
    ["2.3 million", "suspicious wire transfers", "offshore accounts"],
    ["12 members", "voir dire", "mistrial", "dismissed", "alternate"],
]


def score_results(results, expected_facts, entity_context=None):
    """Score retrieval quality 1-10 based on fact coverage.

    Score = (facts_found / total_expected_facts) * 8 + base of 2
    Bonus +1 if entity context adds unique relevant info not in results.
    """
    if not results:
        return 1

    all_text = " ".join(r[1].lower() for r in results)
    if entity_context:
        all_text += " " + " ".join(block.lower() for block in entity_context)

    facts_found = 0
    for fact in expected_facts:
        if fact.lower() in all_text:
            facts_found += 1

    coverage = facts_found / max(len(expected_facts), 1)
    score = 2 + int(coverage * 8)
    return min(10, max(1, score))


def run_comparison():
    """Run queries with and without entity context, compare scores."""
    db = setup_courtroom_db()

    results_without = []
    results_with = []

    for i, query in enumerate(QUERIES):
        # Search WITHOUT entity context
        _config._total_queries += 1
        search_results = search(db, query, limit=10)
        score_no_entity = score_results(search_results, EXPECTED_FACTS[i])
        results_without.append({
            "query": query,
            "result_count": len(search_results),
            "score": score_no_entity,
            "top_results": [r[1][:80] for r in search_results[:3]],
        })

        # Search WITH entity context
        _config._total_queries += 1
        search_results2 = search(db, query, limit=10)
        result_ids = [r[0] for r in search_results2]
        entity_ctx = get_entity_context(db, result_ids)
        # Also try unconscious recall
        unconscious_ctx = unconscious_entity_recall(db, query)
        all_ctx = entity_ctx + unconscious_ctx

        score_with_entity = score_results(search_results2, EXPECTED_FACTS[i], all_ctx)
        results_with.append({
            "query": query,
            "result_count": len(search_results2),
            "score": score_with_entity,
            "entity_blocks": len(all_ctx),
            "top_results": [r[1][:80] for r in search_results2[:3]],
        })

    db.close()
    return results_without, results_with


def format_comparison(without, with_ent):
    """Format comparison as markdown."""
    lines = []
    lines.append("# Entity Value Comparison: Courtroom Test")
    lines.append("")
    lines.append("## Query-by-Query Comparison")
    lines.append("")
    lines.append("| # | Query | Without Entities | With Entities | Delta | Entity Blocks |")
    lines.append("|---|-------|-----------------|---------------|-------|---------------|")

    total_without = 0
    total_with = 0
    for i in range(len(QUERIES)):
        sw = without[i]["score"]
        se = with_ent[i]["score"]
        delta = se - sw
        delta_str = f"+{delta}" if delta > 0 else str(delta)
        total_without += sw
        total_with += se
        lines.append(
            f"| {i+1} | {QUERIES[i]} | {sw}/10 | {se}/10 | {delta_str} | {with_ent[i]['entity_blocks']} |"
        )

    avg_without = total_without / len(QUERIES)
    avg_with = total_with / len(QUERIES)
    avg_delta = avg_with - avg_without

    lines.append("")
    lines.append(f"## Summary")
    lines.append("")
    lines.append(f"- **Average score WITHOUT entities**: {avg_without:.1f}/10")
    lines.append(f"- **Average score WITH entities**: {avg_with:.1f}/10")
    lines.append(f"- **Average improvement**: {avg_delta:+.1f} points")
    lines.append("")

    if avg_delta >= 1.0:
        lines.append("**Verdict: Entities provide meaningful improvement (>=1 point avg). Keep them.**")
    else:
        lines.append("**Verdict: Entities do NOT improve retrieval by >=1 point on average. Consider deferring.**")

    lines.append("")
    lines.append("## Detail: Top Results Per Query")
    lines.append("")
    for i in range(len(QUERIES)):
        lines.append(f"### Q{i+1}: {QUERIES[i]}")
        lines.append("")
        lines.append("Without entities:")
        for r in without[i]["top_results"]:
            lines.append(f"  - {r}")
        lines.append("")
        lines.append("With entities:")
        for r in with_ent[i]["top_results"]:
            lines.append(f"  - {r}")
        if with_ent[i]["entity_blocks"] > 0:
            lines.append(f"  + {with_ent[i]['entity_blocks']} entity context block(s)")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    print("Running entity value comparison...", file=sys.stderr)
    without, with_ent = run_comparison()
    report = format_comparison(without, with_ent)
    print(report)

    with open("/tmp/entity-comparison.md", "w") as f:
        f.write(report)
    print(f"\nResults written to /tmp/entity-comparison.md", file=sys.stderr)
