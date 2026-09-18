"""Reading-pane parser tests.

The parser walks a flat snapshot and must decide, line by line, which line
carries the sender address and which lines carry the body. That coupling has
regressed twice: once the body address was taken as the sender, once the line
after a From header was consumed whether or not it held an address, which hid
the body. Both shapes are pinned here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from outlook import parse_reading_pane

HEADER_KINDS = ("heading", "button")


def snapshot(*lines):
    return "\n".join(lines)


@pytest.mark.parametrize("kind", HEADER_KINDS)
def test_body_survives_a_header_with_no_address_line(kind):
    """A From header followed straight by the body must not eat the body.

    Outlook does not always render an address row under the header. When the
    parser consumed that line regardless, the document "Message body" marker
    went with it and the message read as empty.
    """
    pane, senders, _ = parse_reading_pane(snapshot(
        f'        - {kind} "From: Alice" [ref=e10]',
        '        - document "Message body" [ref=e12]',
        '          - generic [ref=e13]: Keep this sentence',
        '          - generic [ref=e14]: Contact Bob<bob@example.invalid>',
        '          - generic [ref=e15]: And this sentence',
    ))
    assert senders == []
    text = "\n".join(pane)
    assert "--- From: Alice ---" in text
    assert "Keep this sentence" in text
    assert "Contact Bob<bob@example.invalid>" in text
    assert "And this sentence" in text


@pytest.mark.parametrize("kind", HEADER_KINDS)
def test_address_under_the_header_is_the_sender(kind):
    """Only the row directly under the header names the sender.

    An address written in the body is not a sender, and reading it as one
    misattributed messages.
    """
    pane, senders, _ = parse_reading_pane(snapshot(
        f'        - {kind} "From: Alice" [ref=e10]',
        '        - generic [ref=e11]: Alice Smith<alice@example.invalid>',
        '        - document "Message body" [ref=e12]',
        '          - generic [ref=e13]: Contact Bob<bob@example.invalid>',
        '          - generic [ref=e14]: And this sentence',
    ))
    assert senders == ["alice@example.invalid"]
    text = "\n".join(pane)
    assert "--- From: Alice Smith <alice@example.invalid> ---" in text
    assert "Contact Bob<bob@example.invalid>" in text
    assert "And this sentence" in text


def test_attachment_directly_under_the_header_is_collected():
    pane, _, attachments = parse_reading_pane(snapshot(
        '        - button "From: Alice" [ref=e10]',
        '        - option "flyer.pdf 220 KB" [ref=e11]',
        '        - document "Message body" [ref=e12]',
        '          - generic [ref=e13]: Body text here',
    ))
    assert [a["name"] for a in attachments] == ["flyer.pdf 220 KB"]
    assert [a["ref"] for a in attachments] == ["e11"]
    assert "Body text here" in "\n".join(pane)


def test_second_message_without_an_address_keeps_its_body():
    """Each header starts a message. The second one here has no address row,
    which is the same hole as the first test but only visible in a thread."""
    pane, senders, _ = parse_reading_pane(snapshot(
        '        - button "From: Alice" [ref=e10]',
        '        - generic [ref=e11]: Alice Smith<alice@example.invalid>',
        '        - document "Message body" [ref=e12]',
        '          - generic [ref=e13]: First body',
        '        - button "From: Bob" [ref=e20]',
        '        - document "Message body" [ref=e22]',
        '          - generic [ref=e23]: Second body mentions carol@example.invalid',
    ))
    assert senders == ["alice@example.invalid"]
    text = "\n".join(pane)
    assert "First body" in text
    assert "Second body mentions carol@example.invalid" in text


def test_external_email_banner_is_stripped_from_the_body():
    pane, _, _ = parse_reading_pane(snapshot(
        '        - button "From: Alice" [ref=e10]',
        '        - document "Message body" [ref=e12]',
        '          - generic [ref=e13]: EXTERNAL EMAIL: Use Caution!',
        '          - generic [ref=e14]: Real content',
    ))
    text = "\n".join(pane)
    assert "EXTERNAL EMAIL" not in text
    assert "Real content" in text


def test_empty_snapshot_yields_nothing():
    assert parse_reading_pane("") == ([], [], [])


# --------------------------------------------------- selected message row

from outlook import selected_ref


def test_selected_ref_reads_the_selected_row():
    snap = snapshot(
        '- option "Bonita Stone Subscribe Me" [ref=e10]',
        '- option "Uthraa Manoharr Subscribe me pls" [selected] [ref=e11]',
    )
    assert selected_ref(snap) == "e11"


def test_a_shorter_ref_is_not_a_match():
    """`e1 in "[ref=e10]"` is true, and that reply goes to the wrong person."""
    snap = snapshot('- option "Bonita Stone Subscribe Me" [selected] [ref=e10]')
    assert selected_ref(snap) == "e10"
    assert selected_ref(snap) != "e1"


def test_no_selection_is_not_a_guess():
    snap = snapshot('- option "Bonita Stone Subscribe Me" [ref=e10]')
    assert selected_ref(snap) is None
    assert selected_ref("") is None
