"""Distill extraction tests — accuracy and noise rejection."""
import engine
import distill


class TestPatternExtraction:
    def test_til_pattern(self, store, tmp_path):
        journal = tmp_path / "j.md"
        journal.write_text("TIL: kubectl port-forward requires the pod to be Running. CrashLoopBackOff gives a cryptic error.")
        results = distill.distill([str(journal)])
        stored = [r for r in results if r["action"] in ("stored", "would_store")]
        assert len(stored) >= 1

    def test_fix_pattern(self, store, tmp_path):
        journal = tmp_path / "j.md"
        journal.write_text("The fix is: add --vfs-cache-max-size 10G to prevent unbounded cache growth.")
        results = distill.distill([str(journal)])
        stored = [r for r in results if r["action"] in ("stored", "would_store")]
        assert len(stored) >= 1

    def test_code_block_pattern(self, store, tmp_path):
        journal = tmp_path / "j.md"
        journal.write_text("Use this command:\n```\nssh -L 8080:localhost:3000 user@host\n```\n")
        results = distill.distill([str(journal)])
        stored = [r for r in results if r["action"] in ("stored", "would_store")]
        assert len(stored) >= 1

    def test_noise_rejected(self, store, tmp_path):
        journal = tmp_path / "j.md"
        journal.write_text("Hey team, let's sync tomorrow morning. I think we should look into this next week.")
        results = distill.distill([str(journal)])
        assert len(results) == 0, f"Extracted noise: {results}"

    def test_multiple_facts_in_one_file(self, store, tmp_path):
        journal = tmp_path / "j.md"
        journal.write_text("""
TIL: Redis expires keys lazily — only checked on access.

Also: The fix is: use SCAN instead of KEYS in production to avoid blocking.

TIL: PostgreSQL VACUUM doesn't reclaim disk space, only marks pages reusable.
""")
        results = distill.distill([str(journal)])
        stored = [r for r in results if r["action"] in ("stored", "would_store")]
        assert len(stored) >= 2, f"Only extracted {len(stored)} from 3 facts"


class TestDistillDedup:
    def test_skips_already_known(self, store, tmp_path):
        engine.store_entry("Redis SCAN not KEYS production", "Use SCAN instead of KEYS in production to avoid blocking Redis", tags=["redis"])
        journal = tmp_path / "j.md"
        journal.write_text("TIL: Use SCAN instead of KEYS in production to avoid blocking Redis")
        results = distill.distill([str(journal)])
        stored = [r for r in results if r["action"] == "stored"]
        assert len(stored) == 0
