# Flow UI Probe — 2026-05-24

MCP Chrome on user's personal Chrome (ULTRA tier, deviceId `9eeb12ec-...`).
Probed: `https://labs.google/fx/tools/flow` and 6 existing projects (all i2i, no L1 video output).

## Homepage / left-rail navigation (NEW surfaces vs repo)

| Surface | Label / icon | Repo coverage | Notes |
|---|---|---|---|
| All Media | `dashboard\nAll Media` | Partial (gallery) | Flat aggregated media view |
| Images | `image\nView images` | No | Image-only filtered view |
| Characters | `accessibility_new\nCharacters` | Wave 5 partial (server/models, /api/characters) | Flow has its own characters store; needs sync |
| Scenes | `movie\nView scenes` | **No** | New scene management surface |
| Uploads | `drive_folder_upload\nView uploaded media` | No | Browse uploaded assets (matches Bug B picker tab) |
| Tools | `apps_spark_2\nTools` | **No** | 10+ mini-app marketplace |
| Trash | `delete\nView Trash` | Wave 5 partial (soft delete) | Flow has visible trash UI |

## Composer (project view)

Chip `radix-:r18:` aria-haspopup="menu", label `Video · 8s\ncrop_16_9\nx4`. Click opens Radix menu with:

```
image / Image          play_circle / Video          crop_free / Frames          chrome_extension / Ingredients
crop_9_16 / 9:16       crop_16_9 / 16:9
1x   x2   x3   x4
Veo 3.1 - Lite arrow_drop_down
4s   6s   8s
Generating will use 20 credits
```

Submit buttons (when composer visible):
- `add_2\nCreate` — media picker entry / Agent start (per Bug B WIP commit `2e49830`, this is the ingredient picker in Ingredients mode)
- `Agent` — toggle Agent mode
- `article_spark\nAgent Instructions` — open agent rules dialog
- `tune\nSettings` — composer settings
- `arrow_forward\nCreate` — actual submit
- `expand_content\nExpand` — prompt expand

## Agent mode (NEW)

Toggle button `Agent` next to `add_2\nCreate`. Has companion `article_spark\nAgent Instructions` button. Disclaimer: "Google Flow can make mistakes, so double check it". Not fully reverse-engineered — needs a real Agent run to capture submit network + output.

## L2 Image edit view (NEW inline editing)

URL pattern: `/project/{pid}/edit/{media_id}`. Probed media `2fa0cd8a-cd64-4508-871e-cf243ab55a2e` (image, Nano Banana Pro). Visible buttons:

```
arrow_back\nBack
info\nGet more info about this media
favorite\nFavorite
download\nDownload
history\nShow history
Done
crop\nCrop          ← NEW inline crop
select\nSelect      ← NEW inline select (likely segmentation/mask)
draw\nDraw          ← NEW inline draw / sketch overlay
add_2\nCreate
🍌 Nano Banana Pro\ncrop_landscape   ← model chip
arrow_forward\nCreate
```

`select` likely chains into Mask Magic tool. `crop` is inline. `draw` is a sketch-overlay edit hint.

## Tools menu (`apps_spark_2`)

URL: `/project/{pid}/tools`. List of 10+ tools captured from DOM (each with `by Google` or community author):

1. Mockup — comp image into different environments
2. Image Editor — transform, text, sizing
3. Shot Explorer — see scene from new angles
4. Mask Magic (by Arden Schager, Google) — selective edits via segmentation
5. Converge (by Chris Maestas) — render sketches
6. Grid Architect (by Henry Daubrez) — create grids / extract individual
7. **Video group:**
8. Shader Effects — customizable filters on media
9. Type Overlays — animated text on videos
10. pixelBento (by László Gaal) — lo-fi / glitch post-processing
11. Poster Designer (by Heysu Oh, Kaloyan Kolev) — animated posters

Each entry has its own `more_vert\nTool options`. Each tool is a separate mini-app inside Flow — wrapping all of them is effectively 10+ new operation handlers.

## Models

`veo-3.1-lite` (default, no LP suffix in current chip), `veo-3.1-fast`, `veo-3.1-quality`, `omni-flash` (Gemini Omni Flash promoted on homepage banner). Image side: Nano Banana Pro / Nano Banana 2 / Imagen 4.

## Top-of-page banner

> Introducing Gemini Omni Flash
> Cinematic realism, powerful editing, world knowledge: try our latest video generation model!

Suggests omni-flash is the marketed paid default; repo `flow/model_selector.py` already accepts `omni` prefix (commit `1cfd356`).

## What still needs live DOM evidence

- Ingredients picker → "Uploads" tab → "Add to Prompt" full dialog DOM (Bug B finalize)
- Video L2 edit view buttons (Extend / Insert / Remove / Camera) for 2026-05 — need a video media; no video in current account
- Agent submit network payload + output shape
- Each Tool's submit API endpoint
- Inline crop / select / draw network calls
