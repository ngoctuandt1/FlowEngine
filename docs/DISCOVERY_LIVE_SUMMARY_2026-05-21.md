# Flow Live Capture Summary ? 2026-05-21

**Status:** DONE_WITH_CONCERNS
**Profile:** `s17524h173`
**CDP:** `http://127.0.0.1:19400`
**Captures:** 1 successful generation (`text-to-image`), 1 paid submit (`text-to-video`) failed `ALL_FAILED`, 7 endpoint rows recovered
**Credits burned:** 5 estimated
**Budget:** max 30 credits; stopped below budget after root L1 video failed

## Successful Ops
- `text-to-image`: media_id `e2a63aca-2ee2-400a-bd77-15c9cd2641b0`, duration 62s, cost 0, no endpoint rows captured

## Failed Or Skipped Ops
- `text-to-video`: submitted once, cost 5 estimated, Flow returned `ALL_FAILED`; failure capture `error-captures/1779340991_unknown_all_failed.network.json`
- `frames-to-video`: no submit; UI upload bug `Could not locate file input for Start frame`
- `extend-video`: skipped; required successful text-to-video output missing
- `camera-move`: skipped; required successful text-to-video output missing
- `insert-object`: skipped; required successful text-to-video output missing
- `remove-object`: skipped; required insert-object output missing

## Endpoint Patterns
- `https://aisandbox-pa.googleapis.com/v1/flow:batchLogFrontendEvents`
- `https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoText`
- `https://aisandbox-pa.googleapis.com/v1/video:batchCheckAsyncVideoGenerationStatus`

## Capture Limitations
- Playwright `request`/`response` listeners were attached before submit, but capture window closed after submit confirmation; Flow responses arrived later.
- JSONL rows therefore come from FlowEngine failure-capture response buffer, not full paired request bodies.
- `request_body_shape` is marked unavailable; several response bodies are 200-character previews from `flow/diagnostics.py`.
- No duplicate submit was attempted after this issue, per task constraint.

## Files
- `docs/discovery_live_capture_2026-05-21.jsonl`
- `docs/DISCOVERY_LIVE_SUMMARY_2026-05-21.md`
