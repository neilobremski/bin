"""GiveBacks newsletter CMS commands."""
import json
import os
import re
import sys
import time

import env
import session
from constants import (
    GIVEBACKS_BASE, GIVEBACKS_API, GIVEBACKS_LOGIN, GIVEBACKS_CAUSE_ID, SESSION_NAME,
)

CAUSE_ID = GIVEBACKS_CAUSE_ID


def dispatch(args):
    if not GIVEBACKS_BASE or not CAUSE_ID:
        print("ERROR: GIVEBACKS_BASE and GIVEBACKS_CAUSE_ID must be set in .env", file=sys.stderr)
        return 1
    action = args.action
    if not action:
        print("Usage: b3t givebacks <login|pull|push|open|list|duplicate|upload|screenshot>", file=sys.stderr)
        return 2
    if action == "login":
        return cmd_login(args)
    elif action == "pull":
        return cmd_pull(args)
    elif action == "push":
        return cmd_push(args)
    elif action == "open":
        return cmd_open(args)
    elif action == "list":
        return cmd_list(args)
    elif action == "duplicate":
        return cmd_duplicate(args)
    elif action == "upload":
        return cmd_upload(args)
    elif action == "screenshot":
        return cmd_screenshot(args)
    elif action == "rename":
        return cmd_rename(args)
    return 2


def _api_url(message_id):
    return f"{GIVEBACKS_API}/{message_id}?cause_id={CAUSE_ID}"


def _design_url(message_id):
    return f"{GIVEBACKS_BASE}/messages/{message_id}/design"


def ensure_authenticated():
    """Check auth, auto-login + OTP if needed. Returns True if authenticated.

    Flow:
    1. Navigate to /messages — if we land there, already logged in.
    2. If on /sign-in — fill email/password from env, submit.
    3. If on /one-time-passcode — fetch OTP from Outlook via new tab, enter it,
       check "Trust this browser", submit. Close Outlook tab.
    """
    session.ensure_running()

    # Step 1: Check if already authenticated
    session.navigate(f"{GIVEBACKS_BASE}/messages")
    time.sleep(3)
    url = session.current_url() or ""

    if "/sign-in" not in url and "/sign_in" not in url and "/one-time-passcode" not in url:
        return True

    # Step 2: If on a stale OTP page (browser reopened), sign out first
    # because "Resend Code" doesn't work reliably with automation.
    # A fresh _fill_login() always triggers a new code.
    if "/one-time-passcode" in url:
        print("Stale OTP page — signing out to trigger fresh code...", file=sys.stderr)
        sign_out_ref = _find_ref(session.snapshot() or "", lambda l: "Sign Out" in l and "button" in l.lower())
        if sign_out_ref:
            session.run("click", sign_out_ref)
            time.sleep(3)

    # Step 3: Fill credentials (triggers a fresh OTP email)
    url = session.current_url() or ""
    if "/sign-in" in url or "/sign_in" in url:
        if not _fill_login():
            return False
        time.sleep(4)
        url = session.current_url() or ""

    # Step 4: Handle OTP (code was just sent by _fill_login)
    if "/one-time-passcode" in url:
        if not _handle_otp():
            return False
        time.sleep(3)
        url = session.current_url() or ""

    # Final check
    if "/sign-in" not in url and "/one-time-passcode" not in url:
        session.save_state()
        print("Login successful.", file=sys.stderr)
        return True

    print("ERROR: Login failed — still not on messages page.", file=sys.stderr)
    return False


def _fill_login():
    """Fill email/password on the sign-in page and submit."""
    user = os.environ.get("GIVEBACKS_USER")
    passw = os.environ.get("GIVEBACKS_PASS")
    if not user or not passw:
        print("ERROR: Set GIVEBACKS_USER and GIVEBACKS_PASS in .env", file=sys.stderr)
        return False

    print("Logging in to GiveBacks...", file=sys.stderr)

    snap = session.snapshot()
    if not snap:
        print("ERROR: Could not snapshot login page.", file=sys.stderr)
        return False

    email_ref = pass_ref = submit_ref = None
    for line in snap.split("\n"):
        ll = line.lower()
        if "email" in ll and "textbox" in ll and not email_ref:
            m = re.search(r'\[ref=(\w+)\]', line)
            if m:
                email_ref = m.group(1)
        if "password" in ll and "textbox" in ll and not pass_ref:
            m = re.search(r'\[ref=(\w+)\]', line)
            if m:
                pass_ref = m.group(1)
        # "Sign In" button — NOT "Sign in with Apple/Google"
        if "button" in ll and "sign in" in ll and "apple" not in ll and "google" not in ll and not submit_ref:
            m = re.search(r'\[ref=(\w+)\]', line)
            if m:
                submit_ref = m.group(1)

    if not email_ref or not pass_ref:
        print("ERROR: Cannot find login form fields.", file=sys.stderr)
        return False

    session.run("fill", email_ref, user)
    session.run("fill", pass_ref, passw)

    if submit_ref:
        session.run("click", submit_ref)
    else:
        session.run("press", pass_ref, "Enter")

    return True


