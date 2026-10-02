"""OurSchoolPages CMS archive and site scan commands."""
import json
import os
import re
import sys
import time

import archive
import env
import ospscan
import session
from constants import (OSP_BASE, OSP_LOGIN, OSP_CREATE_PAGE, OSP_SCAN_PAGES,
                        OSP_LISTING_PAGE_ID, OSP_LISTING_EDIT, OSP_LISTING_URL)


def dispatch(args):
    action = args.action
    if not action:
        print("Usage: b3t osp <login|archive|listing|scan>", file=sys.stderr)
        return 2
    if action == "login":
        return cmd_login(args)
    elif action == "archive":
        return cmd_archive(args)
    elif action == "listing":
        return cmd_listing(args)
    elif action == "scan":
        return cmd_scan(args)
    return 2


def ensure_authenticated():
    """Check OSP auth, auto-login if needed."""
    session.ensure_running()

    # Test by navigating to admin
    session.navigate(f"{OSP_BASE}/Admin/Index")
    time.sleep(2)
    url = session.current_url()
    if url and "/Account/LogOn" not in url:
        return True

    user = os.environ.get("OURSCHOOLPAGES_USER")
    passw = os.environ.get("OURSCHOOLPAGES_PASS")
    if not user or not passw:
        print("ERROR: Set OURSCHOOLPAGES_USER and OURSCHOOLPAGES_PASS in .env", file=sys.stderr)
        return False

    print("Logging in to OurSchoolPages...", file=sys.stderr)
    session.navigate(OSP_LOGIN)
    time.sleep(2)

    # The form's fields are named, so address them by name. Reading them out of
    # the accessibility snapshot failed twice over: OSP puts each label on its
    # own line above its box, so no one line says both "email" and "textbox",
    # and the Google Translate widget adds five more text inputs to the form
    # for a snapshot scan to pick from. The submit control is an image button.
    fields = session.run("eval", """() => {
  const u = document.querySelector('#EmailAddress, input[name=EmailAddress]');
  const p = document.querySelector('#Password, input[name=Password]');
  const s = document.querySelector('form input[type=image], form button[type=submit], form input[type=submit]');
  return 'OSPFORM:' + (u ? 'U' : '-') + (p ? 'P' : '-') + (s ? 'S' : '-');
}""")
    # A plain token, because `eval` hands back a JSON-escaped string and a
    # check written against the unescaped shape silently never matches.
    found = re.search(r'OSPFORM:(...)', fields.stdout or "")
    if not found or found.group(1)[:2] != "UP":
        print("ERROR: the sign-in form is not the one this expects; "
              f"look at {OSP_LOGIN}.", file=sys.stderr)
        return False

    # `run-code` wraps its argument as `(async function main(page){...})(page)`,
    # so it takes a whole function, not a bare body. A body parses as garbage.
    session.run("run-code", """async function main(page) {
  await page.fill('#EmailAddress', %s);
  await page.fill('#Password', %s);
  const submit = page.locator('form input[type=image], form button[type=submit], form input[type=submit]').first();
  if (await submit.count()) { await submit.click(); }
  else { await page.press('#Password', 'Enter'); }
  await page.waitForTimeout(3000);
}""" % (json.dumps(user), json.dumps(passw)), timeout=60)

    # Say whether it worked. Reporting a login that did not happen sends every
    # command after this one into a sign-in page and blames them for it.
    session.navigate(f"{OSP_BASE}/Admin/Index")
    time.sleep(2)
    url = session.current_url()
    if url and "/Account/LogOn" in url:
        print("ERROR: OurSchoolPages rejected the sign-in. Check "
              "OURSCHOOLPAGES_USER and OURSCHOOLPAGES_PASS.", file=sys.stderr)
        return False

    session.save_state()
    print("OurSchoolPages login successful.", file=sys.stderr)
    return True


def cmd_login(args):
    if ensure_authenticated():
        return 0
    return 1


def _eval(js, timeout=30):
    """Evaluate JS and return only the result value.

    Mirrors `givebacks._eval`: playwright-cli's plain `eval` echoes the
    snippet it ran back onto stdout ahead of the value, so parsing raw stdout
    for a sentinel matches the source of the check itself. `--raw` prints the
    value alone.
    """
    result = session.run("--raw", "eval", js, timeout=timeout)
    out = (result.stdout or "").strip()
    if not out:
        return None
    try:
        value = json.loads(out)
    except json.JSONDecodeError:
        return out
    return value if isinstance(value, str) else value


