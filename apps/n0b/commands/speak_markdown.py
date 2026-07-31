"""Markdown-aware segmentation for n0b ai speak."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator, Literal

Style = Literal["plain", "strong", "code"]
BlockKind = Literal["heading", "paragraph", "list_item"]

DEFAULT_PAUSE_MAJOR = 2.0
DEFAULT_PAUSE_MINOR = 1.0
DEFAULT_PAUSE_PARA = 0.4
LIST_PAUSE_CAP = 0.25

_MD_HINT_RE = re.compile(
    r"(?m)"
    r"^(#{1,6}\s|\s{0,3}([-*+]|\d+\.)\s|>\s|```|\|)"
    r"|(\*\*[^*\n]+\*\*|__[^_\n]+__|`[^`\n]+`)"
)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_LIST_RE = re.compile(r"^(\s{0,3})([-*+]|\d+\.)\s+(.*)$")
_HR_RE = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MISAKI_RE = re.compile(r"\[[^\]]+\]\(/[^/]+/\)")


@dataclass(frozen=True)
class SpeakSpan:
    text: str
    style: Style = "plain"


@dataclass(frozen=True)
class SpeakBlock:
    kind: BlockKind
    level: int
    spans: tuple[SpeakSpan, ...]
    pause_before: float = 0.0


@dataclass(frozen=True)
class PauseConfig:
    major: float = DEFAULT_PAUSE_MAJOR
    minor: float = DEFAULT_PAUSE_MINOR
    para: float = DEFAULT_PAUSE_PARA

    def before_heading(self, level: int) -> float:
        return self.major if level <= 2 else self.minor


def looks_like_markdown(text: str) -> bool:
    return bool(_MD_HINT_RE.search(text))


def _word_edge(text: str, i: int) -> bool:
    return i <= 0 or i >= len(text) or not text[i].isalnum()


def _strip_md_links(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        if _MISAKI_RE.fullmatch(m.group(0)):
            return m.group(0)
        return m.group(1)

    return _MD_LINK_RE.sub(repl, text)


def parse_inline_spans(text: str) -> tuple[SpeakSpan, ...]:
    text = _strip_md_links(text)
    spans: list[SpeakSpan] = []
    i = 0
    n = len(text)

    def push(raw: str, style: Style) -> None:
        if not raw:
            return
        if spans and style == "plain" and spans[-1].style == "plain":
            spans[-1] = SpeakSpan(spans[-1].text + raw, "plain")
        else:
            spans.append(SpeakSpan(raw, style))

    while i < n:
        misaki = _MISAKI_RE.match(text, i)
        if misaki:
            push(misaki.group(0), "plain")
            i = misaki.end()
            continue
        if text.startswith("**", i):
            end = text.find("**", i + 2)
            if end != -1:
                push(text[i + 2 : end], "strong")
                i = end + 2
                continue
        if text.startswith("__", i) and _word_edge(text, i - 1):
            end = text.find("__", i + 2)
            if end != -1 and _word_edge(text, end + 2):
                push(text[i + 2 : end], "strong")
                i = end + 2
                continue
        if text[i] == "`":
            end = text.find("`", i + 1)
            if end != -1:
                push(text[i + 1 : end], "code")
                i = end + 1
                continue
        if text[i] == "*":
            end = text.find("*", i + 1)
            if end != -1 and end > i + 1:
                push(text[i + 1 : end], "strong")
                i = end + 1
                continue
        if text[i] == "_" and _word_edge(text, i - 1):
            end = text.find("_", i + 1)
            if end != -1 and end > i + 1 and _word_edge(text, end + 1):
                push(text[i + 1 : end], "strong")
                i = end + 1
                continue
        next_special = n
        for token in ("**", "__", "`", "[", "*", "_"):
            pos = text.find(token, i)
            if pos != -1:
                next_special = min(next_special, pos)
        if next_special == i:
            push(text[i], "plain")
            i += 1
            continue
        push(text[i:next_special], "plain")
        i = next_special
    return tuple(spans)


def _pause_for(
    kind: BlockKind, level: int, *, first: bool, pauses: PauseConfig
) -> float:
    if first:
        return 0.0
    if kind == "heading":
        return pauses.before_heading(level)
    if kind == "list_item":
        return min(pauses.para, LIST_PAUSE_CAP)
    return pauses.para


def iter_markdown_blocks(
    markdown: str, pauses: PauseConfig | None = None
) -> Iterator[SpeakBlock]:
    pauses = pauses or PauseConfig()
    fenced = False
    para_lines: list[str] = []
    first = True

    def flush_para() -> Iterator[SpeakBlock]:
        nonlocal first, para_lines
        if not para_lines:
            return
        text = " ".join(line.strip() for line in para_lines if line.strip())
        para_lines = []
        if not text:
            return
        spans = parse_inline_spans(text)
        if not spans:
            return
        pause = _pause_for("paragraph", 0, first=first, pauses=pauses)
        first = False
        yield SpeakBlock("paragraph", 0, spans, pause)

    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            yield from flush_para()
            fenced = not fenced
            continue
        if fenced or stripped.startswith("|") or _HR_RE.match(line):
            continue
        if re.match(r"^#{1,6}\s*$", stripped):
            yield from flush_para()
            continue
        heading = _HEADING_RE.match(stripped)
        if heading:
            yield from flush_para()
            level = len(heading.group(1))
            spans = parse_inline_spans(heading.group(2).strip())
            if not spans:
                continue
            pause = _pause_for("heading", level, first=first, pauses=pauses)
            first = False
            yield SpeakBlock("heading", level, spans, pause)
            continue
        list_item = _LIST_RE.match(line)
        if list_item:
            yield from flush_para()
            spans = parse_inline_spans(list_item.group(3).strip())
            if not spans:
                continue
            pause = _pause_for("list_item", 0, first=first, pauses=pauses)
            first = False
            yield SpeakBlock("list_item", 0, spans, pause)
            continue
        if stripped.startswith(">"):
            para_lines.append(stripped.lstrip("> ").strip())
            continue
        if not stripped:
            yield from flush_para()
            continue
        para_lines.append(line)
    yield from flush_para()


def parse_markdown_blocks(
    markdown: str, pauses: PauseConfig | None = None
) -> list[SpeakBlock]:
    return list(iter_markdown_blocks(markdown, pauses))


def map_span_text(
    blocks: list[SpeakBlock], transform
) -> list[SpeakBlock]:
    out: list[SpeakBlock] = []
    for block in blocks:
        spans = tuple(
            SpeakSpan(transform(span.text), span.style) for span in block.spans
        )
        spans = tuple(s for s in spans if s.text.strip())
        if not spans:
            continue
        out.append(
            SpeakBlock(block.kind, block.level, spans, block.pause_before)
        )
    return out


def _say_strong(text: str) -> str:
    parts: list[str] = []
    for token in re.finditer(r"\S+|\s+", text):
        chunk = token.group(0)
        if chunk.isspace():
            parts.append(chunk)
        else:
            parts.append(f"[[emph +]]{chunk}")
    return "".join(parts)


def render_say_text(blocks: list[SpeakBlock], *, emphasis: bool = True) -> str:
    parts: list[str] = []
    for block in blocks:
        if block.pause_before > 0:
            ms = max(1, int(round(block.pause_before * 1000)))
            parts.append(f"[[slnc {ms}]]")
        chunk: list[str] = []
        for span in block.spans:
            text = span.text
            if not text:
                continue
            if not emphasis or span.style == "plain":
                chunk.append(text)
                continue
            if span.style == "code":
                chunk.append(f"[[slnc 120]]{text}[[slnc 120]]")
                continue
            chunk.append(_say_strong(text))
        spoken = "".join(chunk).strip()
        if spoken:
            parts.append(spoken)
    return "\n".join(parts)


@dataclass(frozen=True)
class KokoroPiece:
    text: str
    speed: float
    silence_after: float = 0.0


def render_kokoro_pieces(
    blocks: list[SpeakBlock],
    *,
    base_speed: float = 1.0,
    emphasis: bool = True,
) -> list[KokoroPiece]:
    pieces: list[KokoroPiece] = []
    for block in blocks:
        if block.pause_before > 0:
            pieces.append(KokoroPiece("", base_speed, block.pause_before))
        spans = [s for s in block.spans if s.text.strip()]
        for i, span in enumerate(spans):
            text = span.text.strip()
            if span.style == "strong" and emphasis:
                speed = base_speed * 0.92
                silence_before = 0.08
            elif span.style == "code" and emphasis:
                speed = base_speed * 0.85
                silence_before = 0.12
            else:
                speed = base_speed
                silence_before = 0.0
            if silence_before:
                pieces.append(KokoroPiece("", speed, silence_before))
            trailing = 0.12 if span.style == "code" and emphasis else 0.0
            if i < len(spans) - 1 and trailing == 0.0:
                trailing = 0.05 if span.style != "plain" else 0.0
            pieces.append(KokoroPiece(text, speed, trailing))
    return [p for p in pieces if p.text.strip() or p.silence_after > 0]