def _handle_otp():
    """Handle the one-time-passcode page.

    1. Note where code was sent
    2. Click "Resend Code" (prior code may be expired)
    3. Open Outlook in new tab, find OTP email, extract 6-digit code
    4. Close Outlook tab (switch back to GiveBacks tab)
    5. Enter code into pin inputs
    6. Check "Trust this browser"
    7. Submit
    """
    snap = session.snapshot()
    if not snap:
        print("ERROR: Cannot snapshot OTP page.", file=sys.stderr)
        return False

    # Note where code was sent
    sent_to = ""
    for line in snap.split("\n"):
        if "sent a 6-digit code to" in line:
            m = re.search(r'code to ([^\s."]+)', line)
            if m:
                sent_to = m.group(1)
                break
    print(f"OTP required (sent to {sent_to}). Checking email...", file=sys.stderr)

    # Wait a moment for the email to arrive
    time.sleep(5)

    # Open Outlook in a new tab to retrieve the code
    session.run("tab-new", "https://outlook.office.com/mail/")
    time.sleep(5)

    # Get the OTP from the most recent GiveBacks email
    otp_code = _extract_otp_from_outlook()

    # Close Outlook tab and switch back to GiveBacks (tab 0)
    session.run("tab-close")
    time.sleep(1)
    session.run("tab-select", "0")
    time.sleep(1)

    if not otp_code:
        print("ERROR: Could not extract OTP code from email.", file=sys.stderr)
        return False

    print(f"Got OTP: {otp_code}", file=sys.stderr)

    # Enter code into pin inputs (6 individual textboxes)
    snap = session.snapshot()
    pin_refs = []
    for line in snap.split("\n"):
        if "PinInput" in line and "textbox" in line:
            m = re.search(r'\[ref=(\w+)\]', line)
            if m:
                pin_refs.append(m.group(1))

    if len(pin_refs) < 6:
        print(f"ERROR: Found {len(pin_refs)} pin inputs, need 6.", file=sys.stderr)
        return False

    # Fill each digit
    for i, digit in enumerate(otp_code[:6]):
        session.run("fill", pin_refs[i], digit)
        time.sleep(0.2)

    # Check "Trust this browser" if present
    trust_ref = _find_ref(snap, lambda l: "trust" in l.lower() and ("checkbox" in l.lower() or "check" in l.lower()))
    if trust_ref:
        session.run("check", trust_ref)
        time.sleep(0.5)

    # Click Submit
    submit_ref = _find_ref(snap, lambda l: "Submit" in l and "button" in l.lower() and "disabled" not in l.lower())
    if submit_ref:
        session.run("click", submit_ref)
    else:
        # Submit may auto-trigger after all digits entered
        session.run("press", pin_refs[-1], "Enter")

    time.sleep(3)
    return True


