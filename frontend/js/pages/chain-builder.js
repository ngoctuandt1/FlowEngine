/**
 * Chain Builder — sequence a chain of Flow ops from an L1 root into L2 edits.
 *
 * Backend contract: POST /api/chains with
 *   { profile: "<name>", jobs: [JobCreate, JobCreate, ...] }
 * See server/models/chain.py :: ChainCreate.
 *
 * Step field names mirror server/models/job.py JobCreate:
 *   type, prompt, model, aspect_ratio, direction, bbox{x,y,w,h},
 *   start_image_path, end_image_path, ref_image_path, ingredient_image_paths.
 */
(() => {
  const {
    JOB_TYPES, MODELS, DEFAULT_MODEL, IMAGE_MODELS, DEFAULT_IMAGE_MODEL,
    ASPECT_RATIOS, ASPECT_RATIOS_IMAGE, DEFAULT_ASPECT,
    CAMERA_PRESETS,
    TYPES_WITH_PROMPT, TYPES_WITH_BBOX, TYPES_WITH_MODEL, TYPES_WITH_ASPECT,
    TYPES_WITH_IMAGES, TYPES_WITH_INGREDIENTS,
  } = CONST;

  // L1 types create a new project; chained follow-up steps must be L2 edits.
  const FIRST_TYPE = 'text-to-video';
  const L1_ONLY_TYPES = new Set(['text-to-video', 'text-to-image', 'frames-to-video', 'ingredients-to-video']);
  const FIRST_TYPES = JOB_TYPES.filter((t) => L1_ONLY_TYPES.has(t.id));
  const SUBSEQUENT_TYPES = JOB_TYPES.filter((t) => !L1_ONLY_TYPES.has(t.id));
  const REQUIRED_PROMPT_TYPES = new Set([
    'text-to-video',
    'frames-to-video',
    'ingredients-to-video',
    'text-to-image',
    'insert-object',
  ]);
  const MAX_INGREDIENT_IMAGES = 10;

  let steps = [];
  let profiles = [];
  let pinnedProfile = '';
  let selectedFirstType = FIRST_TYPE;
  let selectedNextType = SUBSEQUENT_TYPES[0]?.id || '';

  function isL1Type(type) { return L1_ONLY_TYPES.has(type); }

  function renderOptions(items, { selected } = {}) {
    return items
      .map((o) => {
        const value = typeof o === 'string' ? o : o.value;
        const label = typeof o === 'string' ? o : o.label;
        const isSelected = value === selected ? ' selected' : '';
        return `<option value="${App.escapeHtml(value)}"${isSelected}>${App.escapeHtml(label)}</option>`;
      })
      .join('');
  }

  function getModelOptions(type) {
    return type === 'text-to-image' ? IMAGE_MODELS : MODELS;
  }

  function getDefaultModel(type) {
    return type === 'text-to-image' ? DEFAULT_IMAGE_MODEL : DEFAULT_MODEL;
  }

  function getAspectOptions(type) {
    return type === 'text-to-image' ? ASPECT_RATIOS_IMAGE : ASPECT_RATIOS;
  }

  function clearResult() {
    const el = document.getElementById('chain-result');
    if (el) el.innerHTML = '';
  }

  function showResult(kind, title, bodyHtml) {
    const box = document.getElementById('chain-result');
    if (!box) return;
    const color =
      kind === 'ok' ? 'var(--success)' :
      kind === 'err' ? 'var(--error)' :
      'var(--warning, #f39c12)';
    const icon =
      kind === 'ok' ? 'check_circle' :
      kind === 'err' ? 'error' :
      'warning';
    const bg =
      kind === 'ok' ? 'rgba(46,204,113,0.05)' :
      kind === 'err' ? 'rgba(231,76,60,0.05)' :
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

  function emptyStep(type) {
    return {
      type,
      prompt: '',
      model: TYPES_WITH_MODEL.has(type) ? getDefaultModel(type) : '',
      aspect_ratio: TYPES_WITH_ASPECT.has(type) ? DEFAULT_ASPECT : '',
      direction: '',
      bbox: null,
      start_image_path: '',
      end_image_path: '',
      ref_image_path: '',
      ingredient_image_paths: [],
    };
  }

  function addStep(type) {
    steps.push(emptyStep(type));
    clearResult();
    refreshSteps();
  }
  function removeStep(i) {
    if (i === 0 && steps.length > 1) {
      App.toast('Cannot remove the first step while later steps exist.', 'warning');
      return;
    }
    steps.splice(i, 1);
    clearResult();
    refreshSteps();
  }

  function refreshSteps() {
    const container = document.getElementById('chain-steps');
    if (container) container.innerHTML = renderSteps();
    bindStepEvents();
    updateAddButtons();
    const btn = document.getElementById('submit-chain');
    if (btn) btn.disabled = steps.length === 0;
  }

  function renderProfileSelect() {
    const opts = profiles
      .map((p) => `<option value="${App.escapeHtml(p.name)}" ${pinnedProfile === p.name ? 'selected' : ''}>${App.escapeHtml(p.name)}</option>`)
      .join('');
    return `
      <select class="form-select" id="chain-profile" required>
        <option value="">Select profile...</option>
        ${opts}
      </select>
    `;
  }

  function updateAddButtons() {
    const el = document.getElementById('chain-add-buttons');
    if (!el) return;
    if (steps.length === 0) {
      const options = FIRST_TYPES
        .map((t) => `<option value="${App.escapeHtml(t.id)}" ${selectedFirstType === t.id ? 'selected' : ''}>${App.escapeHtml(t.label)}</option>`)
        .join('');
      el.innerHTML = `
        <p style="color: var(--text-muted); font-size: 13px; margin-bottom: 12px;">
          Chains must start with an L1 step that creates a new project.
        </p>
        <div class="form-row" style="align-items: end;">
          <div class="form-group" style="margin-bottom: 0;">
            <label class="form-label">Root step <span class="required">*</span></label>
            <select class="form-select" id="chain-root-type">${options}</select>
            <span class="form-hint">Choose the L1 job that creates the chain's project.</span>
          </div>
          <div class="form-group" style="margin-bottom: 0;">
            <button class="btn btn-primary" id="chain-add-root">
              <span class="material-icons">add_link</span> Add Root Step
            </button>
          </div>
        </div>
      `;
      el.querySelector('#chain-root-type')?.addEventListener('change', (event) => {
        selectedFirstType = event.target.value;
      });
      el.querySelector('#chain-add-root')?.addEventListener('click', () => addStep(selectedFirstType));
    } else {
      const options = SUBSEQUENT_TYPES
        .map((t) => `<option value="${App.escapeHtml(t.id)}" ${selectedNextType === t.id ? 'selected' : ''}>${App.escapeHtml(t.label)}</option>`)
        .join('');
      el.innerHTML = `
        <p style="color: var(--text-muted); font-size: 13px; margin-bottom: 12px;">
          Add the next chained edit. Only L2 job types are allowed after the root step.
        </p>
        <div class="form-row" style="align-items: end;">
          <div class="form-group" style="margin-bottom: 0;">
            <label class="form-label">Next step <span class="required">*</span></label>
            <select class="form-select" id="chain-next-type">${options}</select>
            <span class="form-hint">Extend, insert, remove, and camera all chain off the parent media.</span>
          </div>
          <div class="form-group" style="margin-bottom: 0;">
            <button class="btn btn-outline" id="chain-add-next">
              <span class="material-icons">add</span> Add Step
            </button>
          </div>
        </div>
      `;
      el.querySelector('#chain-next-type')?.addEventListener('change', (event) => {
        selectedNextType = event.target.value;
      });
      el.querySelector('#chain-add-next')?.addEventListener('click', () => addStep(selectedNextType));
    }
  }

  function renderStepConfig(step, i) {
    const parts = [];
    const tag = (field, html) => parts.push(html);

    if (TYPES_WITH_PROMPT.has(step.type)) {
      const req = REQUIRED_PROMPT_TYPES.has(step.type);
      tag('prompt', `
        <div class="form-group" style="margin-bottom:12px">
          <label class="form-label">Prompt ${req ? '<span class="required">*</span>' : '(optional)'}</label>
          <textarea class="form-textarea step-field" data-step="${i}" data-field="prompt"
                    rows="2" placeholder="Describe...">${App.escapeHtml(step.prompt)}</textarea>
        </div>
      `);
    }

    if (TYPES_WITH_MODEL.has(step.type)) {
      const opts = renderOptions(getModelOptions(step.type), { selected: step.model });
      tag('model', `
        <div class="form-group" style="margin-bottom:12px">
          <label class="form-label">Model</label>
          <select class="form-select step-field" data-step="${i}" data-field="model">${opts}</select>
        </div>
      `);
    }

    if (TYPES_WITH_ASPECT.has(step.type)) {
      const opts = renderOptions(getAspectOptions(step.type), { selected: step.aspect_ratio });
      tag('aspect_ratio', `
        <div class="form-group" style="margin-bottom:12px">
          <label class="form-label">Aspect Ratio</label>
          <select class="form-select step-field" data-step="${i}" data-field="aspect_ratio">${opts}</select>
        </div>
      `);
    }

    if (TYPES_WITH_IMAGES.has(step.type)) {
      tag('images', renderImageUploads(step, i));
    }

    if (TYPES_WITH_INGREDIENTS.has(step.type)) {
      tag('ingredients', renderIngredientsUploads(step, i));
    }

    if (step.type === 'camera-move') {
      const opts = CAMERA_PRESETS
        .map((p) => `<option value="${p}" ${step.direction === p ? 'selected' : ''}>${p}</option>`)
        .join('');
      tag('direction', `
        <div class="form-group" style="margin-bottom:12px">
          <label class="form-label">Direction <span class="required">*</span></label>
          <select class="form-select step-field" data-step="${i}" data-field="direction">
            <option value="">Select preset...</option>
            ${opts}
          </select>
        </div>
      `);
    }

    if (TYPES_WITH_BBOX.has(step.type)) {
      const b = step.bbox || {};
      tag('bbox', `
        <div class="form-group" style="margin-bottom:12px">
          <label class="form-label">Bounding Box (0.0 – 1.0, optional)</label>
          <div class="form-row" style="grid-template-columns: repeat(4, 1fr);">
            ${['x','y','w','h'].map((k) => `
              <input type="number" class="form-input step-bbox" data-step="${i}" data-bbox="${k}"
                     placeholder="${k}" step="0.01" min="0" max="1"
                     value="${b[k] != null ? b[k] : ''}">
            `).join('')}
          </div>
        </div>
      `);
    }

    return parts.join('');
  }

  function renderImageUploads(step, i) {
    if (step.type === 'text-to-image') {
      return `
        <div class="form-row" style="margin-bottom: 12px;">
          ${renderImageUploadField({
            stepIndex: i,
            field: 'ref_image_path',
            label: 'Reference',
            required: false,
            path: step.ref_image_path,
          })}
        </div>
      `;
    }

    return `
      <div class="form-row" style="margin-bottom: 12px;">
        ${renderImageUploadField({
          stepIndex: i,
          field: 'start_image_path',
          label: 'Start',
          required: true,
          path: step.start_image_path,
        })}
        ${renderImageUploadField({
          stepIndex: i,
          field: 'end_image_path',
          label: 'End',
          required: false,
          path: step.end_image_path,
        })}
      </div>
    `;
  }

  function renderIngredientsUploads(step, i) {
    const images = Array.isArray(step.ingredient_image_paths) ? step.ingredient_image_paths : [];
    const cards = images.length > 0
      ? images.map((path, index) => `
        <div class="card" style="padding:12px; position:relative;">
          <button type="button" class="icon-btn step-ingredient-remove" data-step="${i}" data-index="${index}"
                  title="Remove reference"
                  style="position:absolute; top:8px; right:8px; width:28px; height:28px;">
            <span class="material-icons" style="font-size:18px;">close</span>
          </button>
          <img src="/${App.escapeHtml(path)}" alt="Reference ${index + 1}"
               style="width:100%; height:120px; object-fit:cover; border-radius:10px; border:1px solid var(--border-color);">
          <div class="form-hint" style="margin-top:8px; word-break:break-all;">${App.escapeHtml(path)}</div>
        </div>
      `).join('')
      : '<div class="form-hint">No reference images uploaded yet.</div>';

    return `
      <div class="form-group" style="margin-bottom:12px">
        <label class="form-label">Reference Images <span class="required">*</span></label>
        <div style="display:flex; align-items:center; gap:12px; margin-bottom:12px;">
          <button type="button" class="btn btn-outline btn-sm step-ingredient-add" data-step="${i}">
            <span class="material-icons">add_photo_alternate</span> Add reference image
          </button>
          <span class="form-hint">${images.length}/${MAX_INGREDIENT_IMAGES} uploaded</span>
        </div>
        <input type="file" class="step-ingredient-input" id="step-ingredient-images-${i}"
               data-step="${i}" accept="image/png,image/jpeg,image/webp" multiple hidden>
        <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(180px, 1fr)); gap:12px;">
          ${cards}
        </div>
        <span class="form-hint" style="margin-top:8px; display:block;">Upload 1-10 reference images before submit.</span>
      </div>
    `;
  }

  function renderImageUploadField({ stepIndex, field, label, required, path }) {
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
        <input type="file" class="form-input step-upload-input"
               data-step="${stepIndex}" data-field="${field}" data-label="${App.escapeHtml(label)} image"
               accept="image/png,image/jpeg,image/webp">
        ${preview}
      </div>
    `;
  }

  function renderSteps() {
    return steps.map((s, i) => {
      const meta = JOB_TYPES.find((t) => t.id === s.type) || {};
      return `
        <div class="chain-step">
          <div class="chain-step-card">
            <div class="chain-step-header">
              <span class="chain-step-num">
                <span class="material-icons" style="font-size:16px; vertical-align:middle;">${meta.icon || 'work'}</span>
                Step ${i + 1}: ${App.escapeHtml(meta.label || s.type)}
              </span>
              <button class="icon-btn step-remove" data-step-index="${i}" title="Remove step">
                <span class="material-icons" style="font-size:18px;">close</span>
              </button>
            </div>
            ${renderStepConfig(s, i)}
          </div>
        </div>
      `;
    }).join('');
  }

  function bindStepEvents() {
    document.querySelectorAll('.step-field').forEach((el) => {
      const handler = () => {
        const i = parseInt(el.dataset.step);
        const f = el.dataset.field;
        if (steps[i]) {
          steps[i][f] = el.value;
          clearResult();
        }
      };
      el.addEventListener('input', handler);
      el.addEventListener('change', handler);
    });

    document.querySelectorAll('.step-bbox').forEach((el) => {
      const handler = () => {
        const i = parseInt(el.dataset.step);
        const k = el.dataset.bbox;
        if (!steps[i]) return;
        const raw = el.value.trim();
        if (raw === '') {
          if (steps[i].bbox) delete steps[i].bbox[k];
        } else {
          steps[i].bbox = steps[i].bbox || {};
          steps[i].bbox[k] = parseFloat(raw);
        }
        // Drop bbox entirely if no keys remain
        if (steps[i].bbox && Object.keys(steps[i].bbox).length === 0) steps[i].bbox = null;
        clearResult();
      };
      el.addEventListener('input', handler);
      el.addEventListener('change', handler);
    });

    document.querySelectorAll('.step-upload-input').forEach((input) => {
      input.addEventListener('change', async (event) => {
        const i = Number.parseInt(input.dataset.step, 10);
        const field = input.dataset.field;
        if (Number.isNaN(i) || !field || !steps[i]) return;
        const file = event.target.files?.[0];
        if (!file) return;
        try {
          steps[i][field] = await uploadImage(file, input, input.dataset.label || 'Image');
          clearResult();
          refreshSteps();
        } catch {}
      });
    });

    document.querySelectorAll('.step-ingredient-add').forEach((btn) => {
      btn.addEventListener('click', () => {
        const i = Number.parseInt(btn.dataset.step, 10);
        if (Number.isNaN(i) || !steps[i]) return;
        if (steps[i].ingredient_image_paths.length >= MAX_INGREDIENT_IMAGES) {
          App.toast(`You can upload up to ${MAX_INGREDIENT_IMAGES} reference images.`, 'warning');
          return;
        }
        document.getElementById(`step-ingredient-images-${i}`)?.click();
      });
    });

    document.querySelectorAll('.step-ingredient-input').forEach((input) => {
      input.addEventListener('change', async (event) => {
        const i = Number.parseInt(input.dataset.step, 10);
        if (Number.isNaN(i) || !steps[i]) return;
        const files = Array.from(event.target.files || []);
        if (files.length === 0) return;

        const remaining = MAX_INGREDIENT_IMAGES - steps[i].ingredient_image_paths.length;
        if (files.length > remaining) {
          App.toast(`Only ${remaining} more reference image${remaining === 1 ? '' : 's'} can be added.`, 'warning');
        }

        for (const file of files.slice(0, remaining)) {
          try {
            const path = await uploadImage(file, input, 'Reference image');
            steps[i].ingredient_image_paths.push(path);
          } catch {
            break;
          }
        }

        event.target.value = '';
        clearResult();
        refreshSteps();
      });
    });

    document.querySelectorAll('.step-ingredient-remove').forEach((btn) => {
      btn.addEventListener('click', () => {
        const i = Number.parseInt(btn.dataset.step, 10);
        const index = Number.parseInt(btn.dataset.index, 10);
        if (Number.isNaN(i) || Number.isNaN(index) || !steps[i]) return;
        steps[i].ingredient_image_paths.splice(index, 1);
        clearResult();
        refreshSteps();
      });
    });

    document.querySelectorAll('.step-remove').forEach((btn) => {
      btn.addEventListener('click', () => removeStep(parseInt(btn.dataset.stepIndex)));
    });
  }

  function validateChain() {
    if (!pinnedProfile) return 'Select a profile to pin the chain.';
    if (steps.length === 0) return 'Add at least one step.';

    for (let i = 0; i < steps.length; i++) {
      const s = steps[i];
      const meta = JOB_TYPES.find((t) => t.id === s.type);
      const label = meta?.label || s.type;

      if (i === 0 && !isL1Type(s.type)) {
        return `Step 1 must be an L1 root type. ${label} can only be added after a project-creating step.`;
      }
      if (i > 0 && isL1Type(s.type)) {
        return `Step ${i + 1} (${label}) creates a new project and cannot appear after the root step.`;
      }

      if (REQUIRED_PROMPT_TYPES.has(s.type) && !s.prompt.trim()) return `Step ${i + 1} (${label}) requires a prompt.`;
      if (s.type === 'frames-to-video' && !s.start_image_path) return `Step ${i + 1} (Frames to Video) requires a start image.`;
      if (s.type === 'ingredients-to-video' && (!Array.isArray(s.ingredient_image_paths) || s.ingredient_image_paths.length === 0)) {
        return `Step ${i + 1} (Ingredients to Video) requires at least one reference image.`;
      }
      if (s.type === 'ingredients-to-video' && s.ingredient_image_paths.length > MAX_INGREDIENT_IMAGES) {
        return `Step ${i + 1} (Ingredients to Video) supports at most ${MAX_INGREDIENT_IMAGES} reference images.`;
      }
      if (s.type === 'camera-move' && !s.direction) return `Step ${i + 1} (Camera) requires a direction.`;

      if (s.bbox) {
        // Partial bbox not allowed — either all four keys present with
        // valid values, or no bbox at all.
        const keys = ['x', 'y', 'w', 'h'];
        const missing = keys.filter((k) => typeof s.bbox[k] !== 'number' || Number.isNaN(s.bbox[k]));
        if (missing.length && missing.length < 4) return `Step ${i + 1}: bbox needs all of x/y/w/h or none.`;
        for (const k of keys) {
          if (typeof s.bbox[k] === 'number' && (s.bbox[k] < 0 || s.bbox[k] > 1)) {
            return `Step ${i + 1}: bbox.${k} must be between 0 and 1.`;
          }
        }
      }
    }
    return null;
  }

  function buildPayload() {
    return {
      profile: pinnedProfile,
      jobs: steps.map((s) => {
        const job = { type: s.type };
        if (s.prompt) job.prompt = s.prompt;
        if (s.model) job.model = s.model;
        if (s.aspect_ratio) job.aspect_ratio = s.aspect_ratio;
        if (s.direction) job.direction = s.direction;
        if (s.start_image_path) job.start_image_path = s.start_image_path;
        if (s.end_image_path) job.end_image_path = s.end_image_path;
        if (s.ref_image_path) job.ref_image_path = s.ref_image_path;
        if (Array.isArray(s.ingredient_image_paths) && s.ingredient_image_paths.length > 0) {
          job.ingredient_image_paths = [...s.ingredient_image_paths];
        }
        if (s.bbox && ['x','y','w','h'].every((k) => typeof s.bbox[k] === 'number')) {
          job.bbox = { x: s.bbox.x, y: s.bbox.y, w: s.bbox.w, h: s.bbox.h };
        }
        return job;
      }),
    };
  }

  async function fetchProfiles() {
    try {
      const list = await API.profiles.list();
      profiles = Array.isArray(list) ? list : (list?.profiles ?? []);
    } catch {
      profiles = [];
    }
  }

  async function uploadImage(file, input, label) {
    const previousTitle = input.title;
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
      input.title = previousTitle;
    }
  }

  const ChainBuilderPage = {
    name: 'chains',
    title: 'Chain Builder',
    icon: 'link',

    async render() {
      steps = [];
      pinnedProfile = '';
      selectedFirstType = FIRST_TYPE;
      selectedNextType = SUBSEQUENT_TYPES[0]?.id || '';
      await fetchProfiles();

      return `
        <div style="max-width: 800px;">
          <div class="card" style="margin-bottom: 16px;">
            <h3 style="margin-bottom: 8px; font-size: 16px; font-weight: 600;">Build a Job Chain</h3>
            <p style="color: var(--text-muted); font-size: 13px;">
              Chains run sequentially on a single profile. Start with an L1 project-creating step,
              then add only L2 edits that chain off the parent media.
            </p>
          </div>

          <div class="card" style="margin-bottom:16px;">
            <label class="form-label">Profile <span class="required">*</span></label>
            ${renderProfileSelect()}
            <span class="form-hint">All steps run on this Google account. L2+ inherits it automatically.</span>
          </div>

          <div class="chain-timeline" id="chain-steps"></div>

          <div class="card" id="chain-add-buttons" style="margin-bottom: 16px;"></div>

          <div style="display: flex; gap: 12px;">
            <button class="btn btn-primary" id="submit-chain" disabled>
              <span class="material-icons">send</span> Submit Chain
            </button>
            <button class="btn btn-outline" id="reset-chain">
              <span class="material-icons">refresh</span> Reset
            </button>
          </div>

          <div id="chain-result" style="margin-top: 16px;"></div>
        </div>
      `;
    },

    mount() {
      refreshSteps();

      document.getElementById('chain-profile')?.addEventListener('change', (e) => {
        pinnedProfile = e.target.value;
        clearResult();
      });

      document.getElementById('submit-chain')?.addEventListener('click', async () => {
        const err = validateChain();
        if (err) {
          showResult('warn', 'Validation failed', App.escapeHtml(err));
          App.toast(err, 'warning');
          return;
        }

        const btn = document.getElementById('submit-chain');
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> Submitting...';

        try {
          const result = await API.chains.create(buildPayload());
          const cid = result?.chain_id || result?.id || 'unknown';
          showResult(
            'ok',
            'Chain submitted',
            `Chain ID: <code>${App.escapeHtml(String(cid))}</code> — ${steps.length} step(s)`,
          );
          App.toast('Chain submitted', 'success');
        } catch (e) {
          showResult('err', 'Chain failed', App.escapeHtml(e.message));
          App.toast('Chain failed: ' + e.message, 'error');
        } finally {
          btn.disabled = steps.length === 0;
          btn.innerHTML = '<span class="material-icons">send</span> Submit Chain';
        }
      });

      document.getElementById('reset-chain')?.addEventListener('click', () => {
        steps = [];
        pinnedProfile = '';
        App._loadPage('chains');
      });
    },
  };

  App.register(ChainBuilderPage);
})();
