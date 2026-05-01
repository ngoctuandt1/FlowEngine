/**
 * Job Detail Page
 * Full-screen view for a single job with live WS refresh.
 */
(() => {
  const VIDEO_EXTENSIONS = new Set(['mp4', 'webm', 'mov', 'm4v']);
  const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp']);
  const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled']);
  const RETRYABLE_STATUSES = new Set(['failed', 'cancelled']);
  const DELETE_CANCELLED_STATUSES = new Set(['running', 'claimed', 'pending']);
  const JOB_ROUTE_RE = /^(job(?:-detail)?)(?:[/?]|$)/i;

  const state = {
    jobId: '',
    job: null,
    parent: null,
    children: [],
    childrenError: '',
    requestId: 0,
    refreshTimer: null,
    wsUnsubs: [],
    socketListener: null,
    socketTarget: null,
    rootTarget: null,
    rootClickHandler: null,
  };

  let routerPatched = false;

  function patchRouter() {
    if (routerPatched || !window.App || typeof App._onRoute !== 'function') return;

    const originalOnRoute = App._onRoute.bind(App);
    App._onRoute = function patchedOnRoute() {
      const hash = String(location.hash || '').replace(/^#/, '') || 'home';
      if (JOB_ROUTE_RE.test(hash)) {
        if (!this.pages['job-detail']) {
          location.hash = '#home';
          return;
        }
        this._loadPage('job-detail');
        return;
      }
      originalOnRoute();
    };

    routerPatched = true;
  }

  function parseJobIdFromHash() {
    const raw = String(window.location.hash || '').replace(/^#/, '');
    if (!raw) return '';

    const [pathPart, queryString = ''] = raw.split('?');
    const segments = pathPart.split('/').filter(Boolean);
    if (segments.length && /^(job|job-detail)$/i.test(segments[0])) {
      const tail = segments.slice(1).join('/');
      if (tail) return decodeURIComponent(tail);
    }

    const params = new URLSearchParams(queryString);
    const id = params.get('id') || '';
    return id ? decodeURIComponent(id) : '';
  }

  function jobHash(id) {
    return `#job-detail/${encodeURIComponent(String(id || '').trim())}`;
  }

  function shortId(value, maxLen = 12) {
    return App.truncate(String(value || ''), maxLen);
  }

  function jobTypeLabel(type) {
    const meta = typeof CONST?.typeMeta === 'function' ? CONST.typeMeta(type) : null;
    return meta?.label || (type || 'Unknown').replace(/-/g, ' ');
  }

  function jobTypeShortLabel(type) {
    const meta = typeof CONST?.typeMeta === 'function' ? CONST.typeMeta(type) : null;
    return meta?.shortLabel || jobTypeLabel(type);
  }

  function updatePageTitle(title) {
    const titleEl = document.getElementById('page-title');
    if (titleEl) titleEl.textContent = title;
  }

  function formatExactDate(value) {
    if (!value) return '-';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  }

  function formatRelativeDate(value) {
    if (!value) return '';
    return App.formatDate(value);
  }

  function mediaUrl(file) {
    const normalized = String(file || '').replace(/\\/g, '/').trim();
    if (!normalized) return '';
    if (/^https?:\/\//i.test(normalized)) return normalized;

    if (/^\/?downloads\//i.test(normalized)) {
      const relative = normalized.replace(/^\/?downloads\//i, '');
      return `/downloads/${encodeURI(relative)}`;
    }

    const markerIndex = normalized.toLowerCase().lastIndexOf('/downloads/');
    if (markerIndex !== -1) {
      const relative = normalized.slice(markerIndex + '/downloads/'.length);
      return `/downloads/${encodeURI(relative)}`;
    }

    return `/downloads/${encodeURI(normalized)}`;
  }

  function mediaTypeFromFile(file) {
    const normalized = String(file || '').replace(/\\/g, '/');
    const name = normalized.split('/').pop() || normalized;
    const extension = name.includes('.') ? name.split('.').pop().toLowerCase() : '';
    if (VIDEO_EXTENSIONS.has(extension)) return 'video';
    if (IMAGE_EXTENSIONS.has(extension)) return 'image';
    return null;
  }

  function renderableFiles(job) {
    const files = Array.isArray(job?.output_files) ? job.output_files : [];
    return files
      .map((file) => {
        const kind = mediaTypeFromFile(file);
        if (!kind) return null;
        const normalized = String(file).replace(/\\/g, '/').replace(/^\/?downloads\//i, '');
        return {
          file,
          kind,
          name: normalized.split('/').pop() || normalized,
          url: mediaUrl(file),
        };
      })
      .filter(Boolean);
  }

  function primaryMedia(job) {
    const files = renderableFiles(job);
    if (!files.length) return null;

    const primary = files.find((file) => file.kind === 'video') || files[0];
    const poster = primary.kind === 'video'
      ? files.find((file) => file.kind === 'image')?.url || ''
      : '';

    return {
      ...primary,
      poster,
      files,
    };
  }

  function formatBBox(bbox) {
    if (!bbox || !['x', 'y', 'w', 'h'].every((key) => typeof bbox[key] === 'number')) {
      return '-';
    }
    return `x ${bbox.x.toFixed(2)} | y ${bbox.y.toFixed(2)} | w ${bbox.w.toFixed(2)} | h ${bbox.h.toFixed(2)}`;
  }

  function escapeJson(value) {
    return App.escapeHtml(JSON.stringify(value, null, 2));
  }

  function flowLink(job) {
    if (job?.edit_url) return job.edit_url;
    if (job?.project_url && job?.media_id) {
      return `${String(job.project_url).replace(/\/+$/, '')}/edit/${job.media_id}`;
    }
    return job?.project_url || '';
  }

  function normalizeJobList(result) {
    return Array.isArray(result) ? result : result?.jobs || [];
  }

  async function fetchJobChildren(jobId) {
    const children = await API.fetch(`/api/jobs/${encodeURIComponent(jobId)}/children`);
    return normalizeJobList(children);
  }

  function errorMessage(err) {
    if (typeof err?.message === 'string' && err.message.trim()) return err.message;
    if (typeof err === 'string' && err.trim()) return err;
    return 'Unknown error';
  }

  function renderPromptCard(job) {
    const prompt = job.prompt || '';
    const chips = [
      job.model ? `<span class="job-detail-chip">Model: ${App.escapeHtml(job.model)}</span>` : '',
      job.aspect_ratio ? `<span class="job-detail-chip">Aspect: ${App.escapeHtml(job.aspect_ratio)}</span>` : '',
      job.direction ? `<span class="job-detail-chip">Direction: ${App.escapeHtml(job.direction)}</span>` : '',
      job.bbox ? `<span class="job-detail-chip">BBox: ${App.escapeHtml(formatBBox(job.bbox))}</span>` : '',
      job.media_id ? `<span class="job-detail-chip">Media: ${App.escapeHtml(shortId(job.media_id, 18))}</span>` : '',
      job.generation_id ? `<span class="job-detail-chip">Generation: ${App.escapeHtml(shortId(job.generation_id, 18))}</span>` : '',
    ].filter(Boolean).join('');

    const attachments = [
      job.start_image_path ? ['Start Image', job.start_image_path] : null,
      job.end_image_path ? ['End Image', job.end_image_path] : null,
      job.ref_image_path ? ['Reference Image', job.ref_image_path] : null,
      Array.isArray(job.ingredient_image_paths) && job.ingredient_image_paths.length
        ? ['Ingredients', job.ingredient_image_paths.join('\n')]
        : null,
    ].filter(Boolean);

    return `
      <section class="card job-detail-card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Prompt & Params</h3>
            <p class="job-detail-section-copy">Operation inputs and inherited target metadata.</p>
          </div>
        </div>
        <div class="job-detail-prompt-block ${prompt ? '' : 'empty'}">
          ${prompt ? App.escapeHtml(prompt) : 'No prompt attached to this job.'}
        </div>
        ${chips ? `<div class="job-detail-chip-row">${chips}</div>` : ''}
        ${attachments.length ? `
          <div class="detail-list" style="margin-top: 18px;">
            ${attachments.map(([label, value]) => `
              <div class="detail-row">
                <span class="detail-label">${App.escapeHtml(label)}</span>
                <span class="detail-value" style="white-space: pre-wrap;">${App.escapeHtml(value)}</span>
              </div>
            `).join('')}
          </div>
        ` : ''}
      </section>
    `;
  }

  function renderPreviewCard(job) {
    const media = primaryMedia(job);
    const files = Array.isArray(job.output_files) ? job.output_files : [];
    const outputLinks = files
      .map((file) => {
        const normalized = String(file).replace(/\\/g, '/').replace(/^\/?downloads\//i, '');
        return `
          <a class="btn btn-sm btn-outline" href="${App.escapeHtml(mediaUrl(file))}" target="_blank" rel="noopener">
            <span class="material-icons" style="font-size:16px">open_in_new</span> ${App.escapeHtml(normalized.split('/').pop() || normalized)}
          </a>
        `;
      })
      .join('');

    let previewHtml = `
      <div class="job-detail-preview-empty">
        <span class="material-icons">${job.status === 'completed' ? 'broken_image' : 'hourglass_top'}</span>
        <h3>${job.status === 'completed' ? 'No previewable output' : 'Waiting for output'}</h3>
        <p>${job.status === 'completed'
          ? 'This job completed without a renderable image or video file.'
          : 'The preview will appear here automatically when a downloadable output is attached.'}
        </p>
      </div>
    `;

    if (media) {
      previewHtml = media.kind === 'video'
        ? `<video src="${App.escapeHtml(media.url)}" ${media.poster ? `poster="${App.escapeHtml(media.poster)}"` : ''} controls playsinline class="job-detail-preview-media"></video>`
        : `<img src="${App.escapeHtml(media.url)}" alt="${App.escapeHtml(job.prompt || job.direction || 'Job output preview')}" class="job-detail-preview-media">`;
    }

    return `
      <section class="card job-detail-card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Output Preview</h3>
            <p class="job-detail-section-copy">Renderable files exposed through the mounted downloads directory.</p>
          </div>
        </div>
        <div class="job-detail-preview-shell">${previewHtml}</div>
        ${outputLinks ? `
          <div style="margin-top: 16px;">
            <div class="detail-label" style="margin-bottom: 8px;">Files</div>
            <div class="job-detail-chip-row">${outputLinks}</div>
          </div>
        ` : ''}
      </section>
    `;
  }

  function renderChildren(job) {
    if (state.childrenError) {
      return `
        <div class="job-detail-error-banner">
          <span class="material-icons" style="font-size:18px">error_outline</span>
          <div>${App.escapeHtml(`Failed to load children: ${state.childrenError}`)}</div>
        </div>
      `;
    }

    if (!state.children.length) {
      return `
        <div class="job-detail-empty-note">
          <span class="material-icons">account_tree</span>
          <span>No child jobs yet.</span>
        </div>
      `;
    }

    return `
      <div class="job-detail-child-grid">
        ${state.children.map((child) => `
          <a href="${jobHash(child.id)}" class="job-detail-child-card">
            <div style="display:flex; align-items:flex-start; justify-content:space-between; gap:10px;">
              <div style="min-width:0;">
                <div class="job-detail-child-title">${App.escapeHtml(jobTypeLabel(child.type))}</div>
                <div class="job-detail-child-subtitle">
                  <code>${App.escapeHtml(shortId(child.id, 16))}</code>
                </div>
              </div>
              <span class="${App.statusBadge(child.status)}">${App.escapeHtml(child.status || 'pending')}</span>
            </div>
            <div class="job-detail-child-meta">
              <span>${App.escapeHtml(child.profile || 'Unpinned')}</span>
              <span>${App.escapeHtml(formatRelativeDate(child.created_at))}</span>
            </div>
          </a>
        `).join('')}
      </div>
    `;
  }

  function renderLineageCard(job) {
    const parentValue = job.parent_job_id
      ? `
        <a href="${jobHash(job.parent_job_id)}">
          ${App.escapeHtml(shortId(job.parent_job_id, 18))}
        </a>
        ${state.parent ? `<span class="${App.statusBadge(state.parent.status)}" style="margin-left:8px;">${App.escapeHtml(state.parent.status || 'pending')}</span>` : ''}
      `
      : '-';

    const chainValue = job.chain_id
      ? `
        <code>${App.escapeHtml(job.chain_id)}</code>
        <span class="job-detail-link-list">
          <a href="#jobs/${encodeURIComponent(job.chain_id)}">Open chain jobs</a>
          <a href="#chains">Open builder</a>
        </span>
      `
      : '-';

    return `
      <section class="card job-detail-card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Lineage</h3>
            <p class="job-detail-section-copy">Navigate the parent/child chain around this job.</p>
          </div>
        </div>
        <div class="detail-list">
          <div class="detail-row">
            <span class="detail-label">Parent</span>
            <span class="detail-value">${parentValue}</span>
          </div>
          <div class="detail-row">
            <span class="detail-label">Children</span>
            <span class="detail-value">${state.children.length}</span>
          </div>
          <div class="detail-row">
            <span class="detail-label">Chain</span>
            <span class="detail-value">${chainValue}</span>
          </div>
        </div>
        <div style="margin-top: 16px;">
          <div class="detail-label" style="margin-bottom: 8px;">Child Jobs</div>
          ${renderChildren(job)}
        </div>
      </section>
    `;
  }

  function renderMetadataCard(job) {
    const rows = [
      ['ID', `<code>${App.escapeHtml(job.id)}</code>`],
      ['Type', App.escapeHtml(jobTypeLabel(job.type))],
      ['Status', `<span class="${App.statusBadge(job.status)}">${App.escapeHtml(job.status || 'pending')}</span>`],
      ['Job Level', App.escapeHtml(String(job.job_level ?? '-'))],
      ['Profile', App.escapeHtml(job.profile || '-')],
      ['Worker', App.escapeHtml(job.worker_id || '-')],
      ['Model', App.escapeHtml(job.model || '-')],
      ['Aspect Ratio', App.escapeHtml(job.aspect_ratio || '-')],
      ['Created', App.escapeHtml(formatExactDate(job.created_at))],
      ['Claimed', App.escapeHtml(formatExactDate(job.claimed_at))],
      ['Updated', App.escapeHtml(formatExactDate(job.updated_at))],
      ['Completed', App.escapeHtml(formatExactDate(job.completed_at))],
      ['Media ID', job.media_id ? `<code>${App.escapeHtml(job.media_id)}</code>` : '-'],
      ['Generation ID', job.generation_id ? `<code>${App.escapeHtml(job.generation_id)}</code>` : '-'],
      ['Project URL', job.project_url ? `<a href="${App.escapeHtml(job.project_url)}" target="_blank" rel="noopener">${App.escapeHtml(job.project_url)}</a>` : '-'],
      ['Edit URL', flowLink(job) ? `<a href="${App.escapeHtml(flowLink(job))}" target="_blank" rel="noopener">${App.escapeHtml(flowLink(job))}</a>` : '-'],
      ['BBox', App.escapeHtml(formatBBox(job.bbox))],
      ['Error', App.escapeHtml(job.error || '-')],
    ];

    return `
      <section class="card job-detail-card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Metadata</h3>
            <p class="job-detail-section-copy">Server model fields for this specific job record.</p>
          </div>
        </div>
        <div class="detail-list">
          ${rows.map(([label, value]) => `
            <div class="detail-row">
              <span class="detail-label">${label}</span>
              <span class="detail-value">${value}</span>
            </div>
          `).join('')}
        </div>
      </section>
    `;
  }

  function renderRawCard(job) {
    return `
      <details class="card job-detail-card">
        <summary class="job-detail-summary">Raw JSON</summary>
        <pre class="job-detail-json">${escapeJson({
          job,
          parent: state.parent,
          children: state.children,
        })}</pre>
      </details>
    `;
  }

  function renderStatusHeader(job) {
    const flowUrl = flowLink(job);
    const isTerminal = TERMINAL_STATUSES.has(job.status);
    const canRetry = RETRYABLE_STATUSES.has(job.status);
    const statusCopy = isTerminal
      ? `Final state updated ${App.escapeHtml(formatRelativeDate(job.updated_at) || 'recently')}.`
      : 'Live updates active. This page will re-fetch automatically when the worker pushes job updates.';

    return `
      <section class="card job-detail-card job-detail-hero">
        <div class="job-detail-breadcrumbs">
          <a href="#jobs">Jobs</a>
          <span class="material-icons" style="font-size:14px;">chevron_right</span>
          <code>${App.escapeHtml(shortId(job.id, 18))}</code>
        </div>
        <div class="job-detail-hero-top">
          <div>
            <div class="job-detail-badge-row">
              <span class="job-detail-type-pill">
                <span class="material-icons" style="font-size:16px">${App.escapeHtml(App.jobTypeIcon(job.type))}</span>
                ${App.escapeHtml(jobTypeShortLabel(job.type))}
              </span>
              <span class="${App.statusBadge(job.status)}">${App.escapeHtml(job.status || 'pending')}</span>
              <span class="job-detail-chip">L${App.escapeHtml(String(job.job_level || 1))}</span>
              ${job.profile ? `<span class="job-detail-chip">Profile: ${App.escapeHtml(job.profile)}</span>` : ''}
            </div>
            <h3 class="job-detail-title">${App.escapeHtml(job.prompt || job.direction || jobTypeLabel(job.type))}</h3>
            <p class="job-detail-subtitle">
              Job <code>${App.escapeHtml(job.id)}</code>
              ${job.completed_at ? `completed ${App.escapeHtml(formatRelativeDate(job.completed_at))}` : `created ${App.escapeHtml(formatRelativeDate(job.created_at))}`}
            </p>
          </div>
          <div class="job-detail-action-row">
            <button class="btn btn-outline" type="button" data-job-detail-action="refresh">
              <span class="material-icons" style="font-size:16px">refresh</span> Refresh
            </button>
            ${canRetry ? `
              <button class="btn btn-primary" type="button" data-job-detail-action="retry">
                <span class="material-icons" style="font-size:16px">restart_alt</span> Retry
              </button>
            ` : ''}
            <button class="btn btn-danger" type="button" data-job-detail-action="delete">
              <span class="material-icons" style="font-size:16px">delete</span> Delete
            </button>
            ${flowUrl ? `
              <a class="btn btn-outline" href="${App.escapeHtml(flowUrl)}" target="_blank" rel="noopener">
                <span class="material-icons" style="font-size:16px">open_in_new</span> Flow
              </a>
            ` : ''}
          </div>
        </div>
        <div class="job-detail-live-block">
          <div class="job-detail-live-header">
            <span class="material-icons" style="font-size:16px">${isTerminal ? 'task_alt' : 'radio_button_checked'}</span>
            <strong>${isTerminal ? 'Current state' : 'Live status'}</strong>
          </div>
          <div class="job-detail-live-copy">${statusCopy}</div>
          ${isTerminal ? '' : '<div class="job-detail-progress-track"><div class="job-detail-progress-bar"></div></div>'}
        </div>
        <div class="job-detail-meta-grid">
          <div class="job-detail-meta-card">
            <span class="job-detail-meta-label">Created</span>
            <strong>${App.escapeHtml(formatExactDate(job.created_at))}</strong>
            <span>${App.escapeHtml(formatRelativeDate(job.created_at))}</span>
          </div>
          <div class="job-detail-meta-card">
            <span class="job-detail-meta-label">Updated</span>
            <strong>${App.escapeHtml(formatExactDate(job.updated_at))}</strong>
            <span>${App.escapeHtml(formatRelativeDate(job.updated_at) || '-')}</span>
          </div>
          <div class="job-detail-meta-card">
            <span class="job-detail-meta-label">Completed</span>
            <strong>${App.escapeHtml(formatExactDate(job.completed_at))}</strong>
            <span>${App.escapeHtml(job.completed_at ? formatRelativeDate(job.completed_at) : 'Not completed')}</span>
          </div>
          <div class="job-detail-meta-card">
            <span class="job-detail-meta-label">Chain</span>
            <strong>${App.escapeHtml(job.chain_id || '-')}</strong>
            <span>${App.escapeHtml(job.parent_job_id ? `Parent ${shortId(job.parent_job_id, 12)}` : 'Root job')}</span>
          </div>
        </div>
        ${job.error ? `
          <div class="job-detail-error-banner">
            <span class="material-icons" style="font-size:18px">error_outline</span>
            <div>${App.escapeHtml(job.error)}</div>
          </div>
        ` : ''}
      </section>
    `;
  }

  function renderLoadedState(job) {
    return `
      <div class="job-detail-stack">
        ${renderStatusHeader(job)}
        <div class="job-detail-columns">
          <div class="job-detail-main-column">
            ${renderPreviewCard(job)}
            ${renderPromptCard(job)}
          </div>
          <div class="job-detail-side-column">
            ${renderLineageCard(job)}
            ${renderMetadataCard(job)}
          </div>
        </div>
        ${renderRawCard(job)}
      </div>
    `;
  }

  function renderMessageState(icon, title, message) {
    return `
      <div class="empty-state" style="min-height: 320px;">
        <span class="material-icons">${App.escapeHtml(icon)}</span>
        <h3>${App.escapeHtml(title)}</h3>
        <p>${App.escapeHtml(message)}</p>
        <a href="#jobs" class="btn btn-outline" style="margin-top:16px;">
          <span class="material-icons" style="font-size:16px">list</span> Back to Jobs
        </a>
      </div>
    `;
  }

  function repaint(html) {
    const root = document.getElementById('job-detail-root');
    if (root) root.innerHTML = html;
  }

  function setActionBusy(button, busy, busyLabel) {
    if (!button) return;
    button.disabled = busy;
    if (busy) {
      button.dataset.originalHtml = button.innerHTML;
      button.innerHTML = `<span class="spinner"></span> ${busyLabel || ''}`.trim();
      return;
    }
    button.innerHTML = button.dataset.originalHtml || button.innerHTML;
  }

  function buildRetryPayload(job) {
    const payload = { type: job.type };
    [
      'prompt',
      'model',
      'aspect_ratio',
      'profile',
      'parent_job_id',
      'chain_id',
      'project_url',
      'media_id',
      'direction',
      'start_image_path',
      'end_image_path',
      'ref_image_path',
    ].forEach((field) => {
      if (job[field] !== undefined && job[field] !== null && job[field] !== '') {
        payload[field] = job[field];
      }
    });

    if (Array.isArray(job.ingredient_image_paths) && job.ingredient_image_paths.length) {
      payload.ingredient_image_paths = [...job.ingredient_image_paths];
    }

    if (job.bbox && ['x', 'y', 'w', 'h'].every((key) => typeof job.bbox[key] === 'number')) {
      payload.bbox = {
        x: job.bbox.x,
        y: job.bbox.y,
        w: job.bbox.w,
        h: job.bbox.h,
      };
    }

    return payload;
  }

  async function loadJobDetail(options = {}) {
    const jobId = state.jobId;
    if (!jobId) {
      state.job = null;
      state.parent = null;
      state.children = [];
      state.childrenError = '';
      updatePageTitle('Job Detail');
      repaint(renderMessageState('link_off', 'Missing job id', 'Open a job from the Jobs page or navigate to #job-detail/<id>.'));
      return;
    }

    const requestId = ++state.requestId;
    updatePageTitle(`Job ${shortId(jobId, 10)}`);

    if (!options.silent || !state.job) {
      repaint(renderMessageState('hourglass_top', 'Loading job details', 'Fetching job metadata, lineage, and outputs...'));
    }

    try {
      const job = await API.jobs.get(jobId);
      const [parentResult, childrenResult] = await Promise.allSettled([
        job.parent_job_id ? API.jobs.get(job.parent_job_id) : Promise.resolve(null),
        fetchJobChildren(job.id),
      ]);

      if (requestId !== state.requestId || App.currentPage !== 'job-detail') return;

      state.job = job;
      state.parent = parentResult.status === 'fulfilled' ? parentResult.value : null;
      state.children = childrenResult.status === 'fulfilled' ? childrenResult.value : [];
      state.childrenError = childrenResult.status === 'rejected' ? errorMessage(childrenResult.reason) : '';

      repaint(renderLoadedState(job));
      updatePageTitle(`Job ${shortId(job.id, 10)}`);
    } catch (err) {
      if (requestId !== state.requestId || App.currentPage !== 'job-detail') return;
      state.job = null;
      state.parent = null;
      state.children = [];
      state.childrenError = '';
      updatePageTitle('Job Detail');
      repaint(renderMessageState('error_outline', 'Failed to load job', errorMessage(err)));
      if (!options.silent) {
        App.toast('Failed to load job: ' + errorMessage(err), 'error');
      }
    }
  }

  function scheduleRefresh() {
    if (App.currentPage !== 'job-detail') return;
    if (state.refreshTimer) clearTimeout(state.refreshTimer);
    state.refreshTimer = setTimeout(() => {
      state.refreshTimer = null;
      loadJobDetail({ silent: true });
    }, 200);
  }

  function handleSocketMessage(event) {
    try {
      const message = JSON.parse(event.data);
      const eventName = message.event || message.type;
      const payload = message.data || message.payload;
      if (eventName !== 'job_update' || !payload?.id) return;

      if (
        payload.id === state.jobId ||
        payload.parent_job_id === state.jobId ||
        payload.id === state.job?.parent_job_id
      ) {
        scheduleRefresh();
      }
    } catch (_) {
      // Ignore malformed frames.
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

  async function retryCurrentJob(button) {
    if (!state.job || !RETRYABLE_STATUSES.has(state.job.status)) return;
    setActionBusy(button, true, 'Retrying...');
    try {
      const retried = await API.jobs.create(buildRetryPayload(state.job));
      const newId = retried?.id || '';
      App.toast(newId ? `Retry queued as ${shortId(newId, 10)}` : 'Retry queued', 'success');
      if (newId) {
        window.location.hash = `job-detail/${encodeURIComponent(newId)}`;
      }
    } catch (err) {
      App.toast('Retry failed: ' + err.message, 'error');
    } finally {
      setActionBusy(button, false);
    }
  }

  async function deleteCurrentJob(button) {
    if (!state.jobId) return;
    if (!confirm('Delete this job? This cannot be undone.')) return;

    const toastCopy = DELETE_CANCELLED_STATUSES.has(state.job?.status) ? 'Job cancelled' : 'Job deleted';
    setActionBusy(button, true, 'Deleting...');
    try {
      await API.jobs.delete(state.jobId);
      App.toast(toastCopy, 'success');
      window.location.hash = '#jobs';
    } catch (err) {
      App.toast('Failed to delete job: ' + errorMessage(err), 'error');
      setActionBusy(button, false);
    }
  }

  function handlePageClick(event) {
    const button = event.target.closest('[data-job-detail-action]');
    if (!button) return;

    const action = button.dataset.jobDetailAction;
    if (action === 'refresh') {
      loadJobDetail();
      return;
    }
    if (action === 'retry') {
      retryCurrentJob(button);
      return;
    }
    if (action === 'delete') {
      deleteCurrentJob(button);
    }
  }

  patchRouter();

  const JobDetailPage = {
    name: 'job-detail',
    title: 'Job Detail',

    render() {
      return `
        <div id="job-detail-page">
          <style>
            .job-detail-stack {
              display: grid;
              gap: 16px;
            }

            .job-detail-card {
              overflow: hidden;
            }

            .job-detail-hero {
              background:
                radial-gradient(circle at top right, rgba(124, 92, 255, 0.18), transparent 20rem),
                linear-gradient(180deg, rgba(255, 255, 255, 0.02), rgba(255, 255, 255, 0)),
                var(--bg-card);
            }

            .job-detail-breadcrumbs {
              display: flex;
              align-items: center;
              gap: 6px;
              margin-bottom: 16px;
              color: var(--text-muted);
              font-size: 12px;
            }

            .job-detail-hero-top,
            .job-detail-action-row,
            .job-detail-badge-row,
            .job-detail-chip-row,
            .job-detail-link-list {
              display: flex;
              flex-wrap: wrap;
              gap: 8px;
            }

            .job-detail-hero-top {
              align-items: flex-start;
              justify-content: space-between;
              gap: 16px;
              margin-bottom: 18px;
            }

            .job-detail-action-row {
              justify-content: flex-end;
            }

            .job-detail-type-pill,
            .job-detail-chip {
              display: inline-flex;
              align-items: center;
              gap: 6px;
              min-height: 30px;
              padding: 0 10px;
              color: var(--text-primary);
              background: rgba(255, 255, 255, 0.04);
              border: 1px solid var(--border-light);
              border-radius: 999px;
              font-size: 12px;
              line-height: 1;
            }

            .job-detail-type-pill {
              background: rgba(124, 92, 255, 0.16);
              border-color: var(--accent-border);
            }

            .job-detail-title {
              margin-top: 12px;
              font-size: 24px;
              font-weight: 700;
              line-height: 1.25;
              letter-spacing: -0.03em;
            }

            .job-detail-subtitle,
            .job-detail-section-copy,
            .job-detail-live-copy {
              margin-top: 6px;
              color: var(--text-secondary);
              font-size: 13px;
            }

            .job-detail-live-block {
              margin-bottom: 18px;
              padding: 14px 16px;
              background: rgba(10, 10, 12, 0.6);
              border: 1px solid var(--border);
              border-radius: 14px;
            }

            .job-detail-live-header {
              display: flex;
              align-items: center;
              gap: 8px;
              margin-bottom: 6px;
            }

            .job-detail-progress-track {
              position: relative;
              height: 8px;
              margin-top: 12px;
              overflow: hidden;
              background: rgba(255, 255, 255, 0.06);
              border-radius: 999px;
            }

            .job-detail-progress-bar {
              position: absolute;
              inset: 0 auto 0 -25%;
              width: 45%;
              background: linear-gradient(90deg, rgba(124, 92, 255, 0.2), rgba(124, 92, 255, 1), rgba(124, 92, 255, 0.2));
              border-radius: inherit;
              animation: job-detail-progress-slide 1.25s linear infinite;
            }

            @keyframes job-detail-progress-slide {
              from { transform: translateX(0); }
              to { transform: translateX(240%); }
            }

            .job-detail-meta-grid {
              display: grid;
              grid-template-columns: repeat(4, minmax(0, 1fr));
              gap: 12px;
            }

            .job-detail-meta-card {
              display: grid;
              gap: 4px;
              padding: 14px;
              background: rgba(10, 10, 12, 0.6);
              border: 1px solid var(--border);
              border-radius: 14px;
            }

            .job-detail-meta-card strong {
              font-size: 14px;
              color: var(--text-primary);
              overflow-wrap: anywhere;
            }

            .job-detail-meta-card span {
              color: var(--text-secondary);
              font-size: 12px;
              overflow-wrap: anywhere;
            }

            .job-detail-meta-label {
              color: var(--text-muted) !important;
              text-transform: uppercase;
              letter-spacing: 0.06em;
            }

            .job-detail-error-banner {
              display: flex;
              gap: 10px;
              margin-top: 16px;
              padding: 14px 16px;
              color: #fecaca;
              background: rgba(239, 68, 68, 0.12);
              border: 1px solid rgba(239, 68, 68, 0.3);
              border-radius: 14px;
            }

            .job-detail-columns {
              display: grid;
              grid-template-columns: minmax(0, 1.35fr) minmax(320px, 0.95fr);
              gap: 16px;
              align-items: start;
            }

            .job-detail-main-column,
            .job-detail-side-column {
              display: grid;
              gap: 16px;
            }

            .job-detail-prompt-block {
              padding: 16px;
              color: var(--text-primary);
              background: rgba(10, 10, 12, 0.65);
              border: 1px solid var(--border);
              border-radius: 14px;
              white-space: pre-wrap;
              overflow-wrap: anywhere;
            }

            .job-detail-prompt-block.empty {
              color: var(--text-muted);
              font-style: italic;
            }

            .job-detail-preview-shell {
              display: grid;
              place-items: center;
              min-height: 320px;
              overflow: hidden;
              background:
                radial-gradient(circle at top left, rgba(59, 130, 246, 0.10), transparent 18rem),
                #060608;
              border: 1px solid var(--border);
              border-radius: 16px;
            }

            .job-detail-preview-media {
              width: 100%;
              max-height: 72vh;
              background: #000;
              object-fit: contain;
            }

            .job-detail-preview-empty,
            .job-detail-empty-note {
              display: grid;
              gap: 8px;
              place-items: center;
              padding: 28px;
              color: var(--text-secondary);
              text-align: center;
            }

            .job-detail-preview-empty .material-icons,
            .job-detail-empty-note .material-icons {
              color: var(--text-muted);
              font-size: 28px;
            }

            .job-detail-child-grid {
              display: grid;
              gap: 10px;
            }

            .job-detail-child-card {
              display: grid;
              gap: 8px;
              padding: 12px 14px;
              color: inherit;
              background: rgba(255, 255, 255, 0.02);
              border: 1px solid var(--border);
              border-radius: 14px;
              transition: background var(--transition), border-color var(--transition), transform var(--transition);
            }

            .job-detail-child-card:hover {
              color: inherit;
              background: var(--bg-card-hover);
              border-color: var(--accent-border);
              transform: translateY(-1px);
            }

            .job-detail-child-title {
              font-weight: 600;
              color: var(--text-primary);
            }

            .job-detail-child-subtitle,
            .job-detail-child-meta {
              display: flex;
              flex-wrap: wrap;
              gap: 8px 12px;
              color: var(--text-secondary);
              font-size: 12px;
            }

            .job-detail-summary {
              cursor: pointer;
              font-weight: 600;
              list-style: none;
            }

            .job-detail-summary::-webkit-details-marker {
              display: none;
            }

            .job-detail-json {
              margin-top: 14px;
              padding: 16px;
              color: var(--text-secondary);
              background: #0a0a0c;
              border: 1px solid var(--border);
              border-radius: 14px;
              font-size: 12px;
              line-height: 1.55;
              white-space: pre-wrap;
              overflow-wrap: anywhere;
            }

            @media (max-width: 1080px) {
              .job-detail-columns,
              .job-detail-meta-grid {
                grid-template-columns: 1fr 1fr;
              }
            }

            @media (max-width: 760px) {
              .job-detail-columns,
              .job-detail-meta-grid {
                grid-template-columns: 1fr;
              }

              .job-detail-hero-top {
                flex-direction: column;
              }

              .job-detail-action-row {
                width: 100%;
                justify-content: flex-start;
              }

              .job-detail-title {
                font-size: 20px;
              }
            }
          </style>
          <div id="job-detail-root">
            ${renderMessageState('hourglass_top', 'Loading job details', 'Preparing the job detail view...')}
          </div>
        </div>
      `;
    },

    mount() {
      patchRouter();
      state.jobId = parseJobIdFromHash();

      state.rootTarget = document.getElementById('job-detail-page');
      state.rootClickHandler = handlePageClick;
      state.rootTarget?.addEventListener('click', state.rootClickHandler);

      attachSocketListener();
      state.wsUnsubs.push(WS.on('connected', attachSocketListener));

      loadJobDetail();
    },

    destroy() {
      state.requestId += 1;

      if (state.refreshTimer) {
        clearTimeout(state.refreshTimer);
        state.refreshTimer = null;
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

      if (state.rootTarget && state.rootClickHandler) {
        state.rootTarget.removeEventListener('click', state.rootClickHandler);
      }

      state.rootTarget = null;
      state.rootClickHandler = null;
      state.job = null;
      state.parent = null;
      state.children = [];
      state.childrenError = '';
    },
  };

  App.register(JobDetailPage);
})();