def _extract_otp_from_outlook():
    """Find the most recent GiveBacks OTP email and extract the 6-digit code."""
    # Wait for Outlook to load
    snap = session.snapshot()
    if not snap:
        return None

    # Check if we landed on Outlook (not login redirect)
    url = session.current_url()
    if url and "login.microsoftonline.com" in url:
        print("ERROR: Outlook not authenticated.", file=sys.stderr)
        return None

    # Look for OTP email in the message list — click the first unread one from GiveBacks/noreply
    # The email subject is typically "Your one-time passcode" or similar
    # First, look at the message list for something with "passcode" or "code" or "GiveBacks"
    snap = session.snapshot()
    msg_ref = _find_ref(snap, lambda l: "option" in l.lower() and ("passcode" in l.lower() or "givebacks" in l.lower() or "verification" in l.lower() or "code" in l.lower()))

    if msg_ref:
        session.run("click", msg_ref)
        time.sleep(2)
        snap = session.snapshot()

    # Look for the 6-digit code in the reading pane or message preview
    # OTP codes are typically standalone 6-digit numbers
    if snap:
        for line in snap.split("\n"):
            # Look for a standalone 6-digit number
            m = re.search(r'\b(\d{6})\b', line)
            if m:
                code = m.group(1)
                # Verify it's not a phone number or other noise
                if "phone" not in line.lower() and "fax" not in line.lower():
                    return code

    # Fallback: look in generic elements for the code
    if snap:
        for line in snap.split("\n"):
            if "generic" in line and "ref=" in line:
                m = re.search(r'\b(\d{6})\b', line)
                if m:
                    return m.group(1)

    return None


def _find_ref(snapshot_text, test_fn):
    """Find first ref matching test_fn(line) -> bool."""
    for line in snapshot_text.split("\n"):
        if test_fn(line):
            m = re.search(r'\[ref=(\w+)\]', line)
            if m:
                return m.group(1)
    return None


def _parse_json_result(stdout):
    """Parse JSON from run-code output. Result may be double-quoted."""
    for line in stdout.split("\n"):
        line = line.strip()
        if not line or line.startswith("###") or line.startswith("```") or line.startswith("await"):
            continue
        try:
            parsed = json.loads(line)
            if isinstance(parsed, str):
                try:
                    return json.loads(parsed)
                except json.JSONDecodeError:
                    return parsed
            return parsed
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
    return None


def _run_code_json(js, timeout=15):
    result = session.run("run-code", js, timeout=timeout)
    return _parse_json_result(result.stdout)


def _get_image_src(target_index):
    """Return src URL for image slot in Unlayer iframe, or empty string."""
    js = (
        f'async function main() {{ for (const f of page.frames()) {{ '
        f'if (f.url().includes("unlayer")) {{ '
        f'const imgs = await f.locator("img[alt]").all(); '
        f'if (imgs.length > {target_index}) {{ '
        f'return await imgs[{target_index}].getAttribute("src") || ""; }} }} }} return ""; }}'
    )
    value = _run_code_json(js, timeout=10)
    return value if isinstance(value, str) else ""


def _wait_for_unlayer_images(min_count=1, timeout=40):
    """Poll until Unlayer iframe has loaded image placeholders."""
    js = (
        f'async function main() {{ const deadline = Date.now() + {timeout * 1000}; '
        f'while (Date.now() < deadline) {{ for (const f of page.frames()) {{ '
        f'if (f.url().includes("unlayer")) {{ '
        f'const n = await f.locator("img[alt]").count(); '
        f'if (n >= {min_count}) return JSON.stringify({{count: n}}); }} }} '
        f'await new Promise(r => setTimeout(r, 500)); }} '
        f'return JSON.stringify({{count: 0}}); }}'
    )
    result = _run_code_json(js, timeout=timeout + 5)
    return isinstance(result, dict) and result.get("count", 0) >= min_count


def _image_loaded(target_index):
    """Return True when image slot has finished loading (naturalWidth > 50)."""
    js = (
        f'async function main() {{ for (const f of page.frames()) {{ '
        f'if (f.url().includes("unlayer")) {{ '
        f'const imgs = await f.locator("img[alt]").all(); '
        f'if (imgs.length > {target_index}) {{ '
        f'const complete = await imgs[{target_index}].evaluate(el => el.complete); '
        f'const w = await imgs[{target_index}].evaluate(el => el.naturalWidth); '
        f'return JSON.stringify({{loaded: complete && w > 50, width: w}}); }} }} }} '
        f'return JSON.stringify({{loaded: false}}); }}'
    )
    result = _run_code_json(js, timeout=10)
    return isinstance(result, dict) and result.get("loaded")


def _wait_for_image_saved(target_index, before_src="", timeout=90):
    """Wait until image slot has a new persisted URL (Unlayer S3 upload complete)."""
    deadline = time.time() + timeout
    print(f"  Waiting for S3 save (up to {timeout}s)...", file=sys.stderr)
    while time.time() < deadline:
        src = _get_image_src(target_index)
        if src.startswith("http") and src != before_src and _image_loaded(target_index):
            print(f"  Saved: {src[:80]}...", file=sys.stderr)
            return True
        time.sleep(2)
    return False