def _save():
    """Click #SaveButton and wait for the navigation it causes.

    A plain `page.click('#SaveButton')` followed by a fixed timeout looked
    like it worked and had not submitted anything: nothing after the click
    proved the click did anything. Seen live, the actual cause: OSP raises
    its own "Leave this page?" prompt on the way out, because the page is
    dirty and Save is itself a navigation. `window.onbeforeunload = null`
    handles the common case, but an `addEventListener('beforeunload', ...)`
    handler survives that assignment (Outlook's own unsaved-changes prompt is
    registered that way, per `outlook.py`'s `_suppress_unload_guard`; OSP's
    may be too), so a one-shot `page.once('dialog', ...)` handler that
    accepts is registered first, as the fix that actually reaches the prompt
    regardless of how it is wired. `#SaveButton` then navigates to `/Home` on
    a real save, so waiting for that navigation (and reporting when none
    comes) is what tells a save from a silent no-op.

    Returns `(ok, url, status, dialog)`. `status` is `"no-nav"` when nothing
    navigated; `ok` is false in that case. `dialog` is `"none"` or the type
    and text of whatever dialog was accepted along the way.

    playwright-cli tracks dialogs on its own, independent of the page-level
    `page.once('dialog', ...)` handler above, and ends the `run-code`
    response the moment one appears -- before the script ever reaches its
    `return`. Reproduced live: `result.stdout` comes back with only the
    echoed script and a trailing "### Modal state" block, no `### Result`,
    even though the page-level handler already called `d.accept()` and the
    click's navigation already completed (confirmed by reading `page.url()`
    straight afterwards). So a response with no match is not necessarily a
    failed save: it can also be a successful one whose result got lost.
    Since `page.click` cannot be safely repeated (a second real click risks a
    duplicate submit), recovery reads the URL directly instead. That first
    read still sees the same stuck dialog playwright-cli hasn't let go of, so
    it goes through `session.run`, whose own stuck-modal handling (any dialog
    command clears the CLI's bookkeeping, whether or not a real dialog is
    still there to answer) clears it before the read is retried.
    """
    before = session.current_url()
    result = session.run("run-code", r"""async function main(page) {
  let dialog = "none";
  page.once("dialog", d => {
    dialog = (d.type() + ": " + d.message()).replace(/[\s|]+/g, " ").trim();
    d.accept();
  });
  await page.evaluate(() => { window.onbeforeunload = null; });
  const [r] = await Promise.all([
    page.waitForNavigation({timeout: 20000}).catch(e => null),
    page.click("#SaveButton"),
  ]);
  await page.waitForTimeout(3000);
  return page.url() + " " + (r ? r.status() : "no-nav") + " | " + dialog;
}""", timeout=40, on_dialog="accept")
    m = re.search(r"(https?://\S+)\s+(\d+|no-nav)\s+\|\s+(.*)", result.stdout or "")
    if m:
        url, status, dialog = m.group(1), m.group(2), m.group(3).strip()
        if dialog != "none":
            print(f"Save's leave-page dialog was accepted: {dialog}", file=sys.stderr)
        return status != "no-nav", url, status, dialog

    session.run("eval", "() => true", on_dialog="accept")
    time.sleep(1)
    url = session.current_url() or ""
    if url and before and url != before:
        print("Save's own response was interrupted by its leave-page dialog; "
              f"recovered by reading the URL directly (now at {url}).",
              file=sys.stderr)
        return True, url, "recovered", "beforeunload (interrupted response)"
    return False, "", "no answer (%s)" % (result.stdout or "")[:200], "none"


def _load_title(html_path, edition_date, override=None):
    """The archive page's title, from `.meta.json` beside `html_path`.

    `gb archive` leaves the page's title there. Retyping it from the date
    alone produced "Bear Tracks - 2026-09-20", which matches no other page on
    the site and reads as a filename in the archive listing. Returns the
    title, or None with an error already printed.
    """
    heading = override
    meta_path = html_path + ".meta.json"
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)
        heading = heading or meta.get("title")
        if meta.get("edition") and meta["edition"] != edition_date:
            print(f"ERROR: {meta_path} is for the {meta['edition']} edition, "
                  f"not {edition_date}.", file=sys.stderr)
            return None
    if not heading:
        print(f"ERROR: no title. Either generate the page with `b3t gb archive "
              f"--edition {edition_date} --id UUID`, which writes "
              f"{os.path.basename(meta_path)} beside the HTML, or pass --title.",
              file=sys.stderr)
        return None
    return heading


