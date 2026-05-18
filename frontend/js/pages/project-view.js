/**
 * Project View Page
 * DAG canvas for one chain with pan/zoom, node cards, and curved edges.
 */
(() => {
  const PAGE_ROOT_ID = 'project-view-page';
  const CHAIN_STEP_ROUTE = 'chain-builder';
  const DEFAULT_CHAIN_STEP_TYPE = 'extend-video';
  const BACKEND_GAP_WARNED = new Set();
  const MIN_ZOOM = 0.4;
  const MAX_ZOOM = 1.6;
  const FIT_MAX_ZOOM = 1.4;
  const SINGLE_NODE_FIT_ZOOM = 0.95;
  const ZOOM_STEP = 0.14;
  const FIT_PADDING = 80;
  const EXTEND_HINT_DELAY_MS = 600;
  /* === U6 - canvas polish === */
  const X_SPACING = 300;
  const Y_SPACING = 420;
  const STAGE_PADDING = 72;
  /* U6 - portrait nodes 9:16 */
  const DEFAULT_NODE_WIDTH = 240;
  const DEFAULT_NODE_HEIGHT = Math.round((DEFAULT_NODE_WIDTH * 16) / 9);
  const EDGE_MARKER_SPACING = 30;
  const EDGE_MARKER_SIZE = 4;
  const EDGE_PORT_RADIUS = 4;
  const EDGE_SAMPLE_SEGMENTS = 72;
  /* === end U6 === */
  /* U3 - idea/chat right rail */
  const IDEA_RAIL_WIDTH = 380;
  const IDEA_RAIL_COLLAPSED_WIDTH = 64;
  const MAX_IDEA_REF_IMAGES = 5;
  const PROMPT_PREVIEW_CHARS = 140;
  const ALLOWED_STATUS = new Set(['pending', 'claimed', 'running', 'completed', 'failed', 'cancelled']);
  const ACTIVE_STATUSES = new Set(['claimed', 'running']);
  const VIDEO_EXTENSIONS = new Set(['mp4', 'webm', 'mov', 'm4v']);
  const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp']);
  const IDEA_NODE_TYPES = new Set([
    'text-to-video',
    'frames-to-video',
    'ingredients-to-video',
    'text-to-image',
    'extend-video',
    'insert-object',
    'remove-object',
    'camera-move',
  ]);
  const COUNT_PILLS = [1, 2, 4];
  const IDEA_PANEL_TITLE = 'IDEA';
  const IDEA_EMPTY_COPY = 'No idea yet. Describe your video and click Generate.';
  const IDEA_INPUT_PLACEHOLDER = 'Describe your video idea\u2026';
  const IDEA_INPUT_SOURCE_LABEL = 'Reference image(s) (optional)';
  const IDEA_CREATE_NODES_LABEL = 'Create nodes on canvas';
  const IDEA_CAPTION_TEXT = 'Reference images come from the first ImageInput on the flow.';
  const IDEA_BACKEND_MISSING_TOAST = 'AI agent backend not configured';

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
    extendHintTimer: null,
    extendHintVisible: false,
    menuOpen: false,
    drag: null,
    wsUnsubs: [],
    rootEl: null,
    topBarEl: null,
    topBarDisplay: '',
    windowListenersBound: false,
    ideaCollapsed: false,
    ideaDraft: '',
    ideaMessages: [],
    ideaPending: false,
    ideaUploadPending: false,
    ideaCreatePending: false,
    ideaLatestResponse: null,
    ideaRefImages: [],
    ideaAutoScrollPending: false,
    ideaFocusInputPending: false,
    ideaPreserveCollapseOnNextChain: false,
    debugBadges: [],
  };

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
      page: 'project-view',
      field,
      jobId: jobId || '',
      fallbackUsed,
    });
  }

  function renderDebugBadges(items) {
    if (!debugBadgesEnabled() || !Array.isArray(items) || !items.length) return '';
    return `
      <div style="display:flex; flex-wrap:wrap; gap:6px; margin-top:6px;">
        ${items.map((item) => `
          <span
            class="pv-node-status-pill"
            title="${escapeAttr(`${item.field} -> ${item.fallbackUsed}`)}"
            style="opacity:0.65;"
          >
            ${App.escapeHtml(`gap:${item.field}`)}
          </span>
        `).join('')}
      </div>
    `;
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
      const normalizedEdge = { parent, child };
      if (edge && Object.prototype.hasOwnProperty.call(edge, 'active')) normalizedEdge.active = edge.active;
      edges.push(normalizedEdge);
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

  function uploadUrl(path) {
    const normalized = String(path || '').replace(/\\/g, '/').trim();
    if (!normalized) return '';
    if (/^https?:\/\//i.test(normalized)) return normalized;
    return normalized.startsWith('/') ? normalized : `/${normalized}`;
  }

  function mediaTypeFromFile(file) {
    const normalized = String(file || '').replace(/\\/g, '/');
    const filename = normalized.split('/').pop() || normalized;
    const extension = filename.includes('.') ? filename.split('.').pop().toLowerCase() : '';
    if (VIDEO_EXTENSIONS.has(extension)) return 'video';
    if (IMAGE_EXTENSIONS.has(extension)) return 'image';
    return null;
  }

  function resetIdeaState({ preserveCollapse = false } = {}) {
    state.ideaCollapsed = preserveCollapse ? state.ideaCollapsed : false;
    state.ideaDraft = '';
    state.ideaMessages = [];
    state.ideaPending = false;
    state.ideaUploadPending = false;
    state.ideaCreatePending = false;
    state.ideaLatestResponse = null;
    state.ideaRefImages = [];
    state.ideaAutoScrollPending = false;
    state.ideaFocusInputPending = false;
  }

  function createIdeaMessage(role, content) {
    return {
      id: `idea-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      role,
      content: String(content || ''),
    };
  }

  function latestIdeaNodes() {
    return Array.isArray(state.ideaLatestResponse?.nodes)
      ? state.ideaLatestResponse.nodes.filter((node) => node && typeof node === 'object')
      : [];
  }

  function currentProjectProfile() {
    return String(
      state.rootJob?.profile
      || state.jobs.find((job) => String(job?.profile || '').trim())?.profile
      || ''
    ).trim();
  }

  function renderIdeaInline(text) {
    let html = App.escapeHtml(String(text || ''));
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/__([^_]+)__/g, '<strong>$1</strong>');
    html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    html = html.replace(/_([^_]+)_/g, '<em>$1</em>');
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    return html;
  }

  function renderIdeaMarkdown(text) {
    const lines = String(text || '').split(/\r?\n/);
    let html = '';
    let listType = '';

    function closeList() {
      if (!listType) return;
      html += `</${listType}>`;
      listType = '';
    }

    lines.forEach((rawLine) => {
      const line = rawLine.trim();
      if (!line) {
        closeList();
        return;
      }

      let match = line.match(/^\d+[.)]\s+(.+)$/);
      if (match) {
        if (listType !== 'ol') {
          closeList();
          html += '<ol>';
          listType = 'ol';
        }
        html += `<li>${renderIdeaInline(match[1])}</li>`;
        return;
      }

      match = line.match(/^[-*]\s+(.+)$/);
      if (match) {
        if (listType !== 'ul') {
          closeList();
          html += '<ul>';
          listType = 'ul';
        }
        html += `<li>${renderIdeaInline(match[1])}</li>`;
        return;
      }

      closeList();
      html += `<p>${renderIdeaInline(line)}</p>`;
    });

    closeList();
    return html || '<p>No script returned.</p>';
  }

  function setIdeaRailCollapsed(collapsed, { focusInput = false } = {}) {
    const nextCollapsed = Boolean(collapsed);
    if (state.ideaCollapsed === nextCollapsed) {
      if (focusInput && !nextCollapsed) {
        const input = rootElement()?.querySelector('.pv-idea-input');
        if (input) input.focus();
      }
      return;
    }

    state.ideaCollapsed = nextCollapsed;
    state.ideaFocusInputPending = !nextCollapsed && focusInput;
    renderIntoRoot({ fit: false });
  }

  function openIdeaRail({ focusInput = false } = {}) {
    setIdeaRailCollapsed(false, { focusInput });
  }

  function syncIdeaSendButton() {
    const sendButton = rootElement()?.querySelector('.pv-idea-send-btn');
    if (!sendButton) return;
    sendButton.disabled = !String(state.ideaDraft || '').trim() || state.ideaPending || state.ideaUploadPending;
  }

  async function handleIdeaUpload(inputEl) {
    const files = Array.from(inputEl?.files || []);
    if (inputEl) inputEl.value = '';
    if (!files.length) return;

    const remaining = MAX_IDEA_REF_IMAGES - state.ideaRefImages.length;
    if (remaining <= 0) {
      App.toast(`You can attach up to ${MAX_IDEA_REF_IMAGES} images.`, 'warning');
      return;
    }

    const allowedFiles = files.slice(0, remaining);
    if (files.length > remaining) {
      App.toast(`Only ${remaining} more image${remaining === 1 ? '' : 's'} can be attached.`, 'warning');
    }

    state.ideaUploadPending = true;
    renderIntoRoot({ fit: false });

    try {
      for (const file of allowedFiles) {
        const uploaded = await API.uploads.create(file);
        const path = String(uploaded?.path || '').trim();
        if (!path) throw new Error('Upload response missing path');
        state.ideaRefImages.push({
          path,
          url: uploadUrl(path),
          name: file.name || path.split('/').pop() || 'Reference',
        });
      }
    } catch (error) {
      App.toast(`Image upload failed: ${error?.message || 'Unknown error'}`, 'error');
    } finally {
      state.ideaUploadPending = false;
      renderIntoRoot({ fit: false });
    }
  }

  async function submitIdeaPrompt() {
    if (state.ideaPending || state.ideaUploadPending) return;
    const prompt = String(state.ideaDraft || '').trim();
    if (!prompt) {
      App.toast('Enter what you want first.', 'warning');
      openIdeaRail({ focusInput: true });
      return;
    }

    state.ideaMessages = [...state.ideaMessages, createIdeaMessage('user', prompt)];
    state.ideaDraft = '';
    state.ideaPending = true;
    state.ideaAutoScrollPending = true;
    renderIntoRoot({ fit: false });

    try {
      const payload = {
        prompt,
        ref_image_urls: state.ideaRefImages.map((item) => item.url).filter(Boolean),
      };
      if (state.chainId) payload.chain_id = state.chainId;

      const result = await API.fetch('/api/idea/generate', {
        method: 'POST',
        body: JSON.stringify(payload),
      });

      const script = String(result?.script || '').trim();
      const nodes = Array.isArray(result?.nodes) ? result.nodes : [];
      state.ideaLatestResponse = { script, nodes };
      state.ideaMessages = [
        ...state.ideaMessages,
        createIdeaMessage('assistant', script || 'AI returned node suggestions without a script.'),
      ];
      state.ideaAutoScrollPending = true;
    } catch (error) {
      if (error?.status === 404) {
        App.toast(IDEA_BACKEND_MISSING_TOAST, 'warning');
      } else {
        App.toast(`Idea generation failed: ${error?.message || 'Unknown error'}`, 'error');
      }
    } finally {
      state.ideaPending = false;
      renderIntoRoot({ fit: false });
    }
  }

  function sanitizeBBox(value) {
    if (!value || typeof value !== 'object') return null;
    const keys = ['x', 'y', 'w', 'h'];
    if (!keys.every((key) => typeof value[key] === 'number' && Number.isFinite(value[key]))) return null;
    return { x: value.x, y: value.y, w: value.w, h: value.h };
  }

  function normalizeIdeaNodePayload(node) {
    const type = String(node?.type || '').trim();
    if (!IDEA_NODE_TYPES.has(type)) {
      throw new Error(`Unsupported suggested node type: ${type || 'unknown'}`);
    }

    const payload = { type };
    const prompt = String(node?.prompt || '').trim();
    const direction = String(node?.direction || '').trim();
    const aspectRatio = String(node?.aspect_ratio || node?.ratio || '').trim();
    const startImagePath = String(node?.start_image_path || '').trim();
    const endImagePath = String(node?.end_image_path || '').trim();
    const refImagePath = String(node?.ref_image_path || '').trim();
    const ingredientImagePaths = Array.isArray(node?.ingredient_image_paths)
      ? node.ingredient_image_paths.map((path) => String(path || '').trim()).filter(Boolean)
      : [];
    const bbox = sanitizeBBox(node?.bbox);

    if (prompt) payload.prompt = prompt;
    if (direction) payload.direction = direction;
    if (aspectRatio) payload.aspect_ratio = aspectRatio;
    if (startImagePath) payload.start_image_path = startImagePath;
    if (endImagePath) payload.end_image_path = endImagePath;
    if (refImagePath) payload.ref_image_path = refImagePath;
    if (ingredientImagePaths.length) payload.ingredient_image_paths = ingredientImagePaths;
    if (bbox) payload.bbox = bbox;

    return payload;
  }

  async function createIdeaNodesOnCanvas() {
    if (state.ideaCreatePending || state.ideaPending || state.ideaUploadPending) return;
    const nodes = latestIdeaNodes();
    if (!nodes.length) {
      App.toast('No suggested nodes to create yet.', 'warning');
      return;
    }

    const profile = currentProjectProfile();
    if (!profile) {
      App.toast('No project profile available for node creation.', 'warning');
      return;
    }

    state.ideaCreatePending = true;
    renderIntoRoot({ fit: false });

    try {
      const result = await API.chains.create({
        profile,
        jobs: nodes.map((node) => normalizeIdeaNodePayload(node)),
      });

      const createdChainId = String(result?.chain_id || result?.id || '').trim();
      state.ideaCreatePending = false;
      state.ideaCollapsed = true;
      state.ideaPreserveCollapseOnNextChain = true;
      App.toast('Idea nodes created on canvas.', 'success');

      if (createdChainId) {
        location.hash = `#project-view/${encodeURIComponent(createdChainId)}`;
        return;
      }

      await refreshFullView({ silent: true, fit: true });
      renderIntoRoot({ fit: false });
    } catch (error) {
      state.ideaCreatePending = false;
      state.ideaPreserveCollapseOnNextChain = false;
      renderIntoRoot({ fit: false });
      App.toast(`Failed to create nodes: ${error?.message || 'Unknown error'}`, 'error');
    }
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
    if (safe === 'completed') return 'check';
    if (safe === 'failed' || safe === 'cancelled') return 'close';
    if (ACTIVE_STATUSES.has(safe)) return 'autorenew';
    return 'schedule';
  }

  function statusPillLabel(job) {
    const status = safeStatus(job?.status);
    if (status === 'completed') return 'Completed';
    if (status === 'failed') return 'Failed';
    if (status === 'cancelled') return 'Cancelled';
    if (ACTIVE_STATUSES.has(status)) return 'Running';
    if (Number(job?.job_level) === 1) return 'Input';
    return 'Pending';
  }

  function nodeCaption(job) {
    const text = promptText(job) || typeCardLabel(job?.type);
    return typeof App?.truncate === 'function' ? App.truncate(text, 54) : text;
  }

  function nodeMeta(job) {
    const ratio = String(job?.aspect_ratio || '9:16').trim() || '9:16';
    return `${ratio} \u2022 ${modeLabel(job?.type)}`;
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

    return `
      <div class="pv-section">
        <div class="pv-section-label">Prompt</div>
        <div class="pv-prompt ${expanded ? 'pv-prompt--expanded' : ''}">${App.escapeHtml(text)}</div>
        ${needsToggle ? `
          <button type="button" class="pv-prompt-toggle" data-action="prompt-toggle" data-job-id="${escapeAttr(job.id)}">
            ${expanded ? 'Show Less' : 'Show More'}
          </button>
        ` : ''}
      </div>
    `;
  }

  function renderCountPills(job) {
    const active = activeCountPill(job);
    const noun = primaryMedia(job)?.kind === 'image' && String(job?.type || '') === 'text-to-image' ? 'Image' : 'Video';
    return `
      <div class="pv-section">
        <div class="pv-section-label">Count</div>
        <div class="pv-count-pills">
          ${COUNT_PILLS.map((count) => `
            <span class="pv-count-pill ${count === active ? 'pv-count-pill--active' : ''}">
              ${App.escapeHtml(String(count))} ${App.escapeHtml(count === 1 ? noun : `${noun}s`)}
            </span>
          `).join('')}
        </div>
      </div>
    `;
  }

  function renderOutput(job) {
    const status = safeStatus(job?.status);
    const media = status === 'completed' ? primaryMedia(job) : null;
    if (!media) {
      return `
        <div class="pv-output pv-output--empty">
          <span class="material-icons" aria-hidden="true">video_off</span>
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

  /* === U6 - canvas polish === */
  function renderNode(job) {
    const status = safeStatus(job?.status);
    const frame = state.layout.framesById.get(String(job.id)) || draftFrameForJob(job);
    const classes = ['pv-node', `pv-node--${status}`, state.selectedJobId === String(job.id) ? 'pv-node--selected' : '']
      .filter(Boolean).join(' ');
    const statusLabel = statusPillLabel(job);

    return `
      <div class="${classes}" data-job-id="${escapeAttr(job.id)}" data-status="${escapeAttr(status)}" style="position:absolute; left:${frame.left}px; top:${frame.top}px;">
        ${renderOutput(job)}

        <div class="pv-node-header">
          <div class="pv-node-type">
            <span class="pv-node-status-pill">${App.escapeHtml(statusLabel)}</span>
          </div>
        </div>

        <div class="pv-node-footer">
          <button type="button" class="pv-node-caption" data-action="open-node" data-job-id="${escapeAttr(job.id)}" aria-label="Open node">
            <span class="pv-node-name">${App.escapeHtml(nodeCaption(job))}</span>
            <span class="pv-node-meta">${App.escapeHtml(nodeMeta(job))}</span>
          </button>
          ${status === 'completed' ? '<span class="pv-node-footer-star" aria-hidden="true">\u2726</span>' : ''}
        </div>

        <a class="pv-new-step-pill pv-node-action-overlay" href="${App.escapeHtml(newChainStepHref(job.id))}" data-job-id="${escapeAttr(job.id)}" title="New chain step" aria-label="New chain step">
          <span class="material-icons">add</span>
          <span>Chain step</span>
        </a>
      </div>
    `;
  }

  function edgeIsActive(edge) {
    if (edge && Object.prototype.hasOwnProperty.call(edge, 'active')) {
      return edge.active === true || edge.active === 'true' || edge.active === 1 || edge.active === '1';
    }
    const parent = state.jobsById.get(String(edge.parent));
    const child = state.jobsById.get(String(edge.child));
    return ACTIVE_STATUSES.has(safeStatus(parent?.status)) || ACTIVE_STATUSES.has(safeStatus(child?.status));
  }

  function buildEdgeGeometry(frameA, frameB) {
    const sourceX = frameA.left + (frameA.width / 2);
    const sourceY = frameA.top + frameA.height;
    const targetX = frameB.left + (frameB.width / 2);
    const targetY = frameB.top;
    const curve = Math.max(76, Math.abs(targetY - sourceY) * 0.46);
    const control1X = sourceX;
    const control1Y = sourceY + curve;
    const control2X = targetX;
    const control2Y = targetY - curve;
    return {
      sourceX,
      sourceY,
      control1X,
      control1Y,
      control2X,
      control2Y,
      targetX,
      targetY,
      d: `M ${sourceX} ${sourceY} C ${control1X} ${control1Y} ${control2X} ${control2Y} ${targetX} ${targetY}`,
    };
  }

  function edgePointAt(geometry, t) {
    const inv = 1 - t;
    const inv2 = inv * inv;
    const inv3 = inv2 * inv;
    const t2 = t * t;
    const t3 = t2 * t;
    return {
      x: (inv3 * geometry.sourceX)
        + (3 * inv2 * t * geometry.control1X)
        + (3 * inv * t2 * geometry.control2X)
        + (t3 * geometry.targetX),
      y: (inv3 * geometry.sourceY)
        + (3 * inv2 * t * geometry.control1Y)
        + (3 * inv * t2 * geometry.control2Y)
        + (t3 * geometry.targetY),
    };
  }

  function buildEdgeSamples(geometry) {
    const points = [];
    let totalLength = 0;
    let previous = edgePointAt(geometry, 0);
    points.push({ ...previous, distance: 0 });
    for (let index = 1; index <= EDGE_SAMPLE_SEGMENTS; index += 1) {
      const point = edgePointAt(geometry, index / EDGE_SAMPLE_SEGMENTS);
      totalLength += Math.hypot(point.x - previous.x, point.y - previous.y);
      points.push({ ...point, distance: totalLength });
      previous = point;
    }
    return { points, totalLength };
  }

  function pointAtDistance(samples, distance) {
    const points = Array.isArray(samples?.points) ? samples.points : [];
    if (!points.length) return { x: 0, y: 0 };
    if (distance <= 0) return points[0];
    if (distance >= samples.totalLength) return points[points.length - 1];

    for (let index = 1; index < points.length; index += 1) {
      const previous = points[index - 1];
      const current = points[index];
      if (distance > current.distance) continue;
      const segmentLength = Math.max(0.0001, current.distance - previous.distance);
      const ratio = (distance - previous.distance) / segmentLength;
      return {
        x: previous.x + ((current.x - previous.x) * ratio),
        y: previous.y + ((current.y - previous.y) * ratio),
      };
    }

    return points[points.length - 1];
  }

  function renderEdgeMarkerDots(samples) {
    if (!samples?.totalLength || samples.totalLength <= EDGE_MARKER_SPACING * 1.5) return '';
    const dots = [];
    for (
      let distance = EDGE_MARKER_SPACING;
      distance < samples.totalLength - EDGE_MARKER_SPACING;
      distance += EDGE_MARKER_SPACING
    ) {
      const point = pointAtDistance(samples, distance);
      const x = (point.x - (EDGE_MARKER_SIZE / 2)).toFixed(2);
      const y = (point.y - (EDGE_MARKER_SIZE / 2)).toFixed(2);
      const cx = point.x.toFixed(2);
      const cy = point.y.toFixed(2);
      dots.push(`
        <rect
          class="pv-edge-marker-dot"
          x="${x}"
          y="${y}"
          width="${EDGE_MARKER_SIZE}"
          height="${EDGE_MARKER_SIZE}"
          transform="rotate(45 ${cx} ${cy})"
        ></rect>
      `);
    }
    return dots.join('');
  }

  function renderEdgePorts(geometry) {
    const portPairs = [
      [geometry.sourceX, geometry.sourceY],
      [geometry.targetX, geometry.targetY],
    ];
    return portPairs.map(([x, y]) => `
      <circle class="pv-edge-port" cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="${EDGE_PORT_RADIUS}"></circle>
    `).join('');
  }

  function renderEdgeStar(samples, isActive) {
    if (!isActive || !samples?.totalLength) return '';
    const midpoint = pointAtDistance(samples, samples.totalLength / 2);
    return `
      <text
        class="pv-edge-star"
        data-active="true"
        x="${midpoint.x.toFixed(2)}"
        y="${midpoint.y.toFixed(2)}"
        text-anchor="middle"
        dominant-baseline="middle"
      >\u2726</text>
    `;
  }

  function renderEdges() {
    return state.edges.map((edge) => {
      const parentFrame = state.layout.framesById.get(String(edge.parent));
      const childFrame = state.layout.framesById.get(String(edge.child));
      if (!parentFrame || !childFrame) return '';
      const geometry = buildEdgeGeometry(parentFrame, childFrame);
      const samples = buildEdgeSamples(geometry);
      const active = edgeIsActive(edge);

      return `
        <g
          class="pv-edge"
          data-edge-parent="${escapeAttr(edge.parent)}"
          data-edge-child="${escapeAttr(edge.child)}"
          data-active="${active ? 'true' : 'false'}"
        >
          <path
            class="pv-edge-path ${active ? 'pv-edge-path--running' : ''}"
            d="${escapeAttr(geometry.d)}"
            fill="none"
          ></path>
          ${renderEdgeMarkerDots(samples)}
          ${renderEdgePorts(geometry)}
          ${renderEdgeStar(samples, active)}
        </g>
      `;
    }).join('');
  }
  /* === end U6 === */

  function renderToolbar() {
    return `
      <div class="pv-toolbar">
        <div style="display:flex; align-items:center; gap:12px; min-width:0;">
          <button type="button" class="icon-btn" data-action="back" aria-label="Back"><span class="material-icons">arrow_back</span></button>
          <div style="display:grid; gap:4px; min-width:0;">
            <div style="font-size:20px; font-weight:700; line-height:1.2; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${App.escapeHtml(state.title || 'Project')}</div>
            <div style="color: var(--text-muted); font-size: 12px; line-height: 1.4;">${state.chainId ? `Chain ${App.escapeHtml(App.truncate(state.chainId, 18))}` : 'Workflow canvas'}</div>
            ${renderDebugBadges(state.debugBadges)}
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
          <button type="button" class="btn btn-sm btn-outline" data-action="ai-agent">AI Agent</button>
        </div>
      </div>
    `;
  }

  function renderZoomToolbar() {
    return `
      <div class="pv-zoom-toolbar" style="right:${state.ideaCollapsed ? 24 : IDEA_RAIL_WIDTH + 24}px;">
        <button type="button" class="pv-zoom-btn btn btn-sm btn-outline" data-action="zoom-out" aria-label="Zoom out">-</button>
        <button type="button" class="pv-zoom-btn btn btn-sm btn-outline" data-action="zoom-in" aria-label="Zoom in">+</button>
        <button type="button" class="pv-zoom-btn btn btn-sm btn-outline" data-action="fit" aria-label="Fit to screen">Fit</button>
        <button type="button" class="pv-zoom-btn btn btn-sm btn-outline" data-action="reset-zoom" aria-label="Reset zoom">Reset</button>
      </div>
    `;
  }

  function renderExtendHint() {
    if (state.jobs.length !== 1) return '';
    const onlyJob = state.jobs[0];
    if (!onlyJob?.id) return '';
    const frame = state.layout.framesById.get(String(onlyJob.id)) || draftFrameForJob(onlyJob);
    return `
      <div
        class="pv-extend-hint"
        data-role="extend-hint"
        data-visible="${state.extendHintVisible ? 'true' : 'false'}"
        style="left:${frame.left + (frame.width / 2)}px; top:${frame.top + frame.height + 28}px;"
      >Click + New chain step below to extend this chain</div>
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
          ${renderExtendHint()}
        </div>
        ${renderZoomToolbar()}
      </div>
    `;
  }

  function renderIdeaMessages() {
    const transcript = state.ideaMessages.map((message) => `
      <div class="pv-idea-message ${message.role === 'assistant' ? 'pv-idea-message--assistant' : 'pv-idea-message--user'}">
        ${message.role === 'assistant'
          ? renderIdeaMarkdown(message.content)
          : `<p>${App.escapeHtml(message.content)}</p>`}
      </div>
    `).join('');

    const typingIndicator = state.ideaPending ? `
      <div class="pv-idea-message pv-idea-message--assistant">
        <p>...</p>
      </div>
    ` : '';

    return transcript + typingIndicator;
  }

  function renderIdeaAttachments() {
    if (!state.ideaRefImages.length) return '';
    return `
      <div class="pv-idea-attachments">
        ${state.ideaRefImages.map((item, index) => `
          <div class="pv-idea-thumb">
            <img src="${escapeAttr(item.url)}" alt="${escapeAttr(item.name || `Reference ${index + 1}`)}">
            <button
              type="button"
              class="pv-idea-thumb-remove"
              data-action="idea-remove-ref"
              data-index="${index}"
              aria-label="Remove reference image ${index + 1}"
            >&times;</button>
          </div>
        `).join('')}
      </div>
    `;
  }

  function renderIdeaRail() {
    const collapsedClass = state.ideaCollapsed ? 'pv-idea-rail--collapsed' : '';
    const canSend = String(state.ideaDraft || '').trim() && !state.ideaPending && !state.ideaUploadPending;
    const canCreateNodes = latestIdeaNodes().length > 0 && !state.ideaPending && !state.ideaUploadPending && !state.ideaCreatePending;

    return `
      <aside class="pv-idea-rail ${collapsedClass}" aria-label="${App.escapeHtml(IDEA_PANEL_TITLE)}">
        <div class="pv-idea-header">
          <div class="pv-idea-title">
            <span aria-hidden="true">&#10022;</span>
            <span>${App.escapeHtml(IDEA_PANEL_TITLE)}</span>
          </div>
          <button
            type="button"
            class="pv-idea-collapse"
            data-action="toggle-idea-rail"
            aria-expanded="${state.ideaCollapsed ? 'false' : 'true'}"
            aria-label="${state.ideaCollapsed ? 'Open idea panel' : 'Collapse idea panel'}"
          >
            <span class="material-icons" aria-hidden="true">expand_more</span>
          </button>
        </div>
        ${state.ideaCollapsed ? '' : `
          <div class="pv-idea-body" data-role="idea-body">
            ${state.ideaMessages.length || state.ideaPending
              ? renderIdeaMessages()
              : `<div class="pv-idea-empty">${App.escapeHtml(IDEA_EMPTY_COPY)}</div>`}
          </div>
          <div class="pv-idea-footer">
            ${renderIdeaAttachments()}
            <input type="file" accept="image/*" multiple hidden data-role="idea-file-input">
            <div class="pv-idea-input-row">
              <button
                type="button"
                class="pv-idea-attach-btn"
                data-action="idea-attach"
                aria-label="Attach reference images"
                ${state.ideaPending || state.ideaUploadPending ? 'disabled' : ''}
              >
                <span class="material-icons" aria-hidden="true">attach_file</span>
              </button>
              <input
                type="text"
                class="pv-idea-input"
                value="${escapeAttr(state.ideaDraft)}"
                placeholder="${escapeAttr(IDEA_INPUT_PLACEHOLDER)}"
                ${state.ideaPending || state.ideaUploadPending ? 'disabled' : ''}
              >
              <button
                type="button"
                class="pv-idea-send-btn"
                data-action="idea-send"
                aria-label="Send idea prompt"
                ${canSend ? '' : 'disabled'}
              >
                <span class="material-icons" aria-hidden="true">send</span>
              </button>
            </div>
            <button
              type="button"
              data-action="idea-source"
              style="display:inline-flex; align-items:center; gap:6px; width:max-content; margin-top:10px; color:rgba(255,255,255,0.62); background:transparent; border:0; padding:0; font-size:12px;"
            >
              <span>${App.escapeHtml(IDEA_INPUT_SOURCE_LABEL)}</span>
              <span class="material-icons" style="font-size:16px;" aria-hidden="true">expand_more</span>
            </button>
            <button
              type="button"
              class="pv-idea-create-nodes-btn"
              data-action="idea-create-nodes"
              ${canCreateNodes ? '' : 'disabled'}
            >${App.escapeHtml(state.ideaCreatePending ? 'Creating nodes...' : IDEA_CREATE_NODES_LABEL)}</button>
            <div class="pv-idea-caption"><em>${App.escapeHtml(IDEA_CAPTION_TEXT)}</em></div>
          </div>
        `}
      </aside>
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
    const railOffset = state.ideaCollapsed ? IDEA_RAIL_COLLAPSED_WIDTH + 12 : IDEA_RAIL_WIDTH + 12;
    const mainContent = hasNodes
      ? renderCanvas()
      : renderEmptyState(
        state.chainId ? 'No chain nodes yet' : 'Project not found',
        state.loadError || (state.chainId
          ? 'This chain does not have any jobs to render yet. Add a step or open the gallery.'
          : 'Open a chain from the jobs list or gallery to inspect it on the DAG canvas.')
      );

    return `
      <div class="pv-canvas" style="display:grid; gap:16px; min-height:calc(100vh - var(--appbar-height) - 16px); padding-bottom:152px;">
        ${renderToolbar()}
        ${state.loadError && hasNodes ? renderBanner(state.loadError) : ''}
        <div data-role="idea-workspace" style="position:relative; min-height:calc(100vh - 220px);">
          <div data-role="idea-main" style="margin-right:${railOffset}px; min-height:100%; transition:margin-right 180ms ease;">
            ${mainContent}
          </div>
          ${renderIdeaRail()}
        </div>
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
      bounds: null,
    };
  }

  function applyChainData(chain) {
    const jobs = uniqueJobsById(chain?.jobs || []).sort(compareJobs);
    const jobsById = new Map(jobs.map((job) => [String(job.id), job]));
    const rootJob = pickRootJob(jobs, String(chain?.rootId || '').trim());
    const edges = normalizeEdges(chain?.edges, jobsById);
    const latest = latestJob(jobs);
    const previousSingleJobId = state.jobs.length === 1 ? String(state.jobs[0]?.id || '') : '';

    state.jobs = jobs;
    state.jobsById = jobsById;
    state.edges = edges.length ? edges : buildEdgesFromJobs(jobs);
    state.rootId = String(rootJob?.id || chain?.rootId || '').trim();
    state.rootJob = rootJob || null;
    state.latestJobId = String(latest?.id || '').trim();
    state.title = chainTitle(rootJob);
    state.debugBadges = Array.isArray(chain?.debugBadges) ? chain.debugBadges : [];
    if (!jobsById.has(state.selectedJobId)) {
      state.selectedJobId = String(rootJob?.id || latest?.id || '');
    }
    if (jobs.length !== 1 || String(jobs[0]?.id || '') !== previousSingleJobId) {
      clearExtendHintTimer();
      state.extendHintVisible = false;
    }
    rebuildLayoutState();
  }

  async function loadFromBulkChain(chainId) {
    const detail = await API.chains.get(chainId);
    const jobs = normalizeJobList(detail);
    if (!jobs.length) return null;
    const jobsById = new Map(jobs.map((job) => [String(job.id), job]));
    return {
      chainId,
      rootId: String(detail?.root_id || '').trim(),
      jobs,
      edges: normalizeEdges(detail?.edges, jobsById),
      debugBadges: [],
    };
  }

  async function loadFromJobIdFallback(routeKey) {
    // The route key may be a bare job_id for legacy jobs without chain_id.
    // Render that job as a single-node DAG so old tiles still land somewhere useful.
    try {
      const job = await API.jobs.get(routeKey);
      if (!job?.id) return null;
      const fallbackUsed = 'job.id';
      warnBackendGap({ field: 'chain_id', jobId: String(job.id), fallbackUsed });
      return {
        chainId: routeKey,
        rootId: String(job.id),
        jobs: [job],
        edges: [],
        debugBadges: [{ field: 'chain_id', fallbackUsed }],
      };
    } catch (_) {
      return null;
    }
  }

  async function loadFromCompatibilityFallback(chainId) {
    const debugBadges = [];
    const listed = normalizeJobList(await API.jobs.list({ chain_id: chainId, limit: 200 }))
      .filter((job) => String(job?.chain_id || '').trim() === chainId)
      .sort(compareByCreatedAsc);
    if (!listed.length) {
      const single = await loadFromJobIdFallback(chainId);
      if (single) return single;
      return { chainId, rootId: '', jobs: [], edges: [], debugBadges };
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

    const compatibilityJob = relatedJobs.find((job) => !String(job?.chain_id || '').trim());
    if (compatibilityJob) {
      const fallbackUsed = 'compatibility-merge';
      warnBackendGap({ field: 'chain_id', jobId: String(compatibilityJob.id || ''), fallbackUsed });
      debugBadges.push({ field: 'chain_id', fallbackUsed });
    }

    const chainRootId = String(related?.chain_root_id || '').trim();
    const rootId = chainRootId || String(firstJob.id || '').trim();
    if (!chainRootId && firstJob?.id) {
      const fallbackUsed = 'firstJob.id';
      warnBackendGap({ field: 'chain_root_id', jobId: String(firstJob.id), fallbackUsed });
      debugBadges.push({ field: 'chain_root_id', fallbackUsed });
    }
    const walked = walkChainJobs(relatedJobs, rootId);
    return {
      chainId,
      rootId,
      jobs: walked.length ? walked : listed,
      edges: buildEdgesFromJobs(walked.length ? walked : listed),
      debugBadges,
    };
  }

  async function loadChainData() {
    const nextChainId = parseRouteChainId().trim();
    if (nextChainId !== state.chainId) {
      resetIdeaState({ preserveCollapse: state.ideaPreserveCollapseOnNextChain });
      state.ideaPreserveCollapseOnNextChain = false;
    }
    state.chainId = nextChainId;
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
      state.debugBadges = [];
      clearExtendHintTimer();
      state.extendHintVisible = false;
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
      state.debugBadges = [];
      clearExtendHintTimer();
      state.extendHintVisible = false;
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
    state.layout = { framesById: frames, width, height, bounds: frameBounds(frames) };
    panStage.style.width = `${width}px`;
    panStage.style.height = `${height}px`;

    const edgesNode = edgesEl();
    if (edgesNode) {
      edgesNode.setAttribute('viewBox', `0 0 ${width} ${height}`);
      edgesNode.setAttribute('width', String(width));
      edgesNode.setAttribute('height', String(height));
      edgesNode.innerHTML = renderEdges();
    }

    const shouldAutoFit = fit || state.jobs.length >= 2 || !state.userViewportChanged;
    if (shouldAutoFit) {
      fitToScreen({ markUser: false });
    } else {
      applyViewport();
    }
    syncExtendHint();
  }

  function clampZoom(value) {
    return Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, value));
  }

  function clearExtendHintTimer() {
    if (!state.extendHintTimer) return;
    clearTimeout(state.extendHintTimer);
    state.extendHintTimer = null;
  }

  function frameBounds(frames) {
    const list = frames instanceof Map ? Array.from(frames.values()) : [];
    if (!list.length) return null;

    let left = Infinity;
    let top = Infinity;
    let right = -Infinity;
    let bottom = -Infinity;
    list.forEach((frame) => {
      if (!frame) return;
      left = Math.min(left, frame.left);
      top = Math.min(top, frame.top);
      right = Math.max(right, frame.left + frame.width);
      bottom = Math.max(bottom, frame.top + frame.height);
    });

    if (!Number.isFinite(left) || !Number.isFinite(top)) return null;
    return {
      left,
      top,
      right,
      bottom,
      width: Math.max(1, right - left),
      height: Math.max(1, bottom - top),
    };
  }

  function syncExtendHint() {
    const hintEl = rootElement()?.querySelector('[data-role="extend-hint"]');
    const onlyJob = state.jobs.length === 1 ? state.jobs[0] : null;
    const frame = onlyJob?.id ? state.layout.framesById.get(String(onlyJob.id)) : null;
    if (!hintEl || !frame) {
      clearExtendHintTimer();
      if (state.jobs.length !== 1) state.extendHintVisible = false;
      return;
    }

    hintEl.style.left = `${frame.left + (frame.width / 2)}px`;
    hintEl.style.top = `${frame.top + frame.height + 28}px`;
    hintEl.dataset.visible = state.extendHintVisible ? 'true' : 'false';
    if (state.extendHintVisible) return;

    clearExtendHintTimer();
    state.extendHintTimer = setTimeout(() => {
      state.extendHintTimer = null;
      if (App.currentPage !== 'project-view' || state.jobs.length !== 1) return;
      state.extendHintVisible = true;
      const liveHintEl = rootElement()?.querySelector('[data-role="extend-hint"]');
      if (liveHintEl) liveHintEl.dataset.visible = 'true';
    }, EXTEND_HINT_DELAY_MS);
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
    const bounds = state.layout.bounds || frameBounds(state.layout.framesById);
    if (!bounds) return;
    const usableWidth = Math.max(1, rect.width - 32);
    const usableHeight = Math.max(1, rect.height - 32);
    const fitBox = state.jobs.length === 1
      ? { left: bounds.left, top: bounds.top, width: bounds.width, height: bounds.height }
      : {
        left: bounds.left - FIT_PADDING,
        top: bounds.top - FIT_PADDING,
        width: bounds.width + (FIT_PADDING * 2),
        height: bounds.height + (FIT_PADDING * 2),
      };
    const maxFitZoom = Math.min(FIT_MAX_ZOOM, MAX_ZOOM);
    const zoom = clampZoom(Math.min(
      state.jobs.length === 1 ? SINGLE_NODE_FIT_ZOOM : maxFitZoom,
      maxFitZoom,
      usableWidth / fitBox.width,
      usableHeight / fitBox.height,
    ));
    const next = {
      z: zoom,
      x: ((rect.width - (fitBox.width * zoom)) / 2) - (fitBox.left * zoom),
      y: ((rect.height - (fitBox.height * zoom)) / 2) - (fitBox.top * zoom),
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
    requestAnimationFrame(() => {
      measureAndPositionCanvas({ fit });
      const ideaBody = rootElement()?.querySelector('[data-role="idea-body"]');
      if (ideaBody && state.ideaAutoScrollPending) {
        ideaBody.scrollTop = ideaBody.scrollHeight;
      }
      state.ideaAutoScrollPending = false;

      if (state.ideaFocusInputPending) {
        rootElement()?.querySelector('.pv-idea-input')?.focus();
      }
      state.ideaFocusInputPending = false;
    });
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

    const edgesNode = edgesEl();
    if (edgesNode) edgesNode.innerHTML = renderEdges();

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
    if (state.jobs.length >= 2 || !state.userViewportChanged) {
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
    if (action === 'ai-agent') {
      event.preventDefault();
      openIdeaRail({ focusInput: true });
      return;
    }
    if (action === 'toggle-idea-rail') {
      event.preventDefault();
      setIdeaRailCollapsed(!state.ideaCollapsed);
      return;
    }
    if (action === 'idea-attach') {
      event.preventDefault();
      rootElement()?.querySelector('[data-role="idea-file-input"]')?.click();
      return;
    }
    if (action === 'idea-remove-ref') {
      event.preventDefault();
      const index = Number(actionEl.dataset.index);
      if (Number.isFinite(index) && index >= 0) {
        state.ideaRefImages = state.ideaRefImages.filter((_, itemIndex) => itemIndex !== index);
        renderIntoRoot({ fit: false });
      }
      return;
    }
    if (action === 'idea-send') {
      event.preventDefault();
      void submitIdeaPrompt();
      return;
    }
    if (action === 'idea-source') {
      event.preventDefault();
      return;
    }
    if (action === 'idea-create-nodes') {
      event.preventDefault();
      void createIdeaNodesOnCanvas();
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
  }

  function handleRootInput(event) {
    if (event.target instanceof Element && event.target.classList.contains('pv-idea-input')) {
      state.ideaDraft = event.target.value;
      syncIdeaSendButton();
    }
  }

  function handleRootChange(event) {
    if (event.target instanceof Element && event.target.matches('[data-role="idea-file-input"]')) {
      void handleIdeaUpload(event.target);
    }
  }

  function handleRootKeydown(event) {
    if (!(event.target instanceof Element) || !event.target.classList.contains('pv-idea-input')) return;
    if (event.key !== 'Enter' || event.shiftKey) return;
    event.preventDefault();
    void submitIdeaPrompt();
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
      state.rootEl?.addEventListener('input', handleRootInput);
      state.rootEl?.addEventListener('change', handleRootChange);
      state.rootEl?.addEventListener('keydown', handleRootKeydown);
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
      state.rootEl?.removeEventListener('input', handleRootInput);
      state.rootEl?.removeEventListener('change', handleRootChange);
      state.rootEl?.removeEventListener('keydown', handleRootKeydown);
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
      clearExtendHintTimer();

      state.rootEl = null;
      state.menuOpen = false;
      state.drag = null;
      state.userViewportChanged = false;
      state.extendHintVisible = false;
    },
  };

  App.register(ProjectViewPage);
})();