def _click_save_changes_if_needed():
    """Click Save Changes when Unlayer leaves the button enabled."""
    snap = session.snapshot()
    if not snap:
        return
    save_ref = _find_ref(snap, lambda l: "Save Changes" in l and "button" in l.lower() and "[disabled]" not in l.lower())
    if save_ref:
        session.run("click", save_ref, timeout=5)
        time.sleep(3)


def cmd_login(args):
    if ensure_authenticated():
        return 0
    return 1


def cmd_pull(args):
    """Pull design JSON from GiveBacks API."""
    if not ensure_authenticated():
        return 1

    api_url = _api_url(args.id)
    js = (
        f'() => fetch("{api_url}", {{credentials: "include"}})'
        f'.then(r => r.json()).then(d => d.message.template)'
    )

    print(f"Pulling design for {args.id}...", file=sys.stderr)
    result = session.run("eval", js, timeout=15)

    # Parse template string from eval output
    template_str = None
    for line in result.stdout.split("\n"):
        line = line.strip()
        if line.startswith('"') and "counters" in line:
            template_str = json.loads(line)
            break

    if not template_str:
        print("ERROR: Could not parse design from response.", file=sys.stderr)
        print(result.stdout[:500], file=sys.stderr)
        return 1

    design = json.loads(template_str)
    rows = len(design.get("body", {}).get("rows", []))
    print(f"Got design: {rows} rows", file=sys.stderr)

    output = json.dumps(design, indent=2)
    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(output)

    return 0


def cmd_push(args):
    """Push design JSON to GiveBacks API."""
    if not ensure_authenticated():
        return 1

    if not os.path.exists(args.design):
        print(f"ERROR: File not found: {args.design}", file=sys.stderr)
        return 1

    with open(args.design) as f:
        design = json.load(f)

    template_str = json.dumps(design)
    local_rows = len(design.get("body", {}).get("rows", []))
    print(f"Pushing design: {local_rows} rows, {len(template_str)} bytes", file=sys.stderr)

    # Store in localStorage (handles large payloads)
    result = session.run("localstorage-set", "_b3t_template", template_str)
    if result.returncode != 0:
        print(f"ERROR: Could not store design: {result.stderr}", file=sys.stderr)
        return 1

    api_url = _api_url(args.id)
    js = f'''() => {{
  const template = localStorage.getItem("_b3t_template");
  return fetch("{api_url}", {{
    method: "PUT",
    credentials: "include",
    headers: {{"Content-Type": "application/json"}},
    body: JSON.stringify({{message: {{template: template}}}})
  }}).then(r => r.status + " " + r.statusText);
}}'''

    result = session.run("eval", js, timeout=30)

    # Cleanup localStorage
    session.run("localstorage-set", "_b3t_template", "")

    if "200" not in result.stdout:
        print(f"ERROR: Push failed: {result.stdout}", file=sys.stderr)
        return 1

    print("Push successful.", file=sys.stderr)

    if args.verify:
        js_verify = f'''() => fetch("{api_url}", {{credentials: "include"}}).then(r => r.json()).then(d => {{
  const t = JSON.parse(d.message.template);
  return JSON.stringify({{rows: t.body.rows.length}});
}})'''
        result = session.run("eval", js_verify)
        for line in result.stdout.split("\n"):
            line = line.strip().strip('"')
            if "rows" in line:
                try:
                    data = json.loads(line)
                    server_rows = data.get("rows", 0)
                    if server_rows == local_rows:
                        print(f"Verified: {server_rows} rows.", file=sys.stderr)
                    else:
                        print(f"MISMATCH: server={server_rows}, local={local_rows}", file=sys.stderr)
                        return 1
                except json.JSONDecodeError:
                    pass

    return 0


def cmd_open(args):
    """Open newsletter editor in browser."""
    session.ensure_running()
    url = _design_url(args.id)
    print(f"Opening editor: {url}", file=sys.stderr)
    return session.navigate(url)