def cmd_archive(args):
    """Create archive page for an edition."""
    if not ensure_authenticated():
        return 1

    html_path = args.html
    if not os.path.exists(html_path):
        print(f"ERROR: File not found: {html_path}", file=sys.stderr)
        return 1

    with open(html_path) as f:
        html_content = f.read()

    edition_date = args.edition
    slug = archive.page_slug(edition_date)

    heading = _load_title(html_path, edition_date, getattr(args, "title", None))
    if not heading:
        return 1

    # A page at this address already holds an edition. Creating a second one
    # would either fail or leave two pages fighting over the same URL.
    session.navigate(f"{OSP_BASE}/Page/BearTracks/{slug}")
    time.sleep(2)
    landed = session.current_url() or ""
    if "Error404" not in landed and slug in landed:
        print(f"ERROR: {OSP_BASE}/Page/BearTracks/{slug} already exists. "
              "Edit it in the CMS, or archive a different edition.",
              file=sys.stderr)
        return 1

    print(f"Creating archive page: {slug}", file=sys.stderr)
    session.navigate(OSP_CREATE_PAGE)
    time.sleep(3)

    # The form's fields are named. An earlier version hunted for them in the
    # accessibility snapshot by looking for a line with both "name" and
    # "textbox" in it, and found neither field, because OSP puts every label on
    # its own line above its box.
    session.run("run-code", """async function main(page) {
  await page.fill('#PageName', %s);
  await page.fill('#PageHeading', %s);
}""" % (json.dumps(slug), json.dumps(heading)), timeout=60)

    # The HTML goes in through TinyMCE, which rewrites it on the way: inline
    # styles come off and `style="width:580px"` becomes `width="580"`. That is
    # not damage, it is what every archive page on the site already looks like.
    # It also means the content that lands is shorter than the file, so the
    # length is reported rather than compared.
    session.run("localstorage-set", "_b3t_html", html_content)
    result = session.run("eval", """() => {
  const html = localStorage.getItem("_b3t_html");
  if (!html) return "ARCHIVE:no-html";
  const ed = (window.tinymce && (tinymce.get("Html") || tinymce.activeEditor));
  if (!ed) return "ARCHIVE:no-tinymce";
  ed.setContent(html);
  ed.save();
  const t = document.getElementById("Html");
  return "ARCHIVE:ok:" + (t ? t.value.length : 0) + ":"
    + (ed.getContent().match(/<div class="u-row-container"/g) || []).length;
}""", timeout=60)
    session.run("localstorage-set", "_b3t_html", "")

    state = re.search(r"ARCHIVE:(\w+)(?::(\d+):(\d+))?", result.stdout or "")
    if not state or state.group(1) != "ok":
        print("ERROR: the editor did not take the HTML (%s). Nothing was saved."
              % (state.group(1) if state else "no answer"), file=sys.stderr)
        return 1

    stored, kept = int(state.group(2)), int(state.group(3))
    expected = html_content.count('<div class="u-row-container"')
    print(f"Filled: {stored:,} chars in the editor, {kept} of {expected} rows.",
          file=sys.stderr)
    if kept != expected:
        print(f"ERROR: the editor kept {kept} rows out of {expected}. "
              "Not saving a page that lost content.", file=sys.stderr)
        return 1

    folder = session.run("eval", """() => {
  const d = document.getElementById("PageFolderDropDown");
  return "FOLDER:" + (d ? d.options[d.selectedIndex].text : "none");
}""")
    if "BearTracks" not in (folder.stdout or ""):
        print("ERROR: the page is not filed under BearTracks. Not saving.",
              file=sys.stderr)
        return 1

    if not getattr(args, "save", False):
        print(f'Title: {heading}', file=sys.stderr)
        print("The form is filled and NOT saved. Review it in the browser and "
              "click Save, or re-run with --save.", file=sys.stderr)
        print(f"URL will be: {OSP_BASE}/Page/BearTracks/{slug}")
        return 0

    ok, save_url, status, dialog = _save()
    if not ok:
        print(f"ERROR: Save did not navigate away (status={status}). The page "
              "was likely NOT saved. Check the CMS by hand.", file=sys.stderr)
        return 1

    session.navigate(f"{OSP_BASE}/Page/BearTracks/{slug}")
    time.sleep(3)
    live = session.current_url() or ""
    if "Error404" in live or slug not in live:
        print(f"ERROR: after saving, {OSP_BASE}/Page/BearTracks/{slug} is not "
              "there. Check the CMS by hand.", file=sys.stderr)
        return 1

    print(f"Published: {OSP_BASE}/Page/BearTracks/{slug}", file=sys.stderr)
    print("The archive listing page is separate and still needs its entry:", file=sys.stderr)
    print(f"  b3t osp listing --edition {edition_date}", file=sys.stderr)
    print(f"{OSP_BASE}/Page/BearTracks/{slug}")
    return 0


