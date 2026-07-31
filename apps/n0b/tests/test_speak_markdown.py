"""Tests for markdown-aware n0b ai speak segmentation."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from commands.speak_markdown import (
    PauseConfig,
    SpeakSpan,
    looks_like_markdown,
    parse_inline_spans,
    parse_markdown_blocks,
    render_kokoro_pieces,
    render_say_text,
)


def test_looks_like_markdown_detects_headings_and_emphasis():
    assert looks_like_markdown("# Title\n\nHello")
    assert looks_like_markdown("Use **bold** here")
    assert looks_like_markdown("run `n0b` now")
    assert not looks_like_markdown("plain hello world")


def test_parse_inline_spans_styles_and_misaki():
    spans = parse_inline_spans(
        "See **bold** and `code` plus [Pay-i](/pˈeɪ ˈaɪ/) link [docs](https://x)"
    )
    assert spans == (
        SpeakSpan("See ", "plain"),
        SpeakSpan("bold", "strong"),
        SpeakSpan(" and ", "plain"),
        SpeakSpan("code", "code"),
        SpeakSpan(" plus ", "plain"),
        SpeakSpan("[Pay-i](/pˈeɪ ˈaɪ/)", "plain"),
        SpeakSpan(" link docs", "plain"),
    )


def test_parse_markdown_blocks_pauses_by_heading_level():
    md = "# One\n\nIntro.\n\n## Two\n\nBody.\n\n### Three\n\nMore.\n"
    blocks = parse_markdown_blocks(md)
    assert [b.kind for b in blocks] == [
        "heading",
        "paragraph",
        "heading",
        "paragraph",
        "heading",
        "paragraph",
    ]
    assert blocks[0].pause_before == 0.0
    assert blocks[0].level == 1
    assert blocks[2].level == 2
    assert blocks[2].pause_before == 2.0
    assert blocks[4].level == 3
    assert blocks[4].pause_before == 1.0
    assert blocks[1].pause_before == 0.4


def test_parse_skips_fences_and_tables():
    md = "# Title\n\n| a | b |\n|---|---|\n\nHello\n\n```py\nx=1\n```\n\nBye\n"
    blocks = parse_markdown_blocks(md)
    texts = [" ".join(s.text for s in b.spans) for b in blocks]
    assert texts == ["Title", "Hello", "Bye"]


def test_custom_pause_config():
    md = "## A\n\npara\n\n### B\n"
    blocks = parse_markdown_blocks(
        md, PauseConfig(major=1.5, minor=0.75, para=0.2)
    )
    assert blocks[0].pause_before == 0.0
    assert blocks[1].pause_before == 0.2
    assert blocks[2].pause_before == 0.75


def test_render_say_text_inserts_slnc_and_emph():
    blocks = parse_markdown_blocks("# Hi\n\nUse **care** with `x`.\n")
    rendered = render_say_text(blocks)
    assert "[[slnc 400]]" in rendered
    assert "[[emph +]]care" in rendered
    assert "`" not in rendered
    assert "[[slnc 120]]" in rendered


def test_render_say_text_major_heading_silence():
    blocks = parse_markdown_blocks("# A\n\ntext\n\n## B\n\nmore\n")
    rendered = render_say_text(blocks)
    assert "[[slnc 2000]]" in rendered


def test_render_kokoro_pieces_compose_speeds_and_silence():
    blocks = parse_markdown_blocks("# A\n\n**bold** and `code`\n\n## B\n")
    pieces = render_kokoro_pieces(blocks, base_speed=1.0)
    texts = [p.text for p in pieces if p.text.strip()]
    assert "A" in texts[0]
    assert any(p.speed < 1.0 and "bold" in p.text for p in pieces)
    assert any(p.speed < 0.9 and "code" in p.text for p in pieces)
    assert any(p.silence_after >= 2.0 for p in pieces)


def test_no_emphasis_keeps_pauses():
    blocks = parse_markdown_blocks("## A\n\n**bold**\n\n## B\n")
    say = render_say_text(blocks, emphasis=False)
    assert "[[emph +]]" not in say
    assert "[[slnc 2000]]" in say
    pieces = render_kokoro_pieces(blocks, emphasis=False)
    assert all(p.speed == 1.0 for p in pieces if p.text.strip())
    assert any(p.silence_after >= 2.0 for p in pieces)
