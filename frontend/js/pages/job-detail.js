/**
 * Job Detail Page
 * Full-screen view for a single job with live WS refresh and multi-level chain context.
 */
(() => {
  const VIDEO_EXTENSIONS = new Set(['mp4', 'webm', 'mov', 'm4v']);
  const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp']);
  const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled']);
  const RETRYABLE_STATUSES = new Set(['failed', 'cancelled']);
  const DELETE_CANCELLED_STATUSES = new Set(['running', 'claimed', 'pending']);
  const JOB_ROUTE_RE = /^(job(?:-detail)?)(?:[/?]|$)/i;
  const HOME_JOB_ROUTE_RE = /^home\/job(?:[/?]|$)/i;
  const CHAIN_BUILDER_ROUTE_RE = /^chain-builder(?:[/?]|\?|$)/i;
  const CHAIN_TREE_ROUTE_RE = /^chain-tree(?:[/?]|$)/i;
  const MAX_CHAIN_TREE_SELECT_ATTEMPTS = 24;
  const BACKEND_GAP_WARNED = new Set();
  const VIDEO_PARENT_JOB_TYPES = new Set([
    'text-to-video',
    'frames-to-video',
    'ingredients-to-video',
    'extend-video',
    'insert-object',
    'remove-object',
    'camera-move',
  ]);
  const IMAGE_PARENT_JOB_TYPES = new Set(['text-to-image']);
  const CONTINUE_CHAIN_ACTIONS = [
    { type: 'extend-video', label: 'Extend', icon: 'add_to_queue' },
    { type: 'insert-object', label: 'Insert object', icon: 'add_box' },
    { type: 'remove-object', label: 'Remove object', icon: 'delete_sweep' },
    { type: 'camera-move', label: 'Camera move', icon: 'videocam_off' },
  ];

  const state = {
    jobId: '',
    job: null,
    parent: null,
    children: [],
    chainJobs: [],
    ancestors: [],
    siblings: [],
    descendants: [],
    rootJob: null,
    profileMismatch: false,
    projectMismatch: false,
    loadError: '',
    refreshError: '',
    parentError: '',
    childrenError: '',
    chainError: '',
    requestId: 0,
    pageToken: 0,
    refreshTimer: null,
    wsUnsubs: [],
    socketListener: null,
    socketTarget: null,
    rootTarget: null,
    rootClickHandler: null,
    debugBadges: [],
  };

  let routerPatched = false;
  let globalJobLaunchPatched = false;
  let chainTreePatched = false;

  // TODO: promote if reused.
  function escapeAttr(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/"/g, '&quot;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
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
      page: 'job-detail',
      field,
      jobId: jobId || '',
      fallbackUsed,
    });
  }

  function renderDebugBadges(items) {
    if (!debugBadgesEnabled() || !Array.isArray(items) || !items.length) return '';
    return items.map((item) => `
      <span
        class="job-detail-chip"
        title="${escapeAttr(`${item.field} -> ${item.fallbackUsed}`)}"
        style="opacity:0.65;"
      >
        ${App.escapeHtml(`gap:${item.field}`)}
      </span>
    `).join('');
  }

  function collectJobDebugBadges(job) {
    const items = [];
    if (!job?.edit_url && job?.project_url && job?.media_id) {
      const fallbackUsed = 'project_url+media_id';
      warnBackendGap({ field: 'edit_url', jobId: String(job.id || ''), fallbackUsed });
      items.push({ field: 'edit_url', fallbackUsed });
    } else if (!job?.edit_url && job?.project_url) {
      const fallbackUsed = 'project_url';
      warnBackendGap({ field: 'edit_url', jobId: String(job.id || ''), fallbackUsed });
      items.push({ field: 'edit_url', fallbackUsed });
    }
    return items;
  }

  function isJobRouteHash(hash) {
    return JOB_ROUTE_RE.test(hash) || HOME_JOB_ROUTE_RE.test(hash);
  }

  function isChainBuilderRouteHash(hash) {
    return CHAIN_BUILDER_ROUTE_RE.test(hash);
  }

  function patchRouter() {
    if (routerPatched || !window.App || typeof App._onRoute !== 'function') return;

    const originalOnRoute = App._onRoute.bind(App);
    App._onRoute = function patchedOnRoute() {
      const hash = String(location.hash || '').replace(/^#/, '') || 'home';
      if (isJobRouteHash(hash)) {
        if (!this.pages['job-detail']) {
          location.hash = '#home';
          return;
        }
        this._loadPage('job-detail');
        return;
      }
      // Keep `#chain-builder` as a semantic alias while the registered page name remains `chains`.
      if (isChainBuilderRouteHash(hash)) {
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

  function parseJobIdFromHash() {
    const raw = String(window.location.hash || '').replace(/^#/, '');
    if (!raw) return '';

    const [pathPart, queryString = ''] = raw.split('?');
    const segments = pathPart.split('/').filter(Boolean);

    if (segments.length && /^(job|job-detail)$/i.test(segments[0])) {
      const tail = segments.slice(1).join('/');
      if (tail) return decodeURIComponent(tail);
    }

    if (segments.length > 2 && /^home$/i.test(segments[0]) && /^job$/i.test(segments[1])) {
      const tail = segments.slice(2).join('/');
      if (tail) return decodeURIComponent(tail);
    }

    const params = new URLSearchParams(queryString);
    const id = params.get('id') || '';
    return id ? decodeURIComponent(id) : '';
  }

  function parseChainIdFromHash() {
    const raw = String(window.location.hash || '').replace(/^#/, '');
    if (!raw) return '';

    const [pathPart, queryString = ''] = raw.split('?');
    const segments = pathPart.split('/').filter(Boolean);
    if (segments.length && /^chain-tree$/i.test(segments[0])) {
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

  function chainTreeHash(chainId) {
    return `#chain-tree/${encodeURIComponent(String(chainId || '').trim())}`;
  }

  function isPrimaryPlainClick(event) {
    return !event.defaultPrevented &&
      event.button === 0 &&
      !event.metaKey &&
      !event.ctrlKey &&
      !event.shiftKey &&
      !event.altKey;
  }

  function findGlobalJobId(event) {
    const galleryTile = event.target.closest('.gallery-tile[data-job-id]');
    if (galleryTile?.dataset.jobId) return galleryTile.dataset.jobId;

    const homeTile = event.target.closest('#home-recent .project-tile[data-job-id]');
    if (homeTile && !homeTile.classList.contains('new-project-tile') && homeTile.dataset.jobId) {
      return homeTile.dataset.jobId;
    }

    return '';
  }

  function handleGlobalJobLaunch(event) {
    if (!isPrimaryPlainClick(event)) return;

    const jobId = findGlobalJobId(event);
    if (!jobId) return;

    event.preventDefault();
    event.stopPropagation();
    if (typeof event.stopImmediatePropagation === 'function') {
      event.stopImmediatePropagation();
    }

    if (typeof App.closeModal === 'function') {
      App.closeModal();
    }

    window.location.hash = jobHash(jobId);
  }

  function patchGlobalJobLaunch() {
    if (globalJobLaunchPatched) return;
    document.addEventListener('click', handleGlobalJobLaunch, true);
    globalJobLaunchPatched = true;
  }

  function syncChainTreeSelectionFromHash(attempt = 0) {
    const chainId = parseChainIdFromHash();
    if (!chainId || App.currentPage !== 'chain-tree') return;

    const root = document.getElementById('chain-tree-page');
    if (!root) {
      if (attempt < MAX_CHAIN_TREE_SELECT_ATTEMPTS) {
        setTimeout(() => syncChainTreeSelectionFromHash(attempt + 1), 150);
      }
      return;
    }

    const target = Array.from(root.querySelectorAll('[data-chain-id]'))
      .find((entry) => entry.dataset.chainId === chainId);

    if (target) {
      target.click();
      return;
    }

    if (attempt < MAX_CHAIN_TREE_SELECT_ATTEMPTS) {
      setTimeout(() => syncChainTreeSelectionFromHash(attempt + 1), 150);
    }
  }

  function patchChainTreePage() {
    if (chainTreePatched || !window.App?.pages?.['chain-tree']) return;

    const page = App.pages['chain-tree'];
    const originalMount = typeof page.mount === 'function' ? page.mount.bind(page) : null;
    page.mount = function patchedMount() {
      originalMount?.();
      if (CHAIN_TREE_ROUTE_RE.test(String(location.hash || '').replace(/^#/, ''))) {
        syncChainTreeSelectionFromHash(0);
      }
    };

    chainTreePatched = true;
  }

  function shortId(value, maxLen = 12) {
    return App.truncate(String(value || ''), maxLen);
  }

  function safeDateValue(value) {
    const time = new Date(value || 0).getTime();
    return Number.isFinite(time) ? time : 0;
  }

  function compareJobs(a, b) {
    const levelDiff = (Number(a?.job_level) || 0) - (Number(b?.job_level) || 0);
    if (levelDiff !== 0) return levelDiff;

    const createdDiff = safeDateValue(a?.created_at) - safeDateValue(b?.created_at);
    if (createdDiff !== 0) return createdDiff;

    return String(a?.id || '').localeCompare(String(b?.id || ''));
  }

  function jobTypeLabel(type) {
    const meta = typeof CONST?.typeMeta === 'function' ? CONST.typeMeta(type) : null;
    return meta?.label || (type || 'Unknown').replace(/-/g, ' ');
  }

  function jobTypeShortLabel(type) {
    const meta = typeof CONST?.typeMeta === 'function' ? CONST.typeMeta(type) : null;
    return meta?.shortLabel || jobTypeLabel(type);
  }

  function jobDisplayText(job) {
    return job?.prompt || job?.direction || jobTypeLabel(job?.type);
  }

  function getContinueChainAvailability(job) {
    const type = String(job?.type || '').trim();
    if (IMAGE_PARENT_JOB_TYPES.has(type)) {
      return { enabled: false, reason: 'Image jobs have no chain operations' };
    }
    if (!VIDEO_PARENT_JOB_TYPES.has(type)) {
      return { enabled: false, reason: 'This job type does not support chain operations' };
    }
    if (String(job?.status || '').trim() !== 'completed') {
      return { enabled: false, reason: 'Job must complete before chaining' };
    }
    return { enabled: true, reason: '' };
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
    if (!value) return '-';
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

  function thumbUrlForJob(job) {
    const media = primaryMedia(job);
    if (!media) return '';
    if (media.kind === 'image') return media.url;
    return media.poster || '';
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
      warnBackendGap({
        field: 'edit_url',
        jobId: String(job.id || ''),
        fallbackUsed: 'project_url+media_id',
      });
      return `${String(job.project_url).replace(/\/+$/, '')}/edit/${job.media_id}`;
    }
    if (job?.project_url) {
      warnBackendGap({
        field: 'edit_url',
        jobId: String(job.id || ''),
        fallbackUsed: 'project_url',
      });
    }
    return job?.project_url || '';
  }

  function normalizeJobList(result) {
    const items = Array.isArray(result) ? result : result?.jobs || [];
    return items
      .filter((job) => job && typeof job === 'object' && job.id)
      .map((job) => ({ ...job }));
  }

  function uniqueJobsById(jobs) {
    const seen = new Map();
    jobs
      .filter((job) => job && typeof job === 'object' && job.id)
      .forEach((job) => {
        seen.set(String(job.id), { ...job });
      });
    return Array.from(seen.values());
  }

  function pickRootJob(jobs) {
    const sorted = [...jobs].sort(compareJobs);
    return (
      sorted.find((job) => !job.parent_job_id) ||
      sorted.find((job) => Number(job.job_level) === 1) ||
      sorted[0] ||
      null
    );
  }

  function buildChainContext(job, parentJob, children, chainJobs) {
    const allJobs = uniqueJobsById([
      ...normalizeJobList(chainJobs),
      ...(parentJob ? [parentJob] : []),
      ...normalizeJobList(children),
      job,
    ]).sort(compareJobs);

    const byId = new Map(allJobs.map((entry) => [String(entry.id), entry]));
    const currentJob = byId.get(String(job.id)) || job;
    byId.set(String(job.id), currentJob);

    const childrenByParent = new Map();
    allJobs.forEach((entry) => {
      const parentId = String(entry.parent_job_id || '').trim();
      if (!parentId) return;
      if (!childrenByParent.has(parentId)) childrenByParent.set(parentId, []);
      childrenByParent.get(parentId).push(entry);
    });

    const directParent = currentJob.parent_job_id
      ? byId.get(String(currentJob.parent_job_id)) || parentJob || null
      : null;

    const directChildren = uniqueJobsById([
      ...(childrenByParent.get(String(currentJob.id)) || []),
      ...normalizeJobList(children),
    ]).sort(compareJobs);

    const ancestors = [];
    const ancestorSeen = new Set([String(currentJob.id)]);
    let cursor = directParent;
    while (cursor && !ancestorSeen.has(String(cursor.id))) {
      ancestors.unshift(cursor);
      ancestorSeen.add(String(cursor.id));
      cursor = cursor.parent_job_id
        ? byId.get(String(cursor.parent_job_id)) || null
        : null;
    }

    const siblings = directParent
      ? (childrenByParent.get(String(directParent.id)) || [])
        .filter((entry) => String(entry.id) !== String(currentJob.id))
        .sort(compareJobs)
      : [];

    const descendants = [];
    const descendantQueue = [...directChildren];
    const descendantSeen = new Set(descendantQueue.map((entry) => String(entry.id)));
    while (descendantQueue.length) {
      const next = descendantQueue.shift();
      descendants.push(next);
      (childrenByParent.get(String(next.id)) || []).forEach((child) => {
        const childId = String(child.id);
        if (descendantSeen.has(childId)) return;
        descendantSeen.add(childId);
        descendantQueue.push(child);
      });
    }

    const rootJob = pickRootJob(allJobs) || ancestors[0] || currentJob;
    const parentProfile = directParent?.profile || '';
    const currentProfile = currentJob?.profile || '';
    const parentProject = directParent?.project_url || '';
    const currentProject = currentJob?.project_url || '';

    return {
      allJobs,
      currentJob,
      parent: directParent,
      children: directChildren,
      ancestors,
      siblings,
      descendants,
      rootJob,
      profileMismatch: Boolean(parentProfile && currentProfile && parentProfile !== currentProfile),
      projectMismatch: Boolean(parentProject && currentProject && parentProject !== currentProject),
    };
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

  function createRequestToken(jobId) {
    return {
      id: ++state.requestId,
      jobId: String(jobId || ''),
      pageToken: state.pageToken,
    };
  }

  function isActiveRequest(token) {
    return Boolean(
      token &&
      token.id === state.requestId &&
      token.jobId === state.jobId &&
      token.pageToken === state.pageToken &&
      App.currentPage === 'job-detail'
    );
  }

  function resetLoadedState() {
    state.job = null;
    state.parent = null;
    state.children = [];
    state.chainJobs = [];
    state.ancestors = [];
    state.siblings = [];
    state.descendants = [];
    state.rootJob = null;
    state.profileMismatch = false;
    state.projectMismatch = false;
    state.debugBadges = [];
    state.loadError = '';
    state.refreshError = '';
    state.parentError = '';
    state.childrenError = '';
    state.chainError = '';
  }

  function pendingThumb(job) {
    return !job?.media_id && !TERMINAL_STATUSES.has(String(job?.status || 'pending'));
  }

  function renderJobThumb(job, size = 'sm') {
    const thumbUrl = thumbUrlForJob(job);
    if (thumbUrl) {
      return `
        <span class="job-detail-thumb job-detail-thumb--${size}" data-thumb-wrap>
          <img
            src="${escapeAttr(thumbUrl)}"
            alt="${escapeAttr(`${jobTypeLabel(job?.type)} thumbnail`)}"
            loading="lazy"
            decoding="async"
            onerror="var wrap=this.closest('[data-thumb-wrap]'); if (wrap) wrap.classList.add('tile-thumb--broken'); this.remove();"
          >
        </span>
      `;
    }

    if (pendingThumb(job)) {
      return `
        <span class="job-detail-thumb job-detail-thumb--${size} job-detail-thumb--pending">
          <span class="job-detail-pending-pill">Pending</span>
        </span>
      `;
    }

    return `
      <span class="job-detail-thumb job-detail-thumb--${size} job-detail-thumb--icon">
        <span class="material-icons">${App.escapeHtml(App.jobTypeIcon(job?.type))}</span>
      </span>
    `;
  }

  function renderDetailError(title, message, actionLabel = 'Retry', action = 'refresh') {
    return `
      <div class="detail-error" role="alert">
        <span class="material-icons" aria-hidden="true">error_outline</span>
        <div class="detail-error-copy">
          <div class="detail-error-title">${App.escapeHtml(title)}</div>
          <div class="detail-error-message">${App.escapeHtml(message)}</div>
        </div>
        <button class="btn btn-sm btn-outline" type="button" data-job-detail-action="${escapeAttr(action)}">
          <span class="material-icons" style="font-size:16px">refresh</span> ${App.escapeHtml(actionLabel)}
        </button>
      </div>
    `;
  }

  function renderPromptCard(job) {
    const prompt = job.prompt || '';
    const chips = [
      job.model ? `<span class="job-detail-chip">Model: ${App.escapeHtml(job.model)}</span>` : '',
      job.aspect_ratio ? `<span class="job-detail-chip">Aspect: ${App.escapeHtml(job.aspect_ratio)}</span>` : '',
      job.direction ? `<span class="job-detail-chip">Direction: ${App.escapeHtml(job.direction)}</span>` : '',
      job.bbox ? `<span class="job-detail-chip">BBox: ${App.escapeHtml(formatBBox(job.bbox))}</span>` : '',
      job.media_id ? `<span class="job-detail-chip">Media: ${App.escapeHtml(shortId(job.media_id, 18))}</span>` : '<span class="job-detail-chip">Media: Pending</span>',
      job.generation_id ? `<span class="job-detail-chip">Generation: ${App.escapeHtml(shortId(job.generation_id, 18))}</span>` : '',
      renderDebugBadges(state.debugBadges),
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
            <p class="job-detail-section-copy">Operation inputs and the target context this job runs against.</p>
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
          <a class="btn btn-sm btn-outline" href="${escapeAttr(mediaUrl(file))}" target="_blank" rel="noopener">
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
        ? `
          <video
            src="${escapeAttr(media.url)}"
            ${media.poster ? `poster="${escapeAttr(media.poster)}"` : ''}
            controls
            playsinline
            preload="metadata"
            class="job-detail-preview-media"
          ></video>
        `
        : `
          <img
            src="${escapeAttr(media.url)}"
            alt="${escapeAttr(jobDisplayText(job) || 'Job output preview')}"
            class="job-detail-preview-media"
            loading="lazy"
            decoding="async"
          >
        `;
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

  function renderChainSegment(job, isCurrent = false) {
    const mediaLabel = job.media_id ? shortId(job.media_id, 14) : 'Pending media';
    return `
      <a href="${jobHash(job.id)}" class="job-detail-chain-segment ${isCurrent ? 'current' : ''}">
        ${renderJobThumb(job, 'xs')}
        <span class="job-detail-chain-segment-copy">
          <span class="job-detail-chain-segment-top">
            <span class="job-detail-chain-type">
              <span class="material-icons" style="font-size:15px">${App.escapeHtml(App.jobTypeIcon(job.type))}</span>
              ${App.escapeHtml(jobTypeShortLabel(job.type))}
            </span>
            <span class="job-detail-chain-level">L${App.escapeHtml(String(job.job_level || 1))}</span>
          </span>
          <span class="job-detail-chain-segment-meta">
            ${App.escapeHtml(job.profile || 'Unpinned')} · ${App.escapeHtml(mediaLabel)}
          </span>
        </span>
      </a>
    `;
  }

  function renderChainStrip(job) {
    const path = state.ancestors.length ? [...state.ancestors, job] : [job];
    const stats = [
      job.chain_id ? `Chain ${shortId(job.chain_id, 16)}` : 'Standalone job',
      `${state.siblings.length} sibling${state.siblings.length === 1 ? '' : 's'}`,
      `${state.descendants.length} descendant${state.descendants.length === 1 ? '' : 's'}`,
    ].join(' · ');

    return `
      <section class="card job-detail-card job-detail-chain-card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Chain Breadcrumb</h3>
            <p class="job-detail-section-copy">Root to current job, with chain-local media and profile context.</p>
          </div>
          ${job.chain_id ? `
            <a class="btn btn-sm btn-outline" href="${chainTreeHash(job.chain_id)}">
              <span class="material-icons" style="font-size:16px">account_tree</span> View full chain tree
            </a>
          ` : ''}
        </div>
        <div class="job-detail-chain-strip">
          ${path.map((entry, index) => `
            ${index ? '<span class="material-icons job-detail-chain-arrow" aria-hidden="true">chevron_right</span>' : ''}
            ${renderChainSegment(entry, String(entry.id) === String(job.id))}
          `).join('')}
        </div>
        <p class="job-detail-chain-meta">${App.escapeHtml(stats)}</p>
        ${state.chainError ? renderDetailError('Chain context is partial', state.chainError, 'Retry context') : ''}
      </section>
    `;
  }

  function renderContextJobCard(job, label, options = {}) {
    const isCurrent = Boolean(options.current);
    const flowUrl = flowLink(job);
    return `
      <a href="${jobHash(job.id)}" class="job-detail-context-card ${isCurrent ? 'current' : ''}">
        <div class="job-detail-context-top">
          <div class="job-detail-context-title">
            ${renderJobThumb(job, 'sm')}
            <div class="job-detail-context-copy">
              <div class="job-detail-context-label">${App.escapeHtml(label)}</div>
              <div class="job-detail-context-name">${App.escapeHtml(jobDisplayText(job))}</div>
            </div>
          </div>
          <span class="${App.statusBadge(job.status)}">${App.escapeHtml(job.status || 'pending')}</span>
        </div>
        <div class="job-detail-context-grid">
          <span><strong>ID</strong> ${App.escapeHtml(shortId(job.id, 16))}</span>
          <span><strong>Type</strong> ${App.escapeHtml(jobTypeShortLabel(job.type))}</span>
          <span><strong>Profile</strong> ${App.escapeHtml(job.profile || 'Unpinned')}</span>
          <span><strong>Media</strong> ${App.escapeHtml(job.media_id ? shortId(job.media_id, 16) : 'Pending')}</span>
          <span><strong>Project</strong> ${App.escapeHtml(job.project_url ? 'Attached' : 'Pending')}</span>
          <span><strong>Updated</strong> ${App.escapeHtml(formatRelativeDate(job.updated_at))}</span>
        </div>
        ${flowUrl ? `
          <div class="job-detail-context-footer">
            <span class="material-icons" style="font-size:15px">open_in_new</span>
            <span>${App.escapeHtml(shortId(flowUrl, 40))}</span>
          </div>
        ` : ''}
      </a>
    `;
  }

  function renderSiblingStrip() {
    if (!state.siblings.length) {
      return '<div class="job-detail-empty-note job-detail-inline-empty">No siblings in this branch.</div>';
    }

    return `
      <div class="job-detail-sibling-list">
        ${state.siblings.map((job) => `
          <a href="${jobHash(job.id)}" class="job-detail-sibling-chip">
            ${renderJobThumb(job, 'xs')}
            <span>${App.escapeHtml(jobTypeShortLabel(job.type))}</span>
            <code>${App.escapeHtml(shortId(job.id, 10))}</code>
          </a>
        `).join('')}
      </div>
    `;
  }

  function renderChildrenSection() {
    if (state.childrenError) {
      return renderDetailError('Children failed to load', state.childrenError, 'Retry children');
    }

    if (!state.children.length) {
      return `
        <div class="job-detail-empty-note job-detail-inline-empty">
          <span class="material-icons">account_tree</span>
          <span>No child jobs yet.</span>
        </div>
      `;
    }

    return `
      <div class="job-detail-context-children-grid">
        ${state.children.map((child) => renderContextJobCard(child, 'Child')).join('')}
      </div>
    `;
  }

  function renderContextPanel(job) {
    return `
      <section class="card job-detail-card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Multi-level Context</h3>
            <p class="job-detail-section-copy">Parent project, this job, siblings, and direct children from the same chain.</p>
          </div>
        </div>
        <div class="job-detail-context-stack">
          <div class="job-detail-context-section">
            <div class="job-detail-section-heading">Parent</div>
            ${state.parent
              ? renderContextJobCard(state.parent, 'Parent')
              : `
                <div class="job-detail-empty-note job-detail-inline-empty">
                  <span class="material-icons">vertical_align_top</span>
                  <span>${job.parent_job_id ? 'Parent context is unavailable.' : 'This is the root job.'}</span>
                </div>
              `}
            ${state.parentError && !state.parent ? renderDetailError('Parent context is partial', state.parentError, 'Retry parent') : ''}
          </div>

          <div class="job-detail-context-section">
            <div class="job-detail-section-heading">This</div>
            ${renderContextJobCard(job, 'Current job', { current: true })}
            <div class="job-detail-side-metrics">
              <span class="job-detail-side-metric"><strong>${App.escapeHtml(String(state.ancestors.length))}</strong> ancestor${state.ancestors.length === 1 ? '' : 's'}</span>
              <span class="job-detail-side-metric"><strong>${App.escapeHtml(String(state.siblings.length))}</strong> sibling${state.siblings.length === 1 ? '' : 's'}</span>
              <span class="job-detail-side-metric"><strong>${App.escapeHtml(String(state.descendants.length))}</strong> descendant${state.descendants.length === 1 ? '' : 's'}</span>
            </div>
            <div>
              <div class="job-detail-section-heading" style="margin-bottom: 10px;">Siblings</div>
              ${renderSiblingStrip()}
            </div>
          </div>

          <div class="job-detail-context-section">
            <div class="job-detail-section-heading">Children</div>
            ${renderChildrenSection()}
          </div>
        </div>
      </section>
    `;
  }

  function renderInvariantCard(job) {
    const flowUrl = flowLink(job);
    const profileNote = state.parent
      ? (state.profileMismatch
        ? `Mismatch with parent profile ${state.parent.profile || '(missing)'}.`
        : `Matches parent profile ${state.parent.profile || '(missing)'}.`)
      : 'Root job profile for this chain.';
    const projectNote = state.parent?.project_url
      ? (state.projectMismatch
        ? 'Differs from parent project URL.'
        : 'Matches the parent project URL.')
      : 'No parent project URL to compare.';

    const rows = [
      [
        'Profile',
        `
          <div class="job-detail-link-stack">
            <span class="job-detail-invariant-pill ${state.profileMismatch ? 'warn' : (job.profile ? 'ok' : 'muted')}">
              ${App.escapeHtml(job.profile || 'Unpinned')}
            </span>
            <span class="job-detail-soft-note">${App.escapeHtml(profileNote)}</span>
          </div>
        `,
      ],
      [
        'Media ID',
        job.media_id
          ? `<code>${App.escapeHtml(job.media_id)}</code>`
          : '<span class="job-detail-pending-pill">Pending</span>',
      ],
      [
        'Generation ID',
        job.generation_id
          ? `<code>${App.escapeHtml(job.generation_id)}</code>`
          : '<span class="job-detail-soft-note">Pending</span>',
      ],
      [
        'Project URL',
        job.project_url
          ? `
            <div class="job-detail-link-stack">
              <a href="${escapeAttr(job.project_url)}" target="_blank" rel="noopener">${App.escapeHtml(job.project_url)}</a>
              <span class="job-detail-soft-note">${App.escapeHtml(projectNote)}</span>
            </div>
          `
          : '<span class="job-detail-soft-note">Not assigned yet.</span>',
      ],
      [
        'Edit URL',
        flowUrl
          ? `
            <div class="job-detail-link-stack">
              <a href="${escapeAttr(flowUrl)}" target="_blank" rel="noopener">Open Flow editor</a>
              <span class="job-detail-soft-note">${App.escapeHtml(flowUrl)}</span>
            </div>
          `
          : '<span class="job-detail-soft-note">Unavailable until project and media resolve.</span>',
      ],
      [
        'Full Chain Tree',
        job.chain_id
          ? `<a href="${chainTreeHash(job.chain_id)}">${App.escapeHtml(`#chain-tree/${job.chain_id}`)}</a>`
          : '<span class="job-detail-soft-note">No chain id on this job.</span>',
      ],
    ];

    return `
      <section class="card job-detail-card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Chain Invariants</h3>
            <p class="job-detail-section-copy">Fields that should stay stable across the chain are surfaced explicitly here.</p>
          </div>
        </div>
        <div class="detail-list">
          ${rows.map(([label, value]) => `
            <div class="detail-row">
              <span class="detail-label">${App.escapeHtml(label)}</span>
              <span class="detail-value">${value}</span>
            </div>
          `).join('')}
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
      ['Parent ID', job.parent_job_id ? `<a href="${jobHash(job.parent_job_id)}"><code>${App.escapeHtml(job.parent_job_id)}</code></a>` : '-'],
      ['Chain ID', job.chain_id ? `<code>${App.escapeHtml(job.chain_id)}</code>` : '-'],
      ['Root ID', state.rootJob?.id ? `<a href="${jobHash(state.rootJob.id)}"><code>${App.escapeHtml(state.rootJob.id)}</code></a>` : '-'],
      ['Worker', App.escapeHtml(job.worker_id || '-')],
      ['Model', App.escapeHtml(job.model || '-')],
      ['Aspect Ratio', App.escapeHtml(job.aspect_ratio || '-')],
      ['Created', App.escapeHtml(formatExactDate(job.created_at))],
      ['Claimed', App.escapeHtml(formatExactDate(job.claimed_at))],
      ['Updated', App.escapeHtml(formatExactDate(job.updated_at))],
      ['Completed', App.escapeHtml(formatExactDate(job.completed_at))],
      ['Children', App.escapeHtml(String(state.children.length))],
      ['Siblings', App.escapeHtml(String(state.siblings.length))],
      ['Descendants', App.escapeHtml(String(state.descendants.length))],
      ['BBox', App.escapeHtml(formatBBox(job.bbox))],
      ['Error', App.escapeHtml(job.error || '-')],
    ];

    return `
      <section class="card job-detail-card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Metadata</h3>
            <p class="job-detail-section-copy">Full record fields plus derived chain counts for this job.</p>
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

  function renderContinueChainPanel(job) {
    const availability = getContinueChainAvailability(job);
    const statusIcon = availability.reason === 'Job must complete before chaining' ? 'schedule' : 'info';

    return `
      <section class="card job-detail-card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Continue chain</h3>
            <p class="job-detail-section-copy">Start the next L2+ edit from this job without rebuilding the parent context.</p>
          </div>
        </div>
        ${availability.reason ? `
          <div class="job-detail-continue-status" role="note">
            <span class="material-icons" aria-hidden="true">${App.escapeHtml(statusIcon)}</span>
            <span>${App.escapeHtml(availability.reason)}</span>
          </div>
        ` : ''}
        <div
          class="job-detail-continue-actions"
          role="group"
          aria-label="Continue chain operations"
          ${availability.enabled ? '' : `title="${escapeAttr(availability.reason)}"`}
        >
          ${CONTINUE_CHAIN_ACTIONS.map((action) => {
            const buttonTitle = availability.enabled
              ? `Open chain builder for ${action.label}`
              : availability.reason;
            return `
              <button
                class="job-detail-continue-btn"
                type="button"
                data-job-detail-action="continue-chain"
                data-chain-type="${escapeAttr(action.type)}"
                title="${escapeAttr(buttonTitle)}"
                aria-label="${escapeAttr(buttonTitle)}"
                ${availability.enabled ? '' : 'disabled'}
              >
                <span class="material-icons" aria-hidden="true">${App.escapeHtml(action.icon)}</span>
                <span>${App.escapeHtml(action.label)}</span>
              </button>
            `;
          }).join('')}
        </div>
        <div class="job-detail-continue-hint">These actions inherit project, profile, and media from this job.</div>
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
          ancestors: state.ancestors,
          siblings: state.siblings,
          descendants: state.descendants,
          chain_jobs: state.chainJobs,
          errors: {
            load: state.loadError,
            refresh: state.refreshError,
            parent: state.parentError,
            children: state.childrenError,
            chain: state.chainError,
          },
        })}</pre>
      </details>
    `;
  }

  function renderStatusHeader(job) {
    const flowUrl = flowLink(job);
    const isTerminal = TERMINAL_STATUSES.has(job.status);
    const canRetry = RETRYABLE_STATUSES.has(job.status);
    const statusCopy = isTerminal
      ? `Final state updated ${App.escapeHtml(formatRelativeDate(job.updated_at))}.`
      : 'Live updates active. This page re-fetches when the worker pushes job or chain updates.';

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
              ${job.media_id ? `<span class="job-detail-chip">Media: ${App.escapeHtml(shortId(job.media_id, 16))}</span>` : '<span class="job-detail-chip">Media pending</span>'}
            </div>
            <h3 class="job-detail-title">${App.escapeHtml(jobDisplayText(job))}</h3>
            <p class="job-detail-subtitle">
              Job <code>${App.escapeHtml(job.id)}</code>
              ${job.completed_at ? ` completed ${App.escapeHtml(formatRelativeDate(job.completed_at))}` : ` created ${App.escapeHtml(formatRelativeDate(job.created_at))}`}
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
              <a class="btn btn-outline" href="${escapeAttr(flowUrl)}" target="_blank" rel="noopener">
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
            <span>${App.escapeHtml(formatRelativeDate(job.updated_at))}</span>
          </div>
          <div class="job-detail-meta-card">
            <span class="job-detail-meta-label">Completed</span>
            <strong>${App.escapeHtml(formatExactDate(job.completed_at))}</strong>
            <span>${App.escapeHtml(job.completed_at ? formatRelativeDate(job.completed_at) : 'Not completed')}</span>
          </div>
          <div class="job-detail-meta-card">
            <span class="job-detail-meta-label">Chain</span>
            <strong>${App.escapeHtml(job.chain_id || '-')}</strong>
            <span>${App.escapeHtml(`${state.ancestors.length} up · ${state.descendants.length} down`)}</span>
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

  function renderPageAlerts() {
    const alerts = [];
    if (state.refreshError) {
      alerts.push(renderDetailError('Latest refresh failed', state.refreshError, 'Retry refresh'));
    }
    return alerts.length ? `<div class="job-detail-alert-stack">${alerts.join('')}</div>` : '';
  }

  function renderLoadedState(job) {
    return `
      <div class="job-detail-stack">
        ${renderPageAlerts()}
        ${renderChainStrip(job)}
        ${renderStatusHeader(job)}
        <div class="job-detail-columns">
          <div class="job-detail-main-column">
            ${renderPreviewCard(job)}
            ${renderPromptCard(job)}
          </div>
          <div class="job-detail-side-column">
            ${renderContextPanel(job)}
            ${renderInvariantCard(job)}
            ${renderMetadataCard(job)}
            ${renderContinueChainPanel(job)}
          </div>
        </div>
        ${renderRawCard(job)}
      </div>
    `;
  }

  function renderLoadingState(jobId) {
    const title = jobId ? `Loading ${App.escapeHtml(shortId(jobId, 12))}` : 'Loading job detail';
    return `
      <div class="job-detail-stack">
        <section class="card job-detail-card job-detail-chain-card">
          <div class="section-header">
            <div>
              <h3 class="section-title">Chain Breadcrumb</h3>
              <p class="job-detail-section-copy">Resolving ancestors, siblings, and descendants...</p>
            </div>
          </div>
          <div class="job-detail-chain-strip">
            <div class="job-detail-chain-segment current">
              <span class="job-detail-thumb job-detail-thumb--xs job-detail-thumb--skeleton"></span>
              <span class="job-detail-chain-segment-copy">
                <span class="job-detail-skeleton-line w-140"></span>
                <span class="job-detail-skeleton-line w-180"></span>
              </span>
            </div>
            <span class="material-icons job-detail-chain-arrow" aria-hidden="true">chevron_right</span>
            <div class="job-detail-chain-segment">
              <span class="job-detail-thumb job-detail-thumb--xs job-detail-thumb--skeleton"></span>
              <span class="job-detail-chain-segment-copy">
                <span class="job-detail-skeleton-line w-120"></span>
                <span class="job-detail-skeleton-line w-160"></span>
              </span>
            </div>
          </div>
        </section>

        <section class="card job-detail-card job-detail-hero">
          <div class="job-detail-breadcrumbs">
            <a href="#jobs">Jobs</a>
            <span class="material-icons" style="font-size:14px;">chevron_right</span>
            <code>${title}</code>
          </div>
          <div class="job-detail-hero-top">
            <div style="min-width:0;">
              <div class="job-detail-badge-row">
                <span class="job-detail-skeleton-pill"></span>
                <span class="job-detail-skeleton-pill short"></span>
                <span class="job-detail-skeleton-pill short"></span>
              </div>
              <div class="job-detail-skeleton-line w-320" style="margin-top: 14px;"></div>
              <div class="job-detail-skeleton-line w-220"></div>
            </div>
            <div class="job-detail-action-row">
              <span class="job-detail-skeleton-pill"></span>
              <span class="job-detail-skeleton-pill"></span>
            </div>
          </div>
          <div class="job-detail-live-block">
            <div class="job-detail-skeleton-line w-180"></div>
            <div class="job-detail-skeleton-line w-260"></div>
            <div class="job-detail-progress-track">
              <div class="job-detail-progress-bar"></div>
            </div>
          </div>
          <div class="job-detail-meta-grid">
            ${Array.from({ length: 4 }).map(() => `
              <div class="job-detail-meta-card">
                <span class="job-detail-skeleton-line w-80"></span>
                <span class="job-detail-skeleton-line w-160"></span>
                <span class="job-detail-skeleton-line w-120"></span>
              </div>
            `).join('')}
          </div>
        </section>

        <div class="job-detail-columns">
          <div class="job-detail-main-column">
            <section class="card job-detail-card">
              <div class="section-header">
                <div>
                  <h3 class="section-title">Output Preview</h3>
                  <p class="job-detail-section-copy">Loading preview and output files...</p>
                </div>
              </div>
              <div class="job-detail-preview-shell job-detail-preview-shell--loading">
                <span class="job-detail-thumb job-detail-thumb--preview job-detail-thumb--skeleton"></span>
              </div>
            </section>
            <section class="card job-detail-card">
              <div class="section-header">
                <div>
                  <h3 class="section-title">Prompt & Params</h3>
                  <p class="job-detail-section-copy">Loading inputs and inherited target metadata...</p>
                </div>
              </div>
              <div class="job-detail-prompt-block">
                <div class="job-detail-skeleton-line w-100"></div>
                <div class="job-detail-skeleton-line w-240"></div>
                <div class="job-detail-skeleton-line w-180"></div>
              </div>
            </section>
          </div>
          <div class="job-detail-side-column">
            <section class="card job-detail-card">
              <div class="section-header">
                <div>
                  <h3 class="section-title">Multi-level Context</h3>
                  <p class="job-detail-section-copy">Loading related jobs...</p>
                </div>
              </div>
              <div class="job-detail-context-stack">
                ${Array.from({ length: 3 }).map(() => `
                  <div class="job-detail-context-card">
                    <div class="job-detail-context-top">
                      <div class="job-detail-context-title">
                        <span class="job-detail-thumb job-detail-thumb--sm job-detail-thumb--skeleton"></span>
                        <div class="job-detail-context-copy">
                          <span class="job-detail-skeleton-line w-80"></span>
                          <span class="job-detail-skeleton-line w-180"></span>
                        </div>
                      </div>
                      <span class="job-detail-skeleton-pill short"></span>
                    </div>
                    <div class="job-detail-context-grid">
                      <span class="job-detail-skeleton-line w-120"></span>
                      <span class="job-detail-skeleton-line w-120"></span>
                      <span class="job-detail-skeleton-line w-120"></span>
                      <span class="job-detail-skeleton-line w-120"></span>
                    </div>
                  </div>
                `).join('')}
              </div>
            </section>
          </div>
        </div>
      </div>
    `;
  }

  function renderMissingState() {
    return `
      <div class="job-detail-stack">
        ${renderDetailError(
          'Missing job id',
          'Open a job from the Jobs page or navigate to #job-detail/<id>.',
          'Back to Jobs',
          'back-to-jobs'
        )}
      </div>
    `;
  }

  function renderLoadErrorState(message) {
    return `
      <div class="job-detail-stack">
        ${renderDetailError('Failed to load job detail', message, 'Retry load', 'retry-load')}
        <a href="#jobs" class="btn btn-outline" style="width:fit-content;">
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

  function stashPendingChainParent(jobId, type) {
    if (!jobId || !type) return;
    try {
      sessionStorage.setItem('pendingChainParent', JSON.stringify({
        parent_job_id: String(jobId),
        type: String(type),
      }));
    } catch (_) {
      // Ignore storage failures and still route to the builder page.
    }
  }

  async function loadJobDetail(options = {}) {
    const jobId = state.jobId;
    if (!jobId) {
      resetLoadedState();
      updatePageTitle('Job Detail');
      repaint(renderMissingState());
      return;
    }

    const requestToken = createRequestToken(jobId);
    state.loadError = '';
    state.refreshError = '';
    state.parentError = '';
    state.childrenError = '';
    state.chainError = '';

    updatePageTitle(`Job ${shortId(jobId, 10)}`);

    if (!options.silent || !state.job) {
      repaint(renderLoadingState(jobId));
    }

    try {
      const job = await API.jobs.get(jobId);
      const [parentResult, childrenResult, chainResult] = await Promise.allSettled([
        job.parent_job_id ? API.jobs.get(job.parent_job_id) : Promise.resolve(null),
        fetchJobChildren(job.id),
        job.chain_id ? API.jobs.list({ chain_id: job.chain_id }) : Promise.resolve([]),
      ]);

      if (!isActiveRequest(requestToken)) return;

      const parentValue = parentResult.status === 'fulfilled' ? parentResult.value : null;
      const childrenValue = childrenResult.status === 'fulfilled' ? childrenResult.value : [];
      const chainJobsValue = chainResult.status === 'fulfilled'
        ? normalizeJobList(chainResult.value).filter((entry) => String(entry.chain_id || '') === String(job.chain_id || ''))
        : [];

      const context = buildChainContext(job, parentValue, childrenValue, chainJobsValue);
      state.job = context.currentJob;
      state.parent = context.parent;
      state.children = context.children;
      state.chainJobs = context.allJobs;
      state.ancestors = context.ancestors;
      state.siblings = context.siblings;
      state.descendants = context.descendants;
      state.rootJob = context.rootJob;
      state.profileMismatch = context.profileMismatch;
      state.projectMismatch = context.projectMismatch;
      state.debugBadges = collectJobDebugBadges(state.job);
      state.parentError = parentResult.status === 'rejected' && !context.parent ? errorMessage(parentResult.reason) : '';
      state.childrenError = childrenResult.status === 'rejected' && !context.children.length ? errorMessage(childrenResult.reason) : '';
      state.chainError = chainResult.status === 'rejected' && job.chain_id ? errorMessage(chainResult.reason) : '';
      state.loadError = '';
      state.refreshError = '';

      repaint(renderLoadedState(state.job));
      updatePageTitle(`Job ${shortId(state.job.id, 10)}`);
    } catch (err) {
      if (!isActiveRequest(requestToken)) return;

      const message = errorMessage(err);
      if (state.job) {
        state.refreshError = message;
        repaint(renderLoadedState(state.job));
      } else {
        resetLoadedState();
        state.loadError = message;
        updatePageTitle('Job Detail');
        repaint(renderLoadErrorState(message));
      }

      if (!options.silent) {
        App.toast('Failed to load job: ' + message, 'error');
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

      const currentChainId = String(state.job?.chain_id || '');
      if (
        String(payload.id) === String(state.jobId) ||
        String(payload.parent_job_id || '') === String(state.jobId) ||
        String(payload.id) === String(state.job?.parent_job_id || '') ||
        (currentChainId && String(payload.chain_id || '') === currentChainId)
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
        window.location.hash = jobHash(newId);
      }
    } catch (err) {
      App.toast('Retry failed: ' + errorMessage(err), 'error');
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

  function continueChainFromCurrentJob(button) {
    const chainType = String(button?.dataset.chainType || '').trim();
    const jobId = String(state.job?.id || '').trim();
    const availability = getContinueChainAvailability(state.job);
    if (!chainType || !jobId) return;
    if (!availability.enabled) {
      App.toast(availability.reason, 'warning');
      return;
    }
    stashPendingChainParent(jobId, chainType);
    window.location.hash = '#chain-builder';
  }

  function handlePageClick(event) {
    const button = event.target.closest('[data-job-detail-action]');
    if (!button) return;

    const action = button.dataset.jobDetailAction;
    if (action === 'refresh' || action === 'retry-load') {
      loadJobDetail();
      return;
    }
    if (action === 'retry') {
      retryCurrentJob(button);
      return;
    }
    if (action === 'delete') {
      deleteCurrentJob(button);
      return;
    }
    if (action === 'continue-chain') {
      continueChainFromCurrentJob(button);
      return;
    }
    if (action === 'back-to-jobs') {
      window.location.hash = '#jobs';
    }
  }

  patchRouter();
  patchGlobalJobLaunch();
  patchChainTreePage();

  const JobDetailPage = {
    name: 'job-detail',
    title: 'Job Detail',

    render() {
      return `
        <div id="job-detail-page">
          <style>
            .job-detail-stack,
            .job-detail-alert-stack,
            .job-detail-main-column,
            .job-detail-side-column,
            .job-detail-context-stack,
            .job-detail-context-section,
            .job-detail-link-stack {
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

            .job-detail-chain-card {
              background:
                radial-gradient(circle at top left, rgba(59, 130, 246, 0.14), transparent 18rem),
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
            .job-detail-link-list,
            .job-detail-chain-strip,
            .job-detail-side-metrics,
            .job-detail-sibling-list,
            .detail-error {
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
            .job-detail-chip,
            .job-detail-pending-pill,
            .job-detail-skeleton-pill {
              display: inline-flex;
              align-items: center;
              justify-content: center;
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

            .job-detail-pending-pill {
              min-height: 24px;
              padding: 0 9px;
              color: #fde68a;
              background: rgba(245, 158, 11, 0.14);
              border-color: rgba(245, 158, 11, 0.25);
              font-size: 11px;
              font-weight: 600;
              letter-spacing: 0.01em;
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
            .job-detail-live-copy,
            .job-detail-chain-meta,
            .job-detail-soft-note,
            .job-detail-context-footer {
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

            @keyframes job-detail-shimmer {
              0% { background-position: 100% 50%; }
              100% { background-position: 0 50%; }
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

            .job-detail-continue-status {
              display: flex;
              align-items: center;
              gap: 8px;
              padding: 12px 14px;
              margin-bottom: 14px;
              color: var(--text-secondary);
              background: rgba(255, 255, 255, 0.03);
              border: 1px solid var(--border);
              border-radius: 14px;
              font-size: 13px;
            }

            .job-detail-continue-status .material-icons {
              color: var(--text-muted);
              font-size: 16px;
            }

            .job-detail-continue-actions {
              display: flex;
              flex-wrap: wrap;
              gap: 10px;
            }

            .job-detail-continue-btn {
              display: inline-flex;
              align-items: center;
              gap: 10px;
              flex: 1 1 180px;
              min-height: 48px;
              padding: 0 16px;
              color: var(--text-primary);
              background: linear-gradient(180deg, rgba(255, 255, 255, 0.06), rgba(255, 255, 255, 0.03));
              border: 1px solid rgba(255, 255, 255, 0.10);
              border-radius: 999px;
              box-shadow: 0 10px 24px rgba(0, 0, 0, 0.18);
              transition:
                transform var(--transition),
                border-color var(--transition),
                background var(--transition),
                box-shadow var(--transition),
                opacity var(--transition);
            }

            .job-detail-continue-btn .material-icons {
              font-size: 18px;
              color: #c4b5fd;
            }

            .job-detail-continue-btn:hover:not(:disabled),
            .job-detail-continue-btn:focus-visible:not(:disabled) {
              color: var(--text-primary);
              background: linear-gradient(180deg, rgba(124, 92, 255, 0.18), rgba(255, 255, 255, 0.06));
              border-color: var(--accent-border);
              transform: translateY(-2px);
              box-shadow: 0 16px 32px rgba(0, 0, 0, 0.28);
            }

            .job-detail-continue-btn:focus-visible {
              outline: none;
            }

            .job-detail-continue-btn:disabled {
              opacity: 0.58;
              cursor: not-allowed;
              box-shadow: none;
              pointer-events: none;
            }

            .job-detail-continue-btn:disabled .material-icons {
              color: var(--text-muted);
            }

            .job-detail-continue-hint {
              margin-top: 12px;
              color: var(--text-secondary);
              font-size: 13px;
            }

            .job-detail-error-banner,
            .detail-error {
              align-items: center;
              padding: 14px 16px;
              color: #fecaca;
              background: rgba(239, 68, 68, 0.12);
              border: 1px solid rgba(239, 68, 68, 0.3);
              border-radius: 14px;
            }

            .detail-error {
              justify-content: space-between;
            }

            .detail-error .material-icons {
              flex: 0 0 auto;
              font-size: 18px;
            }

            .detail-error-copy {
              display: grid;
              gap: 4px;
              flex: 1 1 240px;
              min-width: 0;
            }

            .detail-error-title {
              color: #fee2e2;
              font-size: 13px;
              font-weight: 700;
            }

            .detail-error-message {
              color: #fecaca;
              font-size: 13px;
              overflow-wrap: anywhere;
            }

            .job-detail-columns {
              display: grid;
              grid-template-columns: minmax(0, 1.25fr) minmax(320px, 0.95fr);
              gap: 16px;
              align-items: start;
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

            .job-detail-preview-shell--loading {
              min-height: 360px;
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

            .job-detail-inline-empty {
              min-height: 84px;
              padding: 18px;
              border: 1px dashed var(--border);
              border-radius: 14px;
              background: rgba(255, 255, 255, 0.02);
            }

            .job-detail-preview-empty .material-icons,
            .job-detail-empty-note .material-icons {
              color: var(--text-muted);
              font-size: 28px;
            }

            .job-detail-chain-strip {
              align-items: stretch;
              gap: 10px;
              overflow-x: auto;
              padding-bottom: 4px;
            }

            .job-detail-chain-arrow {
              align-self: center;
              color: var(--text-muted);
            }

            .job-detail-chain-segment,
            .job-detail-context-card,
            .job-detail-sibling-chip {
              color: inherit;
              border: 1px solid var(--border);
              background: rgba(255, 255, 255, 0.02);
              transition: background var(--transition), border-color var(--transition), transform var(--transition);
            }

            .job-detail-chain-segment {
              display: flex;
              align-items: center;
              gap: 10px;
              min-width: 240px;
              padding: 12px;
              border-radius: 16px;
            }

            .job-detail-chain-segment.current {
              background: rgba(124, 92, 255, 0.14);
              border-color: var(--accent-border);
            }

            .job-detail-chain-segment:hover,
            .job-detail-context-card:hover,
            .job-detail-sibling-chip:hover {
              color: inherit;
              background: var(--bg-card-hover);
              border-color: var(--accent-border);
              transform: translateY(-1px);
            }

            .job-detail-chain-segment-copy,
            .job-detail-context-copy {
              display: grid;
              gap: 4px;
              min-width: 0;
            }

            .job-detail-chain-segment-top,
            .job-detail-context-top,
            .job-detail-context-title {
              display: flex;
              align-items: center;
              gap: 10px;
            }

            .job-detail-chain-segment-top {
              justify-content: space-between;
            }

            .job-detail-chain-type,
            .job-detail-chain-level,
            .job-detail-context-label,
            .job-detail-side-metric strong {
              color: var(--text-primary);
              font-weight: 600;
            }

            .job-detail-chain-type {
              display: inline-flex;
              align-items: center;
              gap: 6px;
              min-width: 0;
            }

            .job-detail-chain-level {
              color: var(--text-secondary);
              font-size: 12px;
            }

            .job-detail-chain-segment-meta {
              color: var(--text-secondary);
              font-size: 12px;
              overflow-wrap: anywhere;
            }

            .job-detail-context-card {
              display: grid;
              gap: 12px;
              padding: 14px;
              border-radius: 16px;
            }

            .job-detail-context-card.current {
              background: rgba(124, 92, 255, 0.14);
              border-color: var(--accent-border);
            }

            .job-detail-context-title {
              min-width: 0;
              flex: 1 1 auto;
            }

            .job-detail-context-name {
              color: var(--text-primary);
              font-size: 14px;
              font-weight: 600;
              line-height: 1.35;
              overflow-wrap: anywhere;
            }

            .job-detail-context-label {
              color: var(--text-muted);
              font-size: 11px;
              letter-spacing: 0.06em;
              text-transform: uppercase;
            }

            .job-detail-context-grid {
              display: grid;
              grid-template-columns: repeat(2, minmax(0, 1fr));
              gap: 8px 12px;
              color: var(--text-secondary);
              font-size: 12px;
            }

            .job-detail-context-grid strong {
              color: var(--text-primary);
              margin-right: 4px;
            }

            .job-detail-context-footer {
              display: inline-flex;
              align-items: center;
              gap: 6px;
              overflow-wrap: anywhere;
            }

            .job-detail-context-children-grid {
              display: grid;
              gap: 12px;
            }

            .job-detail-section-heading {
              color: var(--text-muted);
              font-size: 11px;
              font-weight: 700;
              letter-spacing: 0.08em;
              text-transform: uppercase;
            }

            .job-detail-side-metrics {
              gap: 10px;
            }

            .job-detail-side-metric {
              display: inline-flex;
              align-items: center;
              gap: 6px;
              padding: 7px 10px;
              color: var(--text-secondary);
              background: rgba(255, 255, 255, 0.03);
              border: 1px solid var(--border);
              border-radius: 999px;
              font-size: 12px;
            }

            .job-detail-sibling-list {
              gap: 10px;
            }

            .job-detail-sibling-chip {
              display: inline-flex;
              align-items: center;
              gap: 8px;
              padding: 8px 10px;
              border-radius: 999px;
              font-size: 12px;
            }

            .job-detail-thumb {
              position: relative;
              display: inline-flex;
              align-items: center;
              justify-content: center;
              flex: 0 0 auto;
              overflow: hidden;
              background:
                radial-gradient(circle at 30% 30%, rgba(124, 92, 255, 0.14), transparent 60%),
                linear-gradient(135deg, #1c1d22 0%, #0e0f12 100%);
              border: 1px solid rgba(255, 255, 255, 0.08);
              border-radius: 12px;
            }

            .job-detail-thumb--preview {
              width: min(100%, 320px);
              height: min(320px, 58vw);
              border-radius: 24px;
            }

            .job-detail-thumb--xs {
              width: 24px;
              height: 24px;
              border-radius: 8px;
            }

            .job-detail-thumb--sm {
              width: 46px;
              height: 46px;
            }

            .job-detail-thumb img {
              width: 100%;
              height: 100%;
              object-fit: cover;
              display: block;
              background: #000;
            }

            .job-detail-thumb--icon .material-icons {
              color: rgba(255, 255, 255, 0.7);
              font-size: 20px;
            }

            .job-detail-thumb--xs.job-detail-thumb--icon .material-icons {
              font-size: 14px;
            }

            .job-detail-thumb--pending {
              background: rgba(245, 158, 11, 0.10);
              border-color: rgba(245, 158, 11, 0.24);
            }

            .job-detail-thumb.tile-thumb--broken {
              background:
                linear-gradient(135deg, rgba(239, 68, 68, 0.16), rgba(31, 41, 55, 0.85)),
                #120b0d;
              border-color: rgba(239, 68, 68, 0.32);
            }

            .job-detail-thumb.tile-thumb--broken::before {
              content: '!';
              color: #fecaca;
              font-size: 12px;
              font-weight: 800;
            }

            .job-detail-link-stack {
              gap: 6px;
            }

            .job-detail-invariant-pill {
              display: inline-flex;
              width: fit-content;
              align-items: center;
              gap: 6px;
              min-height: 26px;
              padding: 0 10px;
              border-radius: 999px;
              border: 1px solid var(--border);
              color: var(--text-primary);
              background: rgba(255, 255, 255, 0.04);
              font-size: 12px;
              font-weight: 600;
            }

            .job-detail-invariant-pill.ok {
              color: #bbf7d0;
              background: rgba(34, 197, 94, 0.16);
              border-color: rgba(34, 197, 94, 0.28);
            }

            .job-detail-invariant-pill.warn {
              color: #fecaca;
              background: rgba(239, 68, 68, 0.16);
              border-color: rgba(239, 68, 68, 0.28);
            }

            .job-detail-invariant-pill.muted {
              color: var(--text-secondary);
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

            .job-detail-skeleton-line,
            .job-detail-skeleton-pill,
            .job-detail-thumb--skeleton {
              background:
                linear-gradient(90deg, rgba(255, 255, 255, 0.05), rgba(255, 255, 255, 0.16), rgba(255, 255, 255, 0.05));
              background-size: 220% 100%;
              animation: job-detail-shimmer 1.4s linear infinite;
            }

            .job-detail-skeleton-line {
              display: block;
              height: 12px;
              border-radius: 999px;
            }

            .job-detail-skeleton-pill {
              width: 110px;
            }

            .job-detail-skeleton-pill.short {
              width: 74px;
            }

            .job-detail-thumb--skeleton {
              border-color: rgba(255, 255, 255, 0.08);
            }

            .w-80 { width: 80px; }
            .w-100 { width: 100px; }
            .w-120 { width: 120px; }
            .w-140 { width: 140px; }
            .w-160 { width: 160px; }
            .w-180 { width: 180px; }
            .w-220 { width: 220px; }
            .w-240 { width: 240px; }
            .w-260 { width: 260px; }
            .w-320 { width: 320px; }

            @media (max-width: 1180px) {
              .job-detail-columns {
                grid-template-columns: 1fr;
              }

              .job-detail-meta-grid {
                grid-template-columns: 1fr 1fr;
              }
            }

            @media (max-width: 760px) {
              .job-detail-meta-grid,
              .job-detail-context-grid {
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

              .job-detail-continue-btn {
                flex-basis: 100%;
              }

              .job-detail-chain-segment {
                min-width: 220px;
              }

              .detail-error {
                align-items: flex-start;
              }
            }
          </style>
          <div id="job-detail-root">
            ${renderLoadingState('')}
          </div>
        </div>
      `;
    },

    mount() {
      patchRouter();
      patchGlobalJobLaunch();
      patchChainTreePage();

      state.pageToken += 1;
      state.jobId = parseJobIdFromHash();

      state.rootTarget = document.getElementById('job-detail-page');
      state.rootClickHandler = handlePageClick;
      state.rootTarget?.addEventListener('click', state.rootClickHandler);

      attachSocketListener();
      state.wsUnsubs.push(WS.on('connected', attachSocketListener));

      loadJobDetail();
    },

    destroy() {
      state.pageToken += 1;
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
      state.jobId = '';
      resetLoadedState();
    },
  };

  App.register(JobDetailPage);
})();
