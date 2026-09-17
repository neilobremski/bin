"""Shared helpers for reading virtualized chat panes.

Teams and WhatsApp both render message lists that unload rows as you scroll,
so covering a period means proving three boundaries rather than assuming them:

1. the read started at the newest message,
2. it walked back past the requested cutoff, or
3. it stopped because the history ended, not because a step budget ran out.

When a boundary cannot be proven, callers report partial coverage. A sweep
that quietly returns half a period is worse than one that says it is unsure,
because it reads as "nothing was posted".
"""
import json

import session

# Scroll steps overlap so no window of rows is skipped between reads.
OVERLAP = 0.6
EDGE_EPSILON = 40


def _run_json(code, timeout=60):
    res = session.run("run-code", "--raw", code, timeout=timeout)
    raw = (res.stdout or "").strip()
    if not raw:
        return None
    for candidate in (raw, raw.strip('"')):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, str):
                parsed = json.loads(parsed)
            return parsed
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return None


# Finds the element that actually scrolls: the located pane, or the first
# descendant taller than its own viewport.
_FIND_SCROLLER = (
    "(root) => {"
    "  if (!root) return null;"
    "  const cands = [root].concat(Array.from(root.querySelectorAll('*')));"
    "  for (const el of cands) {"
    f"    if (el.scrollHeight > el.clientHeight + {EDGE_EPSILON}) return el;"
    "  }"
    "  return null;"
    "}"
)


def geometry(selector):
    """Scroll position of the pane, or None when it cannot be measured."""
    code = (
        "async function main(page){"
        f"  const pane = page.locator({json.dumps(selector)}).first();"
        "  try {"
        f"    const g = await pane.evaluate((root) => {{ const find = {_FIND_SCROLLER};"
        "      const el = find(root);"
        "      return el ? {top: el.scrollTop, h: el.scrollHeight, c: el.clientHeight} : null; });"
        "    return JSON.stringify(g);"
        "  } catch (e) { return JSON.stringify(null); }"
        "}"
    )
    g = _run_json(code)
    return g if isinstance(g, dict) else None


def at_top(geo):
    return bool(geo) and geo.get("top", 1) <= EDGE_EPSILON


def at_bottom(geo):
    if not geo:
        return False
    return geo["top"] + geo["c"] >= geo["h"] - EDGE_EPSILON


def seek_bottom(selector, max_steps=25):
    """Scroll to the newest message. Returns True only when that is proven.

    Opening a conversation can render mid-history, so a read that starts
    where the pane happens to sit will miss the newest messages entirely.
    """
    last_top = None
    for _ in range(max_steps):
        geo = geometry(selector)
        if geo is None:
            return False                 # unmeasurable: caller reports unknown
        if at_bottom(geo):
            return True
        if last_top is not None and geo["top"] == last_top:
            return at_bottom(geo)        # stuck and not at the end
        last_top = geo["top"]
        _wheel(selector, 3000)
    return at_bottom(geometry(selector))


def step_up(selector):
    """Scroll back one overlapping step. Returns geometry after the move."""
    geo = geometry(selector)
    height = geo["c"] if geo and geo.get("c") else 600
    _wheel(selector, -int(height * OVERLAP))
    return geometry(selector)


def _wheel(selector, delta):
    code = (
        "async function main(page){"
        f"  const pane = page.locator({json.dumps(selector)}).first();"
        "  try { await pane.hover({timeout: 3000}); } catch (e) {}"
        f"  await page.mouse.wheel(0, {delta});"
        "  await page.waitForTimeout(1500);"
        "  return JSON.stringify(true);"
        "}"
    )
    session.run("run-code", "--raw", code, timeout=60)
