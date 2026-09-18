"""Image slot tests for the design builder.

Two masthead images sit at the top of every edition: a fixed logo above the
date that never changes, and the thematic header below it that changes every
time. An edition went out with those two swapped, because `gb upload --index 0`
counted images in the editor and nothing in the tool knew one slot from the
other. The rules that prevent a repeat are pinned here:

* the draft's own image lines fill the masthead slots, in order;
* an image already on the web is used as it stands, not queued for upload;
* uploads are named per edition, so a rebuild cannot hand this edition's
  masthead the last edition's `header.jpg`.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import design
from design import INTRO_TAIL, TAIL_HEADING

LOGO = "https://s3.us-east-1.amazonaws.com/unlayer.memberhub/1730662413034-logo.png"


def content(kind, **values):
    return {"id": "c", "type": kind, "values": values}


def row(*contents, **row_values):
    return {"id": "r", "values": row_values,
            "columns": [{"id": "col", "contents": list(contents)}]}


def donor():
    """The smallest design that `build` accepts: masthead, templates, tail."""
    return {"body": {"rows": [
        # masthead: logo, date, theme header, intro
        row(content("image", src={"url": LOGO, "width": 600, "height": 100},
                    altText="old logo")),
        row(content("heading", text="<span>Old date</span>", headingType="h1")),
        row(content("image", src={"url": "https://s3/1700000000000-header.jpg",
                                  "width": 10, "height": 10},
                    altText="Last Time")),
        row(content("text", text="<p>intro</p>")),
        row(content("text", text="<p>%s our website</p>" % INTRO_TAIL)),
        # style templates
        row(content("heading", text="<span>H1</span>", headingType="h1"),
            columnsBackgroundColor="#7a0019"),
        row(content("heading", text="<span>H3</span>", headingType="h3")),
        row(content("image", src={"url": "", "width": 1, "height": 1})),
        # tail
        row(content("heading", text="<span>%s</span>" % TAIL_HEADING,
                    headingType="h1")),
    ]}}


DRAFT = """# Bear Tracks: Meet the Teachers

**Sunday** | September 20, 2026

[![Bear Tracks Newsletter](%s)](https://rmsptsa.org/)

[![Meet the Teachers](header.jpg)](https://rmsptsa.org/)

The intro paragraph.

%s our website.

# Choir Boosters Meet Monday

They meet in the library.

![Choir flyer](wip/choir.png)

## %s
""" % (LOGO, INTRO_TAIL, TAIL_HEADING)


def images(built):
    return [c for r in built["body"]["rows"]
            for col in r["columns"] for c in col["contents"]
            if c["type"] == "image"]


@pytest.fixture
def built():
    return design.build(DRAFT, donor(), slug="20260920")


def test_masthead_slots_follow_the_draft(built):
    logo, header = images(built)[:2]
    assert logo["values"]["src"]["url"] == LOGO
    assert logo["values"]["altText"] == "Bear Tracks Newsletter"
    assert header["values"]["_pending_upload"] == "header.jpg"
    assert header["values"]["altText"] == "Meet the Teachers"


def test_theme_header_does_not_land_in_the_logo_slot(built):
    """The bug: the new header went above the date and the logo disappeared."""
    logo = images(built)[0]
    assert "_pending_upload" not in logo["values"]
    assert logo["values"]["src"]["url"].endswith("logo.png")


def test_an_image_already_on_the_web_needs_no_upload(built):
    assert design.pending_uploads(built) == [
        (1, "header.jpg", "20260920-header.jpg"),
        (2, "wip/choir.png", "20260920-choir.png"),
    ]


def test_uploads_are_named_per_edition(built):
    assert [c["values"].get("_upload_name") for c in images(built)] == [
        None, "20260920-header.jpg", "20260920-choir.png"]


def test_masthead_slot_count_must_match_the_draft():
    thin = donor()
    del thin["body"]["rows"][2]                  # drop the theme header slot
    with pytest.raises(design.DesignError) as e:
        design.build(DRAFT, thin, slug="20260920")
    assert "image slot" in str(e.value)


def test_carry_matches_by_name_not_position(built):
    """An article image added above another must not steal its picture."""
    live = copy.deepcopy(built)
    for c, url in zip(images(live), [
            LOGO,
            "https://s3/1789701662095-20260920-header.jpg",
            "https://s3/1789701741142-20260920-choir.png"]):
        c["values"]["src"] = {"url": url}

    extra = DRAFT.replace("![Choir flyer](wip/choir.png)",
                          "![Band](wip/band.png)\n\n![Choir flyer](wip/choir.png)")
    rebuilt = design.build(extra, donor(), slug="20260920")
    carried, why = design.carry_image_urls(rebuilt, live)

    assert carried == 2
    urls = [c["values"]["src"]["url"] for c in images(rebuilt)]
    assert urls[1].endswith("20260920-header.jpg")
    assert urls[3].endswith("20260920-choir.png")     # the new band image is 2
    assert design.pending_uploads(rebuilt) == [(2, "wip/band.png", "20260920-band.png")]
    assert "wip/band.png" in why


def test_last_editions_header_is_never_carried_over(built):
    """Both editions call the file header.jpg. Only one of them is this one."""
    live = copy.deepcopy(built)
    images(live)[1]["values"]["src"] = {
        "url": "https://s3/1788529614176-20260906-header.jpg"}

    carried, why = design.carry_image_urls(built, live)
    assert carried == 0
    assert "header.jpg" in why
    assert images(built)[1]["values"]["src"]["url"] == ""
