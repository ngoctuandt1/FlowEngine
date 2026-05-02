/**
 * Gallery Page
 * Browse completed media outputs with filters and direct job-detail routing.
 */
(() => {
  const VIDEO_EXTENSIONS = new Set(['mp4', 'webm', 'mov', 'm4v']);
  const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp']);
  const ALLOWED_STATUS = new Set(['pending', 'claimed', 'running', 'completed', 'failed', 'cancelled']);
  const BACKEND_GAP_WARNED = new Set();

  const state = {
    jobs: [],
    profiles: [],
    filters: {
      mediaKind: '',
      jobType: '',
      profile: '',
      dateFrom: '',
      dateTo: '',
    },
    requestId: 0,
    refreshTimer: null,
    wsUnsubs: [],
    socketListener: null,
    socketTarget: null,
  };

  function resetFilters() {
    state.filters.mediaKind = '';
    state.filters.jobType = '';
    state.filters.profile = '';
    state.filters.dateFrom = '';
    state.filters.dateTo = '';
  }

  function jobTypeOptions() {
    return Array.isArray(CONST?.JOB_TYPES) ? CONST.JOB_TYPES : [];
  }

  function normalizeJobList(result) {
    return Array.isArray(result) ? result : result?.jobs || [];
  }

  function normalizeProfiles(result) {
    const list = Array.isArray(result) ? result : result?.profiles || [];
    return list
      .map((profile) => profile?.name || profile?.profile_name || '')
      .filter(Boolean);
  }

  function safeStatus(status) {
    return ALLOWED_STATUS.has(status) ? status : 'pending';
  }

  function debugBadgesEnabled() {
    try {
      return localStorage.getItem('FLOW_DEBUG_BADGES') === '1';
    } catch (_) {
      return false;
    }
  }

  function warnBackendGap({ field, jobId, fallbackUsed }) {
    const key = `${field}|${jobId || ''}|${fallbackUsed}`;
    if (BACKEND_GAP_WARNED.has(key)) return;
    BACKEND_GAP_WARNED.add(key);
    console.warn('[backend-gap]', {
      page: 'gallery',
      field,
      jobId: jobId || '',
      fallbackUsed,
    });
  }

  function renderDebugBadges(items) {
    if (!debugBadgesEnabled() || !Array.isArray(items) || !items.length) return '';
    return items.map((item) => `
      <span
        class="tile-status-badge state-pending"
        title="${App.escapeHtml(`${item.field} -> ${item.fallbackUsed}`)}"
        style="opacity:0.65;"
      >
        ${App.escapeHtml(`gap:${item.field}`)}
      </span>
    `).join('');
  }

  function collectProfiles() {
    const merged = new Set();
    state.profiles.forEach((name) => merged.add(name));
    state.jobs.forEach((job) => {
      if (job?.profile) merged.add(job.profile);
    });
    return Array.from(merged).sort((a, b) => a.localeCompare(b));
  }

  function jobTypeLabel(type) {
    const meta = jobTypeOptions().find((item) => item.id === type);
    return meta?.label || (type || 'Unknown').replace(/-/g, ' ');
  }

  function mediaTypeFromFile(file) {
    const normalized = String(file || '').replace(/\\/g, '/');
    const filename = normalized.split('/').pop() || normalized;
    const extension = filename.includes('.') ? filename.split('.').pop().toLowerCase() : '';
    if (VIDEO_EXTENSIONS.has(extension)) return 'video';
    if (IMAGE_EXTENSIONS.has(extension)) return 'image';
    return null;
  }

  function mediaUrl(file) {
    const normalized = String(file || '').replace(/\\/g, '/').replace(/^downloads\//i, '');
    return `/downloads/${encodeURI(normalized)}`;
  }

  function renderableFiles(job) {
    const files = Array.isArray(job?.output_files) ? job.output_files : [];
    return files
      .map((file) => {
        const kind = mediaTypeFromFile(file);
        if (!kind) return null;
        const normalized = String(file).replace(/\\/g, '/').replace(/^downloads\//i, '');
        const name = normalized.split('/').pop() || normalized;
        return {
          file,
          kind,
          name,
          url: mediaUrl(file),
        };
      })
      .filter(Boolean);
  }

  function primaryMedia(job) {
    const files = renderableFiles(job);
    if (!files.length) return null;

    const primary = files[0];
    const poster = primary.kind === 'video'
      ? files.find((file) => file.kind === 'image')?.url || ''
      : '';

    return {
      ...primary,
      poster,
      files,
    };
  }

  function promptSnippet(job, length = 88) {
    const text = job.prompt || job.direction || job.media_id || job.id || 'Completed media';
    return App.truncate(text, length);
  }

  function mediaTileHelper() {
    return App.mediaTile || window.MediaUtil;
  }

  function galleryItems() {
    return state.jobs
      .map((job) => {
        const media = primaryMedia(job);
        return media ? { job, media } : null;
      })
      .filter(Boolean);
  }

  function matchesDateRange(createdAt) {
    const date = new Date(createdAt);
    if (state.filters.dateFrom) {
      const start = new Date(`${state.filters.dateFrom}T00:00:00`);
      if (date < start) return false;
    }
    if (state.filters.dateTo) {
      const end = new Date(`${state.filters.dateTo}T23:59:59.999`);
      if (date > end) return false;
    }
    return true;
  }

  function filteredItems() {
    return galleryItems().filter(({ job, media }) => {
      if (state.filters.mediaKind && media.kind !== state.filters.mediaKind) return false;
      if (state.filters.jobType && job.type !== state.filters.jobType) return false;
      if (state.filters.profile && (job.profile || '') !== state.filters.profile) return false;
      if (!matchesDateRange(job.created_at || job.createdAt)) return false;
      return true;
    });
  }

  function renderTile(item) {
    const { job, media } = item;
    const status = safeStatus(job.status);
    const title = promptSnippet(job);
    const mediaTile = mediaTileHelper();
    const preview = media.kind === 'video'
      ? mediaTile.videoTag({ src: media.url, poster: media.poster, alt: title })
      : mediaTile.imgTag({ src: media.url, alt: title });
    const stateChip = status === 'completed'
      ? ''
      : `<span class="tile-status-badge state-${status}">${App.escapeHtml(status.toUpperCase())}</span>`;
    const debugBadges = [];
    let tileRouteKey = String(job.chain_id || '').trim();
    if (!tileRouteKey) {
      tileRouteKey = String(job.id || '').trim();
      const fallbackUsed = 'job.id';
      warnBackendGap({ field: 'chain_id', jobId: String(job.id || ''), fallbackUsed });
      debugBadges.push({ field: 'chain_id', fallbackUsed });
    }
    const tileHref = `#project-view/${encodeURIComponent(tileRouteKey)}`;
    return `
      <a
        class="project-tile gallery-tile status-${status}"
        href="${tileHref}"
        data-job-id="${App.escapeHtml(job.id || '')}"
        title="${App.escapeHtml(title)}"
      >
        <div class="tile-thumb">
          ${preview}
          ${stateChip}
          ${renderDebugBadges(debugBadges)}
        </div>
        <div class="tile-overlay">
          <span class="tile-date">${App.escapeHtml(App.formatTileDate(job.created_at || job.createdAt))}</span>
        </div>
      </a>
    `;
  }

  function renderGalleryGrid() {
    const items = filteredItems();
    if (!items.length) {
      return `
        <div class="empty-state">
          <span class="material-icons">collections</span>
          <h3>No media matches these filters</h3>
          <p>Completed jobs with downloadable images or videos will appear here.</p>
        </div>
      `;
    }

    return `
      <div>
        <div class="section-header" style="margin-bottom: 12px;">
          <div>
            <h3 class="section-title">Completed Media</h3>
            <p style="margin-top:4px; color: var(--text-muted); font-size: 13px;">
              Showing ${items.length} tile${items.length === 1 ? '' : 's'} from completed jobs.
            </p>
          </div>
        </div>
        <div class="project-grid">${items.map(renderTile).join('')}</div>
      </div>
    `;
  }

  function renderFilters() {
    const mediaOptions = [
      { value: '', label: 'All media' },
      { value: 'video', label: 'Video' },
      { value: 'image', label: 'Image' },
    ];

    const mediaSelect = mediaOptions
      .map((item) => `
        <option value="${App.escapeHtml(item.value)}" ${state.filters.mediaKind === item.value ? 'selected' : ''}>
          ${App.escapeHtml(item.label)}
        </option>
      `)
      .join('');

    const typeSelect = jobTypeOptions()
      .map((item) => `
        <option value="${App.escapeHtml(item.id)}" ${state.filters.jobType === item.id ? 'selected' : ''}>
          ${App.escapeHtml(item.label)}
        </option>
      `)
      .join('');

    const profileSelect = collectProfiles()
      .map((name) => `
        <option value="${App.escapeHtml(name)}" ${state.filters.profile === name ? 'selected' : ''}>
          ${App.escapeHtml(name)}
        </option>
      `)
      .join('');

    return `
      <div class="card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Completed Media Gallery</h3>
            <p style="margin-top:4px; color: var(--text-muted); font-size: 13px;">
              Browse completed outputs by media type, job type, profile, and date range.
            </p>
          </div>
          <div class="section-actions">
            <button class="btn btn-sm btn-primary" id="gallery-refresh">
              <span class="material-icons" style="font-size:16px">refresh</span> Refresh
            </button>
          </div>
        </div>
        <div class="form-row" style="grid-template-columns: repeat(3, minmax(0, 1fr)); margin-bottom: 16px;">
          <div class="form-group" style="margin-bottom:0;">
            <label class="form-label">Media</label>
            <select class="form-select" id="gallery-filter-media">
              ${mediaSelect}
            </select>
          </div>
          <div class="form-group" style="margin-bottom:0;">
            <label class="form-label">Job Type</label>
            <select class="form-select" id="gallery-filter-job-type">
              <option value="">All job types</option>
              ${typeSelect}
            </select>
          </div>
          <div class="form-group" style="margin-bottom:0;">
            <label class="form-label">Profile</label>
            <select class="form-select" id="gallery-filter-profile">
              <option value="">All profiles</option>
              ${profileSelect}
            </select>
          </div>
        </div>
        <div class="form-row">
          <div class="form-group" style="margin-bottom:0;">
            <label class="form-label">Date From</label>
            <input type="date" class="form-input" id="gallery-filter-date-from" value="${App.escapeHtml(state.filters.dateFrom)}">
          </div>
          <div class="form-group" style="margin-bottom:0;">
            <label class="form-label">Date To</label>
            <input type="date" class="form-input" id="gallery-filter-date-to" value="${App.escapeHtml(state.filters.dateTo)}">
          </div>
        </div>
      </div>
    `;
  }

  function renderPage() {
    return `
      <div style="display:grid; gap:16px;">
        ${renderFilters()}
        <div id="gallery-results">${renderGalleryGrid()}</div>
      </div>
    `;
  }

  async function hydrate() {
    resetFilters();
    const requestId = ++state.requestId;
    const [jobsResult, profilesResult] = await Promise.allSettled([
      API.jobs.list({ status: 'completed' }),
      API.profiles.list(),
    ]);

    if (requestId !== state.requestId) return;

    state.jobs = jobsResult.status === 'fulfilled' ? normalizeJobList(jobsResult.value) : [];
    state.profiles = profilesResult.status === 'fulfilled' ? normalizeProfiles(profilesResult.value) : [];
  }

  function repaintGallery() {
    const results = document.getElementById('gallery-results');
    if (results) {
      results.innerHTML = renderGalleryGrid();
    }
  }

  async function refreshGallery(options = {}) {
    const button = document.getElementById('gallery-refresh');
    const results = document.getElementById('gallery-results');
    const requestId = ++state.requestId;

    if (button) {
      button.disabled = true;
      button.innerHTML = '<span class="spinner"></span> Refreshing...';
    }

    if (results && !state.jobs.length) {
      results.innerHTML = '<div class="loading-center"><div class="spinner spinner-lg"></div></div>';
    }

    try {
      const jobs = await API.jobs.list({ status: 'completed' });
      if (requestId !== state.requestId) return;
      state.jobs = normalizeJobList(jobs);
      repaintGallery();
    } catch (err) {
      if (requestId !== state.requestId) return;
      if (results) {
        results.innerHTML = `
          <div class="empty-state">
            <span class="material-icons">error_outline</span>
            <h3>Failed to load gallery</h3>
            <p>${App.escapeHtml(err.message)}</p>
          </div>
        `;
      }
      if (!options.silent) {
        App.toast('Failed to refresh gallery: ' + err.message, 'error');
      }
    } finally {
      if (requestId === state.requestId && button) {
        button.disabled = false;
        button.innerHTML = '<span class="material-icons" style="font-size:16px">refresh</span> Refresh';
      }
    }
  }

  function scheduleLiveRefresh() {
    if (App.currentPage !== 'gallery') return;
    if (state.refreshTimer) clearTimeout(state.refreshTimer);
    state.refreshTimer = setTimeout(() => {
      state.refreshTimer = null;
      refreshGallery({ silent: true });
    }, 350);
  }

  function handleSocketMessage(event) {
    try {
      const message = JSON.parse(event.data);
      const eventName = message.event || message.type;
      const payload = message.data || message.payload;
      if (eventName === 'job_update' && payload?.id) {
        scheduleLiveRefresh();
      }
    } catch (_) {
      // Ignore malformed messages from other sources.
    }
  }

  function attachSocketListener() {
    if (!state.socketListener) {
      state.socketListener = handleSocketMessage;
    }

    const socket = WS.socket;
    if (!socket || typeof socket.addEventListener !== 'function' || socket === state.socketTarget) {
      return;
    }

    if (state.socketTarget && typeof state.socketTarget.removeEventListener === 'function') {
      state.socketTarget.removeEventListener('message', state.socketListener);
    }

    state.socketTarget = socket;
    socket.addEventListener('message', state.socketListener);
  }

  function detachSocketListener() {
    if (state.socketTarget && state.socketListener && typeof state.socketTarget.removeEventListener === 'function') {
      state.socketTarget.removeEventListener('message', state.socketListener);
    }
    state.socketTarget = null;
  }

  const GalleryPage = {
    name: 'gallery',
    title: 'Gallery',
    icon: 'collections',

    async render() {
      await hydrate();
      return renderPage();
    },

    mount() {
      document.getElementById('gallery-filter-media')?.addEventListener('change', (event) => {
        state.filters.mediaKind = event.target.value;
        repaintGallery();
      });

      document.getElementById('gallery-filter-job-type')?.addEventListener('change', (event) => {
        state.filters.jobType = event.target.value;
        repaintGallery();
      });

      document.getElementById('gallery-filter-profile')?.addEventListener('change', (event) => {
        state.filters.profile = event.target.value;
        repaintGallery();
      });

      document.getElementById('gallery-filter-date-from')?.addEventListener('change', (event) => {
        state.filters.dateFrom = event.target.value;
        repaintGallery();
      });

      document.getElementById('gallery-filter-date-to')?.addEventListener('change', (event) => {
        state.filters.dateTo = event.target.value;
        repaintGallery();
      });

      document.getElementById('gallery-refresh')?.addEventListener('click', () => {
        refreshGallery();
      });
      attachSocketListener();
      state.wsUnsubs.push(WS.on('connected', attachSocketListener));
    },

    destroy() {
      if (state.refreshTimer) {
        clearTimeout(state.refreshTimer);
        state.refreshTimer = null;
      }
      state.wsUnsubs.forEach((unsubscribe) => {
        try {
          unsubscribe?.();
        } catch (_) {
          // Ignore cleanup failures.
        }
      });
      state.wsUnsubs = [];
      detachSocketListener();
    },
  };

  App.register(GalleryPage);
})();
