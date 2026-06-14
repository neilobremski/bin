"""CLI argument parsing and dispatch for b3t."""
import argparse
import sys

import env


def main():
    parser = argparse.ArgumentParser(
        prog="b3t",
        description="Bear Tracks newsletter automation",
    )
    sub = parser.add_subparsers(dest="command")

    # -- Browser management --
    sub.add_parser("open", help="Launch browser with persistent profile")
    sub.add_parser("close", help="Save state and close browser")
    sub.add_parser("status", help="Check browser status")
    sub.add_parser("snap", help="Print page accessibility snapshot")

    p = sub.add_parser("go", help="Navigate to URL")
    p.add_argument("url")

    p = sub.add_parser("click", help="Click element by ref")
    p.add_argument("ref")

    p = sub.add_parser("fill", help="Fill field by ref")
    p.add_argument("ref")
    p.add_argument("text")

    # -- GiveBacks --
    gb = sub.add_parser("givebacks", aliases=["gb"], help="GiveBacks newsletter CMS")
    gb_sub = gb.add_subparsers(dest="action")

    gb_sub.add_parser("login", help="Auto-login using env credentials")

    p = gb_sub.add_parser("pull", help="Pull design JSON from API")
    p.add_argument("--id", required=True, help="Message/draft UUID")
    p.add_argument("-o", "--output", help="Output file (default: stdout)")

    p = gb_sub.add_parser("push", help="Push design JSON to API")
    p.add_argument("--id", required=True, help="Message/draft UUID")
    p.add_argument("--design", required=True, help="Design JSON file path")
    p.add_argument("--verify", action="store_true", help="Verify row count after push")

    p = gb_sub.add_parser("open", help="Open editor in browser")
    p.add_argument("--id", required=True, help="Message/draft UUID")

    gb_sub.add_parser("list", help="List recent drafts")

    p = gb_sub.add_parser("duplicate", help="Duplicate a newsletter (returns new draft UUID)")
    p.add_argument("--id", required=True, help="Source message UUID to duplicate")

    p = gb_sub.add_parser("upload", help="Upload image to editor placeholder")
    p.add_argument("--id", required=True, help="Message/draft UUID")
    p.add_argument("--image", required=True, help="Image file path to upload")
    p.add_argument("--index", type=int, default=0, help="Image placeholder index (0-based, top to bottom)")

    p = gb_sub.add_parser("screenshot", help="Take visual screenshot of newsletter")
    p.add_argument("--id", required=True, help="Message/draft UUID")
    p.add_argument("--dir", default=".", help="Output directory for screenshot")

    p = gb_sub.add_parser("rename", help="Rename draft subject")
    p.add_argument("--id", required=True, help="Message/draft UUID")
    p.add_argument("--subject", required=True, help="New subject line")

    # -- Forms --
    fm = sub.add_parser("forms", help="Microsoft Forms submissions")
    fm_sub = fm.add_subparsers(dest="action")

    fm_sub.add_parser("login", help="Auto-login to M365")

    p = fm_sub.add_parser("download", help="Download submissions.xlsx")
    p.add_argument("--edition", required=True, help="Edition date YYYY-MM-DD")

    p = fm_sub.add_parser("list", help="Parse and filter submissions")
    p.add_argument("--edition", required=True, help="Edition date YYYY-MM-DD")
    p.add_argument("--since", help="Filter submissions since date YYYY-MM-DD")
    p.add_argument("--json", action="store_true", help="Output as JSON")

    # -- PeachJar --
    pj = sub.add_parser("peachjar", aliases=["pj"], help="PeachJar school flyers")
    pj_sub = pj.add_subparsers(dest="action")

    p = pj_sub.add_parser("list", help="List recent flyers")
    p.add_argument("--since", help="Filter flyers since date YYYY-MM-DD")
    p.add_argument("--json", action="store_true", help="Output as JSON")

    p = pj_sub.add_parser("get", help="Get flyer details")
    p.add_argument("flyer_id", help="Flyer ID")
    p.add_argument("--json", action="store_true", help="Output as JSON")

    # -- ParentSquare --
    ps = sub.add_parser("parentsquare", aliases=["ps"], help="ParentSquare school comms")
    ps_sub = ps.add_subparsers(dest="action")

    ps_sub.add_parser("login", help="Auto-login")
    ps_sub.add_parser("scan", help="Scan recent feed posts")

    # -- LWSD --
    lw = sub.add_parser("lwsd", help="School and district website scanning")
    lw_sub = lw.add_subparsers(dest="action")

    lw_sub.add_parser("scan", help="Scan for events and news")

    # -- OurSchoolPages --
    osp = sub.add_parser("osp", help="OurSchoolPages CMS")
    osp_sub = osp.add_subparsers(dest="action")

    osp_sub.add_parser("login", help="Auto-login")
    osp_sub.add_parser("scan", help="Scan site pages for content updates")

    p = osp_sub.add_parser("archive", help="Create archive page")
    p.add_argument("--edition", required=True, help="Edition date YYYY-MM-DD")
    p.add_argument("--html", required=True, help="HTML file to publish")

    # -- Gemini --
    gm = sub.add_parser("gemini", aliases=["gm"], help="Gemini header image generation")
    gm_sub = gm.add_subparsers(dest="action")

    gm_sub.add_parser("login", help="Navigate and verify Google auth")

    p = gm_sub.add_parser("generate", help="Generate header images")
    p.add_argument("--prompt", required=True, help="Generation prompt text")
    p.add_argument("--template", help="Template image to upload")
    p.add_argument("--dir", "-o", default=".", help="Output directory for downloaded image")

    p = gm_sub.add_parser("download", help="Download generated images")
    p.add_argument("--dir", default=".", help="Output directory")

    # -- Outlook --
    ol = sub.add_parser("outlook", aliases=["ol"], help="Outlook email")
    ol_sub = ol.add_subparsers(dest="action")

    ol_sub.add_parser("login", help="Auto-login")

    p = ol_sub.add_parser("check", help="Check for new messages")
    p.add_argument("--folder", default="Submissions", help="Folder to check")

    p = ol_sub.add_parser("read", help="Read a message (expands full thread)")
    p.add_argument("number", help="Message number from check output")
    p.add_argument("--folder", default="Submissions", help="Folder containing the message")
    p.add_argument("--dir", help="Download attachments to this directory")

    # -- Edition --
    ed = sub.add_parser("edition", aliases=["ed"], help="Edition management")
    ed_sub = ed.add_subparsers(dest="action")

    p = ed_sub.add_parser("create", help="Create new edition directory")
    p.add_argument("date", help="Edition date YYYY-MM-DD")
    p.add_argument("--title", required=True, help="Edition title")

    p = ed_sub.add_parser("status", help="Show edition status")
    p.add_argument("date", nargs="?", help="Edition date (default: latest)")

    ed_sub.add_parser("manifest", help="Show manifest")

    # -- Parse and dispatch --
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    env.load_env()

    return _dispatch(args)


