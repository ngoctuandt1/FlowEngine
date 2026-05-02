/**
 * Project View Page
 * DAG canvas for one chain with pan/zoom, node cards, and curved edges.
 */
(() => {
  const PAGE_ROOT_ID = 'project-view-page';
  const CHAIN_STEP_ROUTE = 'chain-builder';
  const DEFAULT_CHAIN_STEP_TYPE = 'extend-video';
  const MIN_ZOOM = 0.4;
  const MAX_ZOOM = 1.6;
  const ZOOM_STEP = 0.14;
  const X_SPACING = 320;
  const Y_SPACING = 280;
  const STAGE_PADDING = 72;
  const DEFAULT_NODE_WIDTH = 292;
  const DEFAULT_NODE_HEIGHT = 356;
  const PROMPT_PREVIEW_CHARS = 180;
  const ALLOWED_STATUS = new Set(['pending', 'claimed', 'running', 'completed', 'failed', 'cancelled']);
  const ACTIVE_STATUSES = new Set(['claimed', 'running']);
  const VIDEO_EXTENSIONS = new Set(['mp4', 'webm', 'mov', 'm4v']);
  const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp']);
  const STUB_ACTIONS = new Set([
    'run-workflow',
    'batch-run',
    'export',
    'ai-agent',
    'settings',
    'node-upload',
    'node-play',
    'node-delete',
    'node-refs',
  ]);
  const COUNT_PILLS = [1, 2, 4];

  const state = {
    chainId: '',
    requestId: 0,
    refreshTimer: null,
    loadError: '',
    jobs: [],
    jobsById: new Map(),
    edges: [],
    rootId: '',
    rootJob: null,
    latestJobId: '',
    title: 'Project',
    selectedJobId: '',
    expandedPrompts: new Set(),
    layoutModel: null,
    layout: {
      framesById: new Map(),
      width: Math.max(1, STAGE_PADDING * 2 + DEFAULT_NODE_WIDTH),
      height: Math.max(1, STAGE_PADDING * 2 + DEFAULT_NODE_HEIGHT),
    },
    viewport: { x: 0, y: 0, z: 1 },
    fitViewport: { x: 0, y: 0, z: 1 },
    userViewportChanged: false,
    menuOpen: false,
    drag: null,
    wsUnsubs: [],
    rootEl: null,
    topBarEl: null,
    topBarDisplay: '',
    windowListenersBound: false,
  };

  function escapeAttr(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/"/g, '&quot;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  function safeStatus(status) {
    const normalized = String(status || 'pending').toLowerCase();
    return ALLOWED_STATUS.has(normalized) ? normalized : 'pending';
  }

  function safeDateValue(value) {
    const time = new Date(value || 0).getTime();
    return Number.isFinite(time) ? time : 0;
  }

  function compareByCreatedAsc(a, b) {
    const createdDiff = safeDateValue(a?.created_at || a?.createdAt) - safeDateValue(b?.created_at || b?.createdAt);
    if (createdDiff !== 0) return createdDiff;
    return String(a?.id || '').localeCompare(String(b?.id || ''));
  }

  function compareJobs(a, b) {
    const levelDiff = (Number(a?.job_level) || 1) - (Number(b?.job_level) || 1);
    if (levelDiff !== 0) return levelDiff;
    return compareByCreatedAsc(a, b);
  }

  function compareByCreatedDesc(a, b) {
    const createdDiff = safeDateValue(b?.created_at || b?.createdAt) - safeDateValue(a?.created_at || a?.createdAt);
    if (createdDiff !== 0) return createdDiff;
    return String(b?.id || '').localeCompare(String(a?.id || ''));
  }

  function formatTileDate(value) {
    if (typeof App?.formatTileDate === 'function') return App.formatTileDate(value);
    if (typeof App?.formatDate === 'function') return App.formatDate(value);
    return '-';
  }

  function parseRouteChainId() {
    const raw = String(location.hash || '').replace(/^#/, '');
    if (!raw) return '';
    const [pathPart, queryString = ''] = raw.split('?');
    const segments = pathPart.split('/').filter(Boolean);
    if ((segments[0] || '') !== 'project-view') return '';
    if (segments.length > 1) return decodeURIComponent(segments.slice(1).join('/'));
    return decodeURIComponent(new URLSearchParams(queryString).get('chain_id') || '');
  }

  function normalizeJobList(result) {
    const items = Array.isArray(result) ? result : result?.jobs || [];
    return items.filter((job) => job && typeof job === 'object' && job.id).map((job) => ({ ...job }));
  }

  function uniqueJobsById(jobs) {
    const byId = new Map();
    jobs.forEach((job) => {
      if (!job?.id) return;
      byId.set(String(job.id), { ...job });
    });
    return Array.from(byId.values());
  }

  function pickRootJob(jobs, explicitRootId = '') {
    if (explicitRootId) {
      const explicit = jobs.find((job) => String(job.id) === explicitRootId);
      if (explicit) return explicit;
    }
    return (
      jobs.find((job) => !String(job.parent_job_id || '').trim()) ||
      jobs.find((job) => Number(job.job_level) === 1) ||
      [...jobs].sort(compareByCreatedAsc)[0] ||
      null
    );
  }

  function normalizeEdges(rawEdges, jobsById) {
    const seen = new Set();
    const edges = [];
    (Array.isArray(rawEdges) ? rawEdges : []).forEach((edge) => {
      const parent = String(edge?.parent || '').trim();
      const child = String(edge?.child || '').trim();
      if (!parent || !child || !jobsById.has(parent) || !jobsById.has(child)) return;
      const key = `${parent}->${child}`;
      if (seen.has(key)) return;
      seen.add(key);
      edges.push({ parent, child });
    });
    return edges;
  }

  function buildEdgesFromJobs(jobs) {
    const jobsById = new Map(jobs.map((job) => [String(job.id), job]));
    const seen = new Set();
    const edges = [];
    jobs.forEach((job) => {
      const child = String(job?.id || '').trim();
      const parent = String(job?.parent_job_id || '').trim();
      if (!parent || !child || !jobsById.has(parent)) return;
      const key = `${parent}->${child}`;
      if (seen.has(key)) return;
      seen.add(key);
      edges.push({ parent, child });
    });
    return edges;
  }

  function walkChainJobs(jobs, rootId = '') {
    if (!jobs.length) return [];
    const nodesById = new Map(jobs.map((job) => [String(job.id), job]));
    const childrenByParent = new Map();
    jobs.forEach((job) => {
      const parentId = String(job?.parent_job_id || '').trim();
      if (!parentId || !nodesById.has(parentId)) return;
      if (!childrenByParent.has(parentId)) childrenByParent.set(parentId, []);
      childrenByParent.get(parentId).push(job);
    });
    childrenByParent.forEach((items) => items.sort(compareJobs));

    const ordered = [];
    const seen = new Set();
    function visit(jobId) {
      const key = String(jobId || '').trim();
      if (!key || seen.has(key) || !nodesById.has(key)) return;
      seen.add(key);
      ordered.push(nodesById.get(key));
      (childrenByParent.get(key) || []).forEach((child) => visit(child.id));
    }

    const rootJob = pickRootJob(jobs, rootId);
    if (rootJob?.id) visit(rootJob.id);
    [...jobs].sort(compareJobs).forEach((job) => visit(job.id));
    return ordered;
  }

  function mediaUrl(file) {
    const normalized = String(file || '').replace(/\\/g, '/').trim();
    if (!normalized) return '';
    if (/^https?:\/\//i.test(normalized)) return normalized;
    if (/^\/?downloads\//i.test(normalized)) {
      return `/downloads/${encodeURI(normalized.replace(/^\/?downloads\//i, ''))}`;
    }
    const markerIndex = normalized.toLowerCase().lastIndexOf('/downloads/');
    if (markerIndex !== -1) {
      return `/downloads/${encodeURI(normalized.slice(markerIndex + '/downloads/'.length))}`;
    }
    return `/downloads/${encodeURI(normalized)}`;
  }

  function mediaTypeFromFile(file) {
    const normalized = String(file || '').replace(/\\/g, '/');
    const filename = normalized.split('/').pop() || normalized;
    const extension = filename.includes('.') ? filename.split('.').pop().toLowerCase() : '';
    if (VIDEO_EXTENSIONS.has(extension)) return 'video';
    if (IMAGE_EXTENSIONS.has(extension)) return 'image';
    return null;
  }

  function renderableFiles(job) {
    const files = Array.isArray(job?.output_files) ? job.output_files : [];
    return files.map((file) => {
      const kind = mediaTypeFromFile(file);
      if (!kind) return null;
      return { kind, url: mediaUrl(file), name: String(file || '') };
    }).filter(Boolean);
  }

  function primaryMedia(job) {
    const files = renderableFiles(job);
    if (!files.length) return null;
    const primary = files.find((file) => file.kind === 'video') || files[0];
    const poster = primary.kind === 'video' ? files.find((file) => file.kind === 'image')?.url || '' : '';
    return { ...primary, poster, files };
  }

  function referenceImageCount(job) {
    const ingredients = Array.isArray(job?.ingredient_image_paths) ? job.ingredient_image_paths.filter(Boolean).length : 0;
    return [job?.start_image_path, job?.end_image_path, job?.ref_image_path].filter(Boolean).length + ingredients;
  }

  function outputCount(job) {
    const files = renderableFiles(job);
    if (!files.length) return 1;
    const videos = files.filter((file) => file.kind === 'video').length;
    const images = files.filter((file) => file.kind === 'image').length;
    return videos || images || 1;
  }

  function activeCountPill(job) {
    const count = outputCount(job);
    if (count === 4) return 4;
    if (count === 2) return 2;
    return 1;
  }

  function typeCardLabel(type) {
    const normalized = String(type || '').trim();
    if (['text-to-video', 'frames-to-video', 'ingredients-to-video'].includes(normalized)) return 'VIDEO LABS';
    if (normalized === 'text-to-image') return 'IMAGE LABS';
    if (normalized === 'extend-video') return 'EXTEND';
    if (normalized === 'insert-object') return 'INSERT';
    if (normalized === 'remove-object') return 'REMOVE';
    if (normalized === 'camera-move') return 'CAMERA';
    return String(normalized || 'WORK').replace(/-/g, ' ').toUpperCase();
  }

  function modeLabel(type) {
    const normalized = String(type || '').trim();
    if (normalized === 'text-to-video') return 'Txt 2 Vid';
    if (normalized === 'frames-to-video') return 'Img 2 Vid';
    if (normalized === 'ingredients-to-video') return 'Refs 2 Vid';
    if (normalized === 'text-to-image') return 'Txt 2 Img';
    if (normalized === 'extend-video') return 'Extend';
    if (normalized === 'insert-object') return 'Insert';
    if (normalized === 'remove-object') return 'Remove';
    if (normalized === 'camera-move') return 'Camera';
    return 'Workflow';
  }

  function promptText(job) {
    return String(job?.prompt || job?.direction || '').trim();
  }

  function statusGlyph(status) {
    const safe = safeStatus(status);
    if (safe === 'completed') return '&check;';
    if (safe === 'failed') return '!';
    if (safe === 'cancelled') return '&times;';
    if (ACTIVE_STATUSES.has(safe)) return '&hellip;';
    return '&#9633;';
  }

  function latestJob(jobs) {
    return [...jobs].sort(compareByCreatedDesc)[0] || null;
  }

  function chainTitle(rootJob) {
    const formatted = formatTileDate(rootJob?.created_at || rootJob?.createdAt || '');
    if (formatted && formatted !== '-') return formatted;
    return state.chainId ? `Chain ${App.truncate(state.chainId, 18)}` : 'Project';
  }

  function buildLayoutModel(jobs) {
    const ranks = new Map();
    jobs.forEach((job) => {
      const rank = Math.max(1, Number(job?.job_level) || 1);
      if (!ranks.has(rank)) ranks.set(rank, []);
      ranks.get(rank).push(job);
    });
    const rankKeys = [...ranks.keys()].sort((a, b) => a - b);
    rankKeys.forEach((rank) => ranks.get(rank).sort(compareByCreatedAsc));

    const maxCount = rankKeys.reduce((max, rank) => Math.max(max, ranks.get(rank).length), 0);
    const anchorsById = new Map();
    rankKeys.forEach((rank, rowIndex) => {
      const row = ranks.get(rank);
      const rowOffset = ((maxCount - row.length) * X_SPACING) / 2;
      row.forEach((job, columnIndex) => {
        anchorsById.set(String(job.id), {
          centerX: STAGE_PADDING + (DEFAULT_NODE_WIDTH / 2) + rowOffset + (columnIndex * X_SPACING),
          top: STAGE_PADDING + (rowIndex * Y_SPACING),
          rowIndex,
          rank,
        });
      });
    });

    return {
      ranks,
      rankKeys,
      maxCount,
      anchorsById,
      estimatedWidth: Math.max(1, STAGE_PADDING * 2 + DEFAULT_NODE_WIDTH + Math.max(0, maxCount - 1) * X_SPACING),
      estimatedHeight: Math.max(1, STAGE_PADDING * 2 + DEFAULT_NODE_HEIGHT + Math.max(0, rankKeys.length - 1) * Y_SPACING),
    };
  }

  function draftFrameForJob(job) {
    const anchor = state.layoutModel?.anchorsById?.get(String(job?.id || ''));
    const centerX = anchor?.centerX || (STAGE_PADDING + DEFAULT_NODE_WIDTH / 2);
    const top = anchor?.top || STAGE_PADDING;
    return { left: centerX - (DEFAULT_NODE_WIDTH / 2), top, width: DEFAULT_NODE_WIDTH, height: DEFAULT_NODE_HEIGHT };
  }

  function newChainStepHref(parentJobId = '') {
    const params = new URLSearchParams();
    if (parentJobId) params.set('parent', String(parentJobId).trim());
    params.set('type', DEFAULT_CHAIN_STEP_TYPE);
    if (state.chainId) params.set('chain_id', state.chainId);
    return `#${CHAIN_STEP_ROUTE}?${params.toString()}`;
  }

  function renderPromptSection(job) {
    const text = promptText(job) || 'No prompt yet.';
    const expanded = state.expandedPrompts.has(String(job.id));
    const needsToggle = text.length > PROMPT_PREVIEW_CHARS;
    const visibleText = expanded || !needsToggle ? text : `${text.slice(0, PROMPT_PREVIEW_CHARS).trim()}...`;

    return `
      <div class="pv-section">
        <div class="pv-prompt">${App.escapeHtml(visibleText)}</div>
        ${needsToggle ? `
          <button type="button" class="pv-prompt-toggle" data-action="prompt-toggle" data-job-id="${escapeAttr(job.id)}">
            ${expanded ? 'Show Less' : 'Show More'}
          </button>
        ` : ''}
      </div>
    `;
  }

  function renderReferenceStub(job) {
    const count = referenceImageCount(job);
    return `
      <button type="button" class="pv-ref-images" data-action="node-refs" data-job-id="${escapeAttr(job.id)}" title="Reference images">
        <span>REFERENCE IMAGES</span>
        <span>${App.escapeHtml(String(count))}</span>
        <span class="material-icons" aria-hidden="true">expand_more</span>
      </button>
    `;
  }

  function renderCountPills(job) {
    const active = activeCountPill(job);
    const noun = primaryMedia(job)?.kind === 'image' && String(job?.type || '') === 'text-to-image' ? 'Image' : 'Video';
    return `
      <div class="pv-count-pills">
        ${COUNT_PILLS.map((count) => `
          <span class="pv-count-pill ${count === active ? 'pv-count-pill--active' : ''}">
            ${App.escapeHtml(String(count))} ${App.escapeHtml(count === 1 ? noun : `${noun}s`)}
          </span>
        `).join('')}
      </div>
    `;
  }

  function renderOutput(job) {
    const status = safeStatus(job?.status);
    const media = status === 'completed' ? primaryMedia(job) : null;
    if (!media) {
      return `
        <div class="pv-output pv-output--empty">
          <span class="material-icons" aria-hidden="true">${App.escapeHtml(App.jobTypeIcon(job?.type))}</span>
          <span>No output yet</span>
        </div>
      `;
    }

    const title = promptText(job) || typeCardLabel(job?.type);
    const mediaTile = App.mediaTile || window.MediaUtil;
    const preview = media.kind === 'video'
      ? mediaTile.videoTag({ src: media.url, poster: media.poster, alt: title })
      : mediaTile.imgTag({ src: media.url, alt: title });

    return `
      <button type="button" class="pv-output" data-action="preview-output" data-job-id="${escapeAttr(job.id)}" title="Preview output">
        ${preview}
      </button>
    `;
  }

  function renderNode(job) {
    const status = safeStatus(job?.status);
    const frame = state.layout.framesById.get(String(job.id)) || draftFrameForJob(job);
    const classes = ['pv-node', `pv-node--${status}`, state.selectedJobId === String(job.id) ? 'pv-node--selected' : '']
      .filter(Boolean).join(' ');

    return `
      <div class="${classes}" data-job-id="${escapeAttr(job.id)}" style="position:absolute; left:${frame.left}px; top:${frame.top}px;">
        <div class="pv-node-header">
          <div class="pv-node-type">
            <span class="material-icons" aria-hidden="true">${App.escapeHtml(App.jobTypeIcon(job?.type))}</span>
            <span>${App.escapeHtml(typeCardLabel(job?.type))}</span>
          </div>
          <div class="pv-node-actions">
            <button type="button" class="icon-btn" data-action="node-upload" data-job-id="${escapeAttr(job.id)}" aria-label="Upload stub"><span class="material-icons">upload</span></button>
            <button type="button" class="icon-btn" data-action="node-play" data-job-id="${escapeAttr(job.id)}" aria-label="Play stub"><span class="material-icons">play_arrow</span></button>
            <button type="button" class="icon-btn" data-action="node-delete" data-job-id="${escapeAttr(job.id)}" aria-label="Delete stub"><span class="material-icons">delete</span></button>
            <span class="pv-node-status" title="${App.escapeHtml(status.toUpperCase())}">${statusGlyph(status)}</span>
          </div>
        </div>

        <div data-action="open-node" data-job-id="${escapeAttr(job.id)}" style="cursor:pointer;">
          <div class="pv-node-name">&#272;&#7863;t t&#234;n node&hellip;</div>
          ${renderPromptSection(job)}
          <div class="pv-section">${renderReferenceStub(job)}</div>
          <div class="pv-section" style="display:flex; flex-wrap:wrap; gap:8px;">
            <span class="pv-ratio-chip">${App.escapeHtml(job?.aspect_ratio || '16:9')}</span>
            <span class="pv-mode-chip">${App.escapeHtml(modeLabel(job?.type))}</span>
          </div>
          ${renderCountPills(job)}
        </div>

        ${renderOutput(job)}

        <a class="pv-new-step-pill" href="${App.escapeHtml(newChainStepHref(job.id))}" data-job-id="${escapeAttr(job.id)}" style="position:absolute; right:12px; bottom:12px; z-index:2;">
          <span class="new-project-pill">
            <span class="material-icons">add</span>
            <span>Chain step</span>
          </span>
        </a>
      </div>
    `;
  }

  function edgeIsActive(edge) {
    const parent = state.jobsById.get(String(edge.parent));
    const child = state.jobsById.get(String(edge.child));
    return ACTIVE_STATUSES.has(safeStatus(parent?.status)) || ACTIVE_STATUSES.has(safeStatus(child?.status));
  }

  function buildEdgePath(frameA, frameB) {
    const sourceX = frameA.left + (frameA.width / 2);
    const sourceY = frameA.top + frameA.height;
    const targetX = frameB.left + (frameB.width / 2);
    const targetY = frameB.top;
    const curve = Math.max(56, Math.abs(targetY - sourceY) * 0.45);
    return `M ${sourceX} ${sourceY} C ${sourceX} ${sourceY + curve} ${targetX} ${targetY - curve} ${targetX} ${targetY}`;
  }

  function renderEdges() {
    return state.edges.map((edge) => {
      const parentFrame = state.layout.framesById.get(String(edge.parent));
      const childFrame = state.layout.framesById.get(String(edge.child));
      if (!parentFrame || !childFrame) return '';

      return `
        <path
          class="pv-edge-path ${edgeIsActive(edge) ? 'pv-edge-path--running' : ''}"
          data-edge-parent="${escapeAttr(edge.parent)}"
          data-edge-child="${escapeAttr(edge.child)}"
          d="${escapeAttr(buildEdgePath(parentFrame, childFrame))}"
          fill="none"
          stroke="rgba(161, 161, 170, 0.5)"
          stroke-width="2"
          stroke-dasharray="6 10"
          stroke-linecap="round"
          opacity="0.5"
        ></path>
      `;
    }).join('');
  }

  function renderToolbar() {
    return `
      <div class="pv-toolbar">
        <div style="display:flex; align-items:center; gap:12px; min-width:0;">
          <button type="button" class="icon-btn" data-action="back" aria-label="Back"><span class="material-icons">arrow_back</span></button>
          <div style="display:grid; gap:4px; min-width:0;">
            <div style="font-size:20px; font-weight:700; line-height:1.2; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${App.escapeHtml(state.title || 'Project')}</div>
            <div style="color: var(--text-muted); font-size: 12px; line-height: 1.4;">${state.chainId ? `Chain ${App.escapeHtml(App.truncate(state.chainId, 18))}` : 'Workflow canvas'}</div>
          </div>
          <div class="pv-topbar-menu" data-role="project-menu-shell">
            <button type="button" class="icon-btn" data-action="toggle-menu" aria-label="Project actions" aria-haspopup="menu" aria-expanded="${state.menuOpen ? 'true' : 'false'}">
              <span class="material-icons">more_vert</span>
            </button>
            <div class="pv-menu-popover" role="menu" ${state.menuOpen ? '' : 'hidden'}>
              ${state.chainId ? `<a class="pv-menu-item" href="#jobs/${encodeURIComponent(state.chainId)}" role="menuitem"><span class="material-icons">list</span><span>Open jobs</span></a>` : ''}
              <a class="pv-menu-item" href="#gallery" role="menuitem"><span class="material-icons">collections</span><span>Gallery</span></a>
            </div>
          </div>
        </div>

        <div class="pv-toolbar-actions">
          <button type="button" class="btn btn-sm btn-outline" data-action="run-workflow">Run Workflow</button>
          <button type="button" class="btn btn-sm btn-outline" data-action="batch-run">Batch Run</button>
          <button type="button" class="btn btn-sm btn-outline" data-action="export">Export</button>
          <button type="button" class="btn btn-sm btn-outline" data-action="ai-agent">AI Agent</button>
          <button type="button" class="btn btn-sm btn-outline" data-action="settings">Settings</button>
        </div>
      </div>
    `;
  }

  function renderZoomToolbar() {
    return `
      <div class="pv-zoom-toolbar" style="position:absolute; right:16px; bottom:16px; z-index:3; display:flex; gap:8px;">
        <button type="button" class="pv-zoom-btn btn btn-sm btn-outline" data-action="zoom-out" aria-label="Zoom out">-</button>
        <button type="button" class="pv-zoom-btn btn btn-sm btn-outline" data-action="zoom-in" aria-label="Zoom in">+</button>
        <button type="button" class="pv-zoom-btn btn btn-sm btn-outline" data-action="fit" aria-label="Fit to screen">Fit</button>
        <button type="button" class="pv-zoom-btn btn btn-sm btn-outline" data-action="reset-zoom" aria-label="Reset zoom">Reset</button>
      </div>
    `;
  }

  function renderCanvas() {
    return `
      <div data-role="pan-shell" style="position:relative; overflow:hidden; min-height:calc(100vh - 220px); border-radius:24px; cursor:${state.drag ? 'grabbing' : 'grab'};">
        <div
          class="pv-pan"
          data-role="pan-stage"
          style="position:relative; width:${Math.max(1, state.layout.width)}px; height:${Math.max(1, state.layout.height)}px; transform-origin:0 0; transform:translate(${state.viewport.x}px, ${state.viewport.y}px) scale(${state.viewport.z});"
        >
          <svg class="pv-edges" data-role="edges" viewBox="0 0 ${Math.max(1, state.layout.width)} ${Math.max(1, state.layout.height)}" width="${Math.max(1, state.layout.width)}" height="${Math.max(1, state.layout.height)}" style="position:absolute; inset:0; overflow:visible;">
            ${renderEdges()}
          </svg>
          ${state.jobs.map(renderNode).join('')}
        </div>
        ${renderZoomToolbar()}
      </div>
    `;
  }

  function renderFooter() {
    return `
      <div class="pv-fab-wrap" style="position:fixed; left:50%; bottom:24px; transform:translateX(-50%); z-index:5; display:flex; flex-direction:column; align-items:center; gap:12px;">
        <div class="pv-disclaimer">Flow can make mistakes, so double check it</div>
        ${state.latestJobId ? `
          <a class="new-project-pill pv-new-step-pill" href="${App.escapeHtml(newChainStepHref(state.latestJobId))}" title="New chain step" aria-label="New chain step">
            <span class="material-icons">add</span>
            <span>New chain step</span>
          </a>
        ` : ''}
      </div>
    `;
  }

  function renderBanner(message) {
    return `
      <div class="card" style="padding:14px 16px; color:#fde68a; background:rgba(234, 179, 8, 0.10); border-color:rgba(234, 179, 8, 0.28);">
        <div style="display:flex; align-items:flex-start; gap:10px;">
          <span class="material-icons" style="font-size:18px;">warning</span>
          <div style="font-size:13px; line-height:1.5;">${App.escapeHtml(message)}</div>
        </div>
      </div>
    `;
  }

  function renderEmptyState(title, body) {
    return `
      <div class="card">
        <div class="empty-state">
          <span class="material-icons">account_tree</span>
          <h3>${App.escapeHtml(title)}</h3>
          <p>${App.escapeHtml(body)}</p>
          <div style="margin-top:12px;">
            <a href="#gallery" class="btn btn-primary"><span class="material-icons" style="font-size:16px">collections</span> Open gallery</a>
          </div>
        </div>
      </div>
    `;
  }

  function renderPage() {
    const hasNodes = state.jobs.length > 0;
    return `
      <div class="pv-canvas" style="display:grid; gap:16px; min-height:calc(100vh - var(--appbar-height) - 16px); padding-bottom:152px;">
        ${renderToolbar()}
        ${state.loadError && hasNodes ? renderBanner(state.loadError) : ''}
        ${hasNodes ? renderCanvas() : renderEmptyState(
          state.chainId ? 'No chain nodes yet' : 'Project not found',
          state.loadError || (state.chainId
            ? 'This chain does not have any jobs to render yet. Add a step or open the gallery.'
            : 'Open a chain from the jobs list or gallery to inspect it on the DAG canvas.')
        )}
        ${renderFooter()}
      </div>
    `;
  }

  function hideShellTopBar() {
    const topBar = document.getElementById('top-bar');
    if (!topBar) return;
    state.topBarEl = topBar;
    state.topBarDisplay = topBar.style.display;
    topBar.style.display = 'none';
  }

  function restoreShellTopBar() {
    if (!state.topBarEl) return;
    state.topBarEl.style.display = state.topBarDisplay || '';
    state.topBarEl = null;
  }

  function rebuildLayoutState() {
    state.layoutModel = buildLayoutModel(state.jobs);
    state.layout = {
      framesById: new Map(state.jobs.map((job) => [String(job.id), draftFrameForJob(job)])),
      width: state.layoutModel?.estimatedWidth || Math.max(1, STAGE_PADDING * 2 + DEFAULT_NODE_WIDTH),
      height: state.layoutModel?.estimatedHeight || Math.max(1, STAGE_PADDING * 2 + DEFAULT_NODE_HEIGHT),
    };
  }

  function applyChainData(chain) {
    const jobs = uniqueJobsById(chain?.jobs || []).sort(compareJobs);
    const jobsById = new Map(jobs.map((job) => [String(job.id), job]));
    const rootJob = pickRootJob(jobs, String(chain?.rootId || '').trim());
    const edges = normalizeEdges(chain?.edges, jobsById);
    const latest = latestJob(jobs);

    state.jobs = jobs;
    state.jobsById = jobsById;
    state.edges = edges.length ? edges : buildEdgesFromJobs(jobs);
    state.rootId = String(rootJob?.id || chain?.rootId || '').trim();
    state.rootJob = rootJob || null;
    state.latestJobId = String(latest?.id || '').trim();
    state.title = chainTitle(rootJob);
    if (!jobsById.has(state.selectedJobId)) {
      state.selectedJobId = String(rootJob?.id || latest?.id || '');
    }
    rebuildLayoutState();
  }

  async function loadFromBulkChain(chainId) {
    const detail = await API.chains.get(chainId);
    const jobs = normalizeJobList(detail);
    if (!jobs.length) return null;
    const jobsById = new Map(jobs.map((job) => [String(job.id), job]));
    return { chainId, rootId: String(detail?.root_id || '').trim(), jobs, edges: normalizeEdges(detail?.edges, jobsById) };
  }

  async function loadFromJobIdFallback(routeKey) {
    // The route key may be a bare job_id for legacy jobs without chain_id.
    // Render that job as a single-node DAG so old tiles still land somewhere useful.
    try {
      const job = await API.jobs.get(routeKey);
      if (!job?.id) return null;
      return { chainId: routeKey, rootId: String(job.id), jobs: [job], edges: [] };
    } catch (_) {
      return null;
    }
  }

  async function loadFromCompatibilityFallback(chainId) {
    const listed = normalizeJobList(await API.jobs.list({ limit: 500 }))
      .filter((job) => String(job?.chain_id || '').trim() === chainId)
      .sort(compareByCreatedAsc);
    if (!listed.length) {
      const single = await loadFromJobIdFallback(chainId);
      if (single) return single;
      return { chainId, rootId: '', jobs: [], edges: [] };
    }

    const firstJob = listed[0];
    let related = null;
    try {
      related = await API.fetch(`/api/jobs/${encodeURIComponent(firstJob.id)}/related`);
    } catch (_) {
      related = null;
    }

    const relatedJobs = uniqueJobsById([
      ...listed,
      related?.self,
      related?.parent,
      ...(Array.isArray(related?.ancestors) ? related.ancestors : []),
      ...(Array.isArray(related?.children) ? related.children : []),
      ...(Array.isArray(related?.siblings) ? related.siblings : []),
    ]).filter((job) => String(job?.chain_id || '').trim() === chainId || !job?.chain_id);

    const rootId = String(related?.chain_root_id || '').trim() || String(firstJob.id || '').trim();
    const walked = walkChainJobs(relatedJobs, rootId);
    return { chainId, rootId, jobs: walked.length ? walked : listed, edges: buildEdgesFromJobs(walked.length ? walked : listed) };
  }

  async function loadChainData() {
    state.chainId = parseRouteChainId().trim();
    state.loadError = '';
    const requestId = ++state.requestId;

    if (!state.chainId) {
      state.jobs = [];
      state.jobsById = new Map();
      state.edges = [];
      state.rootId = '';
      state.rootJob = null;
      state.latestJobId = '';
      state.title = 'Project';
      rebuildLayoutState();
      return;
    }

    try {
      let chain = null;
      try {
        chain = await loadFromBulkChain(state.chainId);
      } catch (error) {
        if (error?.status !== 404) state.loadError = error?.message || '';
      }
      if (!chain) chain = await loadFromCompatibilityFallback(state.chainId);
      if (requestId !== state.requestId) return;
      applyChainData(chain);
      if (!state.jobs.length && !state.loadError) state.loadError = '';
    } catch (error) {
      if (requestId !== state.requestId) return;
      state.jobs = [];
      state.jobsById = new Map();
      state.edges = [];
      state.rootId = '';
      state.rootJob = null;
      state.latestJobId = '';
      state.title = state.chainId ? `Chain ${App.truncate(state.chainId, 18)}` : 'Project';
      state.loadError = error?.message || 'Failed to load project view.';
      rebuildLayoutState();
    }
  }

  function rootElement() {
    return state.rootEl || document.getElementById(PAGE_ROOT_ID);
  }

  function panShellEl() {
    return rootElement()?.querySelector('[data-role="pan-shell"]') || null;
  }

  function panStageEl() {
    return rootElement()?.querySelector('[data-role="pan-stage"]') || null;
  }

  function edgesEl() {
    return rootElement()?.querySelector('[data-role="edges"]') || null;
  }

  function measureAndPositionCanvas({ fit = false } = {}) {
    const root = rootElement();
    const panStage = panStageEl();
    if (!root || !panStage || !state.layoutModel) return;

    const nodeElements = Array.from(panStage.querySelectorAll('.pv-node[data-job-id]'));
    if (!nodeElements.length) {
      panStage.style.width = `${Math.max(1, state.layout.width)}px`;
      panStage.style.height = `${Math.max(1, state.layout.height)}px`;
      applyViewport();
      return;
    }

    const frames = new Map();
    let minLeft = Infinity;
    let maxRight = 0;
    let maxBottom = 0;

    nodeElements.forEach((nodeEl) => {
      const jobId = String(nodeEl.dataset.jobId || '').trim();
      const anchor = state.layoutModel.anchorsById.get(jobId);
      const rect = nodeEl.getBoundingClientRect();
      const width = rect.width || DEFAULT_NODE_WIDTH;
      const height = rect.height || DEFAULT_NODE_HEIGHT;
      const left = (anchor?.centerX || (STAGE_PADDING + width / 2)) - (width / 2);
      const top = anchor?.top || STAGE_PADDING;
      frames.set(jobId, { left, top, width, height });
      minLeft = Math.min(minLeft, left);
      maxRight = Math.max(maxRight, left + width);
      maxBottom = Math.max(maxBottom, top + height);
    });

    const shiftX = Number.isFinite(minLeft) && minLeft < STAGE_PADDING ? STAGE_PADDING - minLeft : 0;
    frames.forEach((frame, jobId) => {
      const shifted = { ...frame, left: frame.left + shiftX };
      frames.set(jobId, shifted);
      const nodeEl = nodeElements.find((entry) => String(entry.dataset.jobId || '').trim() === jobId);
      if (nodeEl) {
        nodeEl.style.left = `${shifted.left}px`;
        nodeEl.style.top = `${shifted.top}px`;
      }
    });

    const width = Math.max(1, maxRight + shiftX + STAGE_PADDING);
    const height = Math.max(1, maxBottom + STAGE_PADDING);
    state.layout = { framesById: frames, width, height };
    panStage.style.width = `${width}px`;
    panStage.style.height = `${height}px`;

    const edgesNode = edgesEl();
    if (edgesNode) {
      edgesNode.setAttribute('viewBox', `0 0 ${width} ${height}`);
      edgesNode.setAttribute('width', String(width));
      edgesNode.setAttribute('height', String(height));
      edgesNode.innerHTML = renderEdges();
    }

    if (fit || !state.userViewportChanged) {
      fitToScreen({ markUser: false });
    } else {
      applyViewport();
    }
  }

  function clampZoom(value) {
    return Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, value));
  }

  function applyViewport() {
    const panStage = panStageEl();
    if (!panStage) return;
    panStage.style.transform = `translate(${state.viewport.x}px, ${state.viewport.y}px) scale(${state.viewport.z})`;
  }

  function shellCenterPoint() {
    const rect = panShellEl()?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    return { x: rect.left + (rect.width / 2), y: rect.top + (rect.height / 2) };
  }

  function setViewport(next, { markUser = true } = {}) {
    state.viewport = {
      x: Number.isFinite(next.x) ? next.x : state.viewport.x,
      y: Number.isFinite(next.y) ? next.y : state.viewport.y,
      z: clampZoom(Number.isFinite(next.z) ? next.z : state.viewport.z),
    };
    if (markUser) state.userViewportChanged = true;
    applyViewport();
  }

  function zoomAroundPoint(nextZoom, clientX, clientY, { markUser = true } = {}) {
    const shell = panShellEl();
    if (!shell) return;
    const rect = shell.getBoundingClientRect();
    const currentZoom = state.viewport.z;
    const clamped = clampZoom(nextZoom);
    if (clamped === currentZoom) return;
    const localX = clientX - rect.left;
    const localY = clientY - rect.top;
    const scale = clamped / currentZoom;

    setViewport({
      z: clamped,
      x: localX - ((localX - state.viewport.x) * scale),
      y: localY - ((localY - state.viewport.y) * scale),
    }, { markUser });
  }

  function zoomBy(delta) {
    const center = shellCenterPoint();
    zoomAroundPoint(state.viewport.z + delta, center.x, center.y, { markUser: true });
  }

  function fitToScreen({ markUser = false } = {}) {
    const shell = panShellEl();
    if (!shell) return;
    const rect = shell.getBoundingClientRect();
    const usableWidth = Math.max(1, rect.width - 32);
    const usableHeight = Math.max(1, rect.height - 32);
    const zoom = clampZoom(Math.min(usableWidth / state.layout.width, usableHeight / state.layout.height));
    const next = {
      z: zoom,
      x: (rect.width - (state.layout.width * zoom)) / 2,
      y: (rect.height - (state.layout.height * zoom)) / 2,
    };

    state.fitViewport = { ...next };
    state.userViewportChanged = markUser;
    state.viewport = { ...next };
    applyViewport();
  }

  function resetZoom() {
    const shell = panShellEl();
    if (!shell) return;
    const rect = shell.getBoundingClientRect();
    setViewport({
      z: 1,
      x: Math.max(24, (rect.width - state.layout.width) / 2),
      y: Math.max(24, (rect.height - state.layout.height) / 2),
    }, { markUser: true });
  }

  function renderIntoRoot({ fit = false } = {}) {
    const root = rootElement();
    if (!root) return;
    root.innerHTML = renderPage();
    requestAnimationFrame(() => measureAndPositionCanvas({ fit }));
  }

  function toggleMenu(force) {
    state.menuOpen = typeof force === 'boolean' ? force : !state.menuOpen;
    renderIntoRoot({ fit: false });
  }

  function selectNode(jobId) {
    const nextId = String(jobId || '').trim();
    if (!nextId || state.selectedJobId === nextId) return;
    state.selectedJobId = nextId;
    Array.from(rootElement()?.querySelectorAll('.pv-node[data-job-id]') || []).forEach((nodeEl) => {
      nodeEl.classList.toggle('pv-node--selected', String(nodeEl.dataset.jobId || '').trim() === nextId);
    });
  }

  function openOutputLightbox(jobId) {
    const job = state.jobsById.get(String(jobId || '').trim());
    const media = primaryMedia(job);
    if (!job || !media) return;

    const title = promptText(job) || typeCardLabel(job.type);
    const mediaHtml = media.kind === 'video'
      ? `<video src="${escapeAttr(media.url)}" ${media.poster ? `poster="${escapeAttr(media.poster)}"` : ''} controls autoplay playsinline style="width:100%; max-height:75vh; background:#000; border-radius:16px;"></video>`
      : `<img src="${escapeAttr(media.url)}" alt="${escapeAttr(title)}" style="width:100%; max-height:75vh; object-fit:contain; background:#000; border-radius:16px;">`;

    App.openModal(
      title,
      `<div style="display:grid; gap:12px;"><div style="display:grid; place-items:center;">${mediaHtml}</div><div style="color: var(--text-secondary); font-size: 13px; line-height: 1.5;">${App.escapeHtml(typeCardLabel(job.type))} &middot; ${App.escapeHtml(formatTileDate(job.created_at || job.createdAt))}</div></div>`
    );
  }

  function navigateBack() {
    if (window.history.length > 1) {
      window.history.back();
      return;
    }
    location.hash = '#gallery';
  }

  function toastComingSoon() {
    App.toast('Coming soon', 'info');
  }

  function nodeElementFor(jobId) {
    return Array.from(rootElement()?.querySelectorAll('.pv-node[data-job-id]') || [])
      .find((nodeEl) => String(nodeEl.dataset.jobId || '').trim() === String(jobId || '').trim()) || null;
  }

  function refreshNode(jobId, { measure = true } = {}) {
    const job = state.jobsById.get(String(jobId || '').trim());
    const nodeEl = nodeElementFor(jobId);
    if (!job || !nodeEl) return;

    const frame = state.layout.framesById.get(String(job.id)) || draftFrameForJob(job);
    const replacement = document.createElement('div');
    replacement.innerHTML = renderNode(job);
    const nextNode = replacement.firstElementChild;
    if (!nextNode) return;

    nextNode.style.left = `${frame.left}px`;
    nextNode.style.top = `${frame.top}px`;
    nodeEl.replaceWith(nextNode);

    Array.from(rootElement()?.querySelectorAll('[data-edge-parent], [data-edge-child]') || [])
      .filter((pathEl) => String(pathEl.getAttribute('data-edge-parent') || '') === String(jobId || '').trim()
        || String(pathEl.getAttribute('data-edge-child') || '') === String(jobId || '').trim())
      .forEach((pathEl) => {
        const parent = pathEl.getAttribute('data-edge-parent') || '';
        const child = pathEl.getAttribute('data-edge-child') || '';
        pathEl.classList.toggle('pv-edge-path--running', edgeIsActive({ parent, child }));
      });

    if (measure) requestAnimationFrame(() => measureAndPositionCanvas({ fit: false }));
  }

  async function refreshFullView({ silent = false, fit = false } = {}) {
    await loadChainData();
    renderIntoRoot({ fit });
    if (state.loadError && !silent && state.jobs.length === 0) {
      App.toast(`Failed to refresh project view: ${state.loadError}`, 'error');
    }
  }

  function scheduleRefresh() {
    if (App.currentPage !== 'project-view') return;
    if (state.refreshTimer) clearTimeout(state.refreshTimer);
    state.refreshTimer = setTimeout(() => {
      state.refreshTimer = null;
      void refreshFullView({ silent: true, fit: false });
    }, 250);
  }

  function belongsToCurrentChain(job) {
    const jobId = String(job?.id || '').trim();
    if (state.jobsById.has(jobId)) return true;
    if (state.chainId && String(job?.chain_id || '').trim() === state.chainId) return true;
    if (String(job?.parent_job_id || '').trim() && state.jobsById.has(String(job.parent_job_id).trim())) return true;
    return false;
  }

  function handleJobUpdate(job) {
    if (!belongsToCurrentChain(job)) return;
    const jobId = String(job?.id || '').trim();
    if (!state.jobsById.has(jobId)) {
      scheduleRefresh();
      return;
    }

    const merged = { ...state.jobsById.get(jobId), ...job };
    state.jobsById.set(jobId, merged);
    state.jobs = state.jobs.map((entry) => (String(entry.id) === jobId ? merged : entry));
    state.latestJobId = String(latestJob(state.jobs)?.id || '').trim();

    if (String(state.rootId || '') === jobId) {
      state.rootJob = merged;
      state.title = chainTitle(merged);
      renderIntoRoot({ fit: false });
      return;
    }

    refreshNode(jobId, { measure: true });
  }

  function bindWindowListeners() {
    if (state.windowListenersBound) return;
    state.windowListenersBound = true;
    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    document.addEventListener('keydown', handleKeyDown);
    window.addEventListener('resize', handleResize);
  }

  function unbindWindowListeners() {
    if (!state.windowListenersBound) return;
    state.windowListenersBound = false;
    document.removeEventListener('mousemove', handleMouseMove);
    document.removeEventListener('mouseup', handleMouseUp);
    document.removeEventListener('keydown', handleKeyDown);
    window.removeEventListener('resize', handleResize);
  }

  function handleResize() {
    if (App.currentPage !== 'project-view' || !state.jobs.length) return;
    if (!state.userViewportChanged) {
      requestAnimationFrame(() => fitToScreen({ markUser: false }));
    } else {
      applyViewport();
    }
  }

  function handleKeyDown(event) {
    if (event.key === 'Escape' && state.menuOpen) {
      event.preventDefault();
      toggleMenu(false);
    }
  }

  function handleMouseDown(event) {
    if (event.button !== 0) return;
    const shell = event.target.closest('[data-role="pan-shell"]');
    if (!shell || event.target.closest('.pv-node, .pv-toolbar, .pv-zoom-toolbar')) return;

    state.drag = {
      startX: event.clientX,
      startY: event.clientY,
      originX: state.viewport.x,
      originY: state.viewport.y,
    };
    event.preventDefault();
  }

  function handleMouseMove(event) {
    if (!state.drag) return;
    setViewport({
      x: state.drag.originX + (event.clientX - state.drag.startX),
      y: state.drag.originY + (event.clientY - state.drag.startY),
      z: state.viewport.z,
    }, { markUser: true });
  }

  function handleMouseUp() {
    state.drag = null;
  }

  function handleWheel(event) {
    const shell = event.target.closest('[data-role="pan-shell"]');
    if (!shell || !event.ctrlKey) return;
    event.preventDefault();
    zoomAroundPoint(state.viewport.z + (event.deltaY < 0 ? ZOOM_STEP : -ZOOM_STEP), event.clientX, event.clientY, { markUser: true });
  }

  function handleRootClick(event) {
    if (state.menuOpen && !event.target.closest('[data-role="project-menu-shell"]')) {
      state.menuOpen = false;
      renderIntoRoot({ fit: false });
      return;
    }

    const actionEl = event.target.closest('[data-action]');
    if (!actionEl) {
      const selectedNode = event.target.closest('.pv-node[data-job-id]');
      if (selectedNode?.dataset.jobId) selectNode(selectedNode.dataset.jobId);
      return;
    }

    const action = String(actionEl.dataset.action || '').trim();
    const jobId = String(actionEl.dataset.jobId || '').trim();
    if (jobId) selectNode(jobId);

    if (action === 'back') {
      event.preventDefault();
      navigateBack();
      return;
    }
    if (action === 'toggle-menu') {
      event.preventDefault();
      toggleMenu();
      return;
    }
    if (action === 'open-node') {
      event.preventDefault();
      location.hash = `#job-detail/${encodeURIComponent(jobId)}`;
      return;
    }
    if (action === 'prompt-toggle') {
      event.preventDefault();
      if (state.expandedPrompts.has(jobId)) state.expandedPrompts.delete(jobId);
      else state.expandedPrompts.add(jobId);
      refreshNode(jobId, { measure: true });
      return;
    }
    if (action === 'preview-output') {
      event.preventDefault();
      openOutputLightbox(jobId);
      return;
    }
    if (action === 'zoom-in') {
      event.preventDefault();
      zoomBy(ZOOM_STEP);
      return;
    }
    if (action === 'zoom-out') {
      event.preventDefault();
      zoomBy(-ZOOM_STEP);
      return;
    }
    if (action === 'fit') {
      event.preventDefault();
      fitToScreen({ markUser: false });
      return;
    }
    if (action === 'reset-zoom') {
      event.preventDefault();
      resetZoom();
      return;
    }
    if (STUB_ACTIONS.has(action)) {
      event.preventDefault();
      toastComingSoon();
    }
  }

  const ProjectViewPage = {
    name: 'project-view',
    title: 'Project View',
    icon: 'account_tree',

    async render() {
      await loadChainData();
      return `<div id="${PAGE_ROOT_ID}">${renderPage()}</div>`;
    },

    mount() {
      state.rootEl = document.getElementById(PAGE_ROOT_ID);
      hideShellTopBar();
      bindWindowListeners();

      state.rootEl?.addEventListener('click', handleRootClick);
      state.rootEl?.addEventListener('mousedown', handleMouseDown);
      state.rootEl?.addEventListener('wheel', handleWheel, { passive: false });

      requestAnimationFrame(() => measureAndPositionCanvas({ fit: true }));

      state.wsUnsubs.push(WS.on('job_update', handleJobUpdate));
      state.wsUnsubs.push(WS.on('connected', () => {
        if (App.currentPage === 'project-view') scheduleRefresh();
      }));
    },

    destroy() {
      if (state.refreshTimer) {
        clearTimeout(state.refreshTimer);
        state.refreshTimer = null;
      }

      state.rootEl?.removeEventListener('click', handleRootClick);
      state.rootEl?.removeEventListener('mousedown', handleMouseDown);
      state.rootEl?.removeEventListener('wheel', handleWheel);

      state.wsUnsubs.forEach((unsubscribe) => {
        try {
          unsubscribe?.();
        } catch (_) {
          // Ignore cleanup failures.
        }
      });
      state.wsUnsubs = [];

      unbindWindowListeners();
      restoreShellTopBar();

      state.rootEl = null;
      state.menuOpen = false;
      state.drag = null;
      state.userViewportChanged = false;
    },
  };

  App.register(ProjectViewPage);
})();
