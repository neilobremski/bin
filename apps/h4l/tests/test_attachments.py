from __future__ import annotations

from attachments import ATTACHED_FILE_PREFIX, split_attached_files


class TestSplitAttachedFiles:
    def test_no_attachments(self):
        body, files = split_attached_files("#war hello")
        assert body == "#war hello"
        assert files == []

    def test_trailing_attached_file(self, tmp_path):
        path = tmp_path / "doc.txt"
        path.write_text("x", encoding="utf-8")
        raw = f"#war hello\n{ATTACHED_FILE_PREFIX}{path.resolve()}"
        body, files = split_attached_files(raw)
        assert body == "#war hello"
        assert files == [path.resolve()]

    def test_blank_line_before_attachments(self, tmp_path):
        path = tmp_path / "doc.txt"
        path.write_text("x", encoding="utf-8")
        raw = f"#war hello\n\n{ATTACHED_FILE_PREFIX}{path.resolve()}"
        body, files = split_attached_files(raw)
        assert body == "#war hello"
        assert files == [path.resolve()]

    def test_preserves_order(self, tmp_path):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("a", encoding="utf-8")
        b.write_text("b", encoding="utf-8")
        raw = (
            f"body\n"
            f"{ATTACHED_FILE_PREFIX}{a.resolve()}\n"
            f"{ATTACHED_FILE_PREFIX}{b.resolve()}"
        )
        body, files = split_attached_files(raw)
        assert body == "body"
        assert files == [a.resolve(), b.resolve()]
