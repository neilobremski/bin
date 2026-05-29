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


class TestTitleDedup:
    """Test title-similarity based deduplication."""

    def test_paraphrased_titles_detected(self, store):
        import distill
        engine.store_entry(
            "Send email via knobert-google",
            "Use tell knobert-google /email to send an email message",
            tags=["email"],
        )
        candidates = [
            {"title": "Sending emails via knobert-google", "content": "Send emails using the knobert-google command", "tags": ["email"]},
        ]
        new = distill.diff_against_store(candidates)
        assert len(new) == 0, f"Paraphrased title should be deduped, got: {new}"

    def test_gerund_normalization(self):
        import distill
        assert distill._normalize_title("Sending emails via knobert") == distill._normalize_title("Send email via knobert")

    def test_title_similarity_high_for_paraphrases(self):
        import distill
        sim = distill._title_similarity(
            "Capture photo via knobert-android",
            "Capturing photos with knobert-android",
        )
        assert sim >= 0.6, f"Expected >= 0.6, got {sim}"

    def test_title_similarity_low_for_different_topics(self):
        import distill
        sim = distill._title_similarity(
            "Send email via knobert-google",
            "Redis default port configuration",
        )
        assert sim < 0.3, f"Expected < 0.3, got {sim}"

    def test_distinct_topics_not_merged(self, store):
        import distill
        engine.store_entry("Redis port", "Redis runs on port 6379", tags=["redis"])
        candidates = [
            {"title": "PostgreSQL port", "content": "PostgreSQL default port is 5432", "tags": ["postgres"]},
        ]
        new = distill.diff_against_store(candidates)
        assert len(new) == 1, "Distinct topics must not be merged"


class TestConsolidate:
    """Test the consolidate command."""

    def test_merges_duplicate_titles(self, store):
        import distill
        engine.store_entry("Web Search Capabilities", "Agent can search the web", tags=["capabilities"])
        engine.store_entry("Web Search Capabilities", "System has web search", tags=["capabilities"])
        engine.store_entry("Web Search Capabilities", "Web search is available", tags=["capabilities"])
        results = distill.consolidate()
        assert len(results) == 1
        assert results[0]["action"] == "consolidated"
        assert results[0]["count"] == 2  # 2 superseded, 1 kept

    def test_merges_similar_titles(self, store):
        import distill
        engine.store_entry("Send email via knobert-google", "Use tell to send", tags=["email"])
        engine.store_entry("Sending emails via knobert-google", "Send emails with tell", tags=["email"])
        engine.store_entry("Sending an email via knobert-google", "Email sending procedure", tags=["email"])
        results = distill.consolidate()
        assert len(results) >= 1
        total_superseded = sum(r["count"] for r in results)
        assert total_superseded >= 2

    def test_dry_run_does_not_modify(self, store):
        import distill
        engine.store_entry("Duplicate Fact", "Content A", tags=["test"])
        engine.store_entry("Duplicate Fact", "Content B", tags=["test"])
        results = distill.consolidate(dry_run=True)
        assert results[0]["action"] == "would_consolidate"
        active = engine.list_nodes(status="active")
        assert len(active) == 2  # nothing actually changed

    def test_leaves_distinct_nodes_alone(self, store):
        import distill
        engine.store_entry("Redis port", "Redis runs on 6379", tags=["redis"])
        engine.store_entry("PostgreSQL port", "Postgres runs on 5432", tags=["postgres"])
        results = distill.consolidate()
        assert len(results) == 0


class TestGenericCapabilityRejection:
    """Test that generic capability descriptions are rejected."""

    def test_rejects_agent_capability_statement(self):
        import distill
        assert distill._should_reject("The agent is equipped with web search capabilities to look up information")

    def test_rejects_system_capability(self):
        import distill
        assert distill._should_reject("The system has available tools for searching and sending messages")

    def test_accepts_specific_fact(self):
        import distill
        assert not distill._should_reject("Redis default port is 6379, configurable via redis.conf bind directive")
