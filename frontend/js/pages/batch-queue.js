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
  const SUPPORTED_BATCH_TYPES = new Set(['text-to-video', 'frames-to-video', 'text-to-image']);
  const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled']);
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
    rows: [],
    nextRowNumber: 1,
    isSubmitting: false,
    renderFrame: null,
    wsUnsubs: [],
    socketListener: null,
    socketTarget: null,
    form: {
      inputText: '',
      inputSource: 'textarea',
      fileName: '',
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

  function looksLikeHeader(entry) {
    const prompt = normalizeToken(entry.prompt);
    const profile = normalizeToken(entry.profile);
    return ['prompt', 'text', 'description'].includes(prompt)
      && (!profile || ['profile', 'account', 'profile_name'].includes(profile));
  }

  function parseBatchInput(text, source = 'textarea') {
    const rawLines = String(text || '')
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);

    if (!rawLines.length) {
      return { mode: 'lines', rows: [] };
    }

    const parsedLines = rawLines.map((line) => parseCsvLine(line));
    const csvLikeCount = parsedLines.filter((cols) => cols.length > 1).length;
    const hasTooManyColumns = parsedLines.some((cols) => cols.length > 2);
    const firstEntry = {
      prompt: parsedLines[0]?.[0] || '',
      profile: parsedLines[0]?.[1] || '',
    };
    const knownProfiles = new Set(state.profiles.map((name) => normalizeToken(name)));
    const csvSecondColumnLooksValid = parsedLines.every((cols) => {
      if (cols.length <= 1) return true;
      const second = normalizeToken(cols[1]);
      return !second || second === 'auto' || knownProfiles.has(second);
    });
    const shouldUseCsv = csvLikeCount > 0
      && !hasTooManyColumns
      && (
        source === 'file'
        || looksLikeHeader(firstEntry)
        || (csvSecondColumnLooksValid && csvLikeCount >= Math.ceil(parsedLines.length / 2))
      );

    const rows = shouldUseCsv
      ? parsedLines.map((cols) => ({
        prompt: cols[0] || '',
        profile: cols[1] || '',
      }))
      : rawLines.map((line) => ({ prompt: line, profile: '' }));

    if (rows.length && looksLikeHeader(rows[0])) {
      rows.shift();
    }

    return {
      mode: shouldUseCsv ? 'csv' : 'lines',
      rows: rows
        .map((entry) => ({
          prompt: String(entry.prompt || '').trim(),
          profile: String(entry.profile || '').trim(),
        }))
        .filter((entry) => entry.prompt),
    };
  }

  function collectDraft() {
    const parsed = parseBatchInput(state.form.inputText, state.form.inputSource);
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
      return 'Paste one prompt per line, or upload a CSV where column 1 is prompt and column 2 is profile.';
    }

    const parts = [
      `${draft.rows.length} row${draft.rows.length === 1 ? '' : 's'}`,
      draft.mode === 'csv' ? 'CSV detected' : 'line mode',
    ];
    if (draft.profileOverrides > 0) {
      parts.push(`${draft.profileOverrides} profile override${draft.profileOverrides === 1 ? '' : 's'}`);
    }
    return parts.join(' | ');
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
      <div style="display:grid; gap:16px;">
        <div class="card">
          <div class="section-header">
            <div>
              <h3 class="section-title">Batch Queue</h3>
              <p style="margin-top:4px; color: var(--text-muted); font-size: 13px;">
                Submit prompt lists as individual jobs with a capped concurrency of ${CONCURRENCY_LIMIT} requests.
              </p>
            </div>
          </div>

          <div class="form-group">
            <label class="form-label">Prompts or CSV <span class="required">*</span></label>
            <textarea class="form-textarea" id="batch-queue-input" rows="10"
              placeholder="One prompt per line&#10;or CSV rows: prompt,profile">${App.escapeHtml(state.form.inputText)}</textarea>
            <span class="form-hint" id="batch-queue-summary">${App.escapeHtml(draftSummaryText())}</span>
          </div>

          <div class="form-row">
            <div class="form-group">
              <label class="form-label">CSV Upload</label>
              <input type="file" class="form-input" id="batch-queue-file" accept=".csv,.txt,text/csv">
              <span class="form-hint" id="batch-queue-file-label">${App.escapeHtml(state.form.fileName || 'Optional. Upload a CSV and it will populate the textarea above.')}</span>
            </div>
            <div class="form-group">
              <label class="form-label">Type</label>
              <select class="form-select" id="batch-queue-type">${typeOptions}</select>
            </div>
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
    } catch (err) {
      console.warn('[BatchQueue] could not load profiles:', err.message);
      state.profiles = [];
    }
  }

  async function render() {
    await fetchProfiles();
    return renderPage();
  }

  function syncDraftSummary() {
    const summary = document.getElementById('batch-queue-summary');
    if (summary) {
      summary.textContent = draftSummaryText();
    }
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
      fileLabel.textContent = state.form.fileName || 'Optional. Upload a CSV and it will populate the textarea above.';
    }
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

  function handleSocketMessage(event) {
    try {
      const message = JSON.parse(event.data);
      const eventName = message.event || message.type;
      const payload = message.data || message.payload;
      if (eventName === 'job_update' && payload?.id) {
        applyJobUpdate(payload);
      }
    } catch (_) {
      // Ignore malformed messages from unrelated producers.
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

  function validateSubmission(draft) {
    if (!draft.rows.length) {
      return 'Enter at least one prompt or upload a CSV.';
    }
    return null;
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
    const error = validateSubmission(draft);
    if (error) {
      App.toast(error, 'warning');
      return;
    }

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
    state.form.inputSource = 'file';
    state.form.fileName = file.name || '';
    const textarea = document.getElementById('batch-queue-input');
    if (textarea) {
      textarea.value = text;
    }
    syncDraftSummary();
    syncControls();
  }

  function mount() {
    document.getElementById('batch-queue-input')?.addEventListener('input', (event) => {
      state.form.inputText = event.target.value;
      state.form.inputSource = 'textarea';
      state.form.fileName = '';
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

    document.getElementById('batch-queue-type')?.addEventListener('change', (event) => {
      state.form.type = event.target.value;

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

    attachSocketListener();
    state.wsUnsubs.push(WS.on('connected', attachSocketListener));
    syncControls();
  }

  function destroy() {
    if (state.renderFrame !== null) {
      cancelAnimationFrame(state.renderFrame);
      state.renderFrame = null;
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
