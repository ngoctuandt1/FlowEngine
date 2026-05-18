/**
 * Jobs Page
 * Full job history with filters, actions, and live status refresh.
 */
(() => {
  const STATUS_OPTIONS = [
    { value: '', label: 'All statuses' },
    { value: 'pending', label: 'Pending' },
    { value: 'claimed', label: 'Claimed' },
    { value: 'running', label: 'Running' },
    { value: 'completed', label: 'Completed' },
    { value: 'failed', label: 'Failed' },
    { value: 'cancelled', label: 'Cancelled' },
  ];

  const GREY_BADGE_STYLE = 'background: rgba(107, 114, 128, 0.22); border-color: rgba(107, 114, 128, 0.38); color: #e5e7eb;';
  const SEARCH_DEBOUNCE_MS = 300;
  const MIN_SERVER_SEARCH_LENGTH = 2;

  const state = {
    jobs: [],
    profiles: [],
    filters: {
      status: '',
      type: '',
      profile: '',
      chain_id: '',
      search: '',
    },
    pagination: {
      page: 0,
      pageSize: 50,
      hasNext: false,
    },
    requestId: 0,
    refreshTimer: null,
    searchTimer: null,
    wsUnsubs: [],
    socketListener: null,
    socketTarget: null,
  };

  function resetFilters() {
    state.filters.status = '';
    state.filters.type = '';
    state.filters.profile = '';
    state.filters.chain_id = '';
    state.filters.search = '';
    state.pagination.page = 0;
    state.pagination.hasNext = false;
  }

  function jobTypeOptions() {
    return Array.isArray(CONST?.JOB_TYPES) ? CONST.JOB_TYPES : [];
  }

  function routeChainId() {
    const parts = location.hash.slice(1).split('/');
    return parts.length > 1 ? decodeURIComponent(parts[1] || '') : '';
  }

  function normalizeJobList(result) {
    return Array.isArray(result) ? result : result?.jobs || [];
  }

  function normalizeHasMore(result, jobs) {
    if (result && typeof result === 'object' && !Array.isArray(result) && typeof result.has_more === 'boolean') {
      return result.has_more;
    }
    return jobs.length === state.pagination.pageSize;
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

  function buildJobFilters() {
    const filters = {
      limit: state.pagination.pageSize,
      offset: state.pagination.page * state.pagination.pageSize,
    };
    ['status', 'type', 'profile', 'chain_id'].forEach((key) => {
      if (state.filters[key]) filters[key] = state.filters[key];
    });
    const query = state.filters.search.trim();
    if (query.length >= MIN_SERVER_SEARCH_LENGTH) filters.q = query;
    return filters;
  }

  function resetPageAndRefresh() {
    state.pagination.page = 0;
    refreshJobs();
  }

  function debounceSearchRefresh() {
    if (state.searchTimer) clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(() => {
      state.searchTimer = null;
      resetPageAndRefresh();
    }, SEARCH_DEBOUNCE_MS);
  }

  function searchMatches(job) {
    const query = state.filters.search.trim().toLowerCase();
    if (!query) return true;
    return [
      job?.id,
      job?.project_url,
      job?.type,
      job?.profile,
    ].some((value) => String(value || '').toLowerCase().includes(query));
  }

  function filteredJobs() {
    return state.jobs.filter(searchMatches);
  }

  function renderPaginationControls(visibleCount) {
    const pageSizeOptions = [25, 50, 100]
      .map((size) => `
        <option value="${size}" ${state.pagination.pageSize === size ? 'selected' : ''}>${size}</option>
      `)
      .join('');
    const start = state.jobs.length ? state.pagination.page * state.pagination.pageSize + 1 : 0;
    const end = state.pagination.page * state.pagination.pageSize + state.jobs.length;
    const searchSuffix = state.filters.search.trim()
      ? `, ${visibleCount} search match${visibleCount === 1 ? '' : 'es'}`
      : '';

    return `
      <div style="display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; padding: 14px 20px; border-top: 1px solid var(--border);">
        <div style="display:flex; align-items:center; gap:10px; color: var(--text-muted); font-size:13px;">
          <span>Rows per page</span>
          <select class="form-select" id="jobs-page-size" aria-label="Rows per page" style="width:auto; min-width:80px; padding:6px 28px 6px 10px;">
            ${pageSizeOptions}
          </select>
          <span>Showing ${start}-${end}${searchSuffix}</span>
        </div>
        <div style="display:flex; gap:8px;">
          <button class="btn btn-sm btn-outline" data-action="page-prev" ${state.pagination.page === 0 ? 'disabled' : ''}>
            <span class="material-icons" style="font-size:16px">chevron_left</span> Prev
          </button>
          <button class="btn btn-sm btn-outline" data-action="page-next" ${state.pagination.hasNext ? '' : 'disabled'}>
            Next <span class="material-icons" style="font-size:16px">chevron_right</span>
          </button>
        </div>
      </div>
    `;
  }

  function jobTypeLabel(type) {
    const meta = jobTypeOptions().find((item) => item.id === type);
    return meta?.label || (type || 'Unknown').replace(/-/g, ' ');
  }

  function renderStatusBadge(status) {
    const safe = status || 'pending';
    if (safe === 'cancelled') {
      return `<span class="badge" style="${GREY_BADGE_STYLE}">cancelled</span>`;
    }
    return `<span class="${App.statusBadge(safe)}">${App.escapeHtml(safe)}</span>`;
  }

  function renderActionButtons(job) {
    const id = App.escapeHtml(job.id || '');
    const buttons = [
      `<button class="btn btn-sm btn-outline" data-action="view" data-job-id="${id}">
        <span class="material-icons" style="font-size:16px">visibility</span> View
      </button>`,
    ];

    if (job.status === 'failed') {
      buttons.push(`
        <button class="btn btn-sm btn-outline" data-action="retry" data-job-id="${id}">
          <span class="material-icons" style="font-size:16px">restart_alt</span> Retry
        </button>
      `);
    }

    if (['failed', 'cancelled'].includes(job.status)) {
      buttons.push(`
        <button class="btn btn-sm btn-outline" data-action="requeue" data-job-id="${id}">
          <span class="material-icons" style="font-size:16px">redo</span> Requeue
        </button>
      `);
    }

    buttons.push(`
      <button class="btn btn-sm btn-danger" data-action="delete" data-job-id="${id}">
        <span class="material-icons" style="font-size:16px">delete</span> Delete
      </button>
    `);

    return buttons.join('');
  }

  function renderJobsTable() {
    const visibleJobs = filteredJobs();

    if (!state.jobs.length) {
      const message = state.pagination.page > 0
        ? 'Go back a page or adjust filters to find jobs.'
        : 'Adjust the filters or create a new job to populate the history view.';
      return `
        <div class="table-container">
          <div class="empty-state">
            <span class="material-icons">work_history</span>
            <h3>No matching jobs</h3>
            <p>${message}</p>
          </div>
          ${state.pagination.page > 0 ? renderPaginationControls(0) : ''}
        </div>
      `;
    }

    if (!visibleJobs.length) {
      return `
        <div class="table-container">
          <div class="empty-state">
            <span class="material-icons">search_off</span>
            <h3>No jobs match search</h3>
            <p>Search checks ID, project URL, type, and profile across all jobs.</p>
          </div>
          ${renderPaginationControls(0)}
        </div>
      `;
    }

    const rows = visibleJobs.map((job) => {
      const jobId = String(job.id || '');
      const createdAt = job.created_at || job.createdAt || '';
      const profile = job.profile || '-';

      const chainId = String(job.chain_id || '');
      return `
        <tr data-job-id="${App.escapeHtml(jobId)}" data-chain-id="${App.escapeHtml(chainId)}" style="cursor:pointer;">
          <td title="${App.escapeHtml(jobId)}">
            <code>${App.escapeHtml(App.truncate(jobId, 12))}</code>
          </td>
          <td>
            <div style="display:flex; align-items:center; gap:10px; min-width:0;">
              <span class="material-icons" style="font-size:18px; color: var(--accent-hover);">
                ${App.escapeHtml(App.jobTypeIcon(job.type))}
              </span>
              <span>${App.escapeHtml(jobTypeLabel(job.type))}</span>
            </div>
          </td>
          <td title="${App.escapeHtml(profile)}">${App.escapeHtml(App.truncate(profile, 24) || '-')}</td>
          <td>${renderStatusBadge(job.status)}</td>
          <td title="${App.escapeHtml(createdAt)}">${App.escapeHtml(App.formatTileDate(createdAt))}</td>
          <td>
            <div style="display:flex; flex-wrap:wrap; gap:8px;">
              ${renderActionButtons(job)}
            </div>
          </td>
        </tr>
      `;
    }).join('');

    return `
      <div class="table-container">
        <div class="section-header" style="padding: 18px 20px 0;">
          <div>
            <h3 class="section-title">History</h3>
            <p style="margin-top:4px; color: var(--text-muted); font-size: 13px;">
              Showing ${visibleJobs.length} of ${state.jobs.length} job${state.jobs.length === 1 ? '' : 's'} on page ${state.pagination.page + 1}${state.filters.chain_id ? ' in this chain' : ''}.
            </p>
          </div>
        </div>
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Type</th>
              <th>Profile</th>
              <th>Status</th>
              <th>Created</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
        ${renderPaginationControls(visibleJobs.length)}
      </div>
    `;
  }

  function renderFilterBar() {
    const profileOptions = collectProfiles()
      .map((name) => `
        <option value="${App.escapeHtml(name)}" ${state.filters.profile === name ? 'selected' : ''}>
          ${App.escapeHtml(name)}
        </option>
      `)
      .join('');

    const typeOptions = jobTypeOptions()
      .map((item) => `
        <option value="${App.escapeHtml(item.id)}" ${state.filters.type === item.id ? 'selected' : ''}>
          ${App.escapeHtml(item.label)}
        </option>
      `)
      .join('');

    const statusOptions = STATUS_OPTIONS
      .map((item) => `
        <option value="${App.escapeHtml(item.value)}" ${state.filters.status === item.value ? 'selected' : ''}>
          ${App.escapeHtml(item.label)}
        </option>
      `)
      .join('');

    return `
      <div class="card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Job History</h3>
            <p style="margin-top:4px; color: var(--text-muted); font-size: 13px;">
              Full queue history with retry, delete, and live status refresh.
            </p>
          </div>
          <div class="section-actions">
            <button class="btn btn-sm btn-outline" id="jobs-recover" title="Reset stale claimed and running jobs back to pending">
              <span class="material-icons" style="font-size:16px">healing</span> Resume all stalled
            </button>
            <button class="btn btn-sm btn-primary" id="jobs-refresh">
              <span class="material-icons" style="font-size:16px">refresh</span> Refresh
            </button>
          </div>
        </div>
        ${state.filters.chain_id ? `
          <div style="margin-bottom: 16px; padding: 12px 14px; border: 1px solid var(--accent-border); border-radius: 10px; background: rgba(124, 92, 255, 0.10); display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap;">
            <div style="color: var(--text-secondary); font-size: 13px;">
              Chain filter active: <code>${App.escapeHtml(state.filters.chain_id)}</code>
            </div>
            <a href="#jobs" class="btn btn-sm btn-outline">
              <span class="material-icons" style="font-size:16px">filter_alt_off</span> Clear chain filter
            </a>
          </div>
        ` : ''}
        <div class="form-row" style="grid-template-columns: minmax(220px, 1.5fr) repeat(3, minmax(0, 1fr));">
          <div class="form-group" style="margin-bottom:0;">
            <label class="form-label">Search jobs</label>
            <input
              class="form-input"
              id="jobs-search"
              type="search"
              placeholder="ID, project URL, type, profile"
              value="${App.escapeHtml(state.filters.search)}"
            >
          </div>
          <div class="form-group" style="margin-bottom:0;">
            <label class="form-label">Status</label>
            <select class="form-select" id="jobs-filter-status">
              ${statusOptions}
            </select>
          </div>
          <div class="form-group" style="margin-bottom:0;">
            <label class="form-label">Type</label>
            <select class="form-select" id="jobs-filter-type">
              <option value="">All types</option>
              ${typeOptions}
            </select>
          </div>
          <div class="form-group" style="margin-bottom:0;">
            <label class="form-label">Profile</label>
            <select class="form-select" id="jobs-filter-profile">
              <option value="">All profiles</option>
              ${profileOptions}
            </select>
          </div>
        </div>
      </div>
    `;
  }

  function renderPage() {
    return `
      <div style="display:grid; gap:16px;">
        ${renderFilterBar()}
        <div id="jobs-results">${renderJobsTable()}</div>
      </div>
    `;
  }

  async function hydrate() {
    resetFilters();
    state.filters.chain_id = routeChainId();
    const requestId = ++state.requestId;
    const [jobsResult, profilesResult] = await Promise.allSettled([
      API.jobs.list(buildJobFilters()),
      API.profiles.list(),
    ]);

    if (requestId !== state.requestId) return;

    state.jobs = jobsResult.status === 'fulfilled' ? normalizeJobList(jobsResult.value) : [];
    state.pagination.hasNext = jobsResult.status === 'fulfilled' ? normalizeHasMore(jobsResult.value, state.jobs) : false;
    state.profiles = profilesResult.status === 'fulfilled' ? normalizeProfiles(profilesResult.value) : [];
  }

  async function refreshJobs(options = {}) {
    const button = document.getElementById('jobs-refresh');
    const results = document.getElementById('jobs-results');
    const requestId = ++state.requestId;

    if (button) {
      button.disabled = true;
      button.innerHTML = '<span class="spinner"></span> Refreshing...';
    }

    if (results && !state.jobs.length) {
      results.innerHTML = '<div class="loading-center"><div class="spinner spinner-lg"></div></div>';
    }

    try {
      const list = await API.jobs.list(buildJobFilters());
      if (requestId !== state.requestId) return;
      state.jobs = normalizeJobList(list);
      state.pagination.hasNext = normalizeHasMore(list, state.jobs);
      if (results) results.innerHTML = renderJobsTable();
    } catch (err) {
      if (requestId !== state.requestId) return;
      if (results) {
        results.innerHTML = `
          <div class="empty-state">
            <span class="material-icons">error_outline</span>
            <h3>Failed to load jobs</h3>
            <p>${App.escapeHtml(err.message)}</p>
          </div>
        `;
      }
      if (!options.silent) {
        App.toast('Failed to refresh jobs: ' + err.message, 'error');
      }
    } finally {
      if (requestId === state.requestId && button) {
        button.disabled = false;
        button.innerHTML = '<span class="material-icons" style="font-size:16px">refresh</span> Refresh';
      }
    }
  }

  function setActionBusy(button, busy, labelHtml) {
    if (!button) return;
    button.disabled = busy;
    if (busy) {
      button.dataset.originalHtml = button.innerHTML;
      button.innerHTML = '<span class="spinner"></span>';
      return;
    }
    button.innerHTML = labelHtml || button.dataset.originalHtml || button.innerHTML;
  }

  function buildRetryPayload(job) {
    const payload = { type: job.type };
    [
      'prompt',
      'model',
      'aspect_ratio',
      'profile',
      'parent_job_id',
      'chain_id',
      'project_url',
      'media_id',
      'direction',
      'start_image_path',
      'end_image_path',
      'ref_image_path',
    ].forEach((field) => {
      if (job[field] !== undefined && job[field] !== null && job[field] !== '') {
        payload[field] = job[field];
      }
    });

    if (Array.isArray(job.ingredient_image_paths) && job.ingredient_image_paths.length) {
      payload.ingredient_image_paths = [...job.ingredient_image_paths];
    }

    if (
      job.bbox &&
      ['x', 'y', 'w', 'h'].every((key) => typeof job.bbox[key] === 'number')
    ) {
      payload.bbox = {
        x: job.bbox.x,
        y: job.bbox.y,
        w: job.bbox.w,
        h: job.bbox.h,
      };
    }

    return payload;
  }

  function openJobDetail(jobId, chainId) {
    const key = chainId || jobId;
    window.location.hash = `#project-view/${encodeURIComponent(key)}`;
  }

  async function retryJob(jobId, button) {
    setActionBusy(button, true);
    try {
      const job = await API.jobs.get(jobId);
      const retried = await API.jobs.create(buildRetryPayload(job));
      const newId = retried?.id ? ` as ${retried.id.slice(0, 8)}` : '';
      App.toast(`Retry queued${newId}`, 'success');
      await refreshJobs({ silent: true });
    } catch (err) {
      App.toast('Retry failed: ' + err.message, 'error');
    } finally {
      setActionBusy(button, false);
    }
  }

  async function requeueJob(jobId, button) {
    if (!confirm(`Requeue job ${jobId}?`)) return;

    setActionBusy(button, true);
    try {
      await API.jobs.requeue(jobId);
      App.toast('Job requeued', 'success');
      await refreshJobs({ silent: true });
    } catch (err) {
      App.toast('Requeue failed: ' + err.message, 'error');
    } finally {
      setActionBusy(button, false);
    }
  }

  async function removeJob(jobId, button) {
    const prompt = 'Delete this job? This cannot be undone.';
    if (!confirm(prompt)) return;

    setActionBusy(button, true);
    try {
      await API.jobs.delete(jobId);
      App.toast('Job deleted', 'success');
      App.closeModal();
      await refreshJobs({ silent: true });
    } catch (err) {
      App.toast('Failed to delete: ' + err.message, 'error');
    } finally {
      setActionBusy(button, false);
    }
  }

  async function recoverJobs(button) {
    if (!button) return;
    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span> Resuming...';
    try {
      const result = await API.jobs.recover();
      const count = result?.recovered || 0;
      App.toast(
        count > 0 ? `Recovered ${count} stalled job(s)` : 'No stalled jobs found',
        count > 0 ? 'success' : 'info'
      );
      await refreshJobs({ silent: true });
    } catch (err) {
      App.toast('Resume all stalled failed: ' + err.message, 'error');
    } finally {
      button.disabled = false;
      button.innerHTML = '<span class="material-icons" style="font-size:16px">healing</span> Resume all stalled';
    }
  }

  function scheduleLiveRefresh() {
    if (App.currentPage !== 'jobs') return;
    if (state.refreshTimer) clearTimeout(state.refreshTimer);
    state.refreshTimer = setTimeout(() => {
      state.refreshTimer = null;
      refreshJobs({ silent: true });
    }, 250);
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

  const JobsPage = {
    name: 'jobs',
    title: 'Jobs',
    icon: 'list',

    async render() {
      await hydrate();
      return renderPage();
    },

    mount() {
      document.getElementById('jobs-filter-status')?.addEventListener('change', (event) => {
        state.filters.status = event.target.value;
        resetPageAndRefresh();
      });

      document.getElementById('jobs-filter-type')?.addEventListener('change', (event) => {
        state.filters.type = event.target.value;
        resetPageAndRefresh();
      });

      document.getElementById('jobs-filter-profile')?.addEventListener('change', (event) => {
        state.filters.profile = event.target.value;
        resetPageAndRefresh();
      });

      document.getElementById('jobs-search')?.addEventListener('input', (event) => {
        state.filters.search = event.target.value;
        debounceSearchRefresh();
      });

      document.getElementById('jobs-results')?.addEventListener('change', (event) => {
        if (event.target.id !== 'jobs-page-size') return;
        state.pagination.pageSize = Number(event.target.value) || 50;
        state.pagination.page = 0;
        refreshJobs();
      });

      document.getElementById('jobs-refresh')?.addEventListener('click', () => {
        refreshJobs();
      });

      document.getElementById('jobs-recover')?.addEventListener('click', (event) => {
        recoverJobs(event.currentTarget);
      });

      document.getElementById('jobs-results')?.addEventListener('click', async (event) => {
        const button = event.target.closest('[data-action]');
        if (!button) {
          const row = event.target.closest('tr[data-job-id]');
          if (row?.dataset.jobId) openJobDetail(row.dataset.jobId, row.dataset.chainId);
          return;
        }

        const action = button.dataset.action;
        const jobId = button.dataset.jobId;

        if (action === 'page-prev') {
          state.pagination.page = Math.max(0, state.pagination.page - 1);
          refreshJobs();
          return;
        }
        if (action === 'page-next') {
          state.pagination.page += 1;
          refreshJobs();
          return;
        }

        if (!action || !jobId) return;

        if (action === 'view') {
          openJobDetail(jobId, button.dataset.chainId);
          return;
        }
        if (action === 'retry') {
          retryJob(jobId, button);
          return;
        }
        if (action === 'requeue') {
          requeueJob(jobId, button);
          return;
        }
        if (action === 'delete') {
          removeJob(jobId, button);
        }
      });

      attachSocketListener();
      state.wsUnsubs.push(WS.on('connected', attachSocketListener));
    },

    destroy() {
      if (state.refreshTimer) {
        clearTimeout(state.refreshTimer);
        state.refreshTimer = null;
      }
      if (state.searchTimer) {
        clearTimeout(state.searchTimer);
        state.searchTimer = null;
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

  App.register(JobsPage);
})();
