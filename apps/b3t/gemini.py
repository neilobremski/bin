"""Gemini header image generation — deterministic single-command flow.

Usage:
    b3t gemini generate --prompt "BEAR TRACKS / SUMMER SEND-OFF" --template header-image-template.png -o wip/
    b3t gemini login
    b3t gemini download -o wip/

The generate command does everything: opens Gemini, uploads template, sends prompt,
waits for generation, downloads the result. One command, one image.
"""
import os
import re
import shutil
import sys
import time

import session
from constants import GEMINI_URL

# Timeouts (seconds)
GENERATION_TIMEOUT = 45
GENERATION_POLL = 3
PAGE_LOAD_WAIT = 3
UPLOAD_WAIT = 3


def dispatch(args):
    action = args.action
    if not action:
        print("Usage: b3t gemini <login|generate|download>", file=sys.stderr)
        return 2
    if action == "login":
        return cmd_login(args)
    elif action == "generate":
        return cmd_generate(args)
    elif action == "download":
        return cmd_download(args)
    return 2


def _find_ref(snapshot_text, test_fn):
    """Find first ref matching test_fn(line) -> bool."""
    for line in snapshot_text.split("\n"):
        if test_fn(line):
            m = re.search(r'\[ref=(\w+)\]', line)
            if m:
                return m.group(1)
    return None


