"""PeachJar school flyer queries via GraphQL (no browser needed)."""
import json
import sys
from datetime import date

import requests

from constants import PEACHJAR_GRAPHQL, PEACHJAR_API_KEY, PEACHJAR_AUDIENCE_ID, PEACHJAR_DISTRICT_ID


def _check_config():
    if not PEACHJAR_API_KEY or not PEACHJAR_AUDIENCE_ID or not PEACHJAR_DISTRICT_ID:
        print("ERROR: PEACHJAR_API_KEY, PEACHJAR_AUDIENCE_ID, and PEACHJAR_DISTRICT_ID must be set in .env", file=sys.stderr)
        sys.exit(1)


HEADERS = {
    "Content-Type": "application/json",
    "X-Api-Key": PEACHJAR_API_KEY,
}

DISTRICT_ID = PEACHJAR_DISTRICT_ID

FLYERS_QUERY = """
query($input: GetAllFlyersInput) {
  getAllFlyers(input: $input) {
    items {
      flyerId
      title
      startDate
      endDate
      categories
      frontImageUrl
      criticalDate
      org { name }
      ctas { type value isPrimary }
    }
    pagination { pageSize pageNumber total }
  }
}
"""

FLYER_QUERY = """
query($input: GetFlyerInput!) {
  getFlyer(input: $input) {
    flyerId
    title
    startDate
    endDate
    categories
    grades
    frontImageUrl
    criticalDate
    status
    org { name orgId description links { call website email } }
    pages { imageUrl pageNumber userText }
    ctas { type value isPrimary }
  }
}
"""


def dispatch(args):
    _check_config()
    action = args.action
    if not action:
        print("Usage: b3t peachjar <list|get>", file=sys.stderr)
        return 2
    if action == "list":
        return cmd_list(args)
    elif action == "get":
        return cmd_get(args)
    return 2


def _graphql(query, variables):
    try:
        resp = requests.post(
            PEACHJAR_GRAPHQL,
            json={"query": query, "variables": variables},
            headers=HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            print(f"GraphQL errors: {json.dumps(data['errors'][:2])}", file=sys.stderr)
            return None
        return data.get("data")
    except requests.RequestException as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return None


def cmd_list(args):
    """List recent flyers."""
    since = None
    if hasattr(args, "since") and args.since:
        since = date.fromisoformat(args.since)

    variables = {
        "input": {
            "pagination": {"pageSize": 30, "pageNumber": 1},
            "filters": {
                "districtId": DISTRICT_ID,
                "schoolIds": [PEACHJAR_AUDIENCE_ID],
            },
        }
    }
    data = _graphql(FLYERS_QUERY, variables)
    if not data:
        return 1

    result = data.get("getAllFlyers", {})
    items = result.get("items", [])
    total = result.get("pagination", {}).get("total", len(items))

    flyers = []
    for f in items:
        if since and f.get("endDate"):
            try:
                end = date.fromisoformat(f["endDate"][:10])
                if end < since:
                    continue
            except (ValueError, TypeError):
                pass
        flyers.append(f)

    if hasattr(args, "json") and args.json:
        json.dump(flyers, sys.stdout, indent=2)
        print()
    else:
        print(f"{len(flyers)} flyers (of {total} total)", file=sys.stderr)
        for f in flyers:
            end = f.get("endDate", "")[:10] if f.get("endDate") else "?"
            org = (f.get("org") or {}).get("name", "?")
            cats = ", ".join(f.get("categories") or [])
            print(f"  [{f['flyerId']}] {f['title']}")
            print(f"         org={org}  ends={end}  cats={cats}")

    return 0


def cmd_get(args):
    """Get flyer details."""
    flyer_id = int(args.flyer_id)
    variables = {
        "input": {
            "flyerId": flyer_id,
            "districtId": DISTRICT_ID,
            "schoolId": PEACHJAR_AUDIENCE_ID,
        }
    }
    data = _graphql(FLYER_QUERY, variables)
    if not data:
        return 1

    flyer = data.get("getFlyer")
    if not flyer:
        print(f"ERROR: Flyer {flyer_id} not found.", file=sys.stderr)
        return 1

    if hasattr(args, "json") and args.json:
        json.dump(flyer, sys.stdout, indent=2)
        print()
    else:
        print(f"Title: {flyer['title']}")
        print(f"Dates: {(flyer.get('startDate') or '?')[:10]} - {(flyer.get('endDate') or '?')[:10]}")
        org = flyer.get("org") or {}
        print(f"Org: {org.get('name', '?')}")
        print(f"Categories: {', '.join(flyer.get('categories') or [])}")
        if flyer.get("grades"):
            print(f"Grades: {', '.join(flyer['grades'])}")
        if flyer.get("ctas"):
            print("Links:")
            for cta in flyer["ctas"]:
                print(f"  {cta.get('type', '?')}: {cta.get('value', '?')}")
        if flyer.get("pages"):
            print("Images:")
            for p in flyer["pages"]:
                print(f"  {p.get('imageUrl')}")

    return 0
