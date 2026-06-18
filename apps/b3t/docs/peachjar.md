# PeachJar

## Overview

School flyer distribution platform. Flyers are digital documents (PDF-like) posted by organizations (camps, health providers, nonprofits, the school itself).

## Authentication

**None required.** PeachJar has a public GraphQL API.

Env vars: `PEACHJAR_API_KEY`, `PEACHJAR_AUDIENCE_ID`

## API

Endpoint: `https://parent-app-bff.peachjar.com/graphql`

Auth header: `X-Api-Key: {PEACHJAR_API_KEY}`

### List Flyers

```graphql
query($input: GetAllFlyersInput) {
  getAllFlyers(input: $input) {
    items { id title startDate endDate categories
            distributionDetails { organization { name } } }
    totalCount
  }
}
```

Variables:
```json
{"input": {"schoolIds": [AUDIENCE_ID], "statuses": ["APPROVED"], "limit": 50, "offset": 0}}
```

### Get Flyer Detail

```graphql
query($input: GetOneFlyerInput) {
  getOneFlyer(input: $input) {
    id title startDate endDate description
    categories flyerPages { pageImage }
    distributionDetails { organization { name } }
  }
}
```

Variables: `{"input": {"flyerId": ID, "schoolId": AUDIENCE_ID}}`

## Filtering

- `--since DATE` filters by `endDate >= DATE` (active flyers)
- Categories: `health_and_safety`, `volunteer_and_fundraising`, `enrichment_and_camps`, `community`, `school_info`
- Skip paid programs/camps unless board-approved

## Output

Human-readable (default) or `--json` for structured output. Each flyer shows: ID, title, org, end date, categories.

## Flyer Images

`flyerPages[].pageImage` contains CDN URLs to rendered flyer page images. These can be downloaded and included in the newsletter.
