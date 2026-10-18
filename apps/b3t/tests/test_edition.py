import os

import edition


def test_source_path_prefers_newsletter_md(tmp_path):
    (tmp_path / "draft.md").write_text("old")
    (tmp_path / "newsletter.md").write_text("new")
    assert edition.source_path(str(tmp_path)) == os.path.join(str(tmp_path), "newsletter.md")


def test_source_path_falls_back_to_draft_md(tmp_path):
    (tmp_path / "draft.md").write_text("old")
    assert edition.source_path(str(tmp_path)) == os.path.join(str(tmp_path), "draft.md")


def test_source_path_missing_names_newsletter_md(tmp_path):
    assert edition.source_path(str(tmp_path)) == os.path.join(str(tmp_path), "newsletter.md")


def test_create_writes_newsletter_md(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class Args:
        date = "2026-11-01"
        title = "Test"

    edition.cmd_create(Args())
    d = tmp_path / "editions" / "2026-11-01"
    assert (d / "newsletter.md").exists()
    assert not (d / "draft.md").exists()
