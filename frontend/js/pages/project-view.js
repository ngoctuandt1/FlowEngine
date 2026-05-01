/**
 * Project View Page
 * Flow-style grid for all jobs on one chain or Flow project.
 */
(() => {
  const VIDEO_EXTENSIONS = new Set(['mp4', 'webm', 'mov', 'm4v']);
  const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp']);
  const CHAIN_STEP_ALIAS_ROUTE = 'chain-builder';
  const DEFAULT_CHAIN_STEP_TYPE = 'extend-video';
  const ALLOWED_STATUS = new Set(['pending', 'claimed', 'running', 'completed', 'failed', 'cancelled']);

  const state = {
    routeChainId: '',
    routeProjectUrl: '',
    chainId: '',
    projectUrl: '',
    jobs: [],
    latestJobId: '',
    title: 'Project',
    requestId: 0,
    refreshTimer: null,
    loadError: '',
    wsUnsubs: [],
    topBarSnapshot: null,
    menuCleanup: null,
  };

  let routerPatched = false;
  let createPagePatched = false;
  let createApiPatched = false;

  function parseHashRoute() {
    const raw = String(location.hash || '').replace(/^#/, '') || 'home';
    const [pathPart, queryString = ''] = raw.split('?');
    const segments = pathPart.split('/').filter(Boolean);
    return {
      raw,
      pathPart,
      queryString,
      segments,
      pageName: segments[0] || 'home',
      params: new URLSearchParams(queryString),
    };
  }

  function parseProjectRoute() {
    const route = parseHashRoute();
    const pathValue = route.pageName === 'project-view'
      ? decodeURIComponent(route.segments.slice(1).join('/'))
      : '';
    const queryChainId = route.params.get('chain_id') || '';
    const queryProjectUrl = route.params.get('project_url') || '';

    return {
      chainId: queryChainId ? decodeURIComponent(queryChainId) : pathValue,
      projectUrl: decodeURIComponent(queryProjectUrl || ''),
    };
  }

  function parseChainStepPrefill() {
    const route = parseHashRoute();
    if (!['create', CHAIN_STEP_ALIAS_ROUTE].includes(route.pageName)) {
      return null;
    }

    const parent = String(route.params.get('parent') || '').trim();
    if (!parent) return null;

    return {
      parent,
      type: String(route.params.get('type') || DEFAULT_CHAIN_STEP_TYPE).trim() || DEFAULT_CHAIN_STEP_TYPE,
      chainId: String(route.params.get('chain_id') || '').trim(),
    };
  }

  function patchRouter() {
    if (routerPatched || !window.App || typeof App._onRoute !== 'function') return;

    // App's stock router treats `#page?x=y` as a literal page name. Strip the
    // query segment here so deep links like `#project-view?chain_id=...` and
    // the chain-step CTA alias stay compatible without editing app.js.
    App._onRoute = function patchedOnRoute() {
      const route = parseHashRoute();
      const resolvedPage = route.pageName === CHAIN_STEP_ALIAS_ROUTE ? 'create' : route.pageName || 'home';

      if (!this.pages[resolvedPage]) {
        location.hash = '#home';
        return;
      }

      this._loadPage(resolvedPage);
    };

    routerPatched = true;
  }

  function patchCreatePagePrefill() {
    if (createPagePatched || !window.App?.pages?.create) return;

    const page = App.pages.create;
    const originalMount = typeof page.mount === 'function' ? page.mount.bind(page) : null;

    page.mount = function patchedCreateMount() {
      originalMount?.();
      requestAnimationFrame(() => applyCreatePrefill());
    };

    createPagePatched = true;
  }

  function patchCreateApiPrefill() {
    if (createApiPatched || !window.API?.jobs?.create) return;

    const originalCreate = API.jobs.create.bind(API.jobs);
    API.jobs.create = function patchedJobsCreate(data) {
      const prefill = parseChainStepPrefill();
      if (
        prefill &&
        App.currentPage === 'create' &&
        data &&
        typeof data === 'object' &&
        !data.chain_id &&
        data.parent_job_id &&
        String(data.parent_job_id) === prefill.parent &&
        prefill.chainId
      ) {
        return originalCreate({ ...data, chain_id: prefill.chainId });
      }

      return originalCreate(data);
    };

    createApiPatched = true;
  }

  function applyCreatePrefill(attempt = 0) {
    const prefill = parseChainStepPrefill();
    if (!prefill || App.currentPage !== 'create') return;

    const singleTab = document.querySelector('#mode-tabs [data-mode="single"]');
    const typeOptions = Array.from(document.querySelectorAll('#type-selector .type-option'));

    if (!typeOptions.length) {
      if (singleTab && !singleTab.classList.contains('btn-primary')) {
        singleTab.click();
        return;
      }
      if (attempt < 6) {
        setTimeout(() => applyCreatePrefill(attempt + 1), 60);
      }
      return;
    }

    const targetType = typeOptions.find((option) => option.dataset.type === prefill.type);
    if (targetType && !targetType.classList.contains('selected')) {
      targetType.click();
    }

    const parentField = document.getElementById('field-parent-job');
    if (!parentField) {
      if (attempt < 6) {
        setTimeout(() => applyCreatePrefill(attempt + 1), 60);
      }
      return;
    }

    if (parentField.value !== prefill.parent) {
      parentField.value = prefill.parent;
      parentField.dispatchEvent(new Event('input', { bubbles: true }));
      parentField.dispatchEvent(new Event('change', { bubbles: true }));
    }

    document.getElementById('field-prompt')?.focus();
  }

  function normalizeJobList(result) {
    return Array.isArray(result) ? result : result?.jobs || [];
  }

  function mediaTileHelper() {
    return App.mediaTile || window.MediaUtil;
  }

  function formatTileDate(value) {
    if (typeof App?.formatTileDate === 'function') {
      return App.formatTileDate(value);
    }
    if (typeof App?.formatDate === 'function') {
      return App.formatDate(value);
    }
    return '-';
  }

  function safeDateValue(value) {
    const time = new Date(value || 0).getTime();
    return Number.isFinite(time) ? time : 0;
  }

  function compareJobsDesc(a, b) {
    const createdDiff = safeDateValue(b?.created_at || b?.createdAt) - safeDateValue(a?.created_at || a?.createdAt);
    if (createdDiff !== 0) return createdDiff;
    return String(b?.id || '').localeCompare(String(a?.id || ''));
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

  function safeStatus(status) {
    const normalized = String(status || 'pending').toLowerCase();
    return ALLOWED_STATUS.has(normalized) ? normalized : 'pending';
  }

  function primaryMedia(job) {
    const files = Array.isArray(job?.output_files) ? job.output_files : [];
    const renderable = files
      .map((file) => {
        const kind = mediaTypeFromFile(file);
        if (!kind) return null;
        return {
          kind,
          url: mediaUrl(file),
        };
      })
      .filter(Boolean);

    if (!renderable.length) return null;

    const primary = renderable[0];
    const poster = primary.kind === 'video'
      ? renderable.find((file) => file.kind === 'image')?.url || ''
      : '';

    return {
      ...primary,
      poster,
    };
  }

  function primaryJobTitle(job) {
    return job?.prompt || job?.direction || '(no prompt)';
  }

  function projectFallbackTitle() {
    const chainRef = state.chainId || state.routeChainId;
    return chainRef ? `Project ${App.truncate(chainRef, 18)}` : 'Project';
  }

  function subtitleText() {
    return state.chainId
      ? `Chain ${App.truncate(state.chainId, 18)}`
      : 'Project';
  }

  async function resolveRootJob(jobs) {
    const seedJobId = String(jobs[0]?.id || '').trim();
    if (!seedJobId) return null;

    let related = null;
    try {
      related = await API.fetch(`/api/jobs/${encodeURIComponent(seedJobId)}/related`);
    } catch (_) {
      related = null;
    }

    const rootId = String(related?.chain_root_id || '').trim();
    const relatedCandidates = [
      related?.self,
      related?.parent,
      ...(Array.isArray(related?.ancestors) ? related.ancestors : []),
    ].filter((job) => job?.id);

    if (rootId) {
      const rootFromRelated = relatedCandidates.find((job) => String(job.id) === rootId);
      if (rootFromRelated) return rootFromRelated;

      const rootFromJobs = jobs.find((job) => String(job?.id || '') === rootId);
      if (rootFromJobs) return rootFromJobs;

      try {
        return await API.jobs.get(rootId);
      } catch (_) {
        // Fall through to local heuristics.
      }
    }

    return jobs.find((job) => Number(job?.job_level) === 1)
      || jobs.find((job) => !String(job?.parent_job_id || '').trim())
      || null;
  }

  function deriveState(jobs, rootJob = null) {
    const sorted = [...jobs].sort(compareJobsDesc);
    const latestCompleted = sorted.find((job) => safeStatus(job.status) === 'completed');
    const latest = latestCompleted || sorted[0] || null;

    state.jobs = sorted;
    state.chainId = state.routeChainId
      || String(rootJob?.chain_id || '').trim()
      || String(sorted.find((job) => job?.chain_id)?.chain_id || '').trim();
    state.projectUrl = String(sorted.find((job) => job?.project_url)?.project_url || state.routeProjectUrl || '').trim();
    state.latestJobId = String(latest?.id || '').trim();
    const rootCreatedAt = rootJob?.created_at || rootJob?.createdAt || '';
    const rootTitle = formatTileDate(rootCreatedAt);
    state.title = rootTitle && rootTitle !== '-'
      ? rootTitle
      : projectFallbackTitle();
  }

  async function fetchProjectJobs() {
    if (state.routeChainId) {
      const jobs = normalizeJobList(await API.jobs.list({ chain_id: state.routeChainId }));
      return jobs.filter((job) => String(job?.chain_id || '') === state.routeChainId);
    }

    if (state.routeProjectUrl) {
      const jobs = normalizeJobList(await API.jobs.list());
      return jobs.filter((job) => String(job?.project_url || '') === state.routeProjectUrl);
    }

    return [];
  }

  async function hydrateProject() {
    const route = parseProjectRoute();
    state.routeChainId = String(route.chainId || '').trim();
    state.routeProjectUrl = String(route.projectUrl || '').trim();
    state.loadError = '';

    const requestId = ++state.requestId;

    try {
      const jobs = await fetchProjectJobs();
      if (requestId !== state.requestId) return;
      const rootJob = await resolveRootJob(jobs);
      if (requestId !== state.requestId) return;
      deriveState(jobs, rootJob);
    } catch (err) {
      if (requestId !== state.requestId) return;
      state.jobs = [];
      state.latestJobId = '';
      state.chainId = state.routeChainId;
      state.projectUrl = state.routeProjectUrl;
      state.title = projectFallbackTitle();
      state.loadError = err.message || 'Failed to load project';
    }
  }

  function newChainStepHref() {
    const query = new URLSearchParams();
    if (state.latestJobId) query.set('parent', state.latestJobId);
    query.set('type', DEFAULT_CHAIN_STEP_TYPE);
    if (state.chainId) query.set('chain_id', state.chainId);
    return `#${CHAIN_STEP_ALIAS_ROUTE}?${query.toString()}`;
  }

  function renderPreview(job) {
    const status = safeStatus(job?.status);
    const media = status === 'completed' ? primaryMedia(job) : null;
    if (!media) return '';

    const title = primaryJobTitle(job);
    const mediaTile = mediaTileHelper();
    if (!mediaTile) return '';
    return media.kind === 'video'
      ? mediaTile.videoTag({ src: media.url, poster: media.poster, alt: title })
      : mediaTile.imgTag({ src: media.url, alt: title });
  }

  function renderStatusBadge(job) {
    const status = safeStatus(job?.status);
    if (status === 'completed') return '';
    return `<span class="tile-status-badge state-${status}">${App.escapeHtml(status.toUpperCase())}</span>`;
  }

  function renderJobTile(job) {
    const status = safeStatus(job?.status);
    return `
      <a
        class="project-tile status-${status}"
        href="#job-detail/${encodeURIComponent(job?.id || '')}"
        data-job-id="${App.escapeHtml(job?.id || '')}"
        title="${App.escapeHtml(primaryJobTitle(job))}"
      >
        <div class="tile-thumb">
          ${renderPreview(job)}
          ${renderStatusBadge(job)}
        </div>
        <div class="tile-overlay">
          <span class="tile-date">${App.escapeHtml(formatTileDate(job?.created_at || job?.createdAt))}</span>
        </div>
      </a>
    `;
  }

  function renderFloatingFooter() {
    if (!state.routeChainId && !state.routeProjectUrl && !state.latestJobId) return '';
    return `
      <div class="pv-fab-wrap">
        <div class="pv-disclaimer">Flow can make mistakes, so double check it</div>
        ${state.latestJobId ? `
          <a
            class="new-project-pill pv-new-step-pill"
            href="${App.escapeHtml(newChainStepHref())}"
            title="New chain step"
            aria-label="New chain step"
          >
            <span class="material-icons">add</span>
            <span>New chain step</span>
          </a>
        ` : ''}
      </div>
    `;
  }

  function renderBanner(message) {
    return `
      <div class="card" style="padding: 14px 16px; color: #fde68a; background: rgba(234, 179, 8, 0.10); border-color: rgba(234, 179, 8, 0.28);">
        <div style="display:flex; align-items:flex-start; gap:10px;">
          <span class="material-icons" style="font-size:18px;">warning</span>
          <div style="font-size: 13px; line-height: 1.5;">${App.escapeHtml(message)}</div>
        </div>
      </div>
    `;
  }

  function renderEmptyState(title, body) {
    return `
      <div class="card">
        <div class="empty-state">
          <span class="material-icons">collections</span>
          <h3>${App.escapeHtml(title)}</h3>
          <p>${App.escapeHtml(body)}</p>
          <div style="margin-top: 12px;">
            <a href="#gallery" class="btn btn-primary">
              <span class="material-icons" style="font-size:16px">collections</span> Open gallery
            </a>
          </div>
        </div>
      </div>
    `;
  }

  function renderGrid() {
    if (state.loadError && !state.jobs.length) {
      return renderEmptyState('Failed to load project', state.loadError);
    }

    if (!state.routeChainId && !state.routeProjectUrl) {
      return renderEmptyState('Project not found', 'Open a chain from the gallery or jobs list to inspect its media grid.');
    }

    if (!state.jobs.length) {
      return renderEmptyState('No chain outputs yet', 'This project does not have any jobs to render yet. Completed image and video steps will appear here.');
    }

    return `<div class="project-grid">${state.jobs.map(renderJobTile).join('')}</div>`;
  }

  function renderBody() {
    return `
      <div class="pv-page">
        ${state.loadError && state.jobs.length ? renderBanner(state.loadError) : ''}
        ${renderGrid()}
        ${renderFloatingFooter()}
      </div>
    `;
  }

  function repaint() {
    const root = document.getElementById('project-view-page');
    if (!root) return;
    root.innerHTML = renderBody();
    applyTopBar();
  }

  async function refreshProject(options = {}) {
    await hydrateProject();
    repaint();
    if (state.loadError && !options.silent) {
      App.toast('Failed to refresh project: ' + state.loadError, 'error');
    }
  }

  function scheduleRefresh() {
    if (App.currentPage !== 'project-view') return;
    if (state.refreshTimer) clearTimeout(state.refreshTimer);
    state.refreshTimer = setTimeout(() => {
      state.refreshTimer = null;
      void refreshProject({ silent: true });
    }, 250);
  }

  function shouldRefreshForJob(job) {
    if (!job?.id) return false;

    const jobId = String(job.id);
    if (state.jobs.some((entry) => String(entry?.id || '') === jobId)) return true;

    if (state.chainId && String(job.chain_id || '') === state.chainId) return true;
    if (state.projectUrl && String(job.project_url || '') === state.projectUrl) return true;

    return false;
  }

  function clearMenuListeners() {
    state.menuCleanup?.();
    state.menuCleanup = null;
  }

  function applyTopBar() {
    const titleEl = document.getElementById('page-title');
    const actionsEl = document.querySelector('#top-bar .top-bar-actions');
    if (!titleEl || !actionsEl) return;
    clearMenuListeners();

    if (!state.topBarSnapshot) {
      state.topBarSnapshot = {
        titleHtml: titleEl.innerHTML,
        titleStyle: titleEl.getAttribute('style') || '',
        actionsHtml: actionsEl.innerHTML,
      };
    }

    titleEl.innerHTML = `
      <button
        id="project-view-back"
        type="button"
        class="icon-btn"
        aria-label="Back"
        style="width:40px; height:40px; margin-left:-8px;"
      >
        <span class="material-icons">arrow_back</span>
      </button>
      <span class="pv-topbar-heading">
        <span class="pv-topbar-title" title="${App.escapeHtml(state.title || projectFallbackTitle())}">
          ${App.escapeHtml(state.title || 'Project')}
        </span>
        <span class="pv-topbar-subtitle" title="${App.escapeHtml(state.chainId || '')}">
          ${App.escapeHtml(subtitleText())}
        </span>
      </span>
    `;
    titleEl.style.display = 'flex';
    titleEl.style.alignItems = 'center';
    titleEl.style.gap = '10px';

    actionsEl.innerHTML = `
      <div class="pv-topbar-menu" id="project-view-menu-shell">
        <button id="project-view-menu" class="icon-btn" type="button" aria-label="Project actions" aria-haspopup="menu" aria-expanded="false">
          <span class="material-icons">more_vert</span>
        </button>
        <div class="pv-menu-popover" id="project-view-menu-popover" role="menu" hidden>
          ${state.chainId ? `
            <a class="pv-menu-item" href="#jobs/${encodeURIComponent(state.chainId)}" role="menuitem">
              <span class="material-icons">list</span>
              <span>Open jobs</span>
            </a>
          ` : ''}
          <a class="pv-menu-item" href="#gallery" role="menuitem">
            <span class="material-icons">collections</span>
            <span>Gallery</span>
          </a>
        </div>
      </div>
    `;

    document.getElementById('project-view-back')?.addEventListener('click', () => {
      if (window.history.length > 1) {
        window.history.back();
        return;
      }
      location.hash = '#home';
    });

    const menuShell = document.getElementById('project-view-menu-shell');
    const menuButton = document.getElementById('project-view-menu');
    const menuPopover = document.getElementById('project-view-menu-popover');
    const syncMenuState = (open) => {
      menuShell?.classList.toggle('is-open', open);
      if (menuPopover) {
        menuPopover.hidden = !open;
      }
      menuButton?.setAttribute('aria-expanded', open ? 'true' : 'false');
    };
    const closeMenu = () => syncMenuState(false);
    const handleDocumentClick = (event) => {
      if (!menuShell?.contains(event.target)) {
        closeMenu();
      }
    };
    const handleEscape = (event) => {
      if (event.key === 'Escape') {
        closeMenu();
      }
    };

    menuButton?.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      syncMenuState(menuPopover?.hidden);
    });

    menuPopover?.querySelectorAll('a').forEach((link) => {
      link.addEventListener('click', closeMenu);
    });

    document.addEventListener('click', handleDocumentClick);
    document.addEventListener('keydown', handleEscape);
    state.menuCleanup = () => {
      document.removeEventListener('click', handleDocumentClick);
      document.removeEventListener('keydown', handleEscape);
    };
  }

  function restoreTopBar() {
    const titleEl = document.getElementById('page-title');
    const actionsEl = document.querySelector('#top-bar .top-bar-actions');
    if (!titleEl || !actionsEl || !state.topBarSnapshot) return;
    clearMenuListeners();

    titleEl.innerHTML = state.topBarSnapshot.titleHtml;
    if (state.topBarSnapshot.titleStyle) {
      titleEl.setAttribute('style', state.topBarSnapshot.titleStyle);
    } else {
      titleEl.removeAttribute('style');
    }

    actionsEl.innerHTML = state.topBarSnapshot.actionsHtml;
    state.topBarSnapshot = null;
  }

  patchRouter();
  patchCreatePagePrefill();
  patchCreateApiPrefill();

  const ProjectViewPage = {
    name: 'project-view',
    title: 'Project View',
    icon: 'view_module',

    async render() {
      await hydrateProject();
      return `<div id="project-view-page">${renderBody()}</div>`;
    },

    mount() {
      applyTopBar();
      state.wsUnsubs.push(WS.on('job_update', (job) => {
        if (shouldRefreshForJob(job)) {
          scheduleRefresh();
        }
      }));
      state.wsUnsubs.push(WS.on('connected', () => {
        if (App.currentPage === 'project-view') {
          scheduleRefresh();
        }
      }));
    },

    destroy() {
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
      restoreTopBar();
    },
  };

  App.register(ProjectViewPage);
})();
