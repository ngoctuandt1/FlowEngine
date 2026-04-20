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
    JOB_TYPES, MODELS, DEFAULT_MODEL, IMAGE_MODELS, DEFAULT_IMAGE_MODEL,
    ASPECT_RATIOS, ASPECT_RATIOS_IMAGE, DEFAULT_ASPECT,
    CAMERA_PRESETS,
    TYPES_WITH_PROMPT, TYPES_WITH_BBOX, TYPES_WITH_MODEL, TYPES_WITH_ASPECT, TYPES_WITH_IMAGES, TYPES_WITH_INGREDIENTS,
  } = CONST;

  let mode = 'single';               // 'single' | 'batch'
  let selectedType = 'text-to-video';
  let profiles = [];                 // populated on mount
  let startImagePath = '';
  let endImagePath = '';
  let refImagePath = '';
  let ingredientImagePaths = [];
  const MAX_INGREDIENT_IMAGES = 10;
  const LEVEL_1_TYPES = new Set(['text-to-video', 'frames-to-video', 'ingredients-to-video', 'text-to-image']);

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
      const required = selectedType === 'text-to-video'
        || selectedType === 'frames-to-video'
        || selectedType === 'ingredients-to-video'
        || selectedType === 'text-to-image'
        || selectedType === 'insert-object';
      fields.push(`
        <div class="form-group">
          <label class="form-label">Prompt ${required ? '<span class="required">*</span>' : '(optional)'}</label>
          <textarea class="form-textarea" id="field-prompt" rows="4"
                    placeholder="Describe the video you want..." ${required ? 'required' : ''}></textarea>
        </div>
      `);
    }

    if (!LEVEL_1_TYPES.has(selectedType)) {
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
    const modelOptions = selectedType === 'text-to-image' ? IMAGE_MODELS : MODELS;
    const defaultModel = selectedType === 'text-to-image' ? DEFAULT_IMAGE_MODEL : DEFAULT_MODEL;
    const aspectOptions = selectedType === 'text-to-image' ? ASPECT_RATIOS_IMAGE : ASPECT_RATIOS;
    if (showModel || showAspect) {
      fields.push(`
        <div class="form-row">
          ${showModel ? `
          <div class="form-group">
            <label class="form-label">Model</label>
            <select class="form-select" id="field-model">${renderOptions(modelOptions, { selected: defaultModel })}</select>
          </div>` : ''}
          ${showAspect ? `
          <div class="form-group">
            <label class="form-label">Aspect Ratio</label>
            <select class="form-select" id="field-aspect-ratio">${renderOptions(aspectOptions, { selected: DEFAULT_ASPECT })}</select>
          </div>` : ''}
        </div>
      `);
    }

    if (TYPES_WITH_IMAGES.has(selectedType)) {
      fields.push(renderImageUploads());
    }

    if (TYPES_WITH_INGREDIENTS.has(selectedType)) {
      fields.push(renderIngredientsUploads());
    }

    // Profile pin — only meaningful for L1 (no parent). Still render for
    // L2+ but disable with a hint; worker inherits from parent anyway.
    const l2 = !LEVEL_1_TYPES.has(selectedType);
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

  function renderImageUploads() {
    if (selectedType === 'text-to-image') {
      return `
        <div class="form-row">
          ${renderImageUploadField({
            id: 'field-ref-image',
            label: 'Reference',
            required: false,
            path: refImagePath,
          })}
        </div>
      `;
    }

    return `
      <div class="form-row">
        ${renderImageUploadField({
          id: 'field-start-image',
          label: 'Start',
          required: true,
          path: startImagePath,
        })}
        ${renderImageUploadField({
          id: 'field-end-image',
          label: 'End',
          required: false,
          path: endImagePath,
        })}
      </div>
    `;
  }

  function renderIngredientsUploads() {
    const cards = ingredientImagePaths.length > 0
      ? ingredientImagePaths.map((path, index) => `
        <div class="card" style="padding:12px; position:relative;">
          <button type="button" class="icon-btn ingredient-remove" data-index="${index}"
                  title="Remove reference"
                  style="position:absolute; top:8px; right:8px; width:28px; height:28px;">
            <span class="material-icons" style="font-size:18px;">close</span>
          </button>
          <img src="/${App.escapeHtml(path)}" alt="Reference ${index + 1}"
               style="width:100%; height:120px; object-fit:cover; border-radius:10px; border:1px solid var(--border-color);">
          <div class="form-hint" style="margin-top:8px; word-break:break-all;">${App.escapeHtml(path)}</div>
        </div>
      `).join('')
      : `<div class="form-hint">No reference images uploaded yet.</div>`;

    return `
      <div class="form-group">
        <label class="form-label">Reference Images <span class="required">*</span></label>
        <div style="display:flex; align-items:center; gap:12px; margin-bottom:12px;">
          <button type="button" class="btn btn-outline btn-sm" id="add-ingredient-image">
            <span class="material-icons">add_photo_alternate</span> Add reference image
          </button>
          <span class="form-hint">${ingredientImagePaths.length}/${MAX_INGREDIENT_IMAGES} uploaded</span>
        </div>
        <input type="file" id="field-ingredient-images" accept="image/png,image/jpeg,image/webp" multiple hidden>
        <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(180px, 1fr)); gap:12px;">
          ${cards}
        </div>
        <span class="form-hint" style="margin-top:8px; display:block;">Upload 1-10 reference images. Each file is stored via <code>/api/uploads</code> before submit.</span>
      </div>
    `;
  }

  function renderImageUploadField({ id, label, required, path }) {
    const preview = path ? `
      <div style="margin-top:8px;">
        <img src="/${App.escapeHtml(path)}" alt="${App.escapeHtml(label)} preview"
             style="width:100%; max-height:180px; object-fit:cover; border-radius:10px; border:1px solid var(--border-color);">
      </div>
      <div class="form-hint" style="margin-top:6px;">${App.escapeHtml(path)}</div>
    ` : '<div class="form-hint" style="margin-top:6px;">No image uploaded.</div>';
    return `
      <div class="form-group">
        <label class="form-label">${label} Image ${required ? '<span class="required">*</span>' : '(optional)'}</label>
        <input type="file" class="form-input" id="${id}" accept="image/png,image/jpeg,image/webp">
        ${preview}
      </div>
    `;
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
    if (profile && LEVEL_1_TYPES.has(selectedType)) data.profile = profile;

    if (selectedType === 'frames-to-video') {
      if (startImagePath) data.start_image_path = startImagePath;
      if (endImagePath) data.end_image_path = endImagePath;
    }
    if (selectedType === 'ingredients-to-video' && ingredientImagePaths.length > 0) {
      data.ingredient_image_paths = [...ingredientImagePaths];
    }
    if (selectedType === 'text-to-image' && refImagePath) data.ref_image_path = refImagePath;

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
    if (data.type === 'frames-to-video' && !data.prompt) return 'Prompt is required for Frames to Video.';
    if (data.type === 'frames-to-video' && !data.start_image_path) return 'Start image is required for Frames to Video.';
    if (data.type === 'ingredients-to-video' && !data.prompt) return 'Prompt is required for Ingredients to Video.';
    if (data.type === 'ingredients-to-video' && (!Array.isArray(data.ingredient_image_paths) || data.ingredient_image_paths.length === 0)) {
      return 'At least one reference image is required for Ingredients to Video.';
    }
    if (data.type === 'ingredients-to-video' && data.ingredient_image_paths.length > MAX_INGREDIENT_IMAGES) {
      return `Ingredients to Video supports at most ${MAX_INGREDIENT_IMAGES} reference images per job.`;
    }
    if (data.type === 'text-to-image' && !data.prompt) return 'Prompt is required for Text to Image.';
    if (data.type === 'insert-object' && !data.prompt) return 'Prompt is required for Insert.';
    if (data.type === 'camera-move' && !data.direction) return 'Camera direction is required.';
    if (!LEVEL_1_TYPES.has(data.type) && !data.parent_job_id && !data.project_url) {
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
      bindImageInputs();
      bindIngredientInputs();
    });

    bindImageInputs();
    bindIngredientInputs();

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
      startImagePath = '';
      endImagePath = '';
      refImagePath = '';
      ingredientImagePaths = [];
      App._loadPage('create');
    });
  }

  function bindImageInputs() {
    if (!TYPES_WITH_IMAGES.has(selectedType)) return;

    document.getElementById('field-start-image')?.addEventListener('change', async (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      try {
        startImagePath = await uploadImage(file, e.target, 'Start image');
        App._loadPage('create');
      } catch {}
    });

    document.getElementById('field-end-image')?.addEventListener('change', async (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      try {
        endImagePath = await uploadImage(file, e.target, 'End image');
        App._loadPage('create');
      } catch {}
    });

    document.getElementById('field-ref-image')?.addEventListener('change', async (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      try {
        refImagePath = await uploadImage(file, e.target, 'Reference image');
        App._loadPage('create');
      } catch {}
    });
  }

  function bindIngredientInputs() {
    if (!TYPES_WITH_INGREDIENTS.has(selectedType)) return;

    document.getElementById('add-ingredient-image')?.addEventListener('click', () => {
      if (ingredientImagePaths.length >= MAX_INGREDIENT_IMAGES) {
        App.toast(`You can upload up to ${MAX_INGREDIENT_IMAGES} reference images.`, 'warning');
        return;
      }
      document.getElementById('field-ingredient-images')?.click();
    });

    document.getElementById('field-ingredient-images')?.addEventListener('change', async (e) => {
      const files = Array.from(e.target.files || []);
      if (files.length === 0) return;

      const remaining = MAX_INGREDIENT_IMAGES - ingredientImagePaths.length;
      if (files.length > remaining) {
        App.toast(`Only ${remaining} more reference image${remaining === 1 ? '' : 's'} can be added.`, 'warning');
      }

      for (const file of files.slice(0, remaining)) {
        try {
          const path = await uploadImage(file, e.target, 'Reference image');
          ingredientImagePaths.push(path);
        } catch {
          break;
        }
      }

      e.target.value = '';
      App._loadPage('create');
    });

    document.querySelectorAll('.ingredient-remove').forEach((btn) => {
      btn.addEventListener('click', () => {
        const index = Number.parseInt(btn.dataset.index, 10);
        if (Number.isNaN(index)) return;
        ingredientImagePaths.splice(index, 1);
        App._loadPage('create');
      });
    });
  }

  async function uploadImage(file, input, label) {
    const previousText = input.title;
    input.disabled = true;
    input.title = 'Uploading...';
    try {
      const result = await API.uploads.create(file);
      const path = result?.path || '';
      if (!path) throw new Error('Upload completed without a path');
      App.toast(`${label} uploaded`, 'success');
      return path;
    } catch (err) {
      App.toast(`${label} failed: ${err.message}`, 'error');
      throw err;
    } finally {
      input.disabled = false;
      input.title = previousText;
    }
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