def cmd_list(args):
    """List recent newsletter drafts."""
    if not ensure_authenticated():
        return 1

    # Must be on a GiveBacks page for fetch to include auth cookies
    js = f'''() => fetch("https://api.givebacks.com/services/communication/messages?cause_id={CAUSE_ID}&per_page=10", {{
  credentials: "include"
}}).then(r => r.json()).then(d => JSON.stringify(d.messages || d.data || d))'''

    result = session.run("eval", js, timeout=15)

    for line in result.stdout.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            # Output may be double-encoded (string within string)
            parsed = line
            if parsed.startswith('"'):
                parsed = json.loads(parsed)
            data = json.loads(parsed) if isinstance(parsed, str) else parsed
            if isinstance(data, list):
                for msg in data[:10]:
                    mid = msg.get("id", msg.get("uuid", "?"))
                    subj = msg.get("subject", msg.get("name", "?"))
                    status = msg.get("status", "?")
                    sent = msg.get("sent_at", "")[:10] if msg.get("sent_at") else ""
                    print(f"  {mid}  [{status}]  {sent}  {subj}")
                return 0
        except (json.JSONDecodeError, TypeError, ValueError):
            continue

    print("Could not parse messages list.", file=sys.stderr)
    print(result.stdout[:500], file=sys.stderr)
    return 1


def cmd_duplicate(args):
    """Duplicate a newsletter. Returns new draft UUID on stdout.

    Flow:
    1. Navigate to /messages
    2. Find the row matching --id
    3. Click its three-dot menu button
    4. Click "Duplicate" menuitem
    5. Wait for page to reload with new draft
    6. Find the "(Copy)" entry via API and return its UUID
    """
    if not ensure_authenticated():
        return 1

    source_id = args.id
    print(f"Duplicating {source_id}...", file=sys.stderr)

    # Navigate to messages list
    session.navigate(f"{GIVEBACKS_BASE}/messages?search%5Bdeleted%5D%5Bvalue%5D=false")
    time.sleep(3)

    # Find the row containing this message and its kebab menu button
    snap = session.snapshot()
    if not snap:
        print("ERROR: Cannot snapshot messages page.", file=sys.stderr)
        return 1

    # Rows have format: row "Subject ..." [ref=eNNN]
    # Each row's last cell contains a button (the kebab menu)
    # We need to find the row that matches our source subject, then click its button
    kebab_ref = _find_kebab_for_message(snap, source_id)

    if not kebab_ref:
        print(f"ERROR: Cannot find message row for {source_id}.", file=sys.stderr)
        return 1

    # Click the three-dot menu
    session.run("click", kebab_ref)
    time.sleep(1)

    # Snapshot to find "Duplicate" menuitem
    snap = session.snapshot()
    dup_ref = _find_ref(snap, lambda l: 'menuitem' in l.lower() and 'Duplicate' in l)
    if not dup_ref:
        print("ERROR: Cannot find Duplicate menuitem.", file=sys.stderr)
        session.run("press", "Escape")
        return 1

    session.run("click", dup_ref)
    time.sleep(4)

    # The duplicate creates a new draft. Find it via API (it'll have "(Copy)" in subject)
    js = f'''() => fetch("https://api.givebacks.com/services/communication/messages?cause_id={CAUSE_ID}&per_page=5&search%5Bstatus%5D%5Bvalue%5D=draft", {{
  credentials: "include"
}}).then(r => r.json()).then(d => JSON.stringify(d.messages || d.data || d))'''

    result = session.run("eval", js, timeout=15)
    new_id = _parse_newest_draft(result.stdout)

    if not new_id:
        print("ERROR: Could not find new draft after duplicate.", file=sys.stderr)
        return 1

    print(f"Created draft: {new_id}", file=sys.stderr)
    print(new_id)
    return 0


def _find_kebab_for_message(snap, message_id):
    """Find the kebab menu button ref for a message row.

    Strategy: the message list is a table. Each row's last cell has a button.
    We match by checking if the row text or the message subject corresponds to
    our target. Since we know the UUID, we can also check by fetching the list
    and matching subjects. But simpler: we rely on row order matching list order.
    """
    lines = snap.split("\n")
    # Find the row that could contain our message by subject/ID
    # We'll look for any row and its kebab button
    # The API list gives us subjects — match by subject from the --id arg?
    # Actually, let's just get the subject from the API first
    js = f'''() => fetch("https://api.givebacks.com/services/communication/messages/{message_id}?cause_id={CAUSE_ID}", {{
  credentials: "include"
}}).then(r => r.json()).then(d => d.message ? d.message.subject : "NOT_FOUND")'''

    result = session.run("eval", js, timeout=15)
    subject = ""
    for line in result.stdout.split("\n"):
        line = line.strip().strip('"')
        if line and line != "NOT_FOUND" and "Bear Tracks" in line:
            subject = line
            break

    if not subject:
        return None

    # Now find the row with this subject in the snapshot
    in_target_row = False
    for line in lines:
        if f'cell "{subject}"' in line:
            in_target_row = True
            continue
        if in_target_row:
            # Look for the button in subsequent cells of this row
            if "button" in line and "ref=" in line and "cursor=pointer" in line:
                m = re.search(r'\[ref=(\w+)\]', line)
                if m:
                    return m.group(1)
            # If we hit the next row, stop
            if 'row "' in line:
                break

    return None


