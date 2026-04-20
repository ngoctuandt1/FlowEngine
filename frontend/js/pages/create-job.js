/**
 * Create Job Page.
 *
 * Two modes:
 *   - single : one job, full form (type, prompt, parent, bbox, camera, etc.)
 *   - batch  : many t2v jobs at once — paste one prompt per line. Each
 *              line becomes an independent L1 job pinned to the selected
 *              profile, with shared model + aspect ratio.
 *
 * Backend contract lives in server/models/job.py :: JobCreate. Type ids,
 * field names, and bbox shape (normalized 0-1, keys x/y/w/h) mirror the
 * Pydantic model exactly — see CONST in constants.js.
 */
(() => {
  const {
    JOB_TYPES, MODELS, DEFAULT_MODEL, ASPECT_RATIOS, DEFAULT_ASPECT,
    CAMERA_PRESETS,
    TYPES_WITH_PROMPT, TYPES_WITH_BBOX, TYPES_WITH_MODEL, TYPES_WITH_ASPECT,
  } = CONST;

  let mode = 'single';               // 'single' | 'batch'
  let selectedType = 'text-to-video';
  let profiles = [];                 // populated on mount

  // ---- helpers --------------------------------------------------------------

  function renderModeTabs() {
    return `
      <div class="tabs" id="mode-tabs" style="display:flex; gap:8px; margin-bottom:20px;">
        <button class="btn ${mode === 'single' ? 'btn-primary' : 'btn-outline'} btn-sm"
                data-mode="single">Single job</button>
        <button class="btn ${mode === 'batch' ? 'btn-primary' : 'btn-outline'} btn-sm"
                data-mode="batch">Batch (many prompts)</button>
      </div>
    `;
  }

  function renderTypeSelector() {
    return `
      <div class="type-selector" id="type-selector">
        ${JOB_TYPES.map((t) => `
          <div class="type-option ${t.id === selectedType ? 'selected' : ''}" data-type="${t.id}">
            <span class="material-icons">${t.icon}</span>
            <div class="type-option-label">${t.shortLabel}</div>
          </div>
        `).join('')}
      </div>
    `;
  }

  function renderProfileSelect(id, { required = false, includeDefault = true } = {}) {
    const defaultOpt = includeDefault
      ? `<option value="">(any available)</option>`
      : '';
    const opts = profiles
      .map((p) => `<option value="${App.escapeHtml(p.name)}">${App.escapeHtml(p.name)}</option>`)
      .join('');
    return `
      <select class="form-select" id="${id}" ${required ? 'required' : ''}>
        ${defaultOpt}${opts}
      </select>
    `;
  }

  function renderOptions(items, { selected } = {}) {
    return items
      .map((o) => {
        const v = typeof o === 'string' ? o : o.value;
        const l = typeof o === 'string' ? o : o.label;
        const sel = v === selected ? ' selected' : '';
        return `<option value="${App.escapeHtml(v)}"${sel}>${App.escapeHtml(l)}</option>`;
      })
      .join('');
  }

  // ---- single-mode dynamic fields ------------------------------------------

  function renderFields() {
    const fields = [];

    if (TYPES_WITH_PROMPT.has(selectedType)) {
      const required = selectedType === 'text-to-video' || selectedType === 'insert-object';
      fields.push(`
        <div class="form-group">
          <label class="form-label">Prompt ${required ? '<span class="required">*</span>' : '(optional)'}</label>
          <textarea class="form-textarea" id="field-prompt" rows="4"
                    placeholder="Describe the video you want..." ${required ? 'required' : ''}></textarea>
        </div>
      `);
    }

    if (selectedType !== 'text-to-video') {
      fields.push(`
        <div class="form-row">
          <div class="form-group">
            <label class="form-label">Parent Job ID</label>
            <input type="text" class="form-input" id="field-parent-job" placeholder="e.g. 4a032d83-...">
            <span class="form-hint">Inherits profile + project_url + media_id from a completed job.</span>
          </div>
          <div class="form-group">
            <label class="form-label">Project URL (override)</label>
            <input type="text" class="form-input" id="field-project-url"
                   placeholder="https://labs.google/fx/tools/flow/project/...">
            <span class="form-hint">Optional. Only used if no parent job is given.</span>
          </div>
        </div>
      `);
    }

    const showModel = TYPES_WITH_MODEL.has(selectedType);
    const showAspect = TYPES_WITH_ASPECT.has(selectedType);
    if (showModel || showAspect) {
      fields.push(`
        <div class="form-row">
          ${showModel ? `
          <div class="form-group">
            <label class="form-label">Model</label>
            <select class="form-select" id="field-model">${renderOptions(MODELS, { selected: DEFAULT_MODEL })}</select>
          </div>` : ''}
          ${showAspect ? `
          <div class="form-group">
            <label class="form-label">Aspect Ratio</label>
            <select class="form-select" id="field-aspect-ratio">${renderOptions(ASPECT_RATIOS, { selected: DEFAULT_ASPECT })}</select>
          </div>` : ''}
        </div>
      `);
    }

    // Profile pin — only meaningful for L1 (no parent). Still render for
    // L2+ but disable with a hint; worker inherits from parent anyway.
    const l2 = selectedType !== 'text-to-video';
    fields.push(`
      <div class="form-group">
        <label class="form-label">Profile ${l2 ? '(inherited from parent — ignored)' : '(optional: pin to account)'}</label>
        ${renderProfileSelect('field-profile')}
      </div>
    `);

    if (TYPES_WITH_BBOX.has(selectedType)) {
      fields.push(`
        <div class="form-group">
          <label class="form-label">Bounding Box (normalized 0.0 – 1.0)</label>
          <div class="form-row" style="grid-template-columns: repeat(4, 1fr);">
            <div class="form-group">
              <label class="form-label" style="font-size:11px">x</label>
              <input type="number" class="form-input" id="field-bbox-x" placeholder="0.1" step="0.01" min="0" max="1">
            </div>
            <div class="form-group">
              <label class="form-label" style="font-size:11px">y</label>
              <input type="number" class="form-input" id="field-bbox-y" placeholder="0.1" step="0.01" min="0" max="1">
            </div>
            <div class="form-group">
              <label class="form-label" style="font-size:11px">w</label>
              <input type="number" class="form-input" id="field-bbox-w" placeholder="0.3" step="0.01" min="0" max="1">
            </div>
            <div class="form-group">
              <label class="form-label" style="font-size:11px">h</label>
              <input type="number" class="form-input" id="field-bbox-h" placeholder="0.3" step="0.01" min="0" max="1">
            </div>
          </div>
          <span class="form-hint">Fractions of the frame. Leave empty to let Flow pick.</span>
        </div>
      `);
    }

    if (selectedType === 'camera-move') {
      fields.push(`
        <div class="form-group">
          <label class="form-label">Camera Direction <span class="required">*</span></label>
          <select class="form-select" id="field-direction" required>
            <option value="">Select preset...</option>
            ${CAMERA_PRESETS.map((p) => `<option value="${App.escapeHtml(p)}">${App.escapeHtml(p)}</option>`).join('')}
          </select>
        </div>
      `);
    }

    return fields.join('');
  }

  function collectSingle() {
    const data = { type: selectedType };
    const val = (id) => document.getElementById(id)?.value?.trim() ?? '';

    const prompt = val('field-prompt');
    if (prompt) data.prompt = prompt;

    const parent = val('field-parent-job');
    if (parent) data.parent_job_id = parent;

    const purl = val('field-project-url');
    if (purl) data.project_url = purl;

    const model = val('field-model');
    if (model) data.model = model;

    const aspect = val('field-aspect-ratio');
    if (aspect) data.aspect_ratio = aspect;

    const profile = val('field-profile');
    if (profile && selectedType === 'text-to-video') data.profile = profile;

    if (TYPES_WITH_BBOX.has(selectedType)) {
      const x = val('field-bbox-x');
      const y = val('field-bbox-y');
      const w = val('field-bbox-w');
      const h = val('field-bbox-h');
      if (x && y && w && h) {
        data.bbox = { x: parseFloat(x), y: parseFloat(y), w: parseFloat(w), h: parseFloat(h) };
      }
    }

    if (selectedType === 'camera-move') {
      const dir = val('field-direction');
      if (dir) data.direction = dir;
    }

    return data;
  }

  function validateSingle(data) {
    if (data.type === 'text-to-video' && !data.prompt) return 'Prompt is required for Text-to-Video.';
    if (data.type === 'insert-object' && !data.prompt) return 'Prompt is required for Insert.';
    if (data.type === 'camera-move' && !data.direction) return 'Camera direction is required.';
    if (data.type !== 'text-to-video' && !data.parent_job_id && !data.project_url) {
      return 'Parent Job ID or Project URL is required for this job type.';
    }
    if (data.bbox) {
      for (const k of ['x', 'y', 'w', 'h']) {
        const v = data.bbox[k];
        if (typeof v !== 'number' || Number.isNaN(v) || v < 0 || v > 1) {
          return `bbox.${k} must be between 0 and 1.`;
        }
      }
    }
    return null;
  }

  // ---- batch mode -----------------------------------------------------------

  function renderBatch() {
    return `
      <div class="form-group">
        <label class="form-label">Prompts <span class="required">*</span></label>
        <textarea class="form-textarea" id="batch-prompts" rows="10"
                  placeholder="One prompt per line.&#10;Blank lines are skipped."></textarea>
        <span class="form-hint">Each line becomes a separate Text-to-Video L1 job.</span>
      </div>
      <div class="form-row">
        <div class="form-group">
          <label class="form-label">Model</label>
          <select class="form-select" id="batch-model">${renderOptions(MODELS, { selected: DEFAULT_MODEL })}</select>
        </div>
        <div class="form-group">
          <label class="form-label">Aspect Ratio</label>
          <select class="form-select" id="batch-aspect">${renderOptions(ASPECT_RATIOS, { selected: DEFAULT_ASPECT })}</select>
        </div>
      </div>
      <div class="form-group">
        <label class="form-label">Profile <span class="required">*</span></label>
        ${renderProfileSelect('batch-profile', { required: true, includeDefault: false })}
        <span class="form-hint">All jobs in this batch run on this account. Stagger across accounts by submitting separate batches.</span>
      </div>
    `;
  }

  function collectBatch() {
    const text = document.getElementById('batch-prompts')?.value ?? '';
    const model = document.getElementById('batch-model')?.value || DEFAULT_MODEL;
    const aspect = document.getElementById('batch-aspect')?.value || DEFAULT_ASPECT;
    const profile = document.getElementById('batch-profile')?.value || '';
    const prompts = text.split(/\r?\n/).map((s) => s.trim()).filter((s) => s.length > 0);
    return { prompts, model, aspect_ratio: aspect, profile };
  }

  function validateBatch(b) {
    if (!b.profile) return 'Profile is required for batch submit.';
    if (b.prompts.length === 0) return 'Enter at least one prompt.';
    if (b.prompts.length > 100) return 'Batch limit is 100 prompts — split into smaller batches.';
    return null;
  }

  // ---- page object ----------------------------------------------------------

  async function fetchProfiles() {
    try {
      const list = await API.profiles.list();
      profiles = Array.isArray(list) ? list : (list?.profiles ?? []);
    } catch (err) {
      console.warn('[CreateJob] could not load profiles:', err.message);
      profiles = [];
    }
  }

  const CreateJobPage = {
    name: 'create',
    title: 'Create Job',
    icon: 'add_circle',

    async render() {
      await fetchProfiles();
      return `
        <div class="card" style="max-width: 800px;">
          ${renderModeTabs()}
          <div id="mode-body">
            ${mode === 'single' ? renderSingleBody() : renderBatchBody()}
          </div>
          <div id="create-result" style="margin-top: 16px;"></div>
        </div>
      `;
    },

    mount() {
      bindModeTabs();
      if (mode === 'single') bindSingle();
      else bindBatch();
    },
  };

  // ---- body renderers -------------------------------------------------------

  function renderSingleBody() {
    return `
      <h3 style="margin-bottom: 20px; font-size: 16px; font-weight: 600;">Select Job Type</h3>
      ${renderTypeSelector()}
      <div id="job-fields">${renderFields()}</div>
      <div style="display: flex; gap: 12px; margin-top: 24px;">
        <button class="btn btn-primary" id="submit-job">
          <span class="material-icons">send</span> Create Job
        </button>
        <button class="btn btn-outline" id="reset-form">
          <span class="material-icons">refresh</span> Reset
        </button>
      </div>
    `;
  }

  function renderBatchBody() {
    return `
      <h3 style="margin-bottom: 20px; font-size: 16px; font-weight: 600;">Batch Text-to-Video</h3>
      ${renderBatch()}
      <div style="display: flex; gap: 12px; margin-top: 24px;">
        <button class="btn btn-primary" id="submit-batch">
          <span class="material-icons">send</span> Submit Batch
        </button>
        <button class="btn btn-outline" id="reset-form">
          <span class="material-icons">refresh</span> Reset
        </button>
      </div>
    `;
  }

  // ---- event wiring ---------------------------------------------------------

  function bindModeTabs() {
    document.getElementById('mode-tabs')?.addEventListener('click', (e) => {
      const b = e.target.closest('[data-mode]');
      if (!b) return;
      mode = b.dataset.mode;
      App._loadPage('create');
    });
  }

  function bindSingle() {
    document.getElementById('type-selector')?.addEventListener('click', (e) => {
      const opt = e.target.closest('.type-option');
      if (!opt) return;
      selectedType = opt.dataset.type;
      document.querySelectorAll('.type-option').forEach((el) => {
        el.classList.toggle('selected', el.dataset.type === selectedType);
      });
      document.getElementById('job-fields').innerHTML = renderFields();
    });

    document.getElementById('submit-job')?.addEventListener('click', async () => {
      const data = collectSingle();
      const err = validateSingle(data);
      if (err) { App.toast(err, 'warning'); return; }

      const btn = document.getElementById('submit-job');
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span> Creating...';

      try {
        const result = await API.jobs.create(data);
        const id = result?.id || result?.job_id || 'unknown';
        showResult('ok', 'Job created', `Job ID: <code>${App.escapeHtml(String(id))}</code>`);
        App.toast('Job created', 'success');
      } catch (e) {
        showResult('err', 'Create failed', App.escapeHtml(e.message));
        App.toast('Failed: ' + e.message, 'error');
      } finally {
        btn.disabled = false;
        btn.innerHTML = '<span class="material-icons">send</span> Create Job';
      }
    });

    bindReset();
  }

  function bindBatch() {
    document.getElementById('submit-batch')?.addEventListener('click', async () => {
      const b = collectBatch();
      const err = validateBatch(b);
      if (err) { App.toast(err, 'warning'); return; }

      const btn = document.getElementById('submit-batch');
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span> Submitting...';

      const results = { ok: [], fail: [] };
      // Submit serially. Server is cheap; serial keeps toast storms down
      // and preserves submission order in the dashboard.
      for (const prompt of b.prompts) {
        try {
          const job = await API.jobs.create({
            type: 'text-to-video',
            prompt,
            model: b.model,
            aspect_ratio: b.aspect_ratio,
            profile: b.profile,
          });
          results.ok.push(job?.id ?? '?');
        } catch (e) {
          results.fail.push({ prompt, error: e.message });
        }
      }

      const okCount = results.ok.length;
      const failCount = results.fail.length;
      const tone = failCount === 0 ? 'ok' : okCount === 0 ? 'err' : 'warn';
      const failLines = results.fail
        .slice(0, 5)
        .map((f) => `<li>${App.escapeHtml(App.truncate(f.prompt, 60))} — ${App.escapeHtml(f.error)}</li>`)
        .join('');
      const more = failCount > 5 ? `<li>…and ${failCount - 5} more</li>` : '';
      const body = `
        <div>Submitted <strong>${okCount}</strong> · Failed <strong>${failCount}</strong></div>
        ${failCount > 0 ? `<ul style="margin-top:8px; font-size:12px;">${failLines}${more}</ul>` : ''}
      `;
      showResult(tone, 'Batch complete', body);
      App.toast(`Batch: ${okCount} submitted, ${failCount} failed`,
                failCount === 0 ? 'success' : failCount === b.prompts.length ? 'error' : 'warning');

      btn.disabled = false;
      btn.innerHTML = '<span class="material-icons">send</span> Submit Batch';
    });

    bindReset();
  }

  function bindReset() {
    document.getElementById('reset-form')?.addEventListener('click', () => {
      selectedType = 'text-to-video';
      App._loadPage('create');
    });
  }

  function showResult(kind, title, bodyHtml) {
    const box = document.getElementById('create-result');
    if (!box) return;
    const color =
      kind === 'ok'  ? 'var(--success)' :
      kind === 'err' ? 'var(--error)'   :
                       'var(--warning, #f39c12)';
    const icon =
      kind === 'ok'  ? 'check_circle' :
      kind === 'err' ? 'error'        :
                       'warning';
    const bg =
      kind === 'ok'  ? 'rgba(46,204,113,0.05)' :
      kind === 'err' ? 'rgba(231,76,60,0.05)'  :
                       'rgba(243,156,18,0.05)';
    box.innerHTML = `
      <div class="card" style="border-color:${color}; background:${bg};">
        <div style="display:flex; align-items:flex-start; gap:10px;">
          <span class="material-icons" style="color:${color};">${icon}</span>
          <div><strong>${App.escapeHtml(title)}</strong>
            <div style="font-size:13px; color:var(--text-secondary); margin-top:4px;">${bodyHtml}</div>
          </div>
        </div>
      </div>
    `;
  }

  App.register(CreateJobPage);
})();
