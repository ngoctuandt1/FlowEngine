# Multi-site deploy — many frontends, one engine

Yes, a single FlowEngine backend can serve many distinct frontend sites — `ai.hassio.io.vn`, `ai.ciem`, the bundled `/` dashboard, anything else you put on top. The server/worker contract is stateless REST + WebSocket; whoever can hit it gets a job number.

## Architecture

```
  https://ai.hassio.io.vn ──┐
  https://ai.ciem ──────────┤
  https://flow.your-host/ ──┴───→  https://flow.your-host/api/*
                                         + /ws/jobs
                                  (single FlowEngine server, Debian)
                                         │
                                         │ /api/worker/*  (Bearer API_KEY)
                                         ▼
                                  Windows worker box(es)
                                  Chrome + warmed Google profiles
```

Each frontend talks to the **same** `https://flow.your-host` backend. The engine doesn't care which UI submitted a job — it just runs through the worker queue.

## What's shared vs. private

| Surface | Public to frontends | Notes |
|---|---|---|
| `POST /api/jobs` | ✅ | Anyone with the URL can submit (gated by your TLS frontend / proxy auth if you want) |
| `GET  /api/jobs` | ✅ | Returns ALL jobs in the DB, regardless of who submitted them |
| `GET  /api/profiles` | ✅ | Returns all warm Chrome profiles |
| `WS   /ws/jobs` | ✅ | Broadcasts every job_created / job_updated / job_completed event |
| `POST /api/worker/claim` | 🔒 Bearer `API_KEY` | Workers only |
| `PATCH /api/jobs/{id}` (worker route) | 🔒 Bearer `API_KEY` | Workers only |

Important consequence: **all frontends see all jobs** — there's no per-tenant isolation today. If `ai.hassio.io.vn` and `ai.ciem` are both run by you, that's fine. If you need stranger A's site to not see stranger B's jobs, you need real multi-tenancy (tracked as future work, see end of this doc).

## Wiring `ai.hassio.io.vn` to FlowEngine

The site already has the right surfaces (Text→Video, Image, Batch, Gallery, Jobs). To swap its backend to FlowEngine:

### 1. Allow the origin

In `/etc/flowengine/flowengine.env`:

```bash
ALLOWED_ORIGINS=https://ai.hassio.io.vn
# Multiple? comma-separate:
# ALLOWED_ORIGINS=https://ai.hassio.io.vn,https://ai.ciem
```

Reload: `systemctl restart flowengine-server`.

### 2. Frontend submit calls

Wire each pipeline on the site to FlowEngine's `JobCreate` payload (mirrors `server/models/job.py`):

| Site UI surface | FlowEngine job type | Required fields |
|---|---|---|
| Text → Video | `text-to-video` | `prompt`, `model`, `aspect_ratio`, `profile` |
| Image Studio | `text-to-image` | `prompt`, `model`, `aspect_ratio`, `profile`, optional `ref_image_path` |
| Audio → Video / Frames | `frames-to-video` | `prompt`, `start_image_path`, optional `end_image_path` |
| Character Manager | `ingredients-to-video` | `prompt`, `ingredient_image_paths[]` |
| Extend | `extend-video` | `prompt`, `parent_job_id` |
| Insert object | `insert-object` | `prompt`, `parent_job_id`, `bbox{x,y,w,h}` |
| Remove object | `remove-object` | `parent_job_id`, `bbox` |
| Camera move | `camera-move` | `parent_job_id`, `direction` |

Example (from a vanilla JS site like `ai.hassio.io.vn`):

```js
const FLOW_API = 'https://flow.your-host';

// Submit a Text-to-Video
async function submitT2V({ prompt, aspect, profile }) {
  const r = await fetch(`${FLOW_API}/api/jobs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      type: 'text-to-video',
      prompt,
      model: 'veo-3.1-fast-lp',  // free LP tier (no credits)
      aspect_ratio: aspect,
      profile,
    }),
  });
  return r.json();  // { id, status, ... }
}

