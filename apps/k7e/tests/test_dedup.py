"""Dedup stress tests — verify near-duplicates don't flood the system."""
import engine

class TestDedupStress:
    def test_distinct_facts_stay_distinct(self, store):
        """N genuinely different facts must NOT collapse."""
        facts = [
            ("Redis port", "Redis default port is 6379", ["redis"]),
            ("Postgres port", "PostgreSQL default port is 5432", ["postgres"]),
            ("MySQL port", "MySQL default port is 3306", ["mysql"]),
            ("Mongo port", "MongoDB default port is 27017", ["mongo"]),
        ]
        for title, content, tags in facts:
            engine.store_entry(title, content, tags=tags)
        all_nodes = engine.list_nodes()
        assert len(all_nodes) == 4, f"False dedup: collapsed distinct facts to {len(all_nodes)}"

    def test_exact_duplicate_titles_still_store(self, store):
        """store_entry has no dedup gate — this documents current behavior."""
        engine.store_entry("Same Title", "Content A", tags=["test"])
        engine.store_entry("Same Title", "Content B", tags=["test"])
        nodes = engine.list_nodes()
        # Current behavior: both stored (no dedup at store level)
        assert len(nodes) == 2

    def test_search_finds_most_relevant_duplicate(self, store):
        """When duplicates exist, search should rank the most specific one first."""
        engine.store_entry("Chrome Debugging", "Use --remote-debugging-port=9222", tags=["chrome"])
        engine.store_entry("Chrome Debugging Advanced", "Use --remote-debugging-port=9222 --user-data-dir=/tmp/p", tags=["chrome"])
        results = engine.search("remote-debugging-port user-data-dir")
        assert results[0]["title"] == "Chrome Debugging Advanced"


class TestDistillDedup:
    """Test that the distill path actually deduplicates."""

    def test_distill_skips_known_content(self, store, tmp_path):
        import distill
        engine.store_entry("Known Fact", "The sky is blue on clear days", tags=["nature"])
        journal = tmp_path / "j.md"
        journal.write_text("TIL: The sky is blue on clear days")
        results = distill.distill([str(journal)])
        stored = [r for r in results if r["action"] == "stored"]
        assert len(stored) == 0, f"Re-stored known fact: {stored}"

    def test_distill_stores_new_content(self, store, tmp_path):
        import distill
        engine.store_entry("Old Fact", "Something unrelated", tags=["misc"])
        journal = tmp_path / "j.md"
        journal.write_text("TIL: Quantum entanglement allows instant correlation across any distance")
        results = distill.distill([str(journal)])
        stored = [r for r in results if r["action"] in ("stored", "would_store")]
        assert len(stored) >= 1