def cmd_listing(args):
    """Add one edition's entry to the archive LISTING page on rmsptsa.org.

    The listing (`/Page/BearTracks/Archive`) is a separate page from the
    per-edition archive page `cmd_archive` makes; this is the table of
    contents that links to it.
    """
    if not OSP_LISTING_PAGE_ID:
        print("ERROR: OSP_LISTING_PAGE_ID not set in .env", file=sys.stderr)
        return 1
    if not ensure_authenticated():
        return 1

    edition_date = args.edition
    html_path = args.html or os.path.join("editions", edition_date, "wip", "archive.html")
    if not os.path.exists(html_path):
        print(f"ERROR: File not found: {html_path}", file=sys.stderr)
        return 1
    with open(html_path) as f:
        archive_html = f.read()

    title = _load_title(html_path, edition_date)
    if not title:
        return 1

    slug = archive.page_slug(edition_date)
    highlights = archive.highlights(archive_html, limit=8)

    # An entry that links a page which is not there yet would be a dead link
    # from the moment the listing goes live. This check has to happen before
    # the listing editor is opened at all, not just before it is edited: once
    # TinyMCE's content is set, the page is dirty and any navigation away from
    # it (this one included) has to get past OSP's "Leave this page?" prompt
    # first. `session.navigate` now answers that prompt itself, but there is
    # nothing to answer if it is never raised in the first place.
    session.navigate(f"{OSP_BASE}/Page/BearTracks/{slug}")
    time.sleep(2)
    live = session.current_url() or ""
    if "Error404" in live or slug not in live:
        print(f"ERROR: {OSP_BASE}/Page/BearTracks/{slug} is not live yet. Run "
              f"`b3t osp archive --edition {edition_date} --html {html_path} "
              "--save` first.", file=sys.stderr)
        return 1

    print(f"Editing archive listing: {OSP_LISTING_EDIT}", file=sys.stderr)
    session.navigate(OSP_LISTING_EDIT)
    time.sleep(3)

    current = _eval("""() => {
  const ed = (window.tinymce && (tinymce.get("Html") || tinymce.activeEditor));
  return ed ? ed.getContent() : null;
}""")
    if current is None:
        print("ERROR: could not find the TinyMCE editor on the listing page.",
              file=sys.stderr)
        return 1

    try:
        updated = archive.listing_insert(current, edition_date, title, highlights)
    except archive.ArchiveError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    # Before writing anything: a copy of what was there, so a bad save or a
    # bad verify has something to recover from.
    backup_dir = os.path.join("editions", edition_date, "wip")
    os.makedirs(backup_dir, exist_ok=True)
    backup_path = os.path.join(backup_dir, "listing-backup.html")
    with open(backup_path, "w") as f:
        f.write(current)
    print(f"Backup of the current listing: {backup_path}", file=sys.stderr)

    before_count = current.count('-english"')

    # Same localStorage bridge as cmd_archive: the listing is easily large
    # enough to trip whatever limit makes passing HTML as a literal unsafe.
    session.run("localstorage-set", "_b3t_listing_html", updated)
    result = session.run("eval", """() => {
  const html = localStorage.getItem("_b3t_listing_html");
  if (!html) return "LISTING:no-html";
  const ed = (window.tinymce && (tinymce.get("Html") || tinymce.activeEditor));
  if (!ed) return "LISTING:no-tinymce";
  ed.setContent(html);
  ed.save();
  return "LISTING:ok:" + (ed.getContent().match(/-english"/g) || []).length;
}""", timeout=30)
    session.run("localstorage-set", "_b3t_listing_html", "")

    state = re.search(r"LISTING:(\w+)(?::(\d+))?", result.stdout or "")
    if not state or state.group(1) != "ok":
        print("ERROR: the editor did not take the HTML (%s). Nothing was saved."
              % (state.group(1) if state else "no answer"), file=sys.stderr)
        return 1

    after_count = int(state.group(2))
    print(f"Section: {archive.school_year(edition_date)}", file=sys.stderr)
    print(f"Title:   {title}", file=sys.stderr)
    print(f"Highlights: {len(highlights)}", file=sys.stderr)
    print(f"Archive links in the editor: {before_count} -> {after_count}", file=sys.stderr)
    if after_count != before_count + 1:
        print(f"ERROR: expected {before_count + 1}, found {after_count}. Not "
              f"saving. Backup: {backup_path}", file=sys.stderr)
        return 1

    if not getattr(args, "save", False):
        print("The listing form is filled and NOT saved. Review it in the "
              "browser and click Save, or re-run with --save.", file=sys.stderr)
        return 0

    ok, save_url, status, dialog = _save()
    if not ok:
        print(f"ERROR: Save did not navigate away (status={status}). The "
              f"listing was likely NOT saved. Backup: {backup_path}",
              file=sys.stderr)
        return 1

    # The editor's own content proves nothing about what went out: verify
    # against the public page itself, cache-busted so a stale cached copy
    # cannot report a false pass.
    verify_js = """async () => {
  const r = await fetch(%s + "?cb=" + Date.now(), {credentials: "include", cache: "no-store"});
  const t = await r.text();
  return JSON.stringify({has_slug: t.includes(%s),
                          count: (t.match(/-english"/g) || []).length});
}""" % (json.dumps(OSP_LISTING_URL), json.dumps(slug))
    verify_raw = _eval(verify_js, timeout=20)
    try:
        verify = json.loads(verify_raw) if isinstance(verify_raw, str) else None
    except (json.JSONDecodeError, TypeError):
        verify = None

    if not verify or not verify.get("has_slug") or verify.get("count") != before_count + 1:
        print(f"ERROR: after saving, the public listing does not show the "
              f"expected change (got {verify}). Check the CMS by hand. "
              f"Backup: {backup_path}", file=sys.stderr)
        return 1

    print(f"Published: {slug} added to the {archive.school_year(edition_date)} "
          "section of the archive listing.", file=sys.stderr)
    print(OSP_LISTING_URL)
    return 0


