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

  // Pull a renderable media URL from a completed job.
  // output_files entries are paths like "downloads\\t2v_1080p_*.mp4".
  // Server mounts /downloads, so we forward-slash and strip the prefix.
  function mediaUrlFor(job) {
    const files = job.output_files || [];
    const mp4 = files.find((f) => /\.mp4$/i.test(f));
    if (!mp4) return null;
    const norm = String(mp4).replace(/\\/g, '/').replace(/^downloads\//, '');
    return `/downloads/${encodeURI(norm)}`;
  }

  function mediaTileHelper() {
    return App.mediaTile || window.MediaUtil;
  }

  // ---- render ---------------------------------------------------------------

  function renderTile(job) {
    const status = safeStatus(job.status);
    const type = job.type || 'text-to-video';
    const promptText = job.prompt || job.direction || '(no prompt)';
    const mediaUrl = status === 'completed' ? mediaUrlFor(job) : null;

    // Completed tiles stay metadata-only on first paint, then play on hover.
    const thumb = mediaUrl
      ? mediaTileHelper().videoTag({ src: mediaUrl, alt: promptText })
      : `<span class="material-icons type-icon ${App.jobTypeClass(type)}">${App.jobTypeIcon(type)}</span>`;

    // Completed = silent (Flow's default). Anything else gets a small
    // status chip so operators can read at a glance.
    const stateChip = status === 'completed'
      ? ''
      : `<span class="tile-status-badge state-${status}">${App.escapeHtml(status.toUpperCase())}</span>`;

    // <a> for keyboard a11y (Tab → Enter activates). The hash route is
    // visual-only; the delegated click handler preventDefault's and
    // opens the existing job-detail modal instead.
    return `
      <a class="project-tile status-${status}"
         href="#home/job/${encodeURIComponent(job.id)}"
         data-job-id="${App.escapeHtml(job.id)}"
         title="${App.escapeHtml(promptText)}">
        <div class="tile-thumb">
          ${thumb}
          ${stateChip}
        </div>
        <div class="tile-overlay">
          <span class="tile-date">${App.escapeHtml(App.formatDate(job.created_at))}</span>
        </div>
      </a>
    `;
  }

  function renderNewProjectTile() {
    return `
      <a class="project-tile new-project-tile" href="#create"
         title="New project" aria-label="New project">
        <div class="tile-thumb">
          <div class="new-project-pill">
            <span class="material-icons">add</span>
            <span>New project</span>
          </div>
        </div>
      </a>
    `;
  }

  function renderGrid() {
    if (recentJobs.length === 0) {
      // Empty-state still gets the + tile so the operator can start.
      return `
        <div class="project-grid" id="home-grid">
          ${renderNewProjectTile()}
        </div>
      `;
    }
    const tiles = recentJobs.map(renderTile);
    // Insert + New tile near the centre — Flow places its CTA roughly
    // mid-grid as a slot, not as a floating FAB.
    const insertAt = Math.min(Math.floor(tiles.length / 2), 5);
    tiles.splice(insertAt, 0, renderNewProjectTile());
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

  // ---- click delegation -----------------------------------------------------

  function bindGrid() {
    // Delegate on the stable parent — #home-recent never gets innerHTML-
    // replaced after mount, only its child #home-grid does. Survives
    // every WS-driven repaintGrid().
    const wrap = document.getElementById('home-recent');
    if (!wrap) return;
    wrap.addEventListener('click', async (e) => {
      const tile = e.target.closest('.project-tile');
      if (!tile || tile.classList.contains('new-project-tile')) return;
      const id = tile.dataset.jobId;
      if (!id) return;
      // Suppress the <a> navigation — keep URL clean; modal-only.
      e.preventDefault();
      try {
        const job = await API.jobs.get(id);
        const body = `
          <pre style="white-space:pre-wrap; font-size:12px; line-height:1.5;">${App.escapeHtml(JSON.stringify(job, null, 2))}</pre>
        `;
        App.openModal(`Job ${id.slice(0, 8)}…`, body);
      } catch (err) {
        App.toast(`Load failed: ${err.message}`, 'error');
      }
    });
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
        </div>
      `;
    },

    mount() {
      bindGrid();
      attachWS();
    },

    destroy() {
      detachWS();
    },
  };

  App.register(HomePage);
})();