def _dispatch(args):
    """Route to the correct handler."""
    import session

    cmd = args.command

    # Top-level browser commands
    if cmd == "open":
        return session.open_browser()
    elif cmd == "close":
        return session.close_browser()
    elif cmd == "status":
        return _cmd_status()
    elif cmd == "snap":
        return _cmd_snap()
    elif cmd == "go":
        return session.navigate(args.url)
    elif cmd == "click":
        result = session.run("click", args.ref)
        if result.returncode != 0:
            print(f"ERROR: {result.stderr}", file=sys.stderr)
        return result.returncode
    elif cmd == "fill":
        result = session.run("fill", args.ref, args.text)
        if result.returncode != 0:
            print(f"ERROR: {result.stderr}", file=sys.stderr)
        return result.returncode

    # Group commands
    elif cmd in ("givebacks", "gb"):
        import givebacks
        return givebacks.dispatch(args)
    elif cmd == "forms":
        import forms
        return forms.dispatch(args)
    elif cmd in ("peachjar", "pj"):
        import peachjar
        return peachjar.dispatch(args)
    elif cmd in ("parentsquare", "ps"):
        import parentsquare
        return parentsquare.dispatch(args)
    elif cmd == "lwsd":
        import lwsd
        return lwsd.dispatch(args)
    elif cmd == "osp":
        import osp
        return osp.dispatch(args)
    elif cmd in ("gemini", "gm"):
        import gemini
        return gemini.dispatch(args)
    elif cmd in ("outlook", "ol"):
        import outlook
        return outlook.dispatch(args)
    elif cmd in ("edition", "ed"):
        import edition
        return edition.dispatch(args)

    return 0


def _cmd_status():
    import session
    if session.is_running():
        url = session.current_url()
        print(f"Running: {url}")
    else:
        print("Not running.")
    return 0


def _cmd_snap():
    import session
    text = session.snapshot()
    if text:
        print(text)
        return 0
    return 1