def _wait_for_ref(test_fn, description, timeout=10, poll=1):
    """Poll snapshots until a ref matching test_fn appears."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = session.snapshot()
        if snap:
            ref = _find_ref(snap, test_fn)
            if ref:
                return ref
        time.sleep(poll)
    print(f"ERROR: Timed out waiting for: {description}", file=sys.stderr)
    return None


def _ensure_gemini():
    """Navigate to Gemini new chat, verify auth. Returns 0 or error code."""
    session.ensure_running()
    session.navigate(GEMINI_URL)
    time.sleep(PAGE_LOAD_WAIT)

    url = session.current_url()
    if url and "accounts.google.com" in url:
        print("ERROR: Not authenticated. Run: b3t open, log into Google, b3t close", file=sys.stderr)
        return 1
    return 0


def cmd_login(args):
    """Navigate to Gemini and verify Google auth."""
    rc = _ensure_gemini()
    if rc == 0:
        print("Gemini authenticated.", file=sys.stderr)
    return rc


def cmd_generate(args):
    """Full flow: navigate to Gemini, upload template, send prompt, wait, download."""
    output_dir = args.dir if hasattr(args, "dir") and args.dir else "."
    os.makedirs(output_dir, exist_ok=True)

    # Step 1: Navigate to fresh Gemini chat
    rc = _ensure_gemini()
    if rc != 0:
        return rc

    # Step 2: Upload template (if provided)
    if args.template:
        template_path = os.path.abspath(args.template)
        if not os.path.exists(template_path):
            print(f"ERROR: Template not found: {template_path}", file=sys.stderr)
            return 1

        rc = _upload_file(template_path)
        if rc != 0:
            return rc

    # Step 3: Send prompt
    rc = _send_prompt(args.prompt)
    if rc != 0:
        return rc

    # Step 4: Wait for generation to complete
    print("Waiting for image generation...", file=sys.stderr)
    download_ref = _wait_for_generation()
    if not download_ref:
        return 1

    # Step 5: Download the result
    return _download_image(download_ref, output_dir)


def _upload_file(filepath):
    """Click Upload & tools -> Upload files -> upload the file."""
    snap = session.snapshot()
    if not snap:
        print("ERROR: Cannot get page snapshot.", file=sys.stderr)
        return 1

    # Find "Upload & tools" button
    upload_btn = _find_ref(snap, lambda l: "Upload & tools" in l and "button" in l)
    if not upload_btn:
        # Fallback: look for any plus/attach button near the prompt
        upload_btn = _find_ref(snap, lambda l: "plus" in l.lower() and "button" in l.lower())
    if not upload_btn:
        print("ERROR: Cannot find 'Upload & tools' button.", file=sys.stderr)
        return 1

    session.run("click", upload_btn)
    time.sleep(1)

    # Find "Upload files" menu item
    upload_files_ref = _wait_for_ref(
        lambda l: "Upload files" in l and "menuitem" in l.lower(),
        "'Upload files' menu item",
        timeout=5,
    )
    if not upload_files_ref:
        # Fallback: look for file uploader button
        snap = session.snapshot()
        upload_files_ref = _find_ref(snap, lambda l: "upload" in l.lower() and ("file" in l.lower() or "document" in l.lower()) and "ref=" in l)
        if not upload_files_ref:
            print("ERROR: Cannot find 'Upload files' menu item.", file=sys.stderr)
            return 1

    # Click upload files and handle file chooser
    result = session.run("click", upload_files_ref)
    time.sleep(0.5)
    session.run("upload", filepath)
    time.sleep(UPLOAD_WAIT)

    print(f"Uploaded: {os.path.basename(filepath)}", file=sys.stderr)
    return 0


def _send_prompt(prompt):
    """Fill the prompt textbox and press Enter."""
    snap = session.snapshot()
    if not snap:
        print("ERROR: Cannot get page snapshot.", file=sys.stderr)
        return 1

    # Find prompt textbox
    input_ref = _find_ref(snap, lambda l: "textbox" in l.lower() and ("prompt" in l.lower() or "gemini" in l.lower() or "ask" in l.lower()))
    if not input_ref:
        input_ref = _find_ref(snap, lambda l: "textbox" in l.lower())
    if not input_ref:
        print("ERROR: Cannot find prompt input field.", file=sys.stderr)
        return 1

    session.run("fill", input_ref, prompt)
    session.run("press", "Enter")
    print("Prompt sent.", file=sys.stderr)
    return 0


def _wait_for_generation():
    """Poll until 'Download full size image' button appears. Returns its ref."""
    deadline = time.time() + GENERATION_TIMEOUT
    while time.time() < deadline:
        snap = session.snapshot()
        if snap:
            ref = _find_ref(snap, lambda l: "Download full size image" in l and "button" in l.lower())
            if ref:
                print("Generation complete.", file=sys.stderr)
                return ref
            # Also check for error states
            if _find_ref(snap, lambda l: "couldn't generate" in l.lower() or "try again" in l.lower()):
                print("ERROR: Gemini failed to generate image.", file=sys.stderr)
                return None
        time.sleep(GENERATION_POLL)

    print("ERROR: Generation timed out.", file=sys.stderr)
    return None


def _download_image(download_ref, output_dir):
    """Click download, wait for file, move to output_dir."""
    # Scroll down to ensure download button is in view
    session.run("mousewheel", "0", "2000")
    time.sleep(2)

    # Re-snapshot after scroll to get fresh ref (element may have shifted)
    snap = session.snapshot()
    fresh_ref = _find_ref(snap, lambda l: "Download full size image" in l and "button" in l.lower())
    if fresh_ref:
        download_ref = fresh_ref

    session.run("click", download_ref)
    time.sleep(1)
    # Second click in case first registered as hover
    session.run("click", download_ref)
    print("Download initiated...", file=sys.stderr)

    # Wait for file to appear in .playwright-cli/
    pw_dir = os.path.join(os.getcwd(), ".playwright-cli")
    deadline = time.time() + 15
    new_file = None
    existing = set(os.listdir(pw_dir)) if os.path.isdir(pw_dir) else set()

    while time.time() < deadline:
        time.sleep(1)
        if os.path.isdir(pw_dir):
            current = set(os.listdir(pw_dir))
            new_files = [f for f in (current - existing)
                         if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
                         and not f.startswith("page-")]
            if new_files:
                new_file = new_files[0]
                break

    if not new_file:
        print("ERROR: Download did not complete in time.", file=sys.stderr)
        return 1

    # Move to output dir
    src = os.path.join(pw_dir, new_file)
    # Name sequentially based on existing header-option-* files
    existing_options = [f for f in os.listdir(output_dir) if f.startswith("header-option-")]
    next_num = len(existing_options) + 1
    ext = os.path.splitext(new_file)[1]
    dst = os.path.join(output_dir, f"header-option-{next_num}{ext}")
    shutil.move(src, dst)

    print(dst)
    print(f"Saved: {dst}", file=sys.stderr)
    return 0


def cmd_download(args):
    """Download image from current Gemini chat (if generation already done)."""
    output_dir = args.dir if hasattr(args, "dir") and args.dir else "."
    os.makedirs(output_dir, exist_ok=True)

    session.ensure_running()

    # Scroll to find download button
    session.run("mousewheel", "0", "3000")
    time.sleep(1)

    snap = session.snapshot()
    if not snap:
        print("ERROR: Cannot get page snapshot.", file=sys.stderr)
        return 1

    download_ref = _find_ref(snap, lambda l: "Download full size image" in l and "button" in l.lower())
    if not download_ref:
        print("ERROR: No 'Download full size image' button found.", file=sys.stderr)
        return 1

    return _download_image(download_ref, output_dir)
