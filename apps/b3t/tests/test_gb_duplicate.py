"""`gb duplicate` finds the source row and the Duplicate item in real snapshots.

On 2026-11-13 it failed with "Cannot find message row": playwright-cli now
prints table cells as `cell [ref=e1]: Subject` instead of `cell "Subject"`,
and the Duplicate menuitem carries its label in a child `generic`. The
fixtures are trimmed from that day's GiveBacks /messages page.

No browser: these are the pure snapshot parsers.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import givebacks

FIXTURES = Path(__file__).resolve().parent / "fixtures"
LIST = (FIXTURES / "gb_messages_list.txt").read_text()
MENU = (FIXTURES / "gb_message_menu.txt").read_text()


def test_kebab_for_plain_subject():
    assert givebacks.kebab_ref_for_subject(LIST, "Bear Tracks - Let's Get Social") == "f219e274"


def test_kebab_for_first_row():
    assert givebacks.kebab_ref_for_subject(LIST, "Bear Tracks - Center Stage") == "f219e256"


def test_kebab_for_yaml_quoted_subject():
    # A colon makes YAML quote the cell text.
    assert givebacks.kebab_ref_for_subject(LIST, "Bear Tracks Extra: Spooky Slice") == "f219e292"


def test_kebab_needs_exact_subject():
    assert givebacks.kebab_ref_for_subject(LIST, "Bear Tracks - Let's Get") is None
    assert givebacks.kebab_ref_for_subject(LIST, "Nope") is None


def test_kebab_does_not_cross_into_next_row():
    snap = (
        "- row [ref=e1]:\n"
        "  - cell [ref=e2]: Bear Tracks - A\n"
        "  - cell [ref=e3]\n"
        "- row [ref=e4]:\n"
        "  - cell [ref=e5]: Bear Tracks - B\n"
        "  - cell [ref=e6]:\n"
        "    - button [ref=e7] [cursor=pointer]\n"
    )
    assert givebacks.kebab_ref_for_subject(snap, "Bear Tracks - A") is None
    assert givebacks.kebab_ref_for_subject(snap, "Bear Tracks - B") == "e7"


def test_kebab_old_quoted_cell_form():
    snap = (
        '- row "Bear Tracks - Old" [ref=e1]:\n'
        '  - cell "Bear Tracks - Old" [ref=e2]\n'
        '  - cell [ref=e3]:\n'
        '    - button [ref=e4] [cursor=pointer]\n'
    )
    assert givebacks.kebab_ref_for_subject(snap, "Bear Tracks - Old") == "e4"


def test_duplicate_item_label_in_child():
    assert givebacks.duplicate_menuitem_ref(MENU) == "f219e730"


def test_duplicate_item_label_on_line():
    snap = '- menuitem "Duplicate" [ref=e9] [cursor=pointer]\n'
    assert givebacks.duplicate_menuitem_ref(snap) == "e9"


def test_duplicate_item_missing():
    snap = "- menu [ref=e1]:\n  - menuitem [ref=e2]:\n    - generic [ref=e3]: Archive\n"
    assert givebacks.duplicate_menuitem_ref(snap) is None


SRC = "d1b46cca-6583-40f0-bf04-e69d6885c250"
NEW = "61ab0bf0-5b64-4ee0-aa74-9d05c4dc0237"


def test_new_id_from_editor_url():
    url = f"https://redmondmsptsa.givebacks.com/messages/{NEW}"
    assert givebacks.new_draft_id_from_url(url, SRC) == NEW


def test_url_still_on_source_or_list_is_not_new():
    assert givebacks.new_draft_id_from_url(
        f"https://redmondmsptsa.givebacks.com/messages/{SRC}/design", SRC) is None
    assert givebacks.new_draft_id_from_url(
        "https://redmondmsptsa.givebacks.com/messages?search%5Bdeleted%5D%5Bvalue%5D=false", SRC) is None
    assert givebacks.new_draft_id_from_url(None, SRC) is None


def test_pick_copy_draft_skips_unrelated_drafts():
    messages = [
        {"uuid": "older-draft", "status": "draft", "subject": "Bear Tracks - Summer Send-Off"},
        {"uuid": NEW, "status": "draft", "subject": "Bear Tracks - Let's Get Social (Copy)"},
        {"uuid": SRC, "status": "sent", "subject": "Bear Tracks - Let's Get Social"},
    ]
    assert givebacks.pick_copy_draft(messages, SRC, "Bear Tracks - Let's Get Social") == NEW
    assert givebacks.pick_copy_draft(messages[:1], SRC, "Bear Tracks - Let's Get Social") is None


def test_parse_message_list_double_encoded():
    import json
    payload = json.dumps(json.dumps([{"uuid": NEW}]))
    assert givebacks._parse_message_list(f"### Result\n{payload}\n") == [{"uuid": NEW}]
