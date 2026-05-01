/**
 * Gallery Page
 * Browse completed media outputs with filters and preview modal.
 */
(() => {
  const VIDEO_EXTENSIONS = new Set(['mp4', 'webm', 'mov', 'm4v']);
  const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp']);

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

  function renderMediaBadge(mediaKind) {
    const style = mediaKind === 'video'
      ? 'background: rgba(59, 130, 246, 0.24); border-color: rgba(59, 130, 246, 0.42); color: #bfdbfe;'
      : 'background: rgba(34, 197, 94, 0.22); border-color: rgba(34, 197, 94, 0.38); color: #bbf7d0;';
    return `<span class="badge" style="${style}">${App.escapeHtml(mediaKind)}</span>`;
  }

  function renderTile(item) {
    const { job, media } = item;
    const typeLabel = jobTypeLabel(job.type);
    const title = promptSnippet(job);
    const preview = media.kind === 'video'
      ? `<video class="tile-video" src="${App.escapeHtml(media.url)}" ${media.poster ? `poster="${App.escapeHtml(media.poster)}"` : ''} muted loop playsinline preload="metadata" onmouseenter="this.play().catch(()=>{})" onmouseleave="this.pause(); this.currentTime=0;"></video>`
      : `<img src="${App.escapeHtml(media.url)}" alt="${App.escapeHtml(title)}" style="position:absolute; inset:0; width:100%; height:100%; object-fit:cover; background:#000;">`;

    return `
      <button type="button" class="project-tile gallery-tile" data-job-id="${App.escapeHtml(job.id || '')}" style="padding:0; text-align:left; border:0; background:transparent;">
        <div class="tile-thumb">
          ${preview}
          <div style="position:absolute; inset:0; background: linear-gradient(180deg, rgba(0,0,0,0.08) 20%, rgba(0,0,0,0.76) 100%);"></div>
          <div style="position:absolute; top:12px; left:12px; z-index:2; display:flex; gap:8px; flex-wrap:wrap;">
            ${renderMediaBadge(media.kind)}
            <span class="badge" style="background: rgba(12, 12, 14, 0.58); border-color: rgba(255, 255, 255, 0.10); color: #f5f5f7;">
              ${App.escapeHtml(typeLabel)}
            </span>
          </div>
          <div style="position:absolute; left:0; right:0; bottom:0; z-index:2; padding:14px;">
            <div style="margin-bottom:6px; color: rgba(255,255,255,0.78); font-size:12px;">
              ${App.escapeHtml(job.profile || 'Unpinned profile')}
            </div>
            <div style="color:#fff; font-size:16px; font-weight:600; line-height:1.35;">
              ${App.escapeHtml(title)}
            </div>
          </div>
        </div>
        <div class="tile-overlay" style="height:auto; min-height:56px; padding:10px 12px 12px 16px; align-items:flex-start;">
          <div style="display:flex; flex-direction:column; gap:2px; min-width:0;">
            <span style="color: var(--text-primary); font-size: 13px; font-weight: 600;">
              ${App.escapeHtml(typeLabel)}
            </span>
            <span style="color: var(--text-muted); font-size: 12px;">
              ${App.escapeHtml(App.truncate(job.media_id || job.id || '', 22))}
            </span>
          </div>
          <span class="tile-date" style="font-size:12px;">${App.escapeHtml(App.formatDate(job.created_at || job.createdAt))}</span>
        </div>
      </button>
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

  function renderModal(job) {
    const media = primaryMedia(job);
    if (!media) {
      return `
        <div class="empty-state" style="padding: 20px;">
          <span class="material-icons">broken_image</span>
          <h3>No preview available</h3>
          <p>This job completed, but it does not expose a renderable image or video file.</p>
        </div>
      `;
    }

    const preview = media.kind === 'video'
      ? `<video src="${App.escapeHtml(media.url)}" ${media.poster ? `poster="${App.escapeHtml(media.poster)}"` : ''} controls autoplay loop muted playsinline style="width:100%; max-height: 70vh; border-radius: 14px; background:#000; object-fit: contain;"></video>`
      : `<img src="${App.escapeHtml(media.url)}" alt="${App.escapeHtml(promptSnippet(job, 120))}" style="width:100%; max-height: 70vh; border-radius: 14px; background:#000; object-fit: contain;">`;

    const extraFiles = media.files
      .map((file) => `
        <a class="btn btn-sm btn-outline" href="${App.escapeHtml(file.url)}" target="_blank" rel="noopener">
          <span class="material-icons" style="font-size:16px">open_in_new</span> ${App.escapeHtml(file.name)}
        </a>
      `)
      .join('');

    return `
      <div style="display:grid; gap:16px;">
        <div>${preview}</div>
        <div class="detail-list">
          <div class="detail-row">
            <span class="detail-label">Type</span>
            <span class="detail-value">${App.escapeHtml(jobTypeLabel(job.type))} / ${App.escapeHtml(media.kind)}</span>
          </div>
          <div class="detail-row">
            <span class="detail-label">Profile</span>
            <span class="detail-value">${App.escapeHtml(job.profile || '-')}</span>
          </div>
          <div class="detail-row">
            <span class="detail-label">Created</span>
            <span class="detail-value">${App.escapeHtml(job.created_at || job.createdAt || '-')}</span>
          </div>
          <div class="detail-row">
            <span class="detail-label">Prompt</span>
            <span class="detail-value">${App.escapeHtml(job.prompt || job.direction || '-')}</span>
          </div>
          <div class="detail-row">
            <span class="detail-label">Chain</span>
            <span class="detail-value">
              ${job.chain_id ? `<a href="#jobs/${encodeURIComponent(job.chain_id)}" onclick="App.closeModal()">${App.escapeHtml(job.chain_id)}</a>` : '-'}
            </span>
          </div>
          <div class="detail-row">
            <span class="detail-label">Flow Link</span>
            <span class="detail-value">
              ${job.edit_url ? `<a href="${App.escapeHtml(job.edit_url)}" target="_blank" rel="noopener">${App.escapeHtml(job.edit_url)}</a>` : (job.project_url ? `<a href="${App.escapeHtml(job.project_url)}" target="_blank" rel="noopener">${App.escapeHtml(job.project_url)}</a>` : '-')}
            </span>
          </div>
        </div>
        <div>
          <div class="detail-label" style="margin-bottom:8px;">Files</div>
          <div style="display:flex; flex-wrap:wrap; gap:8px;">${extraFiles}</div>
        </div>
      </div>
    `;
  }

  async function showMedia(jobId) {
    try {
      const job = await API.jobs.get(jobId);
      App.openModal(promptSnippet(job, 56), renderModal(job));
    } catch (err) {
      App.toast('Failed to load media: ' + err.message, 'error');
    }
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

      document.getElementById('gallery-results')?.addEventListener('click', (event) => {
        const tile = event.target.closest('.gallery-tile');
        if (!tile) return;
        const jobId = tile.dataset.jobId;
        if (jobId) showMedia(jobId);
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
