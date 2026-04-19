/**
 * Chain Builder Page
 * Visual chain builder: add operations in sequence, submit as a chain.
 */
(() => {
  const STEP_TYPES = [
    { id: 'text-to-video', label: 'Text to Video', icon: 'videocam', firstOnly: true },
    { id: 'extend', label: 'Extend', icon: 'add_to_queue' },
    { id: 'insert', label: 'Insert', icon: 'add_box' },
    { id: 'remove', label: 'Remove', icon: 'delete_sweep' },
    { id: 'camera', label: 'Camera', icon: 'videocam_off' },
  ];

  // MODELS / ASPECT_RATIOS / CAMERA_PRESETS live in js/config.js (FlowConfig)
  // and are shared with the single-job Create page — see P2a.

  let steps = [];

  function addStep(type) {
    steps.push({
      type,
      prompt: '',
      model: '',
      aspect_ratio: '',
      camera_direction: '',
      bbox: null,
    });
    refreshSteps();
  }

  function removeStep(index) {
    // Don't allow removing first step if there are subsequent steps
    if (index === 0 && steps.length > 1) {
      App.toast('Cannot remove the first step while other steps exist.', 'warning');
      return;
    }
    steps.splice(index, 1);
    refreshSteps();
  }

  function refreshSteps() {
    const container = document.getElementById('chain-steps');
    if (!container) return;
    container.innerHTML = renderSteps();
    bindStepEvents();
    updateAddButtons();
  }

  function updateAddButtons() {
    const addContainer = document.getElementById('chain-add-buttons');
    if (!addContainer) return;

    if (steps.length === 0) {
      // Only t2v allowed as first step
      addContainer.innerHTML = `
        <p style="color: var(--text-muted); font-size: 13px; margin-bottom: 12px;">
          Chains must start with a Text-to-Video step.
        </p>
        <button class="btn btn-primary" data-add-type="text-to-video">
          <span class="material-icons">videocam</span> Add Text to Video
        </button>
      `;
    } else {
      // Subsequent steps: everything except t2v
      const buttons = STEP_TYPES.filter((t) => !t.firstOnly)
        .map(
          (t) => `
          <button class="btn btn-outline btn-sm" data-add-type="${t.id}">
            <span class="material-icons" style="font-size:16px">${t.icon}</span> ${t.label}
          </button>
        `
        )
        .join('');
      addContainer.innerHTML = `
        <p style="color: var(--text-muted); font-size: 13px; margin-bottom: 12px;">Add next step:</p>
        <div style="display: flex; flex-wrap: wrap; gap: 8px;">${buttons}</div>
      `;
    }

    // Bind add buttons
    addContainer.querySelectorAll('[data-add-type]').forEach((btn) => {
      btn.addEventListener('click', () => addStep(btn.dataset.addType));
    });
  }

  function renderStepConfig(step, index) {
    let html = '';

    // Prompt (for t2v, extend, insert)
    if (step.type !== 'remove' && step.type !== 'camera') {
      const req = step.type === 'text-to-video' || step.type === 'insert';
      html += `
        <div class="form-group" style="margin-bottom:12px">
          <label class="form-label">Prompt ${req ? '<span class="required">*</span>' : '(optional)'}</label>
          <textarea class="form-textarea step-field" data-step="${index}" data-field="prompt"
            placeholder="Describe..." rows="2">${App.escapeHtml(step.prompt)}</textarea>
        </div>
      `;
    }

    // Model (for t2v, extend, insert)
    if (['text-to-video', 'extend', 'insert'].includes(step.type)) {
      const modelOpts = FlowConfig.MODELS.map(
        (m) =>
          `<option value="${m.value}" ${step.model === m.value ? 'selected' : ''}>${App.escapeHtml(m.label)}</option>`
      ).join('');
      html += `
        <div class="form-group" style="margin-bottom:12px">
          <label class="form-label">Model</label>
          <select class="form-select step-field" data-step="${index}" data-field="model">${modelOpts}</select>
        </div>
      `;
    }

    // Aspect ratio (t2v only)
    if (step.type === 'text-to-video') {
      const aspectOpts = FlowConfig.ASPECT_RATIOS.map(
        (a) =>
          `<option value="${a.value}" ${step.aspect_ratio === a.value ? 'selected' : ''}>${App.escapeHtml(a.label)}</option>`
      ).join('');
      html += `
        <div class="form-group" style="margin-bottom:12px">
          <label class="form-label">Aspect Ratio</label>
          <select class="form-select step-field" data-step="${index}" data-field="aspect_ratio">${aspectOpts}</select>
        </div>
      `;
    }

    // Camera direction
    if (step.type === 'camera') {
      const camOpts = FlowConfig.CAMERA_PRESETS.map(
        (p) =>
          `<option value="${p}" ${step.camera_direction === p ? 'selected' : ''}>${p}</option>`
      ).join('');
      html += `
        <div class="form-group" style="margin-bottom:12px">
          <label class="form-label">Direction <span class="required">*</span></label>
          <select class="form-select step-field" data-step="${index}" data-field="camera_direction">
            <option value="">Select preset...</option>
            ${camOpts}
          </select>
        </div>
      `;
    }

    return html;
  }

  function renderSteps() {
    if (steps.length === 0) return '';

    return steps
      .map((step, i) => {
        const typeMeta = STEP_TYPES.find((t) => t.id === step.type) || {};
        return `
          <div class="chain-step">
            <div class="chain-step-card">
              <div class="chain-step-header">
                <span class="chain-step-num">
                  <span class="material-icons" style="font-size:16px; vertical-align:middle;">${typeMeta.icon || 'work'}</span>
                  Step ${i + 1}: ${App.escapeHtml(typeMeta.label || step.type)}
                </span>
                <button class="icon-btn step-remove" data-step-index="${i}" title="Remove step">
                  <span class="material-icons" style="font-size:18px;">close</span>
                </button>
              </div>
              ${renderStepConfig(step, i)}
            </div>
          </div>
        `;
      })
      .join('');
  }

  function bindStepEvents() {
    // Field changes
    document.querySelectorAll('.step-field').forEach((el) => {
      el.addEventListener('input', () => {
        const idx = parseInt(el.dataset.step);
        const field = el.dataset.field;
        if (steps[idx]) steps[idx][field] = el.value;
      });
      el.addEventListener('change', () => {
        const idx = parseInt(el.dataset.step);
        const field = el.dataset.field;
        if (steps[idx]) steps[idx][field] = el.value;
      });
    });

    // Remove buttons
    document.querySelectorAll('.step-remove').forEach((btn) => {
      btn.addEventListener('click', () => {
        removeStep(parseInt(btn.dataset.stepIndex));
      });
    });
  }

  function validateChain() {
    if (steps.length === 0) return 'Add at least one step to the chain.';
    if (steps[0].type !== 'text-to-video') return 'First step must be Text-to-Video.';

    for (let i = 0; i < steps.length; i++) {
      const missing = FlowConfig.missingRequiredLabel(steps[i].type, steps[i]);
      if (missing) {
        const typeLabel = FlowConfig.TYPE_LABELS[steps[i].type] || steps[i].type;
        return `Step ${i + 1} (${typeLabel}) requires ${missing.toLowerCase()}.`;
      }
    }
    return null;
  }

  function buildPayload() {
    return {
      steps: steps.map((s) => {
        const step = { type: s.type };
        if (s.prompt) step.prompt = s.prompt;
        if (s.model) step.model = s.model;
        if (s.aspect_ratio) step.aspect_ratio = s.aspect_ratio;
        if (s.camera_direction) step.camera_direction = s.camera_direction;
        return step;
      }),
    };
  }

  const ChainBuilderPage = {
    name: 'chains',
    title: 'Chain Builder',
    icon: 'link',

    async render() {
      steps = [];

      return `
        <div style="max-width: 800px;">
          <div class="card" style="margin-bottom: 16px;">
            <h3 style="margin-bottom: 8px; font-size: 16px; font-weight: 600;">Build a Job Chain</h3>
            <p style="color: var(--text-muted); font-size: 13px;">
              Chains execute operations in sequence. Start with Text-to-Video, then add extend, insert, remove, or camera steps.
            </p>
          </div>

          <div class="chain-timeline" id="chain-steps">
            <!-- Steps render here -->
          </div>

          <div class="card" id="chain-add-buttons" style="margin-bottom: 16px;">
            <!-- Add buttons render here -->
          </div>

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
      updateAddButtons();

      // Submit chain
      document.getElementById('submit-chain')?.addEventListener('click', async () => {
        const error = validateChain();
        if (error) {
          App.toast(error, 'warning');
          return;
        }

        const btn = document.getElementById('submit-chain');
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> Submitting...';

        try {
          const result = await API.chains.create(buildPayload());
          const chainId = result?.id || result?.chain_id || 'unknown';
          document.getElementById('chain-result').innerHTML = `
            <div class="card" style="border-color: var(--success); background: rgba(46,204,113,0.05);">
              <div style="display:flex; align-items:center; gap:10px;">
                <span class="material-icons" style="color:var(--success);">check_circle</span>
                <div>
                  <strong>Chain submitted!</strong>
                  <div style="font-size:13px; color:var(--text-secondary); margin-top:4px;">
                    Chain ID: <code>${App.escapeHtml(String(chainId))}</code>
                    &mdash; ${steps.length} step(s) queued
                  </div>
                </div>
              </div>
            </div>
          `;
          App.toast('Chain submitted successfully!', 'success');
        } catch (err) {
          document.getElementById('chain-result').innerHTML = `
            <div class="card" style="border-color: var(--error); background: rgba(231,76,60,0.05);">
              <div style="display:flex; align-items:center; gap:10px;">
                <span class="material-icons" style="color:var(--error);">error</span>
                <div>
                  <strong>Chain submission failed</strong>
                  <div style="font-size:13px; color:var(--text-secondary); margin-top:4px;">
                    ${App.escapeHtml(err.message)}
                  </div>
                </div>
              </div>
            </div>
          `;
          App.toast('Chain failed: ' + err.message, 'error');
        } finally {
          btn.disabled = steps.length === 0;
          btn.innerHTML = '<span class="material-icons">send</span> Submit Chain';
        }
      });

      // Reset
      document.getElementById('reset-chain')?.addEventListener('click', () => {
        steps = [];
        refreshSteps();
        updateAddButtons();
        document.getElementById('chain-result').innerHTML = '';
        document.getElementById('submit-chain').disabled = true;
      });

      // Enable submit when steps exist (observe via MutationObserver)
      const observer = new MutationObserver(() => {
        const btn = document.getElementById('submit-chain');
        if (btn) btn.disabled = steps.length === 0;
      });
      const stepsEl = document.getElementById('chain-steps');
      if (stepsEl) observer.observe(stepsEl, { childList: true, subtree: true });
    },
  };

  App.register(ChainBuilderPage);
})();
