# Browser Session Management

## Architecture

b3t launches Chrome directly (no automation flags) and attaches `playwright-cli` via Chrome DevTools Protocol (CDP). This avoids all automation detection banners and fingerprinting.

```
Chrome (user-data-dir profile) ← CDP port 9222 → playwright-cli -s=b3t
```

## Chrome Launch

```python
cmd = [CHROME_PATH, f"--user-data-dir={CHROME_PROFILE_DIR}",
       f"--remote-debugging-port={CDP_PORT}",
       "--no-first-run", "--no-default-browser-check", "about:blank"]
```

- No `--disable-blink-features=AutomationControlled` (playwright-cli injects this; we bypass by launching Chrome ourselves)
- Persistent profile at `~/.b3t-chrome-profile/` — cookies survive naturally
- CDP port 9222 (checked via HTTP GET to `/json/version`)

## Attach

```bash
playwright-cli -s=b3t attach --cdp=http://127.0.0.1:9222
```

## Graceful Close

Must quit Chrome cleanly to avoid "not shut down properly" restore banner:
- macOS: `osascript -e 'tell application "Google Chrome" to quit'`
- Linux: `pgrep -f {profile_dir}` → `SIGTERM`

Wait for Chrome to finish writing profile before returning.

## Configuration

| Env var | Default | Purpose |
|---------|---------|---------|
| `B3T_SESSION` | `b3t` | playwright-cli session name |
| `B3T_CHROME_PROFILE` | `~/.b3t-chrome-profile` | Chrome user data dir |

## Tab Management

Commands: `tab-new URL`, `tab-close`, `tab-select N`

Used for multi-service flows (e.g., opening Outlook to get OTP during GiveBacks login without leaving the GiveBacks page).

## State Persistence

Primary: Chrome profile directory (cookies persist naturally across restarts).
Backup: `state-save`/`state-load` to `.playwright-cli/b3t-state.json`.

## Testing with Fresh Profile

```bash
B3T_SESSION=b3t-test B3T_CHROME_PROFILE=~/.b3t-chrome-profile-test b3t open
```

Useful for testing OTP flows or verifying first-time-user experience.
