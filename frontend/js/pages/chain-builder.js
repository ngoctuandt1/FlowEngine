/**
 * Chain Builder — sequence a chain of Flow ops from an L1 root into L2 edits.
 *
 * Backend contract:
 * - New roots use POST /api/chains with { profile, jobs: [JobCreate, ...] }.
 * - Parent-prefilled continuations submit via existing POST /api/jobs so the
 *   first step preserves the real upstream parent_job_id / chain_id linkage.
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
  const CHAIN_BUILDER_ROUTE_RE = /^(chains|chain-builder)(?:[/?]|$)/i;
  const BACKEND_GAP_WARNED = new Set();

  let steps = [];
  let profiles = [];
  let pinnedProfile = '';
  let selectedFirstType = FIRST_TYPE;
  let selectedNextType = SUBSEQUENT_TYPES[0]?.id || '';
  let parentPrefill = null;
  let prefillError = '';
  let routerPatched = false;
  let collapsedSteps = new Set();

  function isL1Type(type) { return L1_ONLY_TYPES.has(type); }

  function isValidSubsequentType(type) {
    return SUBSEQUENT_TYPES.some((jobType) => jobType.id === type);
  }

  function getJobTypeMeta(type) {
    return JOB_TYPES.find((jobType) => jobType.id === type) || null;
  }

  function getJobTypeLabel(type) {
    return getJobTypeMeta(type)?.label || type || 'Unknown';
  }

  function syncCollapsedSteps() {
    const next = new Set();
    if (steps.length > 1) {
      for (let i = 0; i < steps.length - 1; i++) next.add(i);
    }
    collapsedSteps.forEach((index) => {
      if (index >= 0 && index < steps.length) next.add(index);
    });
    collapsedSteps = next;
  }

  function truncateMiddle(value, max = 42) {
    const text = String(value || '').trim();
    if (!text || text.length <= max) return text;
    const head = Math.ceil((max - 1) / 2);
    const tail = Math.floor((max - 1) / 2);
    return `${text.slice(0, head)}...${text.slice(-tail)}`;
  }

  async function copyText(value, label) {
    const text = String(value || '').trim();
    if (!text) return;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const temp = document.createElement('textarea');
        temp.value = text;
        temp.setAttribute('readonly', '');
        temp.style.position = 'absolute';
        temp.style.left = '-9999px';
        document.body.appendChild(temp);
        temp.select();
        document.execCommand('copy');
        temp.remove();
      }
      App.toast(`${label} copied.`, 'success');
    } catch (err) {
      App.toast(`Failed to copy ${label.toLowerCase()}: ${err.message}`, 'error');
    }
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
      page: 'chain-builder',
      field,
      jobId: jobId || '',
      fallbackUsed,
    });
  }

  function renderDebugBadges(items) {
    if (!debugBadgesEnabled() || !Array.isArray(items) || !items.length) return '';
    return `
      <div style="display:flex; flex-wrap:wrap; gap:6px; margin-top:8px;">
        ${items.map((item) => `
          <span
            class="tile-status-badge state-pending"
            title="${App.escapeHtml(`${item.field} -> ${item.fallbackUsed}`)}"
            style="opacity:0.65;"
          >
            ${App.escapeHtml(`gap:${item.field}`)}
          </span>
        `).join('')}
      </div>
    `;
  }

  function patchRouter() {
    if (routerPatched || !window.App || typeof App._onRoute !== 'function') return;

    const originalOnRoute = App._onRoute.bind(App);
    App._onRoute = function patchedOnRoute() {
      const hash = String(location.hash || '').replace(/^#/, '') || 'home';
      if (CHAIN_BUILDER_ROUTE_RE.test(hash)) {
        if (!this.pages.chains) {
          location.hash = '#home';
          return;
        }
        this._loadPage('chains');
        return;
      }
      originalOnRoute();
    };

    routerPatched = true;
  }

  function getHashQueryParams() {
    const raw = String(window.location.hash || '').replace(/^#/, '');
    const [, queryString = ''] = raw.split('?');
    return new URLSearchParams(queryString);
  }

  function consumePendingParentStorage() {
    let raw = '';
    try {
      raw = sessionStorage.getItem('pendingChainParent') || '';
      sessionStorage.removeItem('pendingChainParent');
    } catch {
      return null;
    }

    if (!raw) return null;

    try {
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object') return null;
      const parentJobId = String(parsed.parent_job_id || parsed.parent || '').trim();
      const type = String(parsed.type || '').trim();
      return parentJobId ? { parentJobId, type, source: 'sessionStorage' } : null;
    } catch {
      return null;
    }
  }

  function resolveLaunchPrefill() {
    const params = getHashQueryParams();
    const parentJobId = String(params.get('parent') || '').trim();
    const type = String(params.get('type') || '').trim();
    const storagePrefill = consumePendingParentStorage();
    if (parentJobId) {
      return { parentJobId, type, source: 'hash' };
    }
    return storagePrefill;
  }

  async function fetchParentPrefill(parentJobId) {
    const related = await API.fetch(`/api/jobs/${encodeURIComponent(parentJobId)}/related`);
    const parentJob = related?.self;
    if (!parentJob?.id) {
      throw new Error(`Parent job ${parentJobId} not found`);
    }

    let chainId = String(related?.chain_id || '').trim();
    const debugBadges = [];
    let fallbackUsed = '';
    if (!chainId) {
      chainId = String(related?.chain_root_id || '').trim();
      fallbackUsed = 'related.chain_root_id';
    }
    if (!chainId) {
      chainId = String(parentJob.chain_id || '').trim();
      fallbackUsed = 'parentJob.chain_id';
    }
    if (!chainId) {
      chainId = String(parentJob.id || '').trim();
      fallbackUsed = 'parentJob.id';
    }
    if (fallbackUsed) {
      warnBackendGap({ field: 'chain_id', jobId: parentJob.id, fallbackUsed });
      debugBadges.push({ field: 'chain_id', fallbackUsed });
    }

    return {
      parentJobId: parentJob.id,
      parentType: parentJob.type || '',
      chainId,
      projectUrl: parentJob.project_url || '',
      profile: parentJob.profile || '',
      mediaId: parentJob.media_id || '',
      thumbUrl: parentJob.thumb_url || '',
      debugBadges,
    };
  }

  async function initializePrefill() {
    parentPrefill = null;
    prefillError = '';

    const launchPrefill = resolveLaunchPrefill();
    const requestedType = isValidSubsequentType(launchPrefill?.type) ? launchPrefill.type : '';
    if (requestedType) {
      selectedNextType = requestedType;
    }

    if (!launchPrefill?.parentJobId) return;

    try {
      parentPrefill = await fetchParentPrefill(launchPrefill.parentJobId);
      if (parentPrefill.profile) {
        pinnedProfile = parentPrefill.profile;
      }

      if (steps.length === 0) {
        const initialType = requestedType || selectedNextType || SUBSEQUENT_TYPES[0]?.id || '';
        if (initialType) {
          selectedNextType = initialType;
          steps = [emptyStep(initialType)];
        }
      }
    } catch (err) {
      parentPrefill = null;
      prefillError = `Failed to load parent job ${launchPrefill.parentJobId}: ${err.message}`;
    }
  }

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
    collapsedSteps.delete(steps.length - 1);
    syncCollapsedSteps();
    clearResult();
    refreshSteps();
  }
  function removeStep(i) {
    if (i === 0 && steps.length > 1) {
      App.toast('Cannot remove the first step while later steps exist.', 'warning');
      return;
    }
    steps.splice(i, 1);
    syncCollapsedSteps();
    clearResult();
    refreshSteps();
  }

  function refreshSteps() {
    syncCollapsedSteps();
    const container = document.getElementById('chain-steps');
    if (container) container.innerHTML = renderSteps();
    bindStepEvents();
    updateAddButtons();
    const btn = document.getElementById('submit-chain');
    if (btn) btn.disabled = steps.length === 0;
  }

  function renderProfileSelect() {
    const knownProfiles = Array.isArray(profiles) ? profiles : [];
    const renderedNames = new Set();
    const options = [];

    if (pinnedProfile && !knownProfiles.some((profile) => profile?.name === pinnedProfile)) {
      renderedNames.add(pinnedProfile);
      options.push(
        `<option value="${App.escapeHtml(pinnedProfile)}" selected>${App.escapeHtml(pinnedProfile)}</option>`,
      );
    }

    knownProfiles.forEach((profile) => {
      const name = String(profile?.name || '').trim();
      if (!name || renderedNames.has(name)) return;
      renderedNames.add(name);
      options.push(
        `<option value="${App.escapeHtml(name)}" ${pinnedProfile === name ? 'selected' : ''}>${App.escapeHtml(name)}</option>`,
      );
    });

    const disabled = parentPrefill?.profile ? ' disabled' : '';
    return `
      <select class="form-select" id="chain-profile" required${disabled}>
        <option value="">Select profile...</option>
        ${options.join('')}
      </select>
    `;
  }

  function updateAddButtons() {
    const el = document.getElementById('chain-add-buttons');
    if (!el) return;
    if (steps.length === 0) {
      if (parentPrefill) {
        const options = SUBSEQUENT_TYPES
          .map((t) => `<option value="${App.escapeHtml(t.id)}" ${selectedNextType === t.id ? 'selected' : ''}>${App.escapeHtml(t.label)}</option>`)
          .join('');
        el.innerHTML = `
          <p class="chain-inline-note">
            This builder continues an existing parent job, so the first step must be an L2 edit.
          </p>
          <div class="chain-inline-control">
            <label class="form-label" for="chain-next-type">First chained step <span class="required">*</span></label>
            <div class="chain-inline-control-main">
              <select class="form-select" id="chain-next-type">${options}</select>
              <button class="btn btn-primary" id="chain-add-next">
                <span class="material-icons">add_link</span> Add Step
              </button>
            </div>
            <span class="form-hint">Choose the first edit that should run against the inherited parent media.</span>
          </div>
        `;
        el.querySelector('#chain-next-type')?.addEventListener('change', (event) => {
          selectedNextType = event.target.value;
        });
        el.querySelector('#chain-add-next')?.addEventListener('click', () => addStep(selectedNextType));
      } else {
        const options = FIRST_TYPES
          .map((t) => `<option value="${App.escapeHtml(t.id)}" ${selectedFirstType === t.id ? 'selected' : ''}>${App.escapeHtml(t.label)}</option>`)
          .join('');
        el.innerHTML = `
          <p class="chain-inline-note">
            Chains must start with an L1 step that creates a new project.
          </p>
          <div class="chain-inline-control">
            <label class="form-label" for="chain-root-type">Root step <span class="required">*</span></label>
            <div class="chain-inline-control-main">
              <select class="form-select" id="chain-root-type">${options}</select>
              <button class="btn btn-primary" id="chain-add-root">
                <span class="material-icons">add_link</span> Add Root Step
              </button>
            </div>
            <span class="form-hint">Choose the L1 job that creates the chain's project.</span>
          </div>
        `;
        el.querySelector('#chain-root-type')?.addEventListener('change', (event) => {
          selectedFirstType = event.target.value;
        });
        el.querySelector('#chain-add-root')?.addEventListener('click', () => addStep(selectedFirstType));
      }
    } else {
      const options = SUBSEQUENT_TYPES
        .map((t) => `<option value="${App.escapeHtml(t.id)}" ${selectedNextType === t.id ? 'selected' : ''}>${App.escapeHtml(t.label)}</option>`)
        .join('');
      el.innerHTML = `
        <p class="chain-inline-note">
          Add the next chained edit. Only L2 job types are allowed after the root step.
        </p>
        <div class="chain-inline-control">
          <label class="form-label" for="chain-next-type">Next step <span class="required">*</span></label>
          <div class="chain-inline-control-main">
            <select class="form-select" id="chain-next-type">${options}</select>
            <button class="btn btn-outline" id="chain-add-next">
              <span class="material-icons">add</span> Add Step
            </button>
          </div>
          <span class="form-hint">Extend, insert, remove, and camera all chain off the parent media.</span>
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
      const meta = getJobTypeMeta(s.type) || {};
      const collapsed = collapsedSteps.has(i);
      return `
        <div class="chain-step">
          <div class="chain-step-card">
            <button type="button" class="chain-step-header chain-step-toggle" data-step-toggle="${i}" aria-expanded="${collapsed ? 'false' : 'true'}">
              <span class="chain-step-num">
                <span class="material-icons" style="font-size:16px; vertical-align:middle;">${meta.icon || 'work'}</span>
                Step ${i + 1}: ${App.escapeHtml(meta.label || s.type)}
              </span>
              <span class="chain-step-actions">
                <span class="material-icons chain-step-chevron">${collapsed ? 'expand_more' : 'expand_less'}</span>
              </span>
            </button>
            <div class="chain-step-body"${collapsed ? ' hidden' : ''}>
              ${renderStepConfig(s, i)}
              <div class="chain-step-footer">
                <button class="icon-btn step-remove" data-step-index="${i}" title="Remove step">
                  <span class="material-icons" style="font-size:18px;">close</span>
                </button>
              </div>
            </div>
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

    document.querySelectorAll('.chain-step-toggle').forEach((btn) => {
      btn.addEventListener('click', () => {
        const index = Number.parseInt(btn.dataset.stepToggle, 10);
        if (Number.isNaN(index)) return;
        if (collapsedSteps.has(index)) collapsedSteps.delete(index);
        else collapsedSteps.add(index);
        refreshSteps();
      });
    });

    document.querySelectorAll('.chain-copy-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const key = btn.dataset.copyValue;
        const label = btn.dataset.copyLabel || 'Value';
        const value = key === 'project_url' ? parentPrefill?.projectUrl : parentPrefill?.mediaId;
        copyText(value, label);
      });
    });
  }

  function validateChain() {
    if (!pinnedProfile) return 'Select a profile to pin the chain.';
    if (steps.length === 0) return 'Add at least one step.';

    for (let i = 0; i < steps.length; i++) {
      const s = steps[i];
      const meta = getJobTypeMeta(s.type);
      const label = meta?.label || s.type;

      if (i === 0 && parentPrefill && isL1Type(s.type)) {
        return `Step 1 (${label}) creates a new project and cannot continue an existing parent job.`;
      }
      if (i === 0 && !parentPrefill && !isL1Type(s.type)) {
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

  function buildJobPayload(step, extra = {}) {
    const job = { type: step.type };
    if (step.prompt) job.prompt = step.prompt;
    if (step.model) job.model = step.model;
    if (step.aspect_ratio) job.aspect_ratio = step.aspect_ratio;
    if (step.direction) job.direction = step.direction;
    if (step.start_image_path) job.start_image_path = step.start_image_path;
    if (step.end_image_path) job.end_image_path = step.end_image_path;
    if (step.ref_image_path) job.ref_image_path = step.ref_image_path;
    if (Array.isArray(step.ingredient_image_paths) && step.ingredient_image_paths.length > 0) {
      job.ingredient_image_paths = [...step.ingredient_image_paths];
    }
    if (step.bbox && ['x', 'y', 'w', 'h'].every((k) => typeof step.bbox[k] === 'number')) {
      job.bbox = { x: step.bbox.x, y: step.bbox.y, w: step.bbox.w, h: step.bbox.h };
    }

    if (extra.profile) job.profile = extra.profile;
    if (extra.parent_job_id) job.parent_job_id = extra.parent_job_id;
    if (extra.chain_id) job.chain_id = extra.chain_id;
    if (extra.project_url) job.project_url = extra.project_url;
    if (extra.media_id) job.media_id = extra.media_id;

    return job;
  }

  function buildPayload() {
    return {
      profile: pinnedProfile,
      jobs: steps.map((step) => buildJobPayload(step)),
    };
  }

  async function createParentPrefilledChain() {
    const createdJobs = [];
    let parentJobId = parentPrefill?.parentJobId || '';
    const chainId = parentPrefill?.chainId || '';

    try {
      for (let i = 0; i < steps.length; i++) {
        const extra = {
          profile: pinnedProfile,
          parent_job_id: parentJobId,
          chain_id: chainId,
        };

        if (i === 0) {
          if (parentPrefill?.projectUrl) extra.project_url = parentPrefill.projectUrl;
          if (parentPrefill?.mediaId) extra.media_id = parentPrefill.mediaId;
        }

        const job = await API.jobs.create(buildJobPayload(steps[i], extra));
        createdJobs.push(job);
        if (job?.id) {
          parentJobId = job.id;
        }
      }
    } catch (err) {
      err.createdJobs = createdJobs;
      throw err;
    }

    return {
      chain_id: createdJobs[0]?.chain_id || chainId || parentPrefill?.parentJobId || 'unknown',
      jobs: createdJobs,
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

  function renderPrefillBanner() {
    if (prefillError) {
      return `
        <div class="card" style="margin-bottom: 16px; border-color: var(--warning, #f39c12); background: rgba(243,156,18,0.05);">
          <div style="display:flex; align-items:flex-start; gap:10px;">
            <span class="material-icons" style="color: var(--warning, #f39c12);">warning</span>
            <div>
              <strong>Parent prefill unavailable</strong>
              <div style="font-size:13px; color:var(--text-secondary); margin-top:4px;">${App.escapeHtml(prefillError)}</div>
            </div>
          </div>
        </div>
      `;
    }

    if (!parentPrefill) return '';

    return `
      <div class="chain-parent-banner">
        <span class="material-icons">link</span>
        <span>Continue Chain</span>
        <span class="chain-parent-banner-meta">${App.escapeHtml(getJobTypeLabel(parentPrefill.parentType))}</span>
        <span class="chain-parent-banner-meta">${App.escapeHtml(parentPrefill.mediaId || 'unknown media')}</span>
        ${renderDebugBadges(parentPrefill.debugBadges)}
      </div>
    `;
  }

  function renderParentPanel() {
    if (!parentPrefill) return '';
    const thumb = String(parentPrefill.thumbUrl || '').trim();
    const projectUrl = String(parentPrefill.projectUrl || '').trim();
    const mediaId = String(parentPrefill.mediaId || '').trim();
    return `
      <aside class="card chain-parent-panel">
        <div class="chain-parent-thumb-wrap">
          ${thumb
            ? `<img class="chain-parent-thumb" src="${App.escapeHtml(thumb)}" alt="${App.escapeHtml(`${getJobTypeLabel(parentPrefill.parentType)} parent thumbnail`)}">`
            : `<div class="chain-parent-thumb chain-parent-thumb-placeholder"><span class="material-icons">video_library</span></div>`}
        </div>
        <div class="chain-parent-panel-head">
          <span class="chain-parent-type"><span class="material-icons">category</span>${App.escapeHtml(getJobTypeLabel(parentPrefill.parentType))}</span>
          <span class="chain-parent-profile"><span class="material-icons">lock</span>${App.escapeHtml(parentPrefill.profile || 'Profile locked')}</span>
        </div>
        <div class="chain-parent-field">
          <span class="chain-parent-field-label">Project URL</span>
          <div class="chain-parent-field-value">
            <code>${App.escapeHtml(truncateMiddle(projectUrl || 'Unavailable', 48))}</code>
            ${projectUrl ? '<button type="button" class="icon-btn chain-copy-btn" data-copy-label="Project URL" data-copy-value="project_url" title="Copy project URL"><span class="material-icons">content_copy</span></button>' : ''}
          </div>
        </div>
        <div class="chain-parent-field">
          <span class="chain-parent-field-label">Media ID</span>
          <div class="chain-parent-field-value">
            <code>${App.escapeHtml(truncateMiddle(mediaId || 'Unavailable', 32))}</code>
            ${mediaId ? '<button type="button" class="icon-btn chain-copy-btn" data-copy-label="Media ID" data-copy-value="media_id" title="Copy media ID"><span class="material-icons">content_copy</span></button>' : ''}
          </div>
        </div>
        <a class="chain-parent-link" href="#job-detail?id=${encodeURIComponent(parentPrefill.parentJobId)}">
          View parent <span class="material-icons">arrow_forward</span>
        </a>
      </aside>
    `;
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
      collapsedSteps = new Set();
      await fetchProfiles();
      await initializePrefill();

      return `
        <style>
          .chain-layout{display:grid;grid-template-columns:minmax(0,480px) minmax(320px,1fr);gap:16px;align-items:start}.chain-stack{display:grid;gap:16px}.chain-builder-card,.chain-step-card,.chain-parent-panel,.chain-parent-banner{border:1px solid rgba(255,255,255,0.08);border-radius:12px}.chain-builder-card{padding:14px}.chain-parent-banner{display:flex;align-items:center;gap:8px;padding:10px 14px;margin-bottom:16px;background:rgba(255,255,255,0.03)}.chain-parent-banner-meta{font-size:12px;color:var(--text-muted)}.chain-parent-panel{padding:14px;display:grid;gap:12px;position:sticky;top:16px}.chain-parent-thumb-wrap{border-radius:12px;overflow:hidden;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08)}.chain-parent-thumb{display:block;width:100%;height:220px;object-fit:cover}.chain-parent-thumb-placeholder{display:grid;place-items:center;color:var(--text-muted)}.chain-parent-thumb-placeholder .material-icons{font-size:40px}.chain-parent-panel-head{display:flex;flex-wrap:wrap;gap:8px}.chain-parent-type,.chain-parent-profile{display:inline-flex;align-items:center;gap:6px;padding:6px 10px;border-radius:999px;background:rgba(255,255,255,0.04);font-size:12px}.chain-parent-field{display:grid;gap:6px}.chain-parent-field-label{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--text-muted)}.chain-parent-field-value{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:10px 12px;border-radius:12px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.08)}.chain-parent-field-value code{font-size:12px;color:var(--text-secondary)}.chain-parent-link{display:inline-flex;align-items:center;gap:6px;color:var(--accent);text-decoration:none}.chain-inline-note{color:var(--text-muted);font-size:13px;margin-bottom:10px}.chain-inline-control{display:grid;gap:8px}.chain-inline-control-main{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;align-items:center}.chain-step-card{padding:12px}.chain-step-header{width:100%;display:flex;align-items:center;justify-content:space-between;gap:12px;background:none;border:0;padding:0;text-align:left}.chain-step-body{padding-top:12px}.chain-step-footer{display:flex;justify-content:flex-end;padding-top:8px}.chain-profile-row{display:grid;grid-template-columns:96px minmax(0,1fr);gap:12px;align-items:center}.chain-actions{display:flex;gap:12px}@media (max-width:899px){.chain-layout{grid-template-columns:1fr}.chain-parent-panel{position:static}.chain-profile-row,.chain-inline-control-main{grid-template-columns:1fr}.chain-actions{flex-direction:column}}
        </style>
        <div class="chain-layout">
          <div class="chain-stack">
            <div class="card chain-builder-card">
            <h3 style="margin-bottom: 8px; font-size: 16px; font-weight: 600;">Build a Job Chain</h3>
            <p style="color: var(--text-muted); font-size: 13px;">
              Chains run sequentially on a single profile. Start with an L1 project-creating step,
              then add only L2 edits that chain off the parent media.
            </p>
            </div>

            ${renderPrefillBanner()}

            <div class="card chain-builder-card" style="margin-bottom:16px;">
              <div class="chain-profile-row">
                <label class="form-label" for="chain-profile">Profile <span class="required">*</span></label>
                <div>
                  ${renderProfileSelect()}
                  <span class="form-hint">${parentPrefill?.profile
                    ? 'Inherited from the parent job and locked to the same Google account.'
                    : 'All steps run on this Google account. L2+ inherits it automatically.'}</span>
                </div>
              </div>
            </div>

            <div class="chain-timeline" id="chain-steps"></div>

            <div class="card chain-builder-card" id="chain-add-buttons" style="margin-bottom: 16px;"></div>

            <div class="chain-actions">
              <button class="btn btn-primary" id="submit-chain" disabled>
                <span class="material-icons">send</span> Submit Chain
              </button>
              <button class="btn btn-outline" id="reset-chain">
                <span class="material-icons">refresh</span> Reset
              </button>
            </div>

            <div id="chain-result" style="margin-top: 16px;"></div>
          </div>
          ${renderParentPanel()}
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
          const result = parentPrefill
            ? await createParentPrefilledChain()
            : await API.chains.create(buildPayload());
          const cid = result?.chain_id || result?.id || 'unknown';
          showResult(
            'ok',
            'Chain submitted',
            `Chain ID: <code>${App.escapeHtml(String(cid))}</code> — ${steps.length} step(s)`,
          );
          App.toast('Chain submitted', 'success');
        } catch (e) {
          const createdCount = Array.isArray(e.createdJobs) ? e.createdJobs.length : 0;
          const suffix = createdCount > 0 ? ` after queuing ${createdCount} step(s)` : '';
          showResult('err', 'Chain failed', App.escapeHtml(`${e.message}${suffix}`));
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

  patchRouter();
  App.register(ChainBuilderPage);
})();