def _parse_newest_draft(stdout):
    """Parse the newest draft UUID from messages API response."""
    for line in stdout.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = line
            if parsed.startswith('"'):
                parsed = json.loads(parsed)
            data = json.loads(parsed) if isinstance(parsed, str) else parsed
            if isinstance(data, list) and data:
                return data[0].get("id", data[0].get("uuid"))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return None


def cmd_upload(args):
    """Upload an image to a placeholder in the newsletter editor.

    Flow:
    1. Open the editor at /messages/{id}/design
    2. Wait for Unlayer iframe to load
    3. Remove overlays (so clicks reach actual elements)
    4. Click the target image placeholder (by index or alt text)
    5. Click "Upload Image" in the right panel
    6. Handle file chooser with the upload command
    """
    if not ensure_authenticated():
        return 1

    image_path = os.path.abspath(args.image)
    if not os.path.exists(image_path):
        print(f"ERROR: File not found: {image_path}", file=sys.stderr)
        return 1

    print(f"Uploading {os.path.basename(image_path)} to editor...", file=sys.stderr)

    # Open editor and resize viewport for full visibility
    url = _design_url(args.id)
    session.navigate(url)
    session.run("resize", "1600", "1000")
    time.sleep(2)
    if not _wait_for_unlayer_images(min_count=1, timeout=40):
        print("ERROR: Unlayer editor did not load image placeholders.", file=sys.stderr)
        return 1

    # Take snapshot of the editor iframe content
    snap = session.snapshot()
    if not snap:
        print("ERROR: Cannot snapshot editor.", file=sys.stderr)
        return 1

    # Find image elements inside the Unlayer iframe (refs start with 'f')
    target_index = getattr(args, 'index', 0) or 0
    before_src = _get_image_src(target_index)
    img_refs = []
    for line in snap.split("\n"):
        if "img" in line and "[ref=f" in line:
            m = re.search(r'\[ref=(f\w+)\]', line)
            if m:
                img_refs.append(m.group(1))

    if not img_refs:
        print("ERROR: No images found in editor.", file=sys.stderr)
        return 1

    if target_index >= len(img_refs):
        print(f"ERROR: Only {len(img_refs)} images found, index {target_index} out of range.", file=sys.stderr)
        print("Available images:", file=sys.stderr)
        for i, ref in enumerate(img_refs):
            for line in snap.split("\n"):
                if ref in line:
                    print(f"  [{i}] {line.strip()}", file=sys.stderr)
                    break
        return 1

    # Scroll image into view, get bounding box, click at center coordinates.
    # Direct click on <img> ref fails because Unlayer's overlay div intercepts
    # pointer events. Coordinate-based mouse.click hits the overlay, selecting the block.
    scroll_and_bbox_js = (
        'async function main() { const frames = page.frames(); '
        'for (const f of frames) { if (f.url().includes("unlayer")) { '
        'const imgs = await f.locator("img[alt]").all(); '
        f'if (imgs.length > {target_index}) {{ '
        f'await imgs[{target_index}].scrollIntoViewIfNeeded(); '
        f'const box = await imgs[{target_index}].boundingBox(); '
        'return JSON.stringify(box); } } } return "none"; }'
    )
    result = session.run("run-code", scroll_and_bbox_js, timeout=10)
    bbox = _parse_json_result(result.stdout)

    if not bbox or "x" not in bbox:
        print("ERROR: Cannot get image bounding box.", file=sys.stderr)
        print(f"  run-code output: {result.stdout[:200]}", file=sys.stderr)
        return 1

    cx = int(bbox["x"] + bbox["width"] / 2)
    cy = int(bbox["y"] + bbox["height"] / 2)
    click_js = f'async function main() {{ await page.mouse.click({cx}, {cy}); return "clicked"; }}'
    session.run("run-code", click_js, timeout=5)
    time.sleep(2)

    # Re-snapshot: "Upload Image" button appears in right panel AFTER selection
    snap = session.snapshot()
    upload_ref = _find_ref(snap, lambda l: "Upload Image" in l and "button" in l.lower())

    if not upload_ref:
        print("ERROR: Cannot find 'Upload Image' button. Image block may not be selected.", file=sys.stderr)
        print("Try: b3t givebacks open --id {id}, manually click the image, then re-run.", file=sys.stderr)
        return 1

    # CRITICAL: Click "Upload Image" AND handle file chooser in ONE chained call.
    # The file chooser is only pending briefly after the button click.
    import subprocess as sp
    shell_cmd = (
        f'playwright-cli -s={SESSION_NAME} click {upload_ref} && '
        f'playwright-cli -s={SESSION_NAME} upload "{image_path}"'
    )
    file_mb = os.path.getsize(image_path) / (1024 * 1024)
    upload_timeout = max(60, int(30 + file_mb * 10))
    result = sp.run(shell_cmd, shell=True, capture_output=True, text=True, timeout=upload_timeout)
    if result.returncode != 0:
        print(f"ERROR: Upload failed: {result.stderr}", file=sys.stderr)
        return 1

    save_timeout = max(90, int(45 + file_mb * 15))
    print(f"Waiting for upload to save ({file_mb:.1f} MB, up to {save_timeout}s)...", file=sys.stderr)
    if not _wait_for_image_saved(target_index, before_src=before_src, timeout=save_timeout):
        print("ERROR: Upload did not finish saving before timeout.", file=sys.stderr)
        print("Stay on this page — do not navigate away — and re-run upload.", file=sys.stderr)
        return 1

    _click_save_changes_if_needed()
    time.sleep(2)
    print(f"Uploaded and saved: {os.path.basename(image_path)}", file=sys.stderr)
    return 0