def cmd_scan(args):
    """Scan the public site's pages for content updates. Read-only, no auth.

    Plain HTTP, not the browser: the pages are public and server-rendered, so
    a scan neither needs nor disturbs the shared browser session. Every page
    reports its title and a hash of its text; with `--state FILE` the hashes
    are kept between runs and each page is marked new, CHANGED or unchanged.
    """
    if not OSP_BASE:
        print("ERROR: OSP_BASE must be set in .env", file=sys.stderr)
        return 1

    pages = ospscan.scan_pages(OSP_SCAN_PAGES)
    print(f"Scanning {OSP_BASE} ({len(pages)} pages)...", file=sys.stderr)
    results = [ospscan.scan_page(OSP_BASE, name, path) for name, path in pages]
    ospscan.compare(results, getattr(args, "state", None))

    failed = [r for r in results if r["error"]]
    if getattr(args, "json", False):
        print(json.dumps(results, indent=2))
    else:
        limit = getattr(args, "lines", 40)
        for r in results:
            print(f"\n{'=' * 60}")
            print(f"PAGE: {r['page']}")
            print(f"URL: {r['url']}")
            if r["error"]:
                print(f"ERROR: {r['error']}")
                continue
            status = f"  [{r['status']}]" if r.get("status") else ""
            print(f"TITLE: {r['title']}")
            print(f"HASH: {r['hash']} ({len(r['lines'])} lines){status}")
            print("-" * 60)
            shown = r["lines"] if not limit else r["lines"][:limit]
            print("\n".join(shown) if shown else "(empty or no text content)")
            if len(shown) < len(r["lines"]):
                print(f"... {len(r['lines']) - len(shown)} more lines "
                      "(--lines 0 shows all)")

    print(f"\n{'=' * 60}", file=sys.stderr)
    print(f"Scanned {len(results)} pages, {len(failed)} failed.", file=sys.stderr)
    for r in failed:
        print(f"  FAILED {r['page']}: {r['error']}", file=sys.stderr)
    return 1 if failed else 0
