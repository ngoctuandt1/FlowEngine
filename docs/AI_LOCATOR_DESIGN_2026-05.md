# AI Locator Design 2026-05

## Motivation

FlowEngine depends on Google Flow DOM surfaces that change without notice. The existing hardcoded Playwright selectors should stay the first line of defense because they are cheap, deterministic, and reviewable. The AI locator is a pure fallback: when callers cannot find a known selector, it can snapshot the current page, ask a vision-capable model for a replacement selector, validate that selector locally, and return a structured miss instead of raising. Phase G1 ships only the helper and tests; integration is deferred to avoid conflicts with active Flow UI work.

## API

`flow.ai_locator.ai_locate(page, intent, candidates=(), include_screenshot=True, max_dom_chars=12000, cache_key=None, visibility_check=True)` returns `AILocatorResult` with `selector`, optional `coordinates`, `method`, `cost_estimate`, and `debug_log`. Methods are `candidate`, `ai`, `cache`, or `miss`. `clear_cache()` resets process-local state for tests.

The helper first checks `candidates` via `page.locator(selector).first.is_visible(timeout=1500)`. Only after candidates fail and `FLOW_AI_LOCATOR_ENABLED=true` does it call AI. DOM capture uses `body.inner_html()`, strips script/style/svg/base64-heavy content, and truncates to `max_dom_chars`. Caller intent, retry hints, and sanitized DOM are treated as untrusted data: intent is length-bounded, NUL-stripped, JSON-string encoded, and explicitly delimited in the prompt so user-derived text cannot masquerade as locator instructions. AI JSON may contain either `{ "selector": "..." }` or `{ "x": 350, "y": 549 }`; both are validated against the live page before success.

## 9router Backend Choice

The module uses `httpx.AsyncClient` and no SDK. Runtime config is env-based: `FLOW_AI_LOCATOR_BASE_URL`, `FLOW_AI_LOCATOR_MODEL`, `FLOW_AI_LOCATOR_TIMEOUT_SEC`, `FLOW_AI_LOCATOR_ENABLED`, and `FLOW_AI_LOCATOR_WIRE`. Auth is `Authorization: Bearer ${NINEROUTER_API_KEY:-dummy}` to match the local 9router OAuth pool.

`FLOW_AI_LOCATOR_WIRE=auto` tries OpenAI Chat Completions first at `/chat/completions`; on 404 or timeout it retries OpenAI Responses at `/responses`, adapting the request body and caching the working wire for the process. Explicit `chat` or `responses` modes do not fallback, which makes production behavior debuggable when a specific 9router surface is required.

## Cache Strategy

When `cache_key` is set, successful AI results are cached in memory by `(cache_key, url_signature)`. The URL signature strips query and fragment so transient Flow routing state does not defeat reuse, while path changes still isolate different screens. Cache hits return `method="cache"`. Nothing is written to disk because selectors are page-shape hints, not durable truth.

## Failure Modes

Network errors, 5xx responses, invalid JSON, invalid selectors, hidden selectors, and empty coordinate hit-tests return `method="miss"` with debug breadcrumbs. The helper never raises for normal locator failure. Logs include cache hits, wire calls, latency, status, and cost estimate, but never raw screenshots or full DOM snapshots.

Cost estimate uses Sonnet defaults: `(prompt_tokens / 1e6 * 3.0) + (completion_tokens / 1e6 * 15.0)`. Responses-style `input_tokens` and `output_tokens` are treated as prompt and completion tokens.

## Integration Plan

Phase G2 can wire this helper into the highest-drift UI boundaries: `landing.py` CTA recovery, `model_selector.py` model-panel controls, `generate.py` composer-panel targeting, and edit-view detection. Each integration should preserve deterministic selectors first, use narrow intent strings and cache keys, and keep caller-specific hard failures separate from AI misses.
