/**
 * Home page — one tile per chain, showing the latest output thumbnail
 * and the root L1 prompt as the tile label.
 */
(() => {
  const FETCH_LIMIT = 400;   // server-side cap; grouping reduces visible tiles further
  const GALLERY_LIMIT = 60;  // max chains shown after dedup
  const WS_DEBOUNCE_MS = 800;
  const VIDEO_EXTENSIONS = new Set(['mp4', 'webm', 'mov', 'm4v']);
  const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp']);

  const JOB_TYPE_LABELS = {
    'text-to-video':        'Text → Video',
    'text-to-image':        'Text → Image',
    'frames-to-video':      'Frames → Video',
    'ingredients-to-video': 'Image → Video',
    'extend-video':         'Extend',
    'camera-move':          'Camera',
    'insert-object':        'Insert',
    'remove-object':        'Remove',
  };

  const state = {
    chains: [],
    creating: false,
    wsUnsubs: [],
    wsRefreshTimer: null,
  };

  function apiClient() { return App.api || API; }
  function mediaTileHelper() { return App.mediaTile || window.MediaUtil; }

  function timestampMs(value) {
    const parsed = Date.parse(value || '');
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function compareByCreatedDesc(a, b) {
    const diff = timestampMs(b?.created_at) - timestampMs(a?.created_at);
    return diff !== 0 ? diff : String(b?.id || '').localeCompare(String(a?.id || ''));
  }

  function normalizeJobs(result) {
    const items = Array.isArray(result) ? result : result?.jobs || [];
    return items.filter((job) => job && typeof job === 'object');
  }

  function mediaKindFromFile(file) {
    const normalized = String(file || '').replace(/\\/g, '/');
    const base = normalized.split('/').pop();
    const dot = base.lastIndexOf('.');
    const ext = dot >= 0 ? base.slice(dot + 1).toLowerCase() : '';
    if (VIDEO_EXTENSIONS.has(ext)) return 'video';
    if (IMAGE_EXTENSIONS.has(ext)) return 'image';
    return null;
  }

  function mediaUrlFor(file) {
    const normalized = String(file || '').replace(/\\/g, '/').replace(/^downloads\//i, '');
    return `/downloads/${encodeURI(normalized)}`;
  }

  function primaryMedia(job) {
    const files = Array.isArray(job?.output_files) ? job.output_files : [];
    const renderable = files
      .map((f) => { const kind = mediaKindFromFile(f); return kind ? { kind, url: mediaUrlFor(f) } : null; })
      .filter(Boolean);
    if (!renderable.length) return null;
    const primary = renderable[0];
    return {
      ...primary,
      poster: primary.kind === 'video' ? (renderable.find((f) => f.kind === 'image')?.url || '') : '',
    };
  }

  /**
   * Group completed jobs by chain_id.
   *
   * Per chain we keep:
   *   - `latest`: job with the most-recent created_at (for the thumbnail)
   *   - `root`:   job_level === 1 root (for the prompt / title text)
   *
   * The returned synthetic object merges both so renderJobTile gets a single item.
   */
  function groupByChain(jobs) {
    const latestByChain = new Map();
    const rootByChain = new Map();

    for (const job of jobs) {
      const key = job.chain_id || job.id;

      const prev = latestByChain.get(key);
      if (!prev || timestampMs(job.created_at) > timestampMs(prev.created_at)) {
        latestByChain.set(key, job);
      }

      if ((job.job_level || 1) === 1) {
        rootByChain.set(key, job);
      }
    }

    return [...latestByChain.entries()]
      .map(([, latest]) => {
        const root = rootByChain.get(latest.chain_id || latest.id);
        return {
          ...latest,
          _displayPrompt: (root?.prompt || latest.prompt || '').trim(),
        };
      })
      .sort(compareByCreatedDesc)
      .slice(0, GALLERY_LIMIT);
  }

  async function loadData() {
    try {
      const result = await apiClient().jobs.list({ status: 'completed', limit: FETCH_LIMIT });
      state.chains = groupByChain(normalizeJobs(result));
    } catch (err) {
      console.warn('[Home] jobs fetch failed:', err?.message || err);
      state.chains = [];
    }
  }

  function scheduleRefresh() {
    clearTimeout(state.wsRefreshTimer);
    state.wsRefreshTimer = setTimeout(() => App._refreshCurrentPage(), WS_DEBOUNCE_MS);
  }

  function attachWS() {
    if (!window.WS || typeof WS.on !== 'function') return;
    state.wsUnsubs.push(WS.on('job_update', (job) => {
      if (App.currentPage !== 'home') return;
      if (String(job?.status || '').toLowerCase() !== 'completed') return;
      scheduleRefresh();
    }));
  }

  function detachWS() {
    clearTimeout(state.wsRefreshTimer);
    state.wsRefreshTimer = null;
    state.wsUnsubs.forEach((fn) => { try { fn?.(); } catch (_) {} });
    state.wsUnsubs = [];
  }

  function renderJobTile(job) {
    const prompt = job._displayPrompt;
    const typeLabel = JOB_TYPE_LABELS[job?.type] || job?.type || '';
    const displayText = prompt || typeLabel || 'Completed job';
    const truncated = displayText.length > 80 ? displayText.slice(0, 80) + '…' : displayText;
    const media = primaryMedia(job);

    const thumbnail = media
      ? (media.kind === 'video'
          ? mediaTileHelper().videoTag({ src: media.url, poster: media.poster, alt: displayText })
          : mediaTileHelper().imgTag({ src: media.url, alt: displayText }))
      : '';

    const routeKey = encodeURIComponent(String(job?.chain_id || job?.id || '').trim());
    const dateStr = typeof App.formatDate === 'function'
      ? App.formatDate(job?.created_at)
      : (job?.created_at || '').slice(0, 10);

    return `
      <a class="project-tile"
         href="#project-view/${routeKey}"
         data-job-id="${App.escapeHtml(String(job?.id || ''))}"
         title="${App.escapeHtml(displayText)}">
        <div class="tile-thumb">
          ${thumbnail}
          ${typeLabel ? `<span class="tile-type-badge">${App.escapeHtml(typeLabel)}</span>` : ''}
        </div>
        <div class="tile-overlay tile-overlay--job">
          <span class="tile-prompt">${App.escapeHtml(truncated)}</span>
          <span class="tile-date">${App.escapeHtml(dateStr)}</span>
        </div>
      </a>
    `;
  }

  function renderGallery() {
    if (!state.chains.length) {
      return `
        <div class="home-empty">
          <span class="material-icons">movie_filter</span>
          <p>Chưa có video nào. Tạo project để bắt đầu.</p>
        </div>
      `;
    }
    return `<div class="project-grid">${state.chains.map(renderJobTile).join('')}</div>`;
  }

  function renderCreateProjectFab() {
    return `
      <button type="button"
              class="new-project-fab"
              id="home-create-project"
              title="Tạo project"
              aria-label="Tạo project"
              ${state.creating ? 'disabled' : ''}
              style="cursor:pointer; border:0; appearance:none; -webkit-appearance:none; font:inherit;">
        <span class="material-icons">add</span>
        <span>Tạo project</span>
      </button>
    `;
  }

  async function handleCreateProject() {
    if (state.creating) return;
    const name = `Project · ${App.formatDate ? App.formatDate(new Date().toISOString()) : new Date().toLocaleDateString()}`;
    const button = document.getElementById('home-create-project');
    state.creating = true;
    if (button) button.disabled = true;
    try {
      const created = await apiClient().projects.create({ name });
      const projectId = String(created?.id || '').trim();
      if (!projectId) throw new Error('Project created but response missing id');
      location.hash = `#project-view/${encodeURIComponent(projectId)}`;
    } catch (err) {
      App.toast(`Tạo project thất bại: ${err?.message || err}`, 'error');
      state.creating = false;
      if (button) button.disabled = false;
    }
  }

  const HomePage = {
    name: 'home',
    title: 'FlowEngine',
    icon: 'movie_filter',

    async render() {
      state.creating = false;
      await loadData();
      return `
        <div class="home-canvas home-canvas-gallery">
          ${renderGallery()}
          ${renderCreateProjectFab()}
        </div>
      `;
    },

    mount() {
      attachWS();
      document.getElementById('home-create-project')
        ?.addEventListener('click', handleCreateProject);
    },

    destroy() {
      detachWS();
      state.creating = false;
    },
  };

  App.register(HomePage);
})();
