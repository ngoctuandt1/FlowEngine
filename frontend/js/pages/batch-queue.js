/**
 * Batch Queue Page
 * Bulk-submit many prompt-driven L1 jobs and track their progress live.
 */
(() => {
  const {
    JOB_TYPES,
    MODELS,
    DEFAULT_MODEL,
    IMAGE_MODELS,
    DEFAULT_IMAGE_MODEL,
    ASPECT_RATIOS,
    ASPECT_RATIOS_IMAGE,
    DEFAULT_ASPECT,
  } = CONST;

  // Keep this page scoped to prompt-only L1 flows.
  const SUPPORTED_BATCH_TYPES = new Set(['text-to-video', 'text-to-image']);
  const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled', 'submit_failed']);
  const QUEUE_LIMIT = 2000;
  const STUCK_RUNNING_IDLE_MS = 10 * 60 * 1000;
  const STUCK_PARENT_STATUSES = new Set(['failed', 'cancelled']);
  const QUEUE_STATUS_FILTERS = ['all', 'pending', 'claimed', 'running', 'completed', 'failed', 'cancelled'];
  const LOCAL_BADGES = {
    queued: {
      label: 'queued',
      style: 'background: rgba(148, 163, 184, 0.14); border-color: rgba(148, 163, 184, 0.28); color: #cbd5e1;',
    },
    submitting: {
      label: 'submitting',
      style: 'background: rgba(96, 165, 250, 0.14); border-color: rgba(96, 165, 250, 0.30); color: #bfdbfe;',
    },
    submit_failed: {
      label: 'submit failed',
      style: 'background: rgba(248, 113, 113, 0.14); border-color: rgba(248, 113, 113, 0.30); color: #fecaca;',
    },
  };
  const CONCURRENCY_LIMIT = 5;

  const state = {
    profiles: [],
    profilesLoadError: '',
    queueJobs: [],
    queueFilter: 'all',
    queueLoadError: '',
    queueLoading: false,
    queueDeleting: false,
    queueRefreshTimer: null,
    rows: [],
    nextRowNumber: 1,
    isSubmitting: false,
    renderFrame: null,
    wsUnsubs: [],
    socketListener: null,
    socketTarget: null,
    validation: {
      formError: '',
      rowErrors: [],
    },
    form: {
      inputText: '',
      fileName: '',
      treatAsCsv: false,
      type: 'text-to-video',
      model: DEFAULT_MODEL,
      aspect: DEFAULT_ASPECT,
      profile: '',
    },
  };

  function batchTypes() {
    return JOB_TYPES.filter((item) => SUPPORTED_BATCH_TYPES.has(item.id));
  }

  function modelOptionsFor(type) {
    return type === 'text-to-image' ? IMAGE_MODELS : MODELS;
  }

  function aspectOptionsFor(type) {
    return type === 'text-to-image' ? ASPECT_RATIOS_IMAGE : ASPECT_RATIOS;
  }

  function defaultModelFor(type) {
    return type === 'text-to-image' ? DEFAULT_IMAGE_MODEL : DEFAULT_MODEL;
  }

  function defaultAspectFor(type) {
    if (type === 'text-to-image') {
      const first = ASPECT_RATIOS_IMAGE.find((item) => item.value === DEFAULT_ASPECT);
      return first?.value || ASPECT_RATIOS_IMAGE[0]?.value || DEFAULT_ASPECT;
    }
    return DEFAULT_ASPECT;
  }

  function renderOptions(items, selected) {
    return items.map((item) => {
      const value = typeof item === 'string' ? item : item.value;
      const label = typeof item === 'string' ? item : item.label;
      const sel = value === selected ? ' selected' : '';
      return `<option value="${App.escapeHtml(value)}"${sel}>${App.escapeHtml(label)}</option>`;
    }).join('');
  }

  function normalizeProfiles(result) {
    const list = Array.isArray(result) ? result : result?.profiles || [];
    return list
      .map((profile) => profile?.name || profile?.profile_name || '')
      .filter(Boolean)
      .sort((a, b) => a.localeCompare(b));
  }

  function normalizeJobList(result) {
    return Array.isArray(result) ? result : result?.jobs || [];
  }

  function queueJobsById(jobs = state.queueJobs) {
    return jobs.reduce((byId, job) => {
      if (job?.id) byId[String(job.id)] = job;
      return byId;
    }, Object.create(null));
  }

  function jobTypeLabel(type) {
    const meta = JOB_TYPES.find((item) => item.id === type);
    return meta?.label || (type || 'Unknown').replace(/-/g, ' ');
  }

  function idleMinutesSince(value) {
    const timestamp = Date.parse(value || '');
    if (!Number.isFinite(timestamp)) return null;
    const idleMs = Date.now() - timestamp;
    if (idleMs <= STUCK_RUNNING_IDLE_MS) return null;
    return Math.max(0, Math.floor(idleMs / 60000));
  }

  function formatIdleMinutes(minutes) {
    if (minutes < 60) return `${minutes}m`;
    const hours = Math.floor(minutes / 60);
    const remainder = minutes % 60;
    return remainder ? `${hours}h ${remainder}m` : `${hours}h`;
  }

  function queueFilterLabel(value) {
    return value === 'all' ? 'All' : value.charAt(0).toUpperCase() + value.slice(1);
  }

  function stuckJobReason(job, byId) {
    if (!job) return '';

    const status = job.status || 'pending';
    if (status === 'pending' && job.parent_job_id) {
      const parent = byId?.[String(job.parent_job_id)];
      if (STUCK_PARENT_STATUSES.has(parent?.status)) return `Orphan: parent ${parent.status}`;
    }

    const level = Number(job.job_level || job.jobLevel || 1);
    if (status === 'pending' && job.profile == null && level >= 2) return 'Orphan: no profile pinned';

    if (status === 'running') {
      const idleMinutes = idleMinutesSince(job.updated_at || job.updatedAt);
      if (idleMinutes !== null) return `Stuck running: idle ${formatIdleMinutes(idleMinutes)}`;
    }
    return '';
  }

  function isJobStuck(job, byId) {
    return Boolean(stuckJobReason(job, byId));
  }

  function queueCounts() {
    const byId = queueJobsById();
    const counts = QUEUE_STATUS_FILTERS.reduce((acc, item) => ({ ...acc, [item]: 0 }), { stuck: 0 });
    counts.all = state.queueJobs.length;
    state.queueJobs.forEach((job) => {
      const status = job?.status || 'pending';
      if (counts[status] !== undefined) counts[status] += 1;
      if (isJobStuck(job, byId)) counts.stuck += 1;
    });
    return counts;
  }

  function visibleQueueJobs() {
    const byId = queueJobsById();
    if (state.queueFilter === 'stuck') return state.queueJobs.filter((job) => isJobStuck(job, byId));
    if (state.queueFilter === 'all') return state.queueJobs;
    return state.queueJobs.filter((job) => (job?.status || 'pending') === state.queueFilter);
  }

  function visibleStuckJobs() {
    const byId = queueJobsById();
    return visibleQueueJobs().filter((job) => job?.id && isJobStuck(job, byId));
  }

  function profileOptions(selected) {
    const options = ['<option value="">Auto (server picks)</option>'];
    state.profiles.forEach((name) => {
      const sel = selected === name ? ' selected' : '';
      options.push(`<option value="${App.escapeHtml(name)}"${sel}>${App.escapeHtml(name)}</option>`);
    });
    return options.join('');
  }

  function parseCsvLine(line) {
    const cols = [];
    let current = '';
    let inQuotes = false;

    for (let index = 0; index < line.length; index += 1) {
      const char = line[index];
      if (char === '"') {
        if (inQuotes && line[index + 1] === '"') {
          current += '"';
          index += 1;
        } else {
          inQuotes = !inQuotes;
        }
        continue;
      }
      if (char === ',' && !inQuotes) {
        cols.push(current.trim());
        current = '';
        continue;
      }
      current += char;
    }

    cols.push(current.trim());
    return cols;
  }

  function normalizeToken(value) {
    return String(value || '').trim().toLowerCase();
  }

  function canonicalProfileName(value, profileLookup = null) {
    const token = normalizeToken(value);
    if (!token) {
      return '';
    }
    if (token === 'auto') {
      return 'auto';
    }

    const lookup = profileLookup || new Map(
      state.profiles.map((name) => [normalizeToken(name), name])
    );
    return lookup.get(token) || null;
  }

  function looksLikeHeader(entry) {
    const prompt = normalizeToken(entry.prompt);
    const profile = normalizeToken(entry.profile);
    return ['prompt', 'text', 'description'].includes(prompt)
      && (!profile || ['profile', 'account', 'profile_name'].includes(profile));
  }

  function parseBatchInput(text, treatAsCsv = false) {
    const rawLines = String(text || '')
      .split(/\r?\n/)
      .map((line, index) => ({
        line: line.trim(),
        lineNumber: index + 1,
      }))
      .filter((entry) => entry.line);

    if (!rawLines.length) {
      return { mode: 'lines', rows: [] };
    }

    const parsedLines = rawLines.map(({ line, lineNumber }) => ({
      cols: parseCsvLine(line),
      lineNumber,
    }));
    const shouldUseCsv = treatAsCsv && rawLines.some(({ line }) => line.includes(','));

    const rows = shouldUseCsv
      ? parsedLines.map(({ cols, lineNumber }) => ({
        prompt: cols[0] || '',
        profile: cols[1] || '',
        columnCount: cols.length,
        lineNumber,
      }))
      : rawLines.map(({ line, lineNumber }) => ({
        prompt: line,
        profile: '',
        columnCount: 1,
        lineNumber,
      }));

    if (shouldUseCsv && rows.length && looksLikeHeader(rows[0])) {
      rows.shift();
    }

    return {
      mode: shouldUseCsv ? 'csv' : 'lines',
      rows: rows
        .map((entry) => ({
          prompt: String(entry.prompt || '').trim(),
          profile: String(entry.profile || '').trim(),
          columnCount: entry.columnCount,
          lineNumber: entry.lineNumber,
        }))
        .filter((entry) => entry.prompt),
    };
  }

  function collectDraft() {
    const parsed = parseBatchInput(state.form.inputText, state.form.treatAsCsv);
    return {
      mode: parsed.mode,
      rows: parsed.rows,
      profileOverrides: parsed.rows.filter((row) => row.profile).length,
    };
  }

  function effectiveProfile(rowProfile, globalProfile) {
    const explicit = String(rowProfile || '').trim();
    if (explicit) {
      return normalizeToken(explicit) === 'auto' ? '' : explicit;
    }
    return globalProfile || '';
  }

  function draftSummaryText() {
    const draft = collectDraft();
    if (!draft.rows.length) {
      return 'Paste one prompt per line, or enable Treat as CSV for prompt,profile rows.';
    }

    const parts = [
      `${draft.rows.length} row${draft.rows.length === 1 ? '' : 's'}`,
      draft.mode === 'csv' ? 'CSV mode' : state.form.treatAsCsv ? 'CSV toggle on, line mode' : 'line mode',
    ];
    if (draft.profileOverrides > 0) {
      parts.push(`${draft.profileOverrides} profile override${draft.profileOverrides === 1 ? '' : 's'}`);
    }
    return parts.join(' | ');
  }

  function renderProfilesWarning() {
    if (!state.profilesLoadError) {
      return '';
    }

    return `
      <div role="alert" style="margin-bottom:16px; padding:12px 14px; border-radius:12px; border:1px solid rgba(250, 204, 21, 0.35); background: rgba(113, 63, 18, 0.22); color:#fde68a; font-size:13px;">
        ${App.escapeHtml(state.profilesLoadError)}
      </div>
    `;
  }

  function renderValidationFeedback() {
    const messages = [];
    if (state.validation.formError) {
      messages.push(`<div style="font-weight:600;">${App.escapeHtml(state.validation.formError)}</div>`);
    }
    state.validation.rowErrors.forEach((issue) => {
      messages.push(`<div>Row ${App.escapeHtml(String(issue.lineNumber))}: ${App.escapeHtml(issue.message)}</div>`);
    });
    return messages.join('');
  }

  function countsSummary() {
    const counts = {
      total: state.rows.length,
      queued: 0,
      active: 0,
      completed: 0,
      failed: 0,
    };

    state.rows.forEach((row) => {
      if (row.status === 'queued') counts.queued += 1;
      if (row.status === 'submitting' || row.status === 'pending' || row.status === 'claimed' || row.status === 'running') {
        counts.active += 1;
      }
      if (row.status === 'completed') counts.completed += 1;
      if (row.status === 'failed' || row.status === 'submit_failed' || row.status === 'cancelled') {
        counts.failed += 1;
      }
    });

    return counts;
  }

  function countsText() {
    const counts = countsSummary();
    return `${counts.total} total | ${counts.active} active | ${counts.completed} completed | ${counts.failed} failed`;
  }

  function renderStatusBadge(status) {
    if (LOCAL_BADGES[status]) {
      return `<span class="badge" style="${LOCAL_BADGES[status].style}">${App.escapeHtml(LOCAL_BADGES[status].label)}</span>`;
    }
    return `<span class="${App.statusBadge(status || 'pending')}">${App.escapeHtml(status || 'pending')}</span>`;
  }

  function renderQueueFilterChips() {
    const counts = queueCounts();
    const chips = QUEUE_STATUS_FILTERS.map((filter) => {
      const active = state.queueFilter === filter;
      const style = active ? 'btn-primary' : 'btn-outline';
      return `
        <button class="btn btn-sm ${style}" data-queue-filter="${App.escapeHtml(filter)}">
          ${App.escapeHtml(queueFilterLabel(filter))} (${counts[filter] || 0})
        </button>
      `;
    });

    chips.push(`
      <button class="btn btn-sm ${state.queueFilter === 'stuck' ? 'btn-danger' : 'btn-outline'}" data-queue-filter="stuck">
        <span class="material-icons" style="font-size:16px">report_problem</span> Stuck (${counts.stuck || 0})
      </button>
    `);
    return chips.join('');
  }

  function renderQueueError() {
    return state.queueLoadError ? `
      <div role="alert" style="margin-bottom:16px; padding:12px 14px; border-radius:12px; border:1px solid rgba(248, 113, 113, 0.35); background: rgba(127, 29, 29, 0.18); color:#fecaca; font-size:13px;">
        ${App.escapeHtml(state.queueLoadError)}
      </div>
    ` : '';
  }

  function renderQueueTable() {
    if (state.queueLoading && !state.queueJobs.length) {
      return '<div class="loading-center"><div class="spinner spinner-lg"></div></div>';
    }

    const byId = queueJobsById();
    const jobs = visibleQueueJobs();
    if (!jobs.length) {
      const message = state.queueFilter === 'stuck'
        ? 'No stuck jobs found.'
        : 'No jobs match this queue filter.';
      return `
        <div class="empty-state">
          <span class="material-icons">queue</span>
          <h3>No matching jobs</h3>
          <p>${App.escapeHtml(message)}</p>
        </div>
      `;
    }

    const rows = jobs.map((job) => {
      const reason = stuckJobReason(job, byId);
      const jobId = String(job.id || '');
      const parentId = String(job.parent_job_id || '');
      const updatedAt = job.updated_at || job.updatedAt || '';
      return `
        <tr data-job-id="${App.escapeHtml(jobId)}" style="${reason ? 'background: rgba(248, 113, 113, 0.06);' : ''}">
          <td title="${App.escapeHtml(jobId)}"><code>${App.escapeHtml(App.truncate(jobId, 12))}</code></td>
          <td>
            <div style="display:grid; gap:4px; min-width:0;">
              <span>${App.escapeHtml(jobTypeLabel(job.type))}</span>
              ${job.prompt ? `<span title="${App.escapeHtml(job.prompt)}" style="font-size:12px; color: var(--text-muted);">${App.escapeHtml(App.truncate(job.prompt, 72))}</span>` : ''}
            </div>
          </td>
          <td>${App.escapeHtml(String(job.job_level || 1))}</td>
          <td title="${App.escapeHtml(parentId)}">${parentId ? `<code>${App.escapeHtml(App.truncate(parentId, 12))}</code>` : '<span style="color: var(--text-muted);">-</span>'}</td>
          <td title="${App.escapeHtml(job.profile || 'null')}">${App.escapeHtml(job.profile || 'null')}</td>
          <td>
            <div style="display:flex; flex-wrap:wrap; gap:6px; align-items:center;">
              ${renderStatusBadge(job.status)}
              ${reason ? '<span class="badge" style="background: rgba(248, 113, 113, 0.18); border-color: rgba(248, 113, 113, 0.42); color: #fecaca;">stuck</span>' : ''}
            </div>
          </td>
          <td title="${App.escapeHtml(updatedAt)}">${App.escapeHtml(App.formatTileDate ? App.formatTileDate(updatedAt) : App.formatDate(updatedAt))}</td>
          <td>${reason ? `<span style="color:#fecaca; font-size:12px;">${App.escapeHtml(reason)}</span>` : '<span style="color: var(--text-muted);">-</span>'}</td>
        </tr>
      `;
    }).join('');

    return `
      <div class="table-container">
        <table>
          <thead>
            <tr>
              <th>Job ID</th>
              <th>Type</th>
              <th>Level</th>
              <th>Parent</th>
              <th>Profile</th>
              <th>Status</th>
              <th>Updated</th>
              <th>Stuck Reason</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }

  function renderQueueCard() {
    const visibleCount = visibleQueueJobs().length;
    const visibleStuckCount = visibleStuckJobs().length;
    return `
      <div class="card" id="batch-queue-global-card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Global Queue</h3>
            <p id="batch-queue-global-counts" style="margin-top:4px; color: var(--text-muted); font-size: 13px;">
              Showing ${visibleCount} of ${state.queueJobs.length} job${state.queueJobs.length === 1 ? '' : 's'}.
            </p>
          </div>
          <div class="section-actions">
            <button class="btn btn-sm btn-danger" id="batch-queue-delete-stuck" ${visibleStuckCount && !state.queueDeleting ? '' : 'disabled'}>
              ${state.queueDeleting ? '<span class="spinner"></span> Deleting...' : `<span class="material-icons" style="font-size:16px">delete_sweep</span> Delete ${visibleStuckCount} stuck job${visibleStuckCount === 1 ? '' : 's'}`}
            </button>
            <button class="btn btn-sm btn-outline" id="batch-queue-refresh" ${state.queueLoading ? 'disabled' : ''}>
              ${state.queueLoading ? '<span class="spinner"></span> Refreshing...' : '<span class="material-icons" style="font-size:16px">refresh</span> Refresh'}
            </button>
          </div>
        </div>
        <div id="batch-queue-global-error">${renderQueueError()}</div>
        <div id="batch-queue-filter-chips" style="display:flex; gap:8px; flex-wrap:wrap; margin-bottom:16px;">
          ${renderQueueFilterChips()}
        </div>
        <div id="batch-queue-global-results">
          ${renderQueueTable()}
        </div>
      </div>
    `;
  }

  function buildDownloadUrl(path) {
    const normalized = String(path || '')
      .replace(/\\/g, '/')
      .replace(/^downloads\//i, '');
    return `/downloads/${encodeURI(normalized)}`;
  }

  function buildEditUrl(projectUrl, mediaId, editUrl) {
    if (editUrl) return editUrl;
    if (!projectUrl || !mediaId) return '';
    return `${String(projectUrl).replace(/\/$/, '')}/edit/${mediaId}`;
  }

  function renderOutputCell(row) {
    if (Array.isArray(row.outputFiles) && row.outputFiles.length > 0) {
      return row.outputFiles.map((file, index) => {
        const fileName = String(file).replace(/\\/g, '/').split('/').pop() || `output-${index + 1}`;
        return `
          <a href="${App.escapeHtml(buildDownloadUrl(file))}" target="_blank" rel="noopener" class="btn btn-sm btn-outline" style="margin-right:6px; margin-bottom:6px;">
            <span class="material-icons" style="font-size:16px">open_in_new</span> ${App.escapeHtml(fileName)}
          </a>
        `;
      }).join('');
    }

    const editUrl = buildEditUrl(row.projectUrl, row.mediaId, row.editUrl);
    if (editUrl) {
      return `
        <a href="${App.escapeHtml(editUrl)}" target="_blank" rel="noopener" class="btn btn-sm btn-outline">
          <span class="material-icons" style="font-size:16px">link</span> Open project
        </a>
      `;
    }

    if (row.error) {
      return `<span style="color: var(--text-muted); font-size: 12px;">${App.escapeHtml(App.truncate(row.error, 90))}</span>`;
    }

    return '<span style="color: var(--text-muted);">-</span>';
  }

  function renderRowsTable() {
    if (!state.rows.length) {
      return `
        <div class="empty-state">
          <span class="material-icons">queue</span>
          <h3>No queued jobs yet</h3>
          <p>Submit a batch above to start tracking row-by-row progress.</p>
        </div>
      `;
    }

    const rows = state.rows.map((row) => `
      <tr data-row-key="${App.escapeHtml(row.key)}">
        <td><code>${App.escapeHtml(String(row.order))}</code></td>
        <td title="${App.escapeHtml(row.jobId || '')}">
          ${row.jobId ? `<code>${App.escapeHtml(App.truncate(row.jobId, 12))}</code>` : '<span style="color: var(--text-muted);">pending</span>'}
        </td>
        <td title="${App.escapeHtml(row.prompt)}">
          <div style="display:grid; gap:4px; min-width:0;">
            <span>${App.escapeHtml(App.truncate(row.prompt, 96))}</span>
            <span style="font-size:12px; color: var(--text-muted);">${App.escapeHtml(row.typeLabel)}</span>
          </div>
        </td>
        <td title="${App.escapeHtml(row.profile || 'auto')}">${App.escapeHtml(row.profile || 'auto')}</td>
        <td>${renderStatusBadge(row.status)}</td>
        <td>${renderOutputCell(row)}</td>
      </tr>
    `).join('');

    return `
      <div class="table-container">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Job ID</th>
              <th>Prompt</th>
              <th>Profile</th>
              <th>Status</th>
              <th>Output</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }

  function renderPage() {
    const typeOptions = batchTypes().map((item) => `
      <option value="${App.escapeHtml(item.id)}" ${item.id === state.form.type ? 'selected' : ''}>
        ${App.escapeHtml(item.label)}
      </option>
    `).join('');

    return `
      <div id="batch-queue-root" style="display:grid; gap:16px;">
        ${renderQueueCard()}

        <div class="card">
          <div class="section-header">
            <div>
              <h3 class="section-title">Batch Queue</h3>
              <p style="margin-top:4px; color: var(--text-muted); font-size: 13px;">
                Submit prompt lists as individual jobs with a capped concurrency of ${CONCURRENCY_LIMIT} requests.
              </p>
            </div>
          </div>

          ${renderProfilesWarning()}

          <div class="form-group">
            <label class="form-label">Prompts or CSV <span class="required">*</span></label>
            <textarea class="form-textarea" id="batch-queue-input" rows="10"
              placeholder="One prompt per line&#10;or CSV rows: prompt,profile">${App.escapeHtml(state.form.inputText)}</textarea>
            <span class="form-hint" id="batch-queue-summary">${App.escapeHtml(draftSummaryText())}</span>
            <div id="batch-queue-validation" style="margin-top:12px; padding:12px 14px; border-radius:12px; border:1px solid rgba(248, 113, 113, 0.35); background: rgba(127, 29, 29, 0.18); color:#fecaca; font-size:13px; gap:6px; ${state.validation.formError || state.validation.rowErrors.length ? 'display:grid;' : 'display:none;'}">
              ${renderValidationFeedback()}
            </div>
          </div>

          <div class="form-row">
            <div class="form-group">
              <label class="form-label">CSV Upload</label>
              <input type="file" class="form-input" id="batch-queue-file" accept=".csv,.txt,text/csv">
              <span class="form-hint" id="batch-queue-file-label">${App.escapeHtml(state.form.fileName || 'Optional. Upload a text or CSV file and it will populate the textarea above.')}</span>
            </div>
            <div class="form-group">
              <label class="form-label">Type</label>
              <select class="form-select" id="batch-queue-type">${typeOptions}</select>
            </div>
          </div>

          <div class="form-group" style="margin-top:-4px;">
            <label style="display:inline-flex; align-items:center; gap:10px; cursor:pointer;">
              <input type="checkbox" id="batch-queue-csv-toggle" ${state.form.treatAsCsv ? 'checked' : ''}>
              <span class="form-label" style="margin:0;">Treat as CSV</span>
            </label>
            <span class="form-hint">Off by default. Enable this only for prompt,profile rows.</span>
          </div>

          <div class="form-row">
            <div class="form-group">
              <label class="form-label">Model</label>
              <select class="form-select" id="batch-queue-model">
                ${renderOptions(modelOptionsFor(state.form.type), state.form.model)}
              </select>
            </div>
            <div class="form-group">
              <label class="form-label">Aspect Ratio</label>
              <select class="form-select" id="batch-queue-aspect">
                ${renderOptions(aspectOptionsFor(state.form.type), state.form.aspect)}
              </select>
            </div>
          </div>

          <div class="form-group">
            <label class="form-label">Profile</label>
            <select class="form-select" id="batch-queue-profile">
              ${profileOptions(state.form.profile)}
            </select>
            <span class="form-hint">Row-level CSV profiles override this selection. Leave on auto to let the server pick per job.</span>
          </div>

          <div style="display:flex; gap:12px; flex-wrap:wrap; margin-top:24px;">
            <button class="btn btn-primary" id="batch-queue-submit">
              ${state.isSubmitting ? '<span class="spinner"></span> Submitting...' : '<span class="material-icons">send</span> Submit Batch'}
            </button>
            <button class="btn btn-outline" id="batch-queue-clear-completed">
              <span class="material-icons">done_all</span> Clear completed
            </button>
          </div>
        </div>

        <div class="card">
          <div class="section-header">
            <div>
              <h3 class="section-title">Progress</h3>
              <p id="batch-queue-counts" style="margin-top:4px; color: var(--text-muted); font-size: 13px;">
                ${App.escapeHtml(countsText())}
              </p>
            </div>
          </div>
          <div id="batch-queue-results">
            ${renderRowsTable()}
          </div>
        </div>
      </div>
    `;
  }

  async function fetchProfiles() {
    try {
      state.profiles = normalizeProfiles(await API.profiles.list());
      state.profilesLoadError = '';
    } catch (err) {
      console.warn('[BatchQueue] could not load profiles:', err.message);
      state.profiles = [];
      state.profilesLoadError = 'Could not load profile list from /api/profiles. Named CSV profile overrides will be rejected until the list is available.';
    }
  }

  async function refreshQueueJobs(options = {}) {
    state.queueLoading = true;
    syncQueueControls();

    try {
      state.queueJobs = normalizeJobList(await API.jobs.list({ limit: QUEUE_LIMIT }));
      state.queueLoadError = '';
    } catch (err) {
      state.queueLoadError = `Failed to load global queue: ${err.message}`;
      if (!options.silent) {
        App.toast(state.queueLoadError, 'error');
      }
    } finally {
      state.queueLoading = false;
      syncQueueControls();
    }
  }

  async function render() {
    await Promise.all([
      fetchProfiles(),
      refreshQueueJobs({ silent: true }),
    ]);
    return renderPage();
  }

  function syncDraftSummary() {
    const summary = document.getElementById('batch-queue-summary');
    if (summary) {
      summary.textContent = draftSummaryText();
    }
  }

  function syncValidationFeedback() {
    const validation = document.getElementById('batch-queue-validation');
    if (!validation) {
      return;
    }

    const hasIssues = Boolean(state.validation.formError) || state.validation.rowErrors.length > 0;
    validation.style.display = hasIssues ? 'grid' : 'none';
    validation.innerHTML = hasIssues ? renderValidationFeedback() : '';
  }

  function clearValidationState() {
    state.validation.formError = '';
    state.validation.rowErrors = [];
    syncValidationFeedback();
  }

  function syncTypeDependentFields() {
    const modelSelect = document.getElementById('batch-queue-model');
    const aspectSelect = document.getElementById('batch-queue-aspect');
    if (modelSelect) {
      modelSelect.innerHTML = renderOptions(modelOptionsFor(state.form.type), state.form.model);
    }
    if (aspectSelect) {
      aspectSelect.innerHTML = renderOptions(aspectOptionsFor(state.form.type), state.form.aspect);
    }
  }

  function syncControls() {
    const submitButton = document.getElementById('batch-queue-submit');
    if (submitButton) {
      submitButton.disabled = state.isSubmitting;
      submitButton.innerHTML = state.isSubmitting
        ? '<span class="spinner"></span> Submitting...'
        : '<span class="material-icons">send</span> Submit Batch';
    }

    const clearButton = document.getElementById('batch-queue-clear-completed');
    if (clearButton) {
      clearButton.disabled = !state.rows.some((row) => TERMINAL_STATUSES.has(row.status));
    }

    const countsEl = document.getElementById('batch-queue-counts');
    if (countsEl) {
      countsEl.textContent = countsText();
    }

    const fileLabel = document.getElementById('batch-queue-file-label');
    if (fileLabel) {
      fileLabel.textContent = state.form.fileName || 'Optional. Upload a text or CSV file and it will populate the textarea above.';
    }
  }

  function syncQueueControls() {
    const card = document.getElementById('batch-queue-global-card');
    if (card) card.outerHTML = renderQueueCard();
  }

  function scheduleRowsRender() {
    if (state.renderFrame !== null) return;
    state.renderFrame = requestAnimationFrame(() => {
      state.renderFrame = null;
      const container = document.getElementById('batch-queue-results');
      if (container) {
        container.innerHTML = renderRowsTable();
      }
      syncControls();
    });
  }

  function setRowState(rowKey, patch) {
    const row = state.rows.find((item) => item.key === rowKey);
    if (!row) return;
    Object.assign(row, patch);
    scheduleRowsRender();
  }

  function applyJobUpdate(job) {
    if (!job?.id) return;
    const row = state.rows.find((item) => item.jobId === job.id);
    if (!row) return;

    row.status = job.status || row.status;
    row.profile = job.profile || row.profile;
    row.projectUrl = job.project_url ?? row.projectUrl;
    row.mediaId = job.media_id ?? row.mediaId;
    row.editUrl = buildEditUrl(job.project_url, job.media_id, job.edit_url) || row.editUrl;
    row.outputFiles = Array.isArray(job.output_files) ? job.output_files : row.outputFiles;
    row.error = job.error || row.error;
    scheduleRowsRender();
  }

  function scheduleQueueRefresh() {
    if (App.currentPage !== 'batch-queue') return;
    if (state.queueRefreshTimer) clearTimeout(state.queueRefreshTimer);
    state.queueRefreshTimer = setTimeout(() => {
      state.queueRefreshTimer = null;
      refreshQueueJobs({ silent: true });
    }, 250);
  }

  function handleSocketMessage(event) {
    try {
      const message = JSON.parse(event.data);
      const eventName = message.event || message.type;
      const payload = message.data || message.payload;
      if (eventName === 'job_update' && payload?.id) {
        applyJobUpdate(payload);
        scheduleQueueRefresh();
      }
    } catch (_) {
      // Ignore malformed messages from unrelated producers.
    }
  }

  async function deleteQueueJob(jobId) {
    if (typeof API.jobs?.delete === 'function') {
      return API.jobs.delete(jobId);
    }

    const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE' });
    if (!response.ok) {
      let message = `HTTP ${response.status}`;
      try {
        const errorData = await response.json();
        message = errorData.detail || errorData.message || message;
      } catch (_) {
        // response was not JSON
      }
      throw new Error(message);
    }
    return response.status === 204 ? null : response.json();
  }

  function updateProgressToast(toast, message) {
    const label = toast?.querySelector?.('.toast-message');
    if (label) {
      label.textContent = message;
    }
  }

  async function deleteVisibleStuckJobs() {
    if (state.queueDeleting) return;

    const jobs = visibleStuckJobs();
    if (!jobs.length) {
      App.toast('No visible stuck jobs to delete', 'info');
      return;
    }

    const message = `Delete ${jobs.length} visible stuck job${jobs.length === 1 ? '' : 's'}? This cannot be undone.`;
    if (!confirm(message)) return;

    state.queueDeleting = true;
    syncQueueControls();

    const toast = App.toast(`Deleting 0/${jobs.length} stuck jobs...`, 'info', 0);
    let deleted = 0;
    let failed = 0;

    for (const job of jobs) {
      try {
        await deleteQueueJob(job.id);
        deleted += 1;
      } catch (err) {
        failed += 1;
        console.warn('[BatchQueue] failed to delete stuck job:', job.id, err.message);
      }
      updateProgressToast(toast, `Deleting ${deleted + failed}/${jobs.length} stuck jobs...`);
    }

    state.queueDeleting = false;
    App.dismissToast(toast);
    App.toast(
      `Deleted ${deleted}/${jobs.length} stuck job${jobs.length === 1 ? '' : 's'}${failed ? `, ${failed} failed` : ''}`,
      failed ? 'warning' : 'success'
    );
    await refreshQueueJobs({ silent: true });
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

  function validateSubmission(draft) {
    if (!SUPPORTED_BATCH_TYPES.has(state.form.type)) {
      return {
        error: 'Batch type must be text-to-video or text-to-image.',
        rowErrors: [],
        rows: draft.rows,
      };
    }

    if (!draft.rows.length) {
      return {
        error: 'Enter at least one prompt or upload a CSV.',
        rowErrors: [],
        rows: draft.rows,
      };
    }

    const profileLookup = new Map(state.profiles.map((name) => [normalizeToken(name), name]));
    const normalizedRows = draft.rows.map((row) => ({ ...row }));
    const rowErrors = [];

    if (draft.mode === 'csv') {
      normalizedRows.forEach((row) => {
        if (row.columnCount > 2) {
          rowErrors.push({
            lineNumber: row.lineNumber,
            message: 'CSV rows support only prompt,profile columns.',
          });
          return;
        }

        if (!row.profile) {
          return;
        }

        const resolvedProfile = canonicalProfileName(row.profile, profileLookup);
        if (resolvedProfile === null) {
          rowErrors.push({
            lineNumber: row.lineNumber,
            message: `Unknown profile "${row.profile}".`,
          });
          return;
        }

        row.profile = resolvedProfile;
      });
    }

    return {
      error: rowErrors.length ? 'Fix the CSV profile errors before submitting.' : null,
      rowErrors,
      rows: normalizedRows,
    };
  }

  function createPendingRows(entries, config) {
    const typeMeta = JOB_TYPES.find((item) => item.id === config.type);
    return entries.map((entry) => ({
      key: `batch-row-${Date.now()}-${state.nextRowNumber}`,
      order: state.nextRowNumber++,
      type: config.type,
      typeLabel: typeMeta?.label || config.type,
      prompt: entry.prompt,
      profile: effectiveProfile(entry.profile, config.profile),
      jobId: '',
      status: 'queued',
      outputFiles: [],
      projectUrl: '',
      mediaId: '',
      editUrl: '',
      error: '',
    }));
  }

  async function submitBatch() {
    if (state.isSubmitting) return;

    const draft = collectDraft();
    const validation = validateSubmission(draft);
    state.validation.formError = validation.error || '';
    state.validation.rowErrors = validation.rowErrors;
    syncValidationFeedback();

    if (validation.error) {
      App.toast(validation.error, 'warning');
      return;
    }
    draft.rows = validation.rows;

    const batchConfig = {
      type: state.form.type,
      model: state.form.model,
      aspect: state.form.aspect,
      profile: state.form.profile,
    };

    const pendingRows = createPendingRows(draft.rows, batchConfig);
    state.rows.push(...pendingRows);
    state.isSubmitting = true;
    scheduleRowsRender();

    const workers = [];
    let nextIndex = 0;
    let successCount = 0;
    let failureCount = 0;

    const worker = async () => {
      while (nextIndex < pendingRows.length) {
        const row = pendingRows[nextIndex];
        nextIndex += 1;

        setRowState(row.key, { status: 'submitting', error: '' });

        const payload = {
          type: batchConfig.type,
          prompt: row.prompt,
          model: batchConfig.model,
          aspect_ratio: batchConfig.aspect,
        };
        if (row.profile) {
          payload.profile = row.profile;
        }

        try {
          const created = await API.jobs.create(payload);
          successCount += 1;
          setRowState(row.key, {
            jobId: created?.id || created?.job_id || '',
            status: created?.status || 'pending',
            profile: created?.profile || row.profile,
            projectUrl: created?.project_url || '',
            mediaId: created?.media_id || '',
            editUrl: buildEditUrl(created?.project_url, created?.media_id, created?.edit_url),
            outputFiles: Array.isArray(created?.output_files) ? created.output_files : [],
            error: created?.error || '',
          });
        } catch (err) {
          failureCount += 1;
          setRowState(row.key, {
            status: 'submit_failed',
            error: err.message,
          });
        }
      }
    };

    for (let index = 0; index < Math.min(CONCURRENCY_LIMIT, pendingRows.length); index += 1) {
      workers.push(worker());
    }

    try {
      await Promise.all(workers);
      App.toast(
        `Batch submitted: ${successCount} created, ${failureCount} failed`,
        failureCount === 0 ? 'success' : successCount === 0 ? 'error' : 'warning'
      );
    } finally {
      state.isSubmitting = false;
      scheduleRowsRender();
    }
  }

  function clearCompleted() {
    const before = state.rows.length;
    state.rows = state.rows.filter((row) => !TERMINAL_STATUSES.has(row.status));
    const removed = before - state.rows.length;
    if (removed > 0) {
      App.toast(`Removed ${removed} finished row${removed === 1 ? '' : 's'}`, 'info');
      scheduleRowsRender();
    }
  }

  async function loadCsvFile(file) {
    const text = await file.text();
    state.form.inputText = text;
    state.form.fileName = file.name || '';
    const textarea = document.getElementById('batch-queue-input');
    if (textarea) {
      textarea.value = text;
    }
    clearValidationState();
    syncDraftSummary();
    syncControls();
  }

  function mount() {
    document.getElementById('batch-queue-input')?.addEventListener('input', (event) => {
      state.form.inputText = event.target.value;
      state.form.fileName = '';
      clearValidationState();
      syncDraftSummary();
      syncControls();
    });

    document.getElementById('batch-queue-file')?.addEventListener('change', async (event) => {
      const file = event.target.files?.[0];
      if (!file) return;
      try {
        await loadCsvFile(file);
        App.toast(`Loaded ${file.name}`, 'success');
      } catch (err) {
        App.toast(`Failed to read file: ${err.message}`, 'error');
      } finally {
        event.target.value = '';
      }
    });

    document.getElementById('batch-queue-csv-toggle')?.addEventListener('change', (event) => {
      state.form.treatAsCsv = event.target.checked;
      clearValidationState();
      syncDraftSummary();
    });

    document.getElementById('batch-queue-type')?.addEventListener('change', (event) => {
      state.form.type = event.target.value;
      clearValidationState();

      const modelValues = new Set(modelOptionsFor(state.form.type).map((item) => item.value));
      if (!modelValues.has(state.form.model)) {
        state.form.model = defaultModelFor(state.form.type);
      }

      const aspectValues = new Set(aspectOptionsFor(state.form.type).map((item) => item.value));
      if (!aspectValues.has(state.form.aspect)) {
        state.form.aspect = defaultAspectFor(state.form.type);
      }

      syncTypeDependentFields();
    });

    document.getElementById('batch-queue-model')?.addEventListener('change', (event) => {
      state.form.model = event.target.value;
    });

    document.getElementById('batch-queue-aspect')?.addEventListener('change', (event) => {
      state.form.aspect = event.target.value;
    });

    document.getElementById('batch-queue-profile')?.addEventListener('change', (event) => {
      state.form.profile = event.target.value;
    });

    document.getElementById('batch-queue-submit')?.addEventListener('click', () => {
      submitBatch();
    });

    document.getElementById('batch-queue-clear-completed')?.addEventListener('click', () => {
      clearCompleted();
    });

    document.getElementById('batch-queue-root')?.addEventListener('click', (event) => {
      const chip = event.target.closest('[data-queue-filter]');
      if (chip) {
        const nextFilter = chip.dataset.queueFilter || 'all';
        state.queueFilter = state.queueFilter === nextFilter ? 'all' : nextFilter;
        syncQueueControls();
        return;
      }
      if (event.target.closest('#batch-queue-refresh')) refreshQueueJobs();
      if (event.target.closest('#batch-queue-delete-stuck')) deleteVisibleStuckJobs();
    });

    attachSocketListener();
    state.wsUnsubs.push(WS.on('connected', attachSocketListener));
    syncValidationFeedback();
    syncControls();
  }

  function destroy() {
    if (state.renderFrame !== null) {
      cancelAnimationFrame(state.renderFrame);
      state.renderFrame = null;
    }
    if (state.queueRefreshTimer) {
      clearTimeout(state.queueRefreshTimer);
      state.queueRefreshTimer = null;
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
  }

  const BatchQueuePage = {
    name: 'batch-queue',
    title: 'Batch Queue',
    render,
    mount,
    destroy,
  };

  App.register(BatchQueuePage);
})();