def cmd_screenshot(args):
    """Take a screenshot of the newsletter for visual verification.

    Opens the editor preview or the share link and captures a full-page screenshot.
    Output: PNG file path on stdout.
    """
    if not ensure_authenticated():
        return 1

    output_dir = getattr(args, 'dir', '.') or '.'
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.abspath(os.path.join(output_dir, f"newsletter-preview.png"))

    # Use the "View Newsletter" share page (public once sent, or preview for drafts)
    # For a cleaner view: use the messages/{id} page which shows a preview
    url = f"{GIVEBACKS_BASE}/messages/{args.id}"
    session.navigate(url)
    time.sleep(4)

    # Take a full-page screenshot
    result = session.run("screenshot", "--full-page", f"--filename={output_path}", timeout=15)
    if result.returncode != 0:
        # Fallback: try using the design editor preview
        url = _design_url(args.id)
        session.navigate(url)
        time.sleep(5)
        result = session.run("screenshot", "--full-page", f"--filename={output_path}", timeout=15)
        if result.returncode != 0:
            print(f"ERROR: Screenshot failed: {result.stderr}", file=sys.stderr)
            return 1

    print(f"Screenshot saved.", file=sys.stderr)
    print(output_path)
    return 0


def cmd_rename(args):
    """Rename a newsletter draft (update subject via API).

    Usage: b3t givebacks rename --id UUID --subject "Bear Tracks - Summer Send-Off"
    """
    if not ensure_authenticated():
        return 1

    subject = args.subject
    print(f"Renaming to: {subject}", file=sys.stderr)

    api_url = _api_url(args.id)
    # Escape quotes in subject for JS
    safe_subject = subject.replace('\\', '\\\\').replace('"', '\\"')
    js = f'''() => fetch("{api_url}", {{
  method: "PUT",
  credentials: "include",
  headers: {{"Content-Type": "application/json"}},
  body: JSON.stringify({{message: {{subject: "{safe_subject}"}}}})
}}).then(r => r.status + " " + r.statusText)'''

    result = session.run("eval", js, timeout=15)

    if "200" not in result.stdout:
        print(f"ERROR: Rename failed: {result.stdout}", file=sys.stderr)
        return 1

    print("Done.", file=sys.stderr)
    return 0
