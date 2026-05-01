/**
 * Workflows Page
 * Template instantiate UI plus auto-prompt helpers.
 */
(() => {
  const PLACEHOLDER_RE = /\{\{([^{}]+)\}\}/g;

  let root = null;
  let handlers = null;
  let state = {
    templates: [],
    selectedTemplateId: null,
    vars: {},
    instantiating: false,
    lastRun: null,
    promptIdea: '',
    generatedPrompt: '',
    promptSource: '',
    promptBusy: false,
  };

  function currentTemplate() {
    return state.templates.find((template) => template.id === state.selectedTemplateId) || null;
  }

  function scanPlaceholders(value, found, seen) {
    if (typeof value === 'string') {
      for (const match of value.matchAll(PLACEHOLDER_RE)) {
        const varName = match[1];
        if (!seen.has(varName)) {
          seen.add(varName);
          found.push(varName);
        }
      }
      return;
    }

    if (Array.isArray(value)) {
      value.forEach((item) => scanPlaceholders(item, found, seen));
      return;
    }

    if (value && typeof value === 'object') {
      Object.values(value).forEach((item) => scanPlaceholders(item, found, seen));
    }
  }

  function extractVariables(template) {
    const found = [];
    scanPlaceholders(template?.steps || [], found, new Set());
    return found;
  }

  function syncTemplateSelection() {
    const template = currentTemplate();
    if (template) {
      const nextVars = {};
      extractVariables(template).forEach((name) => {
        nextVars[name] = state.vars[name] || '';
      });
      state.vars = nextVars;
      return;
    }

    if (state.templates.length > 0) {
      state.selectedTemplateId = state.templates[0].id;
      syncTemplateSelection();
      return;
    }

    state.selectedTemplateId = null;
    state.vars = {};
  }

  async function loadTemplates() {
    const templates = await API.fetch('/api/templates');
    state.templates = Array.isArray(templates) ? templates : [];
    syncTemplateSelection();
  }

  function renderTemplateItem(template) {
    const selected = template.id === state.selectedTemplateId;
    const vars = extractVariables(template);
    const cardStyle = selected
      ? 'border-color: var(--accent); background: var(--accent-muted); box-shadow: inset 0 0 0 1px var(--accent-border);'
      : '';

    return `
      <button
        type="button"
        class="card card-clickable"
        data-template-id="${App.escapeHtml(template.id)}"
        style="width: 100%; margin-bottom: 12px; padding: 16px; text-align: left; ${cardStyle}"
      >
        <div class="section-header" style="margin-bottom: 10px;">
          <div style="display: flex; align-items: center; gap: 10px; min-width: 0;">
            <div class="job-type-icon camera" aria-hidden="true">
              <span class="material-icons">account_tree</span>
            </div>
            <div style="min-width: 0;">
              <div class="job-type-label" style="word-break: break-word;">${App.escapeHtml(template.name)}</div>
              <div class="form-hint">${template.steps.length} step${template.steps.length === 1 ? '' : 's'} · ${vars.length} var${vars.length === 1 ? '' : 's'}</div>
            </div>
          </div>
          <span class="badge badge-running">Template</span>
        </div>
        <div class="job-prompt ${template.description ? '' : 'empty'}" style="min-height: 0;">
          ${template.description ? App.escapeHtml(template.description) : 'No description'}
        </div>
        <div class="profile-details" style="margin-top: 12px; padding-top: 12px;">
          <div class="profile-detail">
            <span class="material-icons">schedule</span>
            Updated ${App.escapeHtml(App.formatDate(template.updated_at))}
          </div>
        </div>
      </button>
    `;
  }

  function renderValue(value) {
    if (value == null || value === '') return '';
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) return value.join(', ');
    return JSON.stringify(value);
  }

  function renderTemplateStep(step, index) {
    const fields = [
      ['Prompt', renderValue(step.prompt)],
      ['Model', renderValue(step.model)],
      ['Aspect Ratio', renderValue(step.aspect_ratio)],
      ['Direction', renderValue(step.direction)],
      ['Parent Job ID', renderValue(step.parent_job_id)],
      ['Start Image', renderValue(step.start_image_path)],
      ['End Image', renderValue(step.end_image_path)],
      ['Reference Image', renderValue(step.ref_image_path)],
      ['Ingredients', renderValue(step.ingredient_image_paths)],
      ['BBox', renderValue(step.bbox)],
    ].filter(([, value]) => value);

    return `
      <div class="chain-step-card" style="margin-bottom: 12px;">
        <div class="chain-step-header">
          <span class="chain-step-num">
            <span class="material-icons" style="font-size: 16px; vertical-align: middle;">${App.jobTypeIcon(step.type)}</span>
            Step ${index + 1}: ${App.escapeHtml(step.type)}
          </span>
        </div>
        ${
          fields.length === 0
            ? '<div class="form-hint">No extra parameters on this step.</div>'
            : fields
                .map(
                  ([label, value]) => `
                    <div class="detail-row" style="padding: 8px 0;">
                      <div class="detail-label">${App.escapeHtml(label)}</div>
                      <div class="detail-value">${App.escapeHtml(value)}</div>
                    </div>
                  `
                )
                .join('')
        }
      </div>
    `;
  }

  function renderRunSummary() {
    if (!state.lastRun) return '';

    return `
      <div class="card" style="margin-top: 20px; border-color: var(--success); background: rgba(34, 197, 94, 0.06);">
        <div style="display: flex; align-items: flex-start; gap: 10px;">
          <span class="material-icons" style="color: var(--success);">check_circle</span>
          <div>
            <strong>Template queued</strong>
            <div class="form-hint" style="margin-top: 4px;">
              Chain ID: <code>${App.escapeHtml(String(state.lastRun.chain_id))}</code> · ${state.lastRun.jobs.length} job${state.lastRun.jobs.length === 1 ? '' : 's'}
            </div>
            <div style="display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px;">
              ${state.lastRun.jobs
                .map(
                  (job) => `
                    <span class="${App.statusBadge(job.status)}">
                      ${App.escapeHtml(job.type)} · ${App.escapeHtml(job.status)}
                    </span>
                  `
                )
                .join('')}
            </div>
          </div>
        </div>
      </div>
    `;
  }

  function renderTemplatePanel() {
    return `
      <div class="card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Workflow Templates</h3>
            <p class="form-hint">${state.templates.length} saved template${state.templates.length === 1 ? '' : 's'}</p>
          </div>
        </div>
        ${
          state.templates.length === 0
            ? `
              <div class="empty-state" style="padding: 40px 16px;">
                <span class="material-icons">account_tree</span>
                <h3>No templates yet</h3>
                <p>Create templates from the backend first, then run them here.</p>
              </div>
            `
            : state.templates.map(renderTemplateItem).join('')
        }
      </div>
    `;
  }

  function renderVariableFields(template) {
    const vars = extractVariables(template);
    if (vars.length === 0) {
      return '<div class="form-hint">This template has no placeholders. You can queue it immediately.</div>';
    }

    return vars
      .map(
        (name) => `
          <div class="form-group">
            <label class="form-label" for="template-var-${App.escapeHtml(name)}">${App.escapeHtml(name)}</label>
            <input
              type="text"
              class="form-input"
              id="template-var-${App.escapeHtml(name)}"
              data-template-var="${App.escapeHtml(name)}"
              placeholder="Value for {{${App.escapeHtml(name)}}}"
              value="${App.escapeHtml(state.vars[name] || '')}"
            >
          </div>
        `
      )
      .join('');
  }

  function renderTemplateDetailPanel() {
    const template = currentTemplate();
    if (!template) {
      return `
        <div class="card">
          <div class="empty-state" style="padding: 40px 16px;">
            <span class="material-icons">touch_app</span>
            <h3>Select a template</h3>
            <p>Choose a workflow from the list to inspect steps and instantiate it.</p>
          </div>
        </div>
      `;
    }

    return `
      <div class="card">
        <div class="section-header">
          <div>
            <h3 class="section-title">${App.escapeHtml(template.name)}</h3>
            <p class="form-hint">${template.description ? App.escapeHtml(template.description) : 'No description'}</p>
          </div>
          <span class="badge badge-completed">${template.steps.length} step${template.steps.length === 1 ? '' : 's'}</span>
        </div>

        <form id="workflow-template-form">
          <div class="form-group" style="margin-bottom: 20px;">
            <label class="form-label">Template Variables</label>
            ${renderVariableFields(template)}
          </div>

          <div>
            <label class="form-label" style="margin-bottom: 10px;">Steps</label>
            ${template.steps.map(renderTemplateStep).join('')}
          </div>

          <div style="display: flex; flex-wrap: wrap; gap: 12px; margin-top: 16px;">
            <button type="submit" class="btn btn-primary" id="workflow-run-template" ${state.instantiating ? 'disabled' : ''}>
              ${
                state.instantiating
                  ? '<span class="spinner"></span> Queueing...'
                  : '<span class="material-icons">play_arrow</span> Run Template'
              }
            </button>
          </div>
        </form>

        ${renderRunSummary()}
      </div>
    `;
  }

  function renderPromptPanel() {
    return `
      <div class="card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Auto-Prompt</h3>
            <p class="form-hint">Turn a raw idea into a Veo-ready prompt using <code>/api/llm</code> with <code>/api/prompt-builder</code> fallback.</p>
          </div>
        </div>

        <div class="form-group">
          <label class="form-label" for="workflow-prompt-idea">Raw Idea <span class="required">*</span></label>
          <textarea
            class="form-textarea"
            id="workflow-prompt-idea"
            rows="5"
            placeholder="e.g. Rain-soaked Tokyo alley with a fashion KOL walking toward neon signs"
          >${App.escapeHtml(state.promptIdea)}</textarea>
        </div>

        <div style="display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 18px;">
          <button type="button" class="btn btn-primary" id="workflow-generate-prompt" ${state.promptBusy ? 'disabled' : ''}>
            ${
              state.promptBusy
                ? '<span class="spinner"></span> Building...'
                : '<span class="material-icons">auto_awesome</span> Generate Prompt'
            }
          </button>
          <button type="button" class="btn btn-outline" id="workflow-copy-prompt" ${state.generatedPrompt ? '' : 'disabled'}>
            <span class="material-icons">content_copy</span> Copy
          </button>
        </div>

        <div class="form-group" style="margin-bottom: 0;">
          <label class="form-label" for="workflow-generated-prompt">Polished Prompt</label>
          <textarea
            class="form-textarea"
            id="workflow-generated-prompt"
            rows="6"
            readonly
            placeholder="Generated prompt will appear here..."
          >${App.escapeHtml(state.generatedPrompt)}</textarea>
          <span class="form-hint">${
            state.promptSource
              ? `Source: ${App.escapeHtml(state.promptSource)}`
              : 'Nothing generated yet.'
          }</span>
        </div>
      </div>
    `;
  }

  function renderPage() {
    if (!root) return;

    root.innerHTML = `
      <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 24px; align-items: start; margin-bottom: 24px;">
        ${renderTemplatePanel()}
        ${renderTemplateDetailPanel()}
      </div>
      ${renderPromptPanel()}
    `;
  }

  function selectTemplate(templateId) {
    state.selectedTemplateId = templateId;
    state.lastRun = null;
    syncTemplateSelection();
    renderPage();
  }

  function validateTemplateVars(template) {
    const vars = extractVariables(template);
    const missing = vars.filter((name) => !String(state.vars[name] || '').trim());
    if (missing.length > 0) {
      return `Provide values for: ${missing.join(', ')}`;
    }
    return null;
  }

  async function instantiateTemplate() {
    const template = currentTemplate();
    if (!template) {
      App.toast('Select a template first.', 'warning');
      return;
    }

    const error = validateTemplateVars(template);
    if (error) {
      App.toast(error, 'warning');
      return;
    }

    state.instantiating = true;
    renderPage();

    try {
      const result = await API.fetch(`/api/templates/${template.id}/instantiate`, {
        method: 'POST',
        body: JSON.stringify({
          template_id: template.id,
          vars: { ...state.vars },
        }),
      });
      state.lastRun = {
        chain_id: result.chain_id,
        jobs: Array.isArray(result.jobs) ? result.jobs : [],
      };
      App.toast('Workflow queued.', 'success');
    } catch (err) {
      App.toast(`Failed to run template: ${err.message}`, 'error');
    }

    state.instantiating = false;
    renderPage();
  }

  async function requestPrompt(idea) {
    const llmAttempts = [
      {
        path: '/api/llm/expand-prompt',
        body: { idea },
        source: 'LLM Expand',
      },
      {
        path: '/api/llm/auto-prompt',
        body: { topic: idea, style: 'cinematic' },
        source: 'LLM Auto',
      },
    ];

    let lastError = null;

    for (const attempt of llmAttempts) {
      try {
        const result = await API.fetch(attempt.path, {
          method: 'POST',
          body: JSON.stringify(attempt.body),
        });
        if (!result?.prompt) {
          throw new Error('Prompt service returned no prompt.');
        }
        return { prompt: result.prompt, source: attempt.source };
      } catch (err) {
        lastError = err;
        if (err.status === 400 || err.status === 422) {
          throw err;
        }
      }
    }

    const fallback = await API.fetch('/api/prompt-builder/assemble', {
      method: 'POST',
      body: JSON.stringify({ subject: idea }),
    });
    if (!fallback?.prompt) {
      throw lastError || new Error('Prompt builder returned no prompt.');
    }
    return { prompt: fallback.prompt, source: 'Prompt Builder Fallback' };
  }

  async function buildPrompt() {
    const idea = state.promptIdea.trim();
    if (!idea) {
      App.toast('Enter a raw idea first.', 'warning');
      return;
    }

    state.promptBusy = true;
    renderPage();

    try {
      const result = await requestPrompt(idea);
      state.generatedPrompt = result.prompt;
      state.promptSource = result.source;
      App.toast('Prompt generated.', 'success');
    } catch (err) {
      App.toast(`Failed to generate prompt: ${err.message}`, 'error');
    }

    state.promptBusy = false;
    renderPage();
  }

  async function copyPrompt() {
    if (!state.generatedPrompt) return;

    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(state.generatedPrompt);
      } else {
        const temp = document.createElement('textarea');
        temp.value = state.generatedPrompt;
        temp.setAttribute('readonly', '');
        temp.style.position = 'absolute';
        temp.style.left = '-9999px';
        document.body.appendChild(temp);
        temp.select();
        document.execCommand('copy');
        temp.remove();
      }
      App.toast('Prompt copied.', 'success');
    } catch (err) {
      App.toast(`Failed to copy prompt: ${err.message}`, 'error');
    }
  }

  async function handleClick(event) {
    const templateCard = event.target.closest('[data-template-id]');
    if (templateCard) {
      selectTemplate(templateCard.dataset.templateId);
      return;
    }

    const promptButton = event.target.closest('#workflow-generate-prompt');
    if (promptButton && !promptButton.disabled) {
      await buildPrompt();
      return;
    }

    const copyButton = event.target.closest('#workflow-copy-prompt');
    if (copyButton && !copyButton.disabled) {
      await copyPrompt();
    }
  }

  function handleInput(event) {
    const templateVar = event.target.dataset.templateVar;
    if (templateVar) {
      state.vars[templateVar] = event.target.value;
      return;
    }

    if (event.target.id === 'workflow-prompt-idea') {
      state.promptIdea = event.target.value;
    }
  }

  async function handleSubmit(event) {
    if (event.target.id !== 'workflow-template-form') return;
    event.preventDefault();
    await instantiateTemplate();
  }

  const WorkflowsPage = {
    name: 'workflows',
    title: 'Workflows',
    icon: 'account_tree',

    async render() {
      await loadTemplates();
      return '<div id="workflows-page"></div>';
    },

    mount() {
      root = document.getElementById('workflows-page');
      if (!root) return;

      handlers = {
        click: (event) => { void handleClick(event); },
        input: handleInput,
        submit: (event) => { void handleSubmit(event); },
      };

      root.addEventListener('click', handlers.click);
      root.addEventListener('input', handlers.input);
      root.addEventListener('submit', handlers.submit);

      renderPage();
    },

    destroy() {
      if (root && handlers) {
        root.removeEventListener('click', handlers.click);
        root.removeEventListener('input', handlers.input);
        root.removeEventListener('submit', handlers.submit);
      }
      root = null;
      handlers = null;
    },
  };

  App.register(WorkflowsPage);
})();
