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
        SpeakSpan(" plus [Pay-i](/pˈeɪ ˈaɪ/) link docs", "plain"),
    )


def test_parse_inline_spans_keeps_snake_case_and_root_links():
    spans = parse_inline_spans(
        "Set API_KEY via snake_case and _italic_ then [install](/docs/install)"
    )
    assert spans == (
        SpeakSpan("Set API_KEY via snake_case and ", "plain"),
        SpeakSpan("italic", "strong"),
        SpeakSpan(" then install", "plain"),
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


def test_empty_and_whitespace_markdown_yield_no_blocks():
    assert parse_markdown_blocks("") == []
    assert parse_markdown_blocks("   \n\n\t\n") == []
    assert not looks_like_markdown("")


def test_fenced_code_only_yields_no_blocks():
    md = "```python\nprint(1)\n```\n"
    assert looks_like_markdown(md)
    assert parse_markdown_blocks(md) == []


def test_list_item_pauses_capped_below_para():
    blocks = parse_markdown_blocks("- alpha\n- beta\n- gamma\n")
    assert [b.kind for b in blocks] == ["list_item", "list_item", "list_item"]
    assert blocks[0].pause_before == 0.0
    assert blocks[1].pause_before == 0.25
    assert blocks[2].pause_before == 0.25
    say = render_say_text(blocks)
    assert "[[slnc 250]]" in say

    short = parse_markdown_blocks(
        "1. one\n2. two\n", PauseConfig(para=0.1)
    )
    assert short[1].pause_before == 0.1


def test_nested_emphasis_stays_inside_outer_strong():
    spans = parse_inline_spans("**bold *nested* more**")
    assert spans == (SpeakSpan("bold *nested* more", "strong"),)


def test_underscore_and_single_star_map_to_strong():
    assert parse_inline_spans("use __strong__ here") == (
        SpeakSpan("use ", "plain"),
        SpeakSpan("strong", "strong"),
        SpeakSpan(" here", "plain"),
    )
    assert parse_inline_spans("use *em* here") == (
        SpeakSpan("use ", "plain"),
        SpeakSpan("em", "strong"),
        SpeakSpan(" here", "plain"),
    )


def test_unclosed_markers_fall_back_to_plain():
    spans = parse_inline_spans("**no close and `also open")
    assert all(s.style == "plain" for s in spans)
    joined = "".join(s.text for s in spans)
    assert "**" in joined or joined.startswith("*")
    assert "`" in joined


def test_horizontal_rule_skipped_between_paragraphs():
    blocks = parse_markdown_blocks("Hi\n\n---\n\nBye\n")
    texts = [" ".join(s.text for s in b.spans) for b in blocks]
    assert texts == ["Hi", "Bye"]
    assert blocks[1].pause_before == 0.4


def test_blockquote_becomes_paragraph_with_inline_styles():
    blocks = parse_markdown_blocks("> quoted **bold**\n\nnext\n")
    assert blocks[0].kind == "paragraph"
    assert blocks[0].spans == (
        SpeakSpan("quoted ", "plain"),
        SpeakSpan("bold", "strong"),
    )
    assert blocks[1].pause_before == 0.4


def test_looks_like_markdown_lists_quotes_and_fences():
    assert looks_like_markdown("- item one")
    assert looks_like_markdown("1. first")
    assert looks_like_markdown("> quoted")
    assert looks_like_markdown("```\ncode\n```")
    assert looks_like_markdown("| a | b |")
    assert not looks_like_markdown("---")


def test_render_kokoro_pieces_empty_blocks():
    assert render_kokoro_pieces([]) == []
    assert render_say_text([]) == ""


def test_render_say_preserves_mid_word_markup():
    spans = parse_inline_spans("a**b**c")
    assert "".join(s.text for s in spans) == "abc"
    blocks = parse_markdown_blocks("a**b**c\n")
    rendered = render_say_text(blocks)
    assert "a[[emph +]]b c" not in rendered
    assert "a[[emph +]]bc" in rendered.replace("\n", "")


def test_empty_heading_markers_are_skipped():
    blocks = parse_markdown_blocks("#\n\nHi\n\n## \n\nBye\n")
    texts = ["".join(s.text for s in b.spans) for b in blocks]
    assert texts == ["Hi", "Bye"]


def test_kokoro_section_pause_not_stacked_with_emphasis():
    blocks = parse_markdown_blocks("# A\n\n`code`\n\n## B\n")
    pieces = render_kokoro_pieces(blocks)
    section = [p for p in pieces if p.silence_after == 2.0 and not p.text.strip()]
    assert section, pieces
    configured = (2.0, 1.0, 0.4)
    assert all(
        any(abs(p.silence_after - c) < 1e-9 for c in configured)
        or p.silence_after < 0.2
        for p in pieces
        if p.silence_after
    )
    silent = [not p.text.strip() for p in pieces]
    assert not any(a and b for a, b in zip(silent, silent[1:]))
