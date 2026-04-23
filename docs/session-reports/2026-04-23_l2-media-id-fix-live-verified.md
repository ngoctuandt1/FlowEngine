# Session — 2026-04-23: L2 media_id bug fix (live verified)

**Branch:** `claude/unruffled-chebyshev-177086`
**Profile:** `ngoctuandt20` (Ultra)
**Commits landed:** `a771d86`, `1183a24`, `0bb9d29`

## TL;DR

Parked HIGH bug from 2026-04-20 handoff §5 (L2 insert + L2 remove returning identical
`media_id`) fixed. Stress-tested with 10 L1 + 10 L2 serial on same project — all 20
distinct media_ids. `flow/operations/_base.py:finalize_operation` now trusts network
events first (same source L1 uses), then DOM tile, then URL.

## Root cause

Pre-fix chain preferred `_extract_settled_route_media_id(page)` — i.e. the `/edit/{slug}`
URL segment — as the canonical `media_id`. Flow's SPA frequently routes `/edit/` to a
**clip-route slug** that is neither the parent nor the new generation id. Live run on
2026-04-23 reproduced it twice: insert + remove both stored `4ef87bb5` despite
producing different video files.

The generation's real id is captured by `flow/client.py:_record_media_id` from
`/pq/api` responses — filtered through `looks_like_media_id`. `L1 generate.py` already
uses `result['media_ids'][0]` as the canonical source; L2 `finalize_operation` did not.

## Fix

`flow/operations/_base.py:finalize_operation` resolution order:

1. **Network mid** — first `result['media_ids']` entry that differs from parent.
2. **DOM tile** — `find_latest_tile_slug(page)` if tile != parent.
3. **URL** — `_extract_settled_route_media_id(page, fallback=job.get('media_id'))`.

Plus prior commits this session:

- `a771d86` — initial DOM-tile preference (sufficient for some cases, not all).
- `1183a24` — unconditional tile activation after nav to force SPA re-hydrate
  (fixed a secondary "video element not found after 15s" failure on tight retries).

## Verification

### Unit tests (`0bb9d29`)
- `tests/test_l2_media_id.py` + `tests/test_latest_tile_slug.py` rewritten to use
  UUID-shaped stubs (prior `"redirect-name"` literal violated the
  `looks_like_media_id` contract enforced by `client.py:_record_media_id`).
- `python -m pytest -q` → **235 / 235 passed**.

### Live stress run (2026-04-23, ngoctuandt20)

| Batch | Count | Distinct mids | Notes |
|---|---|---|---|
| L1 text-to-video | 10/10 | 10 | Separate projects, serialized via profile lock |
| L2 serial (insert×5 + remove×5) | 10/10 | 10 | Same project `0d9113ef`, serialized via project lock |

Sample log line proving fix is on the network branch (not tile fallback):

```
media_id from network events: 7d09cb8b-2ea4-48e9-a (url=bf422a26-0e15-4cb5-8 tile=aec93cc2-b690-4699-8)
```

- `url=bf422a26` — the clip-route slug that caused the original bug.
- `tile=aec93cc2` — still the parent (tile DOM race, new generation not yet rendered).
- `7d09cb8b` — the real generation mid from `/pq/api` capture, chosen as canonical.

## Spec follow-ups

`CLAUDE.md §4` text about L2 media_id extraction being "currently unreliable until the
parked media_id bug is fixed" can be dropped on the next docs-sync. The parked HIGH
item in `2026-04-20_session-handoff.md §5` can be marked RESOLVED with this session's
commit hashes.