// List recent jobs for the gallery
async function listRecent() {
  return (await fetch(`${FLOW_API}/api/jobs?limit=24`)).json();
}

// Upload reference image (gets a path you pass back as start_image_path / etc)
async function uploadImage(file) {
  const fd = new FormData(); fd.append('file', file);
  const r = await fetch(`${FLOW_API}/api/uploads`, { method: 'POST', body: fd });
  return (await r.json()).path;
}
```

### 3. Live updates via WebSocket

```js
const ws = new WebSocket(FLOW_API.replace(/^https/, 'wss') + '/ws/jobs');
ws.onmessage = (e) => {
  const { type, payload } = JSON.parse(e.data);
  // type ∈ { job_created, job_updated, job_completed, job_failed, job_deleted }
  switch (type) {
    case 'job_completed': renderTileDone(payload); break;
    case 'job_updated':   updateTileStatus(payload); break;
    // ...
  }
};
```

The bundled FlowEngine dashboard's `frontend/js/ws.js` is a clean reference implementation with auto-reconnect + exponential backoff — copy or adapt.

### 4. Surfaces that map cleanly

- **Jobs tracker** on the site → `GET /api/jobs?status=...&limit=...&offset=...`
- **Gallery** → `GET /api/jobs?status=completed`, then play `output_files[0]` via `/downloads/<path>`
- **Profiles dropdown** ("which Google account to run on") → `GET /api/profiles`
- **Chains / Workflows** → `POST /api/chains` with a `steps[]` payload
- **Batch render queue** → loop `POST /api/jobs` per prompt with the same profile (the bundled `#create` Batch tab does exactly this)

### 5. Surfaces that need engine work first

- **Character Manager** with reusable persona → maps to `ingredients-to-video` per-shot, but if the site stores characters as objects, you'd need a small `characters` table or treat each character file as a stored upload referenced by path.
- **YouTube downloader** / **video cutter** / **video merger** → not in the engine. Either keep these on the site's existing local processing, or add new endpoints.
- **Workflows save/reuse** → `/api/chains` exists but only stores chains of jobs, not parameter templates. A "workflow" with prompt placeholders would need a small `templates` resource.

## Per-frontend customisation

You can run multiple frontends sharing the same backend with no engine changes:

| Concern | Solution |
|---|---|
| Different brand / look | Each frontend has its own HTML/CSS/JS bundle. The engine doesn't care. |
| Same domain serving the bundled dashboard too | Mount FlowEngine's `/` (default index.html) at a subpath behind nginx/Caddy: `location /admin { proxy_pass http://127.0.0.1:8080/; }`. |
| Don't expose the bundled dashboard at all | nginx-level: `location = / { return 404; }` and `location ~ ^/(?!api\|ws)` → block. Only `/api` + `/ws` reach uvicorn. |
| Hide profiles list from public sites | Add an env-gated dependency on the profiles route similar to `require_worker_token`. (Not done yet — open issue.) |

## Future work — real multi-tenancy

If you need site A and site B to NOT see each other's jobs:

1. Add `tenant_id` column to `jobs` + `profiles` (alembic migration).
2. Each frontend sends a tenant token (signed JWT or simple Bearer per tenant) on `/api/jobs` calls; server filters everything by the resolved tenant.
3. Worker claim still uses `API_KEY` but ALSO carries a `WORKER_TENANTS=tenantA,tenantB` env so a worker only picks up jobs for its tenants.
4. Dashboard surfaces become tenant-scoped automatically.

That's a separate epic — not blocking the "shared backend, multiple skins" pattern this doc describes.

## Health check (per-frontend)

Each frontend should ping the backend on load:

```js
fetch(`${FLOW_API}/health`).then((r) => r.ok || console.warn('engine down'));
```

Returns `{"status":"ok"}` when uvicorn is up.
