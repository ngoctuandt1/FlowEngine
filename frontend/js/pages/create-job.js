/**
 * Create Job Page
 * Job creation form with dynamic fields based on job type.
 */
(() => {
  const JOB_TYPES = [
    { id: 'text-to-video', label: 'Text to Video', icon: 'videocam', shortLabel: 'T2V' },
    { id: 'extend', label: 'Extend', icon: 'add_to_queue', shortLabel: 'Extend' },
    { id: 'insert', label: 'Insert', icon: 'add_box', shortLabel: 'Insert' },
    { id: 'remove', label: 'Remove', icon: 'delete_sweep', shortLabel: 'Remove' },
    { id: 'camera', label: 'Camera', icon: 'videocam_off', shortLabel: 'Camera' },
  ];

  const MODELS = [
    { value: '', label: 'Default' },
    { value: 'kling-v2.1', label: 'Kling v2.1' },
    { value: 'kling-v2.0', label: 'Kling v2.0' },
    { value: 'kling-v1.6', label: 'Kling v1.6' },
    { value: 'kling-v1.5', label: 'Kling v1.5' },
  ];

  const ASPECT_RATIOS = [
    { value: '', label: 'Default' },
    { value: '16:9', label: '16:9 (Landscape)' },
    { value: '9:16', label: '9:16 (Portrait)' },
    { value: '1:1', label: '1:1 (Square)' },
  ];

  const CAMERA_PRESETS = [
    'Orbit Left', 'Orbit Right',
    'Pan Left', 'Pan Right',
    'Pedestal Up', 'Pedestal Down',
    'Tilt Up', 'Tilt Down',
    'Zoom In', 'Zoom Out',
    'Dolly In', 'Dolly Out',
    'Crane Up',
    'Roll CW', 'Roll CCW',
  ];

  let selectedType = 'text-to-video';

  function renderTypeSelector() {
    return `
      <div class="type-selector" id="type-selector">
        ${JOB_TYPES.map(
          (t) => `
          <div class="type-option ${t.id === selectedType ? 'selected' : ''}" data-type="${t.id}">
            <span class="material-icons">${t.icon}</span>
            <div class="type-option-label">${t.shortLabel}</div>
          </div>
        `
        ).join('')}
      </div>
    `;
  }

  function renderOptions(items) {
    return items.map((o) => `<option value="${o.value}">${App.escapeHtml(o.label)}</option>`).join('');
  }

  function renderFields() {
    const fields = [];

    // Prompt field (required for t2v, insert; optional for extend; hidden for remove)
    if (selectedType !== 'remove') {
      const required = selectedType === 'text-to-video' || selectedType === 'insert';
      fields.push(`
        <div class="form-group">
          <label class="form-label">Prompt ${required ? '<span class="required">*</span>' : '(optional)'}</label>
          <textarea class="form-textarea" id="field-prompt" placeholder="Describe the video you want to generate..."
            ${required ? 'required' : ''}></textarea>
        </div>
      `);
    }

    // Parent job / project URL (for non-t2v types)
    if (selectedType !== 'text-to-video') {
      fields.push(`
        <div class="form-row">
          <div class="form-group">
            <label class="form-label">Parent Job ID</label>
            <input type="text" class="form-input" id="field-parent-job" placeholder="e.g. abc123">
            <span class="form-hint">ID of the job to build upon</span>
          </div>
          <div class="form-group">
            <label class="form-label">Project URL</label>
            <input type="text" class="form-input" id="field-project-url" placeholder="https://...">
            <span class="form-hint">Or provide a project URL instead</span>
          </div>
        </div>
      `);
    }

    // Model selector (for t2v, extend, insert)
    if (['text-to-video', 'extend', 'insert'].includes(selectedType)) {
      fields.push(`
        <div class="form-row">
          <div class="form-group">
            <label class="form-label">Model</label>
            <select class="form-select" id="field-model">${renderOptions(MODELS)}</select>
          </div>
          ${selectedType === 'text-to-video' ? `
          <div class="form-group">
            <label class="form-label">Aspect Ratio</label>
            <select class="form-select" id="field-aspect-ratio">${renderOptions(ASPECT_RATIOS)}</select>
          </div>
          ` : ''}
        </div>
      `);
    }

    // BBox fields (for insert, remove)
    if (selectedType === 'insert' || selectedType === 'remove') {
      fields.push(`
        <div class="form-group">
          <label class="form-label">Bounding Box (optional)</label>
          <div class="form-row" style="grid-template-columns: repeat(4, 1fr);">
            <div class="form-group">
              <label class="form-label" style="font-size:11px">X</label>
              <input type="number" class="form-input" id="field-bbox-x" placeholder="0" min="0">
            </div>
            <div class="form-group">
              <label class="form-label" style="font-size:11px">Y</label>
              <input type="number" class="form-input" id="field-bbox-y" placeholder="0" min="0">
            </div>
            <div class="form-group">
              <label class="form-label" style="font-size:11px">Width</label>
              <input type="number" class="form-input" id="field-bbox-w" placeholder="100" min="1">
            </div>
            <div class="form-group">
              <label class="form-label" style="font-size:11px">Height</label>
              <input type="number" class="form-input" id="field-bbox-h" placeholder="100" min="1">
            </div>
          </div>
          <span class="form-hint">Pixel coordinates for the region of interest</span>
        </div>
      `);
    }

    // Camera direction (for camera type)
    if (selectedType === 'camera') {
      const dirOptions = CAMERA_PRESETS.map(
        (p) => `<option value="${p}">${p}</option>`
      ).join('');
      fields.push(`
        <div class="form-group">
          <label class="form-label">Camera Direction <span class="required">*</span></label>
          <select class="form-select" id="field-camera-direction" required>
            <option value="">Select a preset...</option>
            ${dirOptions}
          </select>
        </div>
      `);
    }

    return fields.join('');
  }

  function collectFormData() {
    const data = { type: selectedType };

    const prompt = document.getElementById('field-prompt')?.value?.trim();
    if (prompt) data.prompt = prompt;

    const parentJob = document.getElementById('field-parent-job')?.value?.trim();
    if (parentJob) data.parent_job_id = parentJob;

    const projectUrl = document.getElementById('field-project-url')?.value?.trim();
    if (projectUrl) data.project_url = projectUrl;

    const model = document.getElementById('field-model')?.value;
    if (model) data.model = model;

    const aspectRatio = document.getElementById('field-aspect-ratio')?.value;
    if (aspectRatio) data.aspect_ratio = aspectRatio;

    // BBox
    const bx = document.getElementById('field-bbox-x')?.value;
    const by = document.getElementById('field-bbox-y')?.value;
    const bw = document.getElementById('field-bbox-w')?.value;
    const bh = document.getElementById('field-bbox-h')?.value;
    if (bx !== '' && by !== '' && bw !== '' && bh !== '') {
      data.bbox = {
        x: parseInt(bx),
        y: parseInt(by),
        width: parseInt(bw),
        height: parseInt(bh),
      };
    }

    // Camera direction
    const camDir = document.getElementById('field-camera-direction')?.value;
    if (camDir) data.camera_direction = camDir;

    return data;
  }

  function validate(data) {
    if (data.type === 'text-to-video' && !data.prompt) {
      return 'Prompt is required for Text-to-Video jobs.';
    }
    if (data.type === 'insert' && !data.prompt) {
      return 'Prompt is required for Insert jobs.';
    }
    if (data.type === 'camera' && !data.camera_direction) {
      return 'Camera direction is required.';
    }
    if (data.type !== 'text-to-video' && !data.parent_job_id && !data.project_url) {
      return 'Parent Job ID or Project URL is required for this job type.';
    }
    return null;
  }

  const CreateJobPage = {
    name: 'create',
    title: 'Create Job',
    icon: 'add_circle',

    async render() {
      return `
        <div class="card" style="max-width: 800px;">
          <h3 style="margin-bottom: 20px; font-size: 16px; font-weight: 600;">Select Job Type</h3>
          ${renderTypeSelector()}
          <div id="job-fields">
            ${renderFields()}
          </div>
          <div style="display: flex; gap: 12px; margin-top: 24px;">
            <button class="btn btn-primary" id="submit-job">
              <span class="material-icons">send</span> Create Job
            </button>
            <button class="btn btn-outline" id="reset-form">
              <span class="material-icons">refresh</span> Reset
            </button>
          </div>
          <div id="create-result" style="margin-top: 16px;"></div>
        </div>
      `;
    },

    mount() {
      // Type selector
      document.getElementById('type-selector')?.addEventListener('click', (e) => {
        const option = e.target.closest('.type-option');
        if (!option) return;

        selectedType = option.dataset.type;
        document.querySelectorAll('.type-option').forEach((el) => {
          el.classList.toggle('selected', el.dataset.type === selectedType);
        });

        document.getElementById('job-fields').innerHTML = renderFields();
      });

      // Submit
      document.getElementById('submit-job')?.addEventListener('click', async () => {
        const data = collectFormData();
        const error = validate(data);
        if (error) {
          App.toast(error, 'warning');
          return;
        }

        const btn = document.getElementById('submit-job');
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> Creating...';

        try {
          const result = await API.jobs.create(data);
          const jobId = result?.id || result?.job_id || 'unknown';
          document.getElementById('create-result').innerHTML = `
            <div class="card" style="border-color: var(--success); background: rgba(46,204,113,0.05);">
              <div style="display:flex; align-items:center; gap:10px;">
                <span class="material-icons" style="color:var(--success);">check_circle</span>
                <div>
                  <strong>Job created successfully!</strong>
                  <div style="font-size:13px; color:var(--text-secondary); margin-top:4px;">
                    Job ID: <code>${App.escapeHtml(String(jobId))}</code>
                  </div>
                </div>
              </div>
            </div>
          `;
          App.toast('Job created successfully!', 'success');
        } catch (err) {
          document.getElementById('create-result').innerHTML = `
            <div class="card" style="border-color: var(--error); background: rgba(231,76,60,0.05);">
              <div style="display:flex; align-items:center; gap:10px;">
                <span class="material-icons" style="color:var(--error);">error</span>
                <div>
                  <strong>Failed to create job</strong>
                  <div style="font-size:13px; color:var(--text-secondary); margin-top:4px;">
                    ${App.escapeHtml(err.message)}
                  </div>
                </div>
              </div>
            </div>
          `;
          App.toast('Failed to create job: ' + err.message, 'error');
        } finally {
          btn.disabled = false;
          btn.innerHTML = '<span class="material-icons">send</span> Create Job';
        }
      });

      // Reset
      document.getElementById('reset-form')?.addEventListener('click', () => {
        selectedType = 'text-to-video';
        App._loadPage('create');
      });
    },
  };

  App.register(CreateJobPage);
})();
