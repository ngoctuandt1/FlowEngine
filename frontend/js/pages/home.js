/**
 * Home page
 * Project-first gallery backed by /api/projects.
 */
(() => {
  const RECENT_LIMIT = 12;
  const VIDEO_EXTENSIONS = new Set(['mp4', 'webm', 'mov', 'm4v']);
  const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp']);

  const state = {
    projects: [],
    recentJobs: [],
    creating: false,
    wsUnsubs: [],
  };

  function apiClient() {
    return App.api || API;
  }

  function mediaTileHelper() {
    return App.mediaTile || window.MediaUtil;
  }

  function formatTileDate(value) {
    if (typeof App.formatTileDate === 'function') return App.formatTileDate(value);
    if (typeof App.formatDate === 'function') return App.formatDate(value);
    return '-';
  }

  function timestampMs(value) {
    const parsed = Date.parse(value || '');
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function compareByUpdatedDesc(a, b) {
    const updatedDiff = timestampMs(b?.updated_at || b?.created_at) - timestampMs(a?.updated_at || a?.created_at);
    if (updatedDiff !== 0) return updatedDiff;
    return String(b?.id || '').localeCompare(String(a?.id || ''));
  }

  function compareByCreatedDesc(a, b) {
    const createdDiff = timestampMs(b?.created_at || b?.createdAt) - timestampMs(a?.created_at || a?.createdAt);
    if (createdDiff !== 0) return createdDiff;
    return String(b?.id || '').localeCompare(String(a?.id || ''));
  }

  function normalizeJobs(result) {
    const items = Array.isArray(result) ? result : result?.jobs || [];
    return items.filter((job) => job && typeof job === 'object');
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

  async function loadData() {
    const projects = await apiClient().projects.list();
    state.projects = (Array.isArray(projects) ? projects : [])
      .slice()
      .sort(compareByUpdatedDesc);

    if (state.projects.length) {
      state.recentJobs = [];
      return;
    }

    try {
      const jobs = await apiClient().jobs.list({ limit: RECENT_LIMIT, status: 'completed' });
      state.recentJobs = normalizeJobs(jobs)
        .slice()
        .sort(compareByCreatedDesc)
        .slice(0, RECENT_LIMIT);
    } catch (err) {
      console.warn('[Home] recent jobs fetch failed:', err?.message || err);
      state.recentJobs = [];
    }
  }

  function attachWS() {
    if (!window.WS || typeof WS.on !== 'function') return;
    state.wsUnsubs.push(WS.on('job_update', (job) => {
      if (App.currentPage !== 'home' || state.projects.length) return;
      if (String(job?.status || '').toLowerCase() !== 'completed') return;
      App._refreshCurrentPage();
    }));
  }

  function detachWS() {
    state.wsUnsubs.forEach((unsubscribe) => {
      try {
        unsubscribe?.();
      } catch (_) {
        // Ignore WS cleanup failures.
      }
    });
    state.wsUnsubs = [];
  }

  function renderProjectTile(project) {
    const id = String(project?.id || '').trim();
    const name = String(project?.name || 'Untitled project').trim() || 'Untitled project';
    const coverThumbUrl = String(project?.cover_thumb_url || '').trim();
    const thumbnail = coverThumbUrl
      ? mediaTileHelper().imgTag({ src: coverThumbUrl, alt: name })
      : '';

    return `
      <a class="project-tile"
         href="#project-view/${encodeURIComponent(id)}"
         title="${App.escapeHtml(name)}">
        <div class="tile-thumb">
          ${thumbnail}
        </div>
        <div class="tile-overlay" style="display:grid; justify-content:initial; align-items:start; gap:4px; height:auto; min-height:60px; padding:10px 12px 12px 16px;">
          <strong style="font-size:16px; line-height:1.35; font-weight:600; color:var(--text-flow);">${App.escapeHtml(name)}</strong>
          <span class="tile-date" style="font-size:12px; color:var(--text-muted);">${App.escapeHtml(formatTileDate(project?.updated_at || project?.created_at))}</span>
        </div>
      </a>
    `;
  }

  function renderRecentJobTile(job) {
    const title = job?.prompt || job?.direction || job?.type || 'Completed job';
    const media = primaryMedia(job);
    const thumbnail = media
      ? (media.kind === 'video'
        ? mediaTileHelper().videoTag({ src: media.url, poster: media.poster, alt: title })
        : mediaTileHelper().imgTag({ src: media.url, alt: title }))
      : '';
    const routeKey = encodeURIComponent(String(job?.chain_id || job?.id || '').trim());

    return `
      <a class="project-tile status-completed"
         href="#project-view/${routeKey}"
         data-job-id="${App.escapeHtml(String(job?.id || ''))}"
         title="${App.escapeHtml(title)}"
         style="flex:0 0 calc((100% - 48px) / 4); min-width:220px; max-width:280px;">
        <div class="tile-thumb">
          ${thumbnail}
        </div>
        <div class="tile-overlay" style="height:auto; min-height:44px; padding:8px 10px 10px 12px;">
          <span class="tile-date" style="font-size:12px;">${App.escapeHtml(formatTileDate(job?.created_at || job?.createdAt))}</span>
        </div>
      </a>
    `;
  }

  function renderProjectsSection() {
    if (!state.projects.length) return '';
    return `<div class="project-grid" id="home-project-grid">${state.projects.map(renderProjectTile).join('')}</div>`;
  }

  function renderEmptyState() {
    // Empty state suppressed by design \u2014 match Flow's gallery, which jumps
    // straight to the tile grid without a "no projects yet" placeholder.
    return '';
  }

  function renderRecentJobsSection() {
    // Render recent jobs as the primary grid when no projects exist (and
    // mix them in below the projects when both exist). No section header,
    // no "Recent jobs" label \u2014 just tiles, like Flow.
    if (!state.recentJobs.length) return '';
    return `<div class="project-grid" id="home-recent-jobs">${state.recentJobs.map(renderRecentJobTile).join('')}</div>`;
  }

  function renderCreateProjectFab() {
    return `
      <button type="button"
              class="new-project-fab"
              id="home-create-project"
              title="T\u1ea1o project"
              aria-label="T\u1ea1o project"
              ${state.creating ? 'disabled' : ''}
              style="cursor:pointer; border:0; appearance:none; -webkit-appearance:none; font:inherit;">
        <span class="material-icons">add</span>
        <span>T\u1ea1o project</span>
      </button>
    `;
  }

  async function handleCreateProject() {
    if (state.creating) return;

    // Auto-name with the current date so a click goes straight to the canvas.
    // Users can rename later from the project view (PUT /api/projects/{id}).
    const name = `Project \u00b7 ${App.formatTileDate(new Date().toISOString())}`;

    const button = document.getElementById('home-create-project');
    state.creating = true;
    if (button) button.disabled = true;

    try {
      const created = await apiClient().projects.create({ name });
      const projectId = String(created?.id || '').trim();
      if (!projectId) {
        throw new Error('Project created but response missing id');
      }
      state.projects = [{ ...created }, ...state.projects.filter((project) => String(project?.id || '') !== projectId)];
      location.hash = `#project-view/${encodeURIComponent(projectId)}`;
    } catch (err) {
      App.toast(`T\u1ea1o project th\u1ea5t b\u1ea1i: ${err?.message || err}`, 'error');
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
          ${renderProjectsSection()}
          ${renderEmptyState()}
          ${renderRecentJobsSection()}
          ${renderCreateProjectFab()}
        </div>
      `;
    },

    mount() {
      attachWS();
      document.getElementById('home-create-project')?.addEventListener('click', () => {
        handleCreateProject();
      });
    },

    destroy() {
      detachWS();
      state.creating = false;
    },
  };

  App.register(HomePage);
})();
