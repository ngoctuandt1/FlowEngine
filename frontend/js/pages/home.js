/**
 * Home page — Flow-style landing.
 *
 * Layout: top app bar (handled by index.html shell) + centered composer card
 * + recent project grid below. Mirrors Google Flow's signed-in homepage so
 * operators land on a familiar surface.
 *
 * The composer is a thin wrapper over the same JobCreate payload that
 * create-job.js builds. We deliberately *don't* import that module — its
 * state is page-scoped — and instead redo the small subset we need (no
 * bbox / no parent / no batch). For full power, route to #create.
 *
 * Backend contract: POST /api/jobs (mirrors server/models/job.py JobCreate).
 * Field set per mode:
 *   text-to-video         prompt + model + aspect_ratio + profile
 *   text-to-image         prompt + model + aspect_ratio + profile + ref_image_path?
 *   frames-to-video       prompt + model + aspect_ratio + profile + start_image_path
 *                         (+ end_image_path?)
 *   ingredients-to-video  prompt + model + aspect_ratio + profile + ingredient_image_paths[]
 */
(() => {
  const {
    JOB_TYPES, MODELS, DEFAULT_MODEL, IMAGE_MODELS, DEFAULT_IMAGE_MODEL,
    ASPECT_RATIOS, ASPECT_RATIOS_IMAGE, DEFAULT_ASPECT,
  } = CONST;

  const MODE_TO_TYPE = {
    video: 'text-to-video',
    image: 'text-to-image',
    frames: 'frames-to-video',
    ingredients: 'ingredients-to-video',
  };

  const MODE_TABS = [
    { id: 'video',       label: 'Video',       icon: 'movie' },
    { id: 'image',       label: 'Image',       icon: 'image' },
    { id: 'frames',      label: 'Frames',      icon: 'collections' },
    { id: 'ingredients', label: 'Ingredients', icon: 'photo_library' },
  ];

  const MAX_INGREDIENTS = 10;
  const RECENT_LIMIT = 12;

  // page-scoped state — reset on every render() so re-entering #home
  // never resurrects stale uploads / tab choices from a prior session.
  let mode = 'video';
  let profiles = [];
  let recentJobs = [];
  let model = DEFAULT_MODEL;
  let aspect = DEFAULT_ASPECT;
  let profile = '';
  let startImagePath = '';
  let endImagePath = '';
  let refImagePath = '';
  let ingredientImagePaths = [];
  let wsUnsubs = [];
  // global-listener handles, tracked so destroy() can pull them.
  let docClickHandler = null;

  function resetState() {
    mode = 'video';
    model = DEFAULT_MODEL;
    aspect = DEFAULT_ASPECT;
    profile = '';
    startImagePath = '';
    endImagePath = '';
    refImagePath = '';
    ingredientImagePaths = [];
  }

  // ---- helpers --------------------------------------------------------------

  function currentType() { return MODE_TO_TYPE[mode]; }

  function modelOptionsFor(m) {
    return m === 'image' ? IMAGE_MODELS : MODELS;
  }
  function defaultModelFor(m) {
    return m === 'image' ? DEFAULT_IMAGE_MODEL : DEFAULT_MODEL;
  }
  function aspectOptionsFor(m) {
    return m === 'image' ? ASPECT_RATIOS_IMAGE : ASPECT_RATIOS;
  }

  function findLabel(opts, value) {
    const m = opts.find((o) => (typeof o === 'string' ? o : o.value) === value);
    if (!m) return value;
    return typeof m === 'string' ? m : m.label;
  }

  // ---- render: composer -----------------------------------------------------

  function renderTabs() {
    return `
      <div class="composer-tabs" role="tablist">
        ${MODE_TABS.map((t) => `
          <button class="composer-tab ${t.id === mode ? 'active' : ''}"
                  role="tab" aria-selected="${t.id === mode}"
                  data-mode="${t.id}">
            <span class="material-icons">${t.icon}</span>
            <span>${t.label}</span>
          </button>
        `).join('')}
      </div>
    `;
  }

  function renderAttachments() {
    if (mode === 'frames') {
      return `
        <div class="composer-attachments">
          ${dropzone('home-start-image', 'Start frame', startImagePath, true)}
          ${dropzone('home-end-image', 'End frame (optional)', endImagePath, false)}
        </div>
      `;
    }
    if (mode === 'ingredients') {
      const tiles = ingredientImagePaths.map((p, i) => `
        <div class="ingredient-tile">
          <img src="/${App.escapeHtml(p)}" alt="Reference ${i + 1}">
          <button type="button" class="ingredient-remove" data-index="${i}"
                  aria-label="Remove reference">
            <span class="material-icons">close</span>
          </button>
        </div>
      `).join('');
      const addTile = ingredientImagePaths.length < MAX_INGREDIENTS ? `
        <button type="button" class="ingredient-add" id="home-add-ingredient">
          <span class="material-icons">add_photo_alternate</span>
          <span>Add reference</span>
        </button>
      ` : '';
      return `
        <div class="composer-attachments ingredients-strip">
          ${tiles}${addTile}
          <input type="file" id="home-ingredient-input" accept="image/*" multiple hidden>
        </div>
        <div class="form-hint">${ingredientImagePaths.length}/${MAX_INGREDIENTS} reference images.</div>
      `;
    }
    if (mode === 'image') {
      return `
        <div class="composer-attachments">
          ${dropzone('home-ref-image', 'Reference (optional)', refImagePath, false)}
        </div>
      `;
    }
    return '';
  }

  function dropzone(id, label, path, required) {
    const preview = path
      ? `<img class="dropzone-preview" src="/${App.escapeHtml(path)}" alt="${App.escapeHtml(label)}">`
      : `<div class="dropzone-empty">
           <span class="material-icons">add_photo_alternate</span>
           <span>${App.escapeHtml(label)}${required ? ' *' : ''}</span>
         </div>`;
    return `
      <label class="dropzone" for="${id}">
        ${preview}
        <input type="file" id="${id}" accept="image/png,image/jpeg,image/webp" hidden>
      </label>
    `;
  }

  function renderChips() {
    const modelLabel = findLabel(modelOptionsFor(mode), model);
    const aspectLabel = findLabel(aspectOptionsFor(mode), aspect);
    const profileLabel = profile || 'Any profile';
    return `
      <div class="composer-chips">
        <button class="chip" id="chip-model" data-popover="model">
          <span class="material-icons">tune</span>
          <span class="chip-label">${App.escapeHtml(modelLabel)}</span>
          <span class="material-icons chip-caret">expand_more</span>
        </button>
        <button class="chip" id="chip-aspect" data-popover="aspect">
          <span class="material-icons">aspect_ratio</span>
          <span class="chip-label">${App.escapeHtml(aspectLabel)}</span>
          <span class="material-icons chip-caret">expand_more</span>
        </button>
        <button class="chip" id="chip-profile" data-popover="profile">
          <span class="material-icons">person</span>
          <span class="chip-label">${App.escapeHtml(profileLabel)}</span>
          <span class="material-icons chip-caret">expand_more</span>
        </button>
        <span class="chip chip-locked" title="Engine forces ×1 to control credits">
          <span class="material-icons">looks_one</span>
          <span class="chip-label">×1</span>
        </span>
      </div>
    `;
  }

  function renderPopovers() {
    const modelOpts = modelOptionsFor(mode);
    const aspectOpts = aspectOptionsFor(mode);
    const opt = (o, current) => {
      const v = typeof o === 'string' ? o : o.value;
      const l = typeof o === 'string' ? o : o.label;
      return `<button class="popover-item ${v === current ? 'active' : ''}"
                      data-value="${App.escapeHtml(v)}">${App.escapeHtml(l)}</button>`;
    };
    const profileOpt = (name, current) => `
      <button class="popover-item ${name === current ? 'active' : ''}"
              data-value="${App.escapeHtml(name)}">${App.escapeHtml(name || 'Any profile')}</button>
    `;
    const profileEntries = [
      profileOpt('', profile),
      ...profiles.map((p) => profileOpt(p.name, profile)),
    ].join('');
    return `
      <div class="popover" id="popover-model" hidden>
        <div class="popover-title">Model</div>
        ${modelOpts.map((o) => opt(o, model)).join('')}
      </div>
      <div class="popover" id="popover-aspect" hidden>
        <div class="popover-title">Aspect ratio</div>
        ${aspectOpts.map((o) => opt(o, aspect)).join('')}
      </div>
      <div class="popover" id="popover-profile" hidden>
        <div class="popover-title">Chrome profile</div>
        ${profileEntries}
      </div>
    `;
  }

  function renderComposer() {
    return `
      <section class="composer-card">
        ${renderTabs()}
        <textarea id="home-prompt" class="composer-prompt"
                  rows="3" placeholder="Describe the ${MODE_TABS.find((t) => t.id === mode).label.toLowerCase()} you want to generate..."></textarea>
        <div id="home-attachments">${renderAttachments()}</div>
        <div class="composer-bottom">
          ${renderChips()}
          <button class="composer-send" id="home-send">
            <span class="material-icons">arrow_upward</span>
            <span>Generate</span>
          </button>
        </div>
        ${renderPopovers()}
        <div id="home-feedback" class="composer-feedback" hidden></div>
      </section>
    `;
  }

  // ---- render: project grid -------------------------------------------------

  function renderGrid() {
    if (recentJobs.length === 0) {
      return `
        <section class="recent-section">
          <div class="section-head">
            <h3>Recent</h3>
          </div>
          <div class="empty-state">
            <span class="material-icons">movie_filter</span>
            <h3>No jobs yet</h3>
            <p>Submit your first prompt above to get started.</p>
          </div>
        </section>
      `;
    }
    const tiles = recentJobs.map(renderTile).join('');
    return `
      <section class="recent-section">
        <div class="section-head">
          <h3>Recent</h3>
          <a href="#dashboard" class="section-link">View all →</a>
        </div>
        <div class="project-grid" id="home-grid">${tiles}</div>
      </section>
    `;
  }

  // Whitelist of status values we render. Anything else falls back to
  // 'pending' so a malformed/hostile API value cannot break out of the
  // class attribute and become an XSS sink.
  const ALLOWED_STATUS = new Set(['pending', 'claimed', 'running', 'completed', 'failed', 'cancelled']);

  function safeStatus(s) {
    return ALLOWED_STATUS.has(s) ? s : 'pending';
  }

  function renderTile(job) {
    const status = safeStatus(job.status);
    const type = job.type || 'text-to-video';
    const promptText = job.prompt || job.direction || '(no prompt)';
    return `
      <article class="project-tile status-${status}" data-job-id="${App.escapeHtml(job.id)}">
        <div class="tile-thumb">
          <span class="material-icons type-icon ${App.jobTypeClass(type)}">${App.jobTypeIcon(type)}</span>
          <span class="tile-status-badge ${App.statusBadge(status)}">${App.escapeHtml(status)}</span>
        </div>
        <div class="tile-body">
          <div class="tile-prompt" title="${App.escapeHtml(promptText)}">${App.escapeHtml(App.truncate(promptText, 80))}</div>
          <div class="tile-meta">
            <span>${App.escapeHtml(type)}</span>
            <span>·</span>
            <span>${App.escapeHtml(job.profile || '—')}</span>
            <span>·</span>
            <span>${App.escapeHtml(App.formatDate(job.created_at))}</span>
          </div>
        </div>
      </article>
    `;
  }

  // ---- network --------------------------------------------------------------

  async function fetchProfiles() {
    try {
      const list = await API.profiles.list();
      profiles = Array.isArray(list) ? list : (list?.profiles ?? []);
    } catch (err) {
      console.warn('[Home] profiles fetch failed:', err.message);
      profiles = [];
    }
  }

  async function fetchRecent() {
    try {
      const list = await API.jobs.list({ limit: RECENT_LIMIT });
      const items = Array.isArray(list) ? list : (list?.jobs ?? []);
      recentJobs = items.slice(0, RECENT_LIMIT);
    } catch (err) {
      console.warn('[Home] recent jobs fetch failed:', err.message);
      recentJobs = [];
    }
  }

  // ---- submit ---------------------------------------------------------------

  function buildPayload() {
    const data = { type: currentType() };
    const prompt = document.getElementById('home-prompt')?.value.trim();
    if (prompt) data.prompt = prompt;
    if (model) data.model = model;
    if (aspect) data.aspect_ratio = aspect;
    if (profile) data.profile = profile;
    if (mode === 'frames') {
      if (startImagePath) data.start_image_path = startImagePath;
      if (endImagePath) data.end_image_path = endImagePath;
    } else if (mode === 'ingredients' && ingredientImagePaths.length > 0) {
      data.ingredient_image_paths = [...ingredientImagePaths];
    } else if (mode === 'image' && refImagePath) {
      data.ref_image_path = refImagePath;
    }
    return data;
  }

  function validate(data) {
    if (!data.prompt) return 'Prompt is required.';
    if (data.type === 'frames-to-video' && !data.start_image_path) {
      return 'Start frame is required for Frames mode.';
    }
    if (data.type === 'ingredients-to-video' && (!data.ingredient_image_paths || data.ingredient_image_paths.length === 0)) {
      return 'At least one reference image is required.';
    }
    return null;
  }

  async function submit() {
    const data = buildPayload();
    const err = validate(data);
    if (err) { showFeedback('warn', err); return; }

    const btn = document.getElementById('home-send');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span><span>Sending...</span>';
    try {
      const result = await API.jobs.create(data);
      showFeedback('ok', `Job created · <code>${App.escapeHtml(String(result?.id ?? ''))}</code>`);
      App.toast('Job created', 'success');
      // optimistically prepend
      if (result?.id) {
        recentJobs = [result, ...recentJobs].slice(0, RECENT_LIMIT);
        repaintGrid();
      } else {
        await fetchRecent();
        repaintGrid();
      }
      // reset prompt + attachments (keep mode/model/aspect/profile)
      const ta = document.getElementById('home-prompt');
      if (ta) ta.value = '';
      startImagePath = endImagePath = refImagePath = '';
      ingredientImagePaths = [];
      document.getElementById('home-attachments').innerHTML = renderAttachments();
      bindAttachments();
    } catch (e) {
      showFeedback('err', `Failed: ${App.escapeHtml(e.message)}`);
      App.toast(`Submit failed: ${e.message}`, 'error');
    } finally {
      btn.disabled = false;
      btn.innerHTML = '<span class="material-icons">arrow_upward</span><span>Generate</span>';
    }
  }

  function showFeedback(kind, html) {
    const el = document.getElementById('home-feedback');
    if (!el) return;
    el.hidden = false;
    el.className = `composer-feedback feedback-${kind}`;
    el.innerHTML = html;
    if (kind === 'ok') {
      setTimeout(() => { if (el) el.hidden = true; }, 4000);
    }
  }

  function repaintGrid() {
    const wrap = document.getElementById('home-recent');
    if (!wrap) return;
    wrap.innerHTML = renderGrid();
  }

  // ---- attachments ----------------------------------------------------------

  async function uploadAndAssign(file, target) {
    try {
      const r = await API.uploads.create(file);
      const path = r?.path || '';
      if (!path) throw new Error('Upload returned no path');
      App.toast('Uploaded', 'success');
      target(path);
      const wrap = document.getElementById('home-attachments');
      if (wrap) wrap.innerHTML = renderAttachments();
      bindAttachments();
    } catch (e) {
      App.toast(`Upload failed: ${e.message}`, 'error');
    }
  }

  function bindAttachments() {
    const start = document.getElementById('home-start-image');
    if (start) start.addEventListener('change', (e) => {
      const f = e.target.files?.[0]; if (f) uploadAndAssign(f, (p) => { startImagePath = p; });
    });
    const end = document.getElementById('home-end-image');
    if (end) end.addEventListener('change', (e) => {
      const f = e.target.files?.[0]; if (f) uploadAndAssign(f, (p) => { endImagePath = p; });
    });
    const ref = document.getElementById('home-ref-image');
    if (ref) ref.addEventListener('change', (e) => {
      const f = e.target.files?.[0]; if (f) uploadAndAssign(f, (p) => { refImagePath = p; });
    });
    const addBtn = document.getElementById('home-add-ingredient');
    const ingInput = document.getElementById('home-ingredient-input');
    if (addBtn && ingInput) {
      addBtn.addEventListener('click', () => ingInput.click());
      ingInput.addEventListener('change', async (e) => {
        const files = Array.from(e.target.files || []);
        const remaining = MAX_INGREDIENTS - ingredientImagePaths.length;
        for (const f of files.slice(0, remaining)) {
          try {
            const r = await API.uploads.create(f);
            if (r?.path) ingredientImagePaths.push(r.path);
          } catch (err) {
            App.toast(`Upload failed: ${err.message}`, 'error');
            break;
          }
        }
        e.target.value = '';
        const wrap = document.getElementById('home-attachments');
        if (wrap) wrap.innerHTML = renderAttachments();
        bindAttachments();
      });
    }
    document.querySelectorAll('.ingredient-remove').forEach((btn) => {
      btn.addEventListener('click', () => {
        const i = Number.parseInt(btn.dataset.index, 10);
        if (Number.isNaN(i)) return;
        ingredientImagePaths.splice(i, 1);
        const wrap = document.getElementById('home-attachments');
        if (wrap) wrap.innerHTML = renderAttachments();
        bindAttachments();
      });
    });
  }

  // ---- popovers -------------------------------------------------------------

  function closePopovers() {
    document.querySelectorAll('.popover').forEach((p) => { p.hidden = true; });
    document.querySelectorAll('.chip').forEach((c) => c.classList.remove('chip-open'));
  }

  function bindChips() {
    document.querySelectorAll('[data-popover]').forEach((chip) => {
      chip.addEventListener('click', (e) => {
        e.stopPropagation();
        const id = `popover-${chip.dataset.popover}`;
        const target = document.getElementById(id);
        const wasOpen = !target.hidden;
        closePopovers();
        if (!wasOpen) {
          target.hidden = false;
          chip.classList.add('chip-open');
          // position under chip
          const rect = chip.getBoundingClientRect();
          const card = chip.closest('.composer-card');
          const cardRect = card.getBoundingClientRect();
          target.style.left = `${rect.left - cardRect.left}px`;
          target.style.top = `${rect.bottom - cardRect.top + 6}px`;
        }
      });
    });
    document.querySelectorAll('.popover').forEach((pop) => {
      pop.addEventListener('click', (e) => {
        const item = e.target.closest('.popover-item');
        if (!item) return;
        const v = item.dataset.value;
        if (pop.id === 'popover-model') model = v;
        else if (pop.id === 'popover-aspect') aspect = v;
        else if (pop.id === 'popover-profile') profile = v;
        closePopovers();
        repaintChips();
      });
    });
    // Managed doc-click handler — only attach once across renders so
    // navigating away + back doesn't leak a fresh listener every time.
    if (!docClickHandler) {
      docClickHandler = closePopovers;
      document.addEventListener('click', docClickHandler);
    }
  }

  function repaintChips() {
    const card = document.querySelector('.composer-card');
    if (!card) return;
    const oldChips = card.querySelector('.composer-chips');
    if (oldChips) oldChips.outerHTML = renderChips();
    const oldPops = card.querySelectorAll('.popover');
    oldPops.forEach((p) => p.remove());
    card.insertAdjacentHTML('beforeend', renderPopovers());
    bindChips();
  }

  // ---- WS live updates ------------------------------------------------------

  function attachWS() {
    if (!window.WS || typeof WS.on !== 'function') return;
    const upsert = (job, allowInsert) => {
      if (!job?.id) return false;
      const idx = recentJobs.findIndex((j) => j.id === job.id);
      if (idx >= 0) {
        recentJobs[idx] = { ...recentJobs[idx], ...job };
      } else if (allowInsert) {
        recentJobs = [job, ...recentJobs].slice(0, RECENT_LIMIT);
      } else {
        return false;
      }
      return true;
    };
    wsUnsubs.push(WS.on('job_created',   (p) => { if (upsert(p, true))  repaintGrid(); }));
    wsUnsubs.push(WS.on('job_updated',   (p) => { if (upsert(p, false)) repaintGrid(); }));
    wsUnsubs.push(WS.on('job_completed', (p) => { if (upsert(p, false)) repaintGrid(); }));
    wsUnsubs.push(WS.on('job_failed',    (p) => { if (upsert(p, false)) repaintGrid(); }));
  }

  function detachWS() {
    wsUnsubs.forEach((u) => { try { u(); } catch (_) {} });
    wsUnsubs = [];
  }

  // ---- event wiring ---------------------------------------------------------

  function bindTabs() {
    document.querySelectorAll('.composer-tab').forEach((btn) => {
      btn.addEventListener('click', () => {
        const m = btn.dataset.mode;
        if (m === mode) return;
        mode = m;
        // reset model/aspect to defaults of new mode
        model = defaultModelFor(mode);
        const aOpts = aspectOptionsFor(mode).map((o) => (typeof o === 'string' ? o : o.value));
        if (!aOpts.includes(aspect)) aspect = DEFAULT_ASPECT;
        // reset attachments — they're mode-specific
        startImagePath = endImagePath = refImagePath = '';
        ingredientImagePaths = [];
        repaintComposer();
      });
    });
  }

  function repaintComposer() {
    const card = document.querySelector('.composer-card');
    if (!card) return;
    card.outerHTML = renderComposer();
    bindAll();
  }

  function bindGrid() {
    // Delegate on the stable parent (#home-recent never gets innerHTML-
    // replaced after mount — only its child #home-grid does). This means
    // the click handler survives every WS-driven repaintGrid().
    const wrap = document.getElementById('home-recent');
    if (!wrap) return;
    wrap.addEventListener('click', async (e) => {
      const tile = e.target.closest('.project-tile');
      if (!tile) return;
      const id = tile.dataset.jobId;
      if (!id) return;
      try {
        const job = await API.jobs.get(id);
        const body = `
          <pre style="white-space:pre-wrap; font-size:12px; line-height:1.5;">${App.escapeHtml(JSON.stringify(job, null, 2))}</pre>
        `;
        App.openModal(`Job ${id.slice(0, 8)}…`, body);
      } catch (err) {
        App.toast(`Load failed: ${err.message}`, 'error');
      }
    });
  }

  function bindAll() {
    bindTabs();
    bindAttachments();
    bindChips();
    document.getElementById('home-send')?.addEventListener('click', submit);
    document.getElementById('home-prompt')?.addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') submit();
    });
  }

  // ---- page object ----------------------------------------------------------

  const HomePage = {
    name: 'home',
    title: 'FlowEngine',
    icon: 'movie_filter',

    async render() {
      // Reset all page-scoped state every render — re-entering #home from
      // a different route must not resurrect stale uploads, mode, model,
      // aspect, or profile picks from a prior session. (Codex review #1.)
      resetState();
      await Promise.all([fetchProfiles(), fetchRecent()]);
      return `
        <div class="home-canvas">
          <div class="home-hero">
            <h1 class="home-title">What will you create today?</h1>
            <p class="home-subtitle">Generate, extend, edit. Powered by Google Flow.</p>
          </div>
          ${renderComposer()}
          <div id="home-recent">${renderGrid()}</div>
        </div>
      `;
    },

    mount() {
      bindAll();
      bindGrid();
      attachWS();
    },

    destroy() {
      detachWS();
      // Pull the managed doc-click listener so navigating away from
      // #home doesn't leave a popover-closer attached forever. (Codex
      // review #6.)
      if (docClickHandler) {
        document.removeEventListener('click', docClickHandler);
        docClickHandler = null;
      }
    },
  };

  App.register(HomePage);
})();
