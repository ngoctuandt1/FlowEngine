/**
 * Chain Builder — sequence a chain of Flow ops starting with T2V.
 *
 * Backend contract: POST /api/chains with
 *   { profile: "<name>", jobs: [JobCreate, JobCreate, ...] }
 * See server/models/chain.py :: ChainCreate.
 *
 * Step field names mirror server/models/job.py JobCreate:
 *   type, prompt, model, aspect_ratio, direction, bbox{x,y,w,h}.
 */
(() => {
  const {
    JOB_TYPES, MODELS, DEFAULT_MODEL, ASPECT_RATIOS, DEFAULT_ASPECT,
    CAMERA_PRESETS,
    TYPES_WITH_PROMPT, TYPES_WITH_BBOX, TYPES_WITH_MODEL, TYPES_WITH_ASPECT,
  } = CONST;

  // First step must be t2v. Subsequent steps exclude t2v.
  const FIRST_TYPE = 'text-to-video';
  const L1_ONLY_TYPES = new Set(['text-to-video', 'frames-to-video', 'text-to-image']);
  const SUBSEQUENT_TYPES = JOB_TYPES.filter((t) => !L1_ONLY_TYPES.has(t.id));

  let steps = [];
  let profiles = [];
  let pinnedProfile = '';

  function emptyStep(type) {
    return {
      type,
      prompt: '',
      model: type === FIRST_TYPE ? DEFAULT_MODEL : '',
      aspect_ratio: type === FIRST_TYPE ? DEFAULT_ASPECT : '',
      direction: '',
      bbox: null,
    };
  }

  function addStep(type) { steps.push(emptyStep(type)); refreshSteps(); }
  function removeStep(i) {
    if (i === 0 && steps.length > 1) {
      App.toast('Cannot remove the first step while later steps exist.', 'warning');
      return;
    }
    steps.splice(i, 1);
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
      el.innerHTML = `
        <p style="color: var(--text-muted); font-size: 13px; margin-bottom: 12px;">
          Chains must start with a Text-to-Video step.
        </p>
        <button class="btn btn-primary" data-add-type="${FIRST_TYPE}">
          <span class="material-icons">videocam</span> Add Text to Video
        </button>
      `;
    } else {
      const buttons = SUBSEQUENT_TYPES.map((t) => `
        <button class="btn btn-outline btn-sm" data-add-type="${t.id}">
          <span class="material-icons" style="font-size:16px">${t.icon}</span> ${t.label}
        </button>
      `).join('');
      el.innerHTML = `
        <p style="color: var(--text-muted); font-size: 13px; margin-bottom: 12px;">Add next step:</p>
        <div style="display: flex; flex-wrap: wrap; gap: 8px;">${buttons}</div>
      `;
    }
    el.querySelectorAll('[data-add-type]').forEach((btn) => {
      btn.addEventListener('click', () => addStep(btn.dataset.addType));
    });
  }

  function renderStepConfig(step, i) {
    const parts = [];
    const tag = (field, html) => parts.push(html);

    if (TYPES_WITH_PROMPT.has(step.type)) {
      const req = step.type === 'text-to-video' || step.type === 'insert-object';
      tag('prompt', `
        <div class="form-group" style="margin-bottom:12px">
          <label class="form-label">Prompt ${req ? '<span class="required">*</span>' : '(optional)'}</label>
          <textarea class="form-textarea step-field" data-step="${i}" data-field="prompt"
                    rows="2" placeholder="Describe...">${App.escapeHtml(step.prompt)}</textarea>
        </div>
      `);
    }

    if (TYPES_WITH_MODEL.has(step.type)) {
      const opts = MODELS
        .map((m) => `<option value="${m.value}" ${step.model === m.value ? 'selected' : ''}>${App.escapeHtml(m.label)}</option>`)
        .join('');
      tag('model', `
        <div class="form-group" style="margin-bottom:12px">
          <label class="form-label">Model</label>
          <select class="form-select step-field" data-step="${i}" data-field="model">${opts}</select>
        </div>
      `);
    }

    if (TYPES_WITH_ASPECT.has(step.type)) {
      const opts = ASPECT_RATIOS
        .map((r) => `<option value="${r.value}" ${step.aspect_ratio === r.value ? 'selected' : ''}>${App.escapeHtml(r.label)}</option>`)
        .join('');
      tag('aspect_ratio', `
        <div class="form-group" style="margin-bottom:12px">
          <label class="form-label">Aspect Ratio</label>
          <select class="form-select step-field" data-step="${i}" data-field="aspect_ratio">${opts}</select>
        </div>
      `);
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
        if (steps[i]) steps[i][f] = el.value;
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
      };
      el.addEventListener('input', handler);
      el.addEventListener('change', handler);
    });

    document.querySelectorAll('.step-remove').forEach((btn) => {
      btn.addEventListener('click', () => removeStep(parseInt(btn.dataset.stepIndex)));
    });
  }

  function validateChain() {
    if (!pinnedProfile) return 'Select a profile to pin the chain.';
    if (steps.length === 0) return 'Add at least one step.';
    if (steps[0].type !== FIRST_TYPE) return 'First step must be Text-to-Video.';
    if (!steps[0].prompt.trim()) return 'Step 1 requires a prompt.';

    for (let i = 0; i < steps.length; i++) {
      const s = steps[i];
      if (s.type === 'insert-object' && !s.prompt.trim()) return `Step ${i + 1} (Insert) requires a prompt.`;
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

  const ChainBuilderPage = {
    name: 'chains',
    title: 'Chain Builder',
    icon: 'link',

    async render() {
      steps = [];
      pinnedProfile = '';
      await fetchProfiles();

      return `
        <div style="max-width: 800px;">
          <div class="card" style="margin-bottom: 16px;">
            <h3 style="margin-bottom: 8px; font-size: 16px; font-weight: 600;">Build a Job Chain</h3>
            <p style="color: var(--text-muted); font-size: 13px;">
              Chains run sequentially on a single profile. Start with Text-to-Video,
              then extend / insert / remove / camera.
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
      });

      document.getElementById('submit-chain')?.addEventListener('click', async () => {
        const err = validateChain();
        if (err) { App.toast(err, 'warning'); return; }

        const btn = document.getElementById('submit-chain');
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> Submitting...';

        try {
          const result = await API.chains.create(buildPayload());
          const cid = result?.chain_id || result?.id || 'unknown';
          document.getElementById('chain-result').innerHTML = `
            <div class="card" style="border-color: var(--success); background: rgba(46,204,113,0.05);">
              <div style="display:flex; align-items:center; gap:10px;">
                <span class="material-icons" style="color:var(--success);">check_circle</span>
                <div><strong>Chain submitted</strong>
                  <div style="font-size:13px; color:var(--text-secondary); margin-top:4px;">
                    Chain ID: <code>${App.escapeHtml(String(cid))}</code> — ${steps.length} step(s)
                  </div>
                </div>
              </div>
            </div>
          `;
          App.toast('Chain submitted', 'success');
        } catch (e) {
          document.getElementById('chain-result').innerHTML = `
            <div class="card" style="border-color: var(--error); background: rgba(231,76,60,0.05);">
              <div style="display:flex; align-items:center; gap:10px;">
                <span class="material-icons" style="color:var(--error);">error</span>
                <div><strong>Chain failed</strong>
                  <div style="font-size:13px; color:var(--text-secondary); margin-top:4px;">
                    ${App.escapeHtml(e.message)}
                  </div>
                </div>
              </div>
            </div>
          `;
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
