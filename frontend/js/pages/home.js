/**
 * Home page — Flow-style gallery.
 *
 * Mirrors Google Flow's signed-in homepage IA: a project gallery with
 * an inline "+ New project" tile that routes to the full composer
 * (#create). No inline composer here — composing happens on the
 * dedicated route, like Flow's /project/<id> editor.
 *
 * Real Flow tile metrics, scraped 2026-04-26 via scripts/scrape_flow.py
 * and stored at docs/design_refs/flow_dom_scrape.json:
 *   tile width 453.328 / height 302.984 (img 254.984 + meta strip 48)
 *   border-radius 16, gap 16, row padding 0/16/16/24, justify-content center
 *
 * The previous iteration of this module embedded a full composer
 * (mode tabs + model/aspect/profile chips + send button) on home,
 * plus a settings popover, attachment dropzones, and ingredient
 * uploads. All of that lived behind functions (renderTabs,
 * renderComposer, renderChips, renderPopovers, dropzone, submit, …)
 * that are no longer reached now that home is gallery-only — Codex
 * review #10 / #11 of PR #61 flagged it. Pruned in this revision.
 */
(() => {
  const RECENT_LIMIT = 12;
  const VIDEO_EXTENSIONS = new Set(['mp4', 'webm', 'mov', 'm4v']);
  const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp']);

  let recentJobs = [];
  let wsUnsubs = [];

  // ---- network --------------------------------------------------------------

  async function fetchRecent() {
    try {
      const list = await API.jobs.list({ limit: RECENT_LIMIT });
      const items = Array.isArray(list) ? list : (list?.jobs ?? []);
      recentJobs = items.slice(0, RECENT_LIMIT);
    } catch (err) {
      console.warn('[Home] recent jobs fetch failed:', err.message);
      recentJobs = [];
    }
  }

  // ---- helpers --------------------------------------------------------------

  // Whitelist of status values we render. Anything else falls back to
  // 'pending' so a hostile / malformed API value cannot break out of
  // the class attribute and become an XSS sink.
  const ALLOWED_STATUS = new Set(
    ['pending', 'claimed', 'running', 'completed', 'failed', 'cancelled']);
  const safeStatus = (s) => ALLOWED_STATUS.has(s) ? s : 'pending';

  function createdAtMs(job) {
    const value = Date.parse(job?.created_at || job?.createdAt || '');
    return Number.isFinite(value) ? value : 0;
  }

  function mediaKindFromFile(file) {
    const normalized = String(file || '').replace(/\\/g, '/');
    const filename = normalized.split('/').pop() || normalized;
    const extension = filename.includes('.') ? filename.split('.').pop().toLowerCase() : '';
    if (VIDEO_EXTENSIONS.has(extension)) return 'video';
    if (IMAGE_EXTENSIONS.has(extension)) return 'image';
    return null;
  }

  function mediaUrlFor(file) {
    const normalized = String(file || '').replace(/\\/g, '/').replace(/^downloads\//i, '');
    return `/downloads/${encodeURI(normalized)}`;
  }

  function primaryMedia(job) {
    const files = Array.isArray(job?.output_files) ? job.output_files : [];
    const renderable = files
      .map((file) => {
        const kind = mediaKindFromFile(file);
        if (!kind) return null;
        return {
          kind,
          url: mediaUrlFor(file),
        };
      })
      .filter(Boolean);

    if (!renderable.length) return null;

    const primary = renderable[0];
    const poster = primary.kind === 'video'
      ? renderable.find((file) => file.kind === 'image')?.url || ''
      : '';

    return {
      ...primary,
      poster,
    };
  }

  function mediaTileHelper() {
    return App.mediaTile || window.MediaUtil;
  }

  // ---- render ---------------------------------------------------------------

  function renderTile(job) {
    const status = safeStatus(job.status);
    const safeJobId = App.escapeHtml(String(job.id || ''));
    const promptText = job.prompt || job.direction || '(no prompt)';
    const media = status === 'completed' ? primaryMedia(job) : null;

    // Completed tiles stay metadata-only on first paint, then play on hover.
    const thumb = media
      ? (media.kind === 'video'
        ? mediaTileHelper().videoTag({ src: media.url, poster: media.poster, alt: promptText })
        : mediaTileHelper().imgTag({ src: media.url, alt: promptText }))
      : '';

    // Completed = silent (Flow's default). Anything else gets a small
    // status chip so operators can read at a glance.
    const stateChip = status === 'completed'
      ? ''
      : `<span class="tile-status-badge state-${status}">${App.escapeHtml(status.toUpperCase())}</span>`;

    // Always land on the DAG project view; key by chain_id when available
    // and fall back to job_id for legacy jobs (project-view renders that as
    // a single-node DAG via /api/jobs/{id}).
    const routeKey = encodeURIComponent(job.chain_id || job.id || '');
    const tileHref = `#project-view/${routeKey}`;
    return `
      <a class="project-tile status-${status}"
         href="${tileHref}"
         data-job-id="${safeJobId}"
         title="${App.escapeHtml(promptText)}">
        <div class="tile-thumb">
          ${thumb}
          ${stateChip}
        </div>
        <div class="tile-overlay">
          <span class="tile-date">${App.escapeHtml(App.formatTileDate(job.created_at || job.createdAt))}</span>
        </div>
      </a>
    `;
  }

  function renderNewProjectFab() {
    // Floating action button — sticks to the viewport bottom-center while
    // the user scrolls the gallery. Mirrors the floating "+ New project"
    // pill in Google Flow's gallery (not an inline grid slot).
    return `
      <a class="new-project-fab" href="#create"
         title="New project" aria-label="New project">
        <span class="material-icons">add</span>
        <span>New project</span>
      </a>
    `;
  }

  function renderGrid() {
    if (recentJobs.length === 0) {
      // Empty gallery: still show the FAB so the operator can start.
      return `<div class="project-grid" id="home-grid"></div>`;
    }
    const tiles = recentJobs.map(renderTile);
    return `<div class="project-grid" id="home-grid">${tiles.join('')}</div>`;
  }

  function repaintGrid() {
    const wrap = document.getElementById('home-recent');
    if (!wrap) return;
    wrap.innerHTML = renderGrid();
  }

  // ---- WS live updates ------------------------------------------------------

  function attachWS() {
    if (!window.WS || typeof WS.on !== 'function') return;
    const upsert = (job) => {
      if (!job?.id) return false;
      const idx = recentJobs.findIndex((j) => j.id === job.id);
      if (idx >= 0) {
        recentJobs[idx] = { ...recentJobs[idx], ...job };
      } else {
        const next = [...recentJobs, job]
          .sort((a, b) => {
            const createdDiff = createdAtMs(b) - createdAtMs(a);
            if (createdDiff !== 0) return createdDiff;
            return String(b.id || '').localeCompare(String(a.id || ''));
          })
          .slice(0, RECENT_LIMIT);
        if (!next.some((entry) => entry.id === job.id)) return false;
        recentJobs = next;
      }
      return true;
    };
    wsUnsubs.push(WS.on('job_update', (job) => {
      if (upsert(job)) repaintGrid();
    }));
  }

  function detachWS() {
    wsUnsubs.forEach((u) => { try { u(); } catch (_) {} });
    wsUnsubs = [];
  }

  // ---- page object ----------------------------------------------------------

  const HomePage = {
    name: 'home',
    title: 'FlowEngine',
    icon: 'movie_filter',

    async render() {
      await fetchRecent();
      return `
        <div class="home-canvas home-canvas-gallery">
          <div id="home-recent">${renderGrid()}</div>
          ${renderNewProjectFab()}
        </div>
      `;
    },

    mount() {
      attachWS();
    },

    destroy() {
      detachWS();
    },
  };

  App.register(HomePage);
})();
