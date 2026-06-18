# Gemini (Header Image Generation)

## Overview

Uses Google Gemini (gemini.google.com) to generate newsletter header images. Requires a Google account with Gemini access (AI Pro for image generation).

## Authentication

Manual login required (Google auth is not automatable). Browser state can be saved but is unreliable — plan for re-login each session.

Session: persistent Chrome profile handles this naturally.

## Commands

### `gemini generate --prompt TEXT [--template FILE] --dir DIR`

Full deterministic flow:
1. Navigate to gemini.google.com/app
2. Upload template image (style reference)
3. Send generation prompt
4. Wait for image generation (polls for "Download" button, 45s timeout)
5. Download generated image
6. Save to `--dir` with sequential naming (`header-option-1.png`, etc.)

### `gemini login`

Navigate to Gemini and verify Google auth is active.

## Template Upload

The template image establishes the visual style (colors, bear mascot, layout). Upload flow:
1. Click attachment/upload button in chat input area
2. Click "Upload files" menu item (triggers OS file chooser)
3. Chain: `click {menu_ref} && upload "/path/to/template.png"`

## Image Generation

After uploading template and sending prompt:
- Gemini takes 10-30s to generate
- Poll every 3s for "Download full size image" button appearance
- Timeout after 45s

## Download

The download button is below the fold — must scroll down first.
1. `mousewheel 3000 0` to scroll down
2. Wait 2s for layout to settle
3. Re-snapshot to get fresh ref for download button
4. Click download button (may need double-click with delay)

Downloaded files go to `.playwright-cli/`.

## Quality Check

Before accepting a generated image:
- Visual-check for duplicated/garbled text (common failure mode)
- Verify the title text matches what was requested
- If wrong, follow up in same chat (retry 2x max, then start new chat)

## Sequential Naming

Output files are named `header-option-{N}.png` where N increments from existing files in the output directory.

## Gotchas

- Google saved browser state is unreliable — expect manual re-login
- Download button is below viewport fold — scroll first
- Generated text in images may be garbled — always verify visually
- First click on download may register as hover — click twice with 1s delay
