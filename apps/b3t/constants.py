"""Centralized configuration for b3t.

All org-specific values come from environment variables (loaded from .env).
Platform-level URLs that are the same for any user stay as defaults.
"""
import os
import sys


def _require(key):
    """Get required env var or exit with clear error."""
    val = os.environ.get(key)
    if not val:
        print(f"ERROR: {key} not set. Add it to .env or export it.", file=sys.stderr)
        sys.exit(1)
    return val


def _optional(key, default=""):
    """Get optional env var with default."""
    return os.environ.get(key, default)


# --- Session / Browser ---
SESSION_NAME = _optional("B3T_SESSION", "b3t")
CHROME_PROFILE_DIR = os.path.expanduser(
    _optional("B3T_CHROME_PROFILE", "~/.b3t-chrome-profile")
)
STATE_FILE = ".playwright-cli/b3t-state.json"


# --- GiveBacks ---
# Platform (same for everyone)
GIVEBACKS_API = "https://api.givebacks.com/services/communication/messages"
# Org-specific (from .env)
GIVEBACKS_BASE = _optional("GIVEBACKS_BASE")  # e.g. https://redmondmsptsa.givebacks.com
GIVEBACKS_LOGIN = f"{GIVEBACKS_BASE}/users/sign_in" if GIVEBACKS_BASE else ""
GIVEBACKS_CAUSE_ID = _optional("GIVEBACKS_CAUSE_ID")  # org UUID on GiveBacks


# --- Microsoft 365 ---
# Platform (same for everyone)
OUTLOOK_URL = "https://outlook.office.com/mail/"
# Org-specific (from .env)
FORMS_URL = _optional("FORMS_URL")  # full URL to the Forms responses page
FORMS_DOWNLOAD_PREFIX = _optional("FORMS_DOWNLOAD_PREFIX")  # optional xlsx filename filter


# --- PeachJar ---
# Platform
PEACHJAR_GRAPHQL = "https://parent-app-bff.peachjar.com/graphql"
# Org-specific
PEACHJAR_API_KEY = _optional("PEACHJAR_API_KEY")
_pj_id = _optional("PEACHJAR_AUDIENCE_ID")
PEACHJAR_AUDIENCE_ID = int(_pj_id) if _pj_id else 0
_pj_dist = _optional("PEACHJAR_DISTRICT_ID")
PEACHJAR_DISTRICT_ID = int(_pj_dist) if _pj_dist else 0


# --- ParentSquare ---
# Platform
PARENTSQUARE_BASE = "https://www.parentsquare.com"
PARENTSQUARE_LOGIN = f"{PARENTSQUARE_BASE}/signin"
# Org-specific
_PS_SCHOOL_ID = _optional("PARENTSQUARE_SCHOOL_ID")
PARENTSQUARE_FEED = f"{PARENTSQUARE_BASE}/schools/{_PS_SCHOOL_ID}/feeds" if _PS_SCHOOL_ID else ""


# --- OurSchoolPages ---
# Org-specific
OSP_BASE = _optional("OSP_BASE")  # e.g. https://rmsptsa.org
OSP_LOGIN = f"{OSP_BASE}/Account/LogOn" if OSP_BASE else ""
_osp_folder = _optional("OSP_FOLDER_ID")
OSP_FOLDER_ID = int(_osp_folder) if _osp_folder else 0
OSP_CREATE_PAGE = f"{OSP_BASE}/PageManager/AdminCreate/{OSP_FOLDER_ID}" if OSP_BASE and OSP_FOLDER_ID else ""
# Comma-separated name|path pairs, e.g. "Home|/,Calendar|/Event/MonthCalendar"
OSP_SCAN_PAGES = _optional("OSP_SCAN_PAGES")


# --- LWSD / school website ---
LWSD_SCHOOL_URL = _optional("LWSD_SCHOOL_URL")  # e.g. https://rms.lwsd.org
LWSD_DISTRICT_URL = _optional("LWSD_DISTRICT_URL", "https://www.lwsd.org")


# --- Gemini ---
GEMINI_URL = "https://gemini.google.com/app"
