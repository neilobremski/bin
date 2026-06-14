# LWSD (School District Websites)

## Overview

Scans both the school website and the district website for events and news. No authentication required.

## URLs

- School site: `https://rms.lwsd.org` (configurable per school)
- District site: `https://www.lwsd.org`

## Commands

### `lwsd scan`

Navigates to both sites, parses events and news sections from each.

Output sections:
- RMS School Events
- RMS School News
- LWSD District Events
- LWSD District News

## Parsing: Events

Events section contains elements with:
- Month abbreviation in `generic` elements (Jan, Feb, Mar...)
- Day number in `generic` elements
- Event title in `button` or `group` elements
- Time ranges in `time` elements
- Location in `generic` elements (matched by keywords: GYM, Center, Library, etc.)

Output format: `Mon DD: Event Title (time) @ Location`

## Parsing: School News

News items appear as `link` or `heading` elements in the news section. Filtered by length (>15 chars) and deduplicated. Skip the "News" section when looking for newsletter content — that's Bear Tracks itself.

## Parsing: District News

Links on the lwsd.org homepage filtered to actual news articles (>20 chars, excludes nav/social/generic links).

## Calendar

The school site has a calendar page with events laid out in a grid. Date rows contain 7 cells (one per day of week). Event rows contain events positioned by column index matching the corresponding day.
