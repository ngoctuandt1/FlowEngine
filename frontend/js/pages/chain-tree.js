/**
 * Chain Tree Page
 * Visualize chain job dependencies as a top-down tree.
 */
(() => {
  const PAGE_ROOT_ID = 'chain-tree-page';
  const PAGE_STYLE_ID = 'chain-tree-page-styles';
  const NODE_WIDTH = 248;
  const NODE_HEIGHT = 124;
  const LEVEL_GAP = 178;
  const LEAF_STEP = NODE_WIDTH + 80;
  const CANVAS_PADDING = 36;

  let root = null;
  let handlers = null;
  let lastNonDetailHash = location.hash || '#home';

  const state = {
    chains: [],
    selectedChainId: null,
    selectedSummary: null,
    selectedJobs: [],
    loadingList: false,
    loadingDetail: false,
    listError: '',
    detailError: '',
    listRequestId: 0,
    detailRequestId: 0,
  };

  function escapeAttr(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/"/g, '&quot;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  function ensureStyles() {
    if (document.getElementById(PAGE_STYLE_ID)) return;

    const style = document.createElement('style');
    style.id = PAGE_STYLE_ID;
    style.textContent = `
      .chain-tree-shell {
        display: grid;
        grid-template-columns: minmax(280px, 30%) minmax(0, 1fr);
        gap: 24px;
        align-items: start;
      }

      .chain-tree-rail {
        position: sticky;
        top: calc(var(--topbar-height) + 24px);
        max-height: calc(100vh - var(--topbar-height) - 56px);
        overflow: auto;
      }

      .chain-tree-list {
        display: grid;
        gap: 12px;
      }

      .chain-tree-list-item {
        width: 100%;
        padding: 16px;
        color: var(--text-primary);
        background: rgba(255, 255, 255, 0.01);
        border: 1px solid var(--border);
        border-radius: 12px;
        cursor: pointer;
        text-align: left;
        transition: background var(--transition), border-color var(--transition), transform var(--transition);
      }

      .chain-tree-list-item:hover {
        background: var(--bg-card-hover);
        border-color: var(--border-light);
        transform: translateY(-1px);
      }

      .chain-tree-list-item.selected {
        background: var(--accent-muted);
        border-color: var(--accent-border);
        box-shadow: inset 0 0 0 1px var(--accent-border);
      }

      .chain-tree-list-top,
      .chain-tree-node-top {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 12px;
      }

      .chain-tree-list-type,
      .chain-tree-node-type {
        display: flex;
        align-items: center;
        gap: 10px;
        min-width: 0;
      }

      .chain-tree-list-title {
        color: var(--text-primary);
        font-size: 14px;
        font-weight: 700;
        letter-spacing: -0.02em;
      }

      .chain-tree-list-id {
        margin-top: 4px;
        color: var(--text-muted);
        font-size: 12px;
      }

      .chain-tree-prompt {
        margin: 12px 0;
        color: var(--text-secondary);
        font-size: 13px;
        line-height: 1.5;
        overflow-wrap: anywhere;
      }

      .chain-tree-list-meta {
        display: flex;
        flex-wrap: wrap;
        gap: 8px 12px;
        margin-top: 12px;
        padding-top: 12px;
        border-top: 1px solid var(--border);
        color: var(--text-muted);
        font-size: 12px;
      }

      .chain-tree-meta-item {
        display: inline-flex;
        align-items: center;
        gap: 6px;
      }

      .chain-tree-meta-item .material-icons {
        font-size: 15px;
      }

      .chain-tree-main {
        display: grid;
        gap: 16px;
        min-width: 0;
      }

      .chain-tree-summary-card {
        padding: 20px;
      }

      .chain-tree-summary-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
        gap: 12px;
        margin-top: 16px;
      }

      .chain-tree-kv {
        padding: 12px 14px;
        background: rgba(255, 255, 255, 0.02);
        border: 1px solid var(--border);
        border-radius: 10px;
      }

      .chain-tree-kv-label {
        margin-bottom: 6px;
        color: var(--text-muted);
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.06em;
        text-transform: uppercase;
      }

      .chain-tree-kv-value {
        color: var(--text-primary);
        font-size: 13px;
        overflow-wrap: anywhere;
      }

      .chain-tree-banner {
        display: flex;
        align-items: flex-start;
        gap: 10px;
        padding: 12px 14px;
        border: 1px solid var(--border);
        border-radius: 10px;
        font-size: 13px;
      }

      .chain-tree-banner.warn {
        color: #fde68a;
        background: rgba(234, 179, 8, 0.10);
        border-color: rgba(234, 179, 8, 0.28);
      }

      .chain-tree-banner.error {
        color: #fecaca;
        background: rgba(239, 68, 68, 0.10);
        border-color: rgba(239, 68, 68, 0.28);
      }

      .chain-tree-banner .material-icons {
        margin-top: 1px;
        font-size: 18px;
      }

      .chain-tree-canvas-card {
        padding: 20px;
        min-width: 0;
      }

      .chain-tree-canvas-scroll {
        overflow: auto;
        padding-bottom: 4px;
      }

      .chain-tree-canvas {
        position: relative;
        min-width: 100%;
        min-height: 240px;
      }

      .chain-tree-svg {
        position: absolute;
        inset: 0;
        width: 100%;
        height: 100%;
        pointer-events: none;
        overflow: visible;
      }

      .chain-tree-edge {
        fill: none;
        stroke: rgba(161, 161, 170, 0.38);
        stroke-width: 2;
        stroke-linecap: round;
        stroke-linejoin: round;
      }

      .chain-tree-node {
        position: absolute;
        display: flex;
        flex-direction: column;
        gap: 10px;
        width: ${NODE_WIDTH}px;
        min-height: ${NODE_HEIGHT}px;
        padding: 14px;
        color: var(--text-primary);
        background:
          linear-gradient(180deg, rgba(var(--node-accent-rgb), 0.12), rgba(24, 24, 27, 0.92) 28%),
          var(--bg-card);
        border: 1px solid rgba(var(--node-accent-rgb), 0.28);
        border-left: 4px solid var(--node-accent);
        border-radius: 16px;
        box-shadow: 0 12px 32px rgba(0, 0, 0, 0.24);
        cursor: pointer;
        text-align: left;
        transition: transform var(--transition), box-shadow var(--transition), border-color var(--transition);
      }

      .chain-tree-node:hover {
        transform: translateY(-2px);
        border-color: rgba(var(--node-accent-rgb), 0.48);
        box-shadow: 0 18px 42px rgba(0, 0, 0, 0.32);
      }

      .chain-tree-node-icon {
        flex: 0 0 34px;
        width: 34px;
        height: 34px;
      }

      .chain-tree-node-title {
        color: var(--text-primary);
        font-size: 14px;
        font-weight: 700;
        line-height: 1.3;
      }

      .chain-tree-node-subtitle {
        margin-top: 2px;
        color: var(--text-muted);
        font-size: 12px;
      }

      .chain-tree-node-badges {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
      }

      .chain-tree-node-footer {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        margin-top: auto;
      }

      .chain-tree-id-badge,
      .chain-tree-level-badge {
        display: inline-flex;
        align-items: center;
        height: 24px;
        padding: 0 8px;
        border: 1px solid var(--border-light);
        border-radius: 999px;
        color: var(--text-secondary);
        background: rgba(10, 10, 12, 0.46);
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.04em;
      }

      .chain-tree-level-badge {
        color: var(--text-muted);
      }

      .chain-tree-status {
        display: inline-flex;
        align-items: center;
        height: 22px;
        padding: 0 8px;
        border: 1px solid transparent;
        border-radius: 999px;
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-transform: uppercase;
      }

      .chain-tree-status.pending,
      .chain-tree-status.cancelled {
        color: #e5e7eb;
        background: rgba(107, 114, 128, 0.22);
        border-color: rgba(107, 114, 128, 0.36);
      }

      .chain-tree-status.running,
      .chain-tree-status.claimed {
        color: #fde68a;
        background: rgba(234, 179, 8, 0.22);
        border-color: rgba(234, 179, 8, 0.38);
      }

      .chain-tree-status.completed {
        color: #bbf7d0;
        background: rgba(34, 197, 94, 0.22);
        border-color: rgba(34, 197, 94, 0.38);
      }

      .chain-tree-status.failed {
        color: #fecaca;
        background: rgba(239, 68, 68, 0.22);
        border-color: rgba(239, 68, 68, 0.38);
      }

      .chain-tree-help {
        margin-bottom: 16px;
        color: var(--text-muted);
        font-size: 13px;
      }

      @media (max-width: 1100px) {
        .chain-tree-shell {
          grid-template-columns: 1fr;
        }

        .chain-tree-rail {
          position: static;
          max-height: none;
        }
      }
    `;
    document.head.appendChild(style);
  }

  function getJobTypeLabel(type) {
    const item = Array.isArray(CONST?.JOB_TYPES)
      ? CONST.JOB_TYPES.find((entry) => entry.id === type)
      : null;
    return item?.label || String(type || 'Unknown').replace(/-/g, ' ');
  }

  function typeTheme(type) {
    const normalized = String(type || '');
    if (['text-to-video', 'frames-to-video', 'ingredients-to-video'].includes(normalized)) {
      return { rgb: '124, 92, 255', color: '#8b5cf6', className: 't2v' };
    }
    if (normalized === 'text-to-image') {
      return { rgb: '14, 165, 233', color: '#38bdf8', className: 'insert' };
    }
    if (normalized === 'extend-video') {
      return { rgb: '59, 130, 246', color: '#60a5fa', className: 'extend' };
    }
    if (normalized === 'insert-object') {
      return { rgb: '34, 197, 94', color: '#4ade80', className: 'insert' };
    }
    if (normalized === 'remove-object') {
      return { rgb: '239, 68, 68', color: '#f87171', className: 'remove' };
    }
    if (normalized === 'camera-move') {
      return { rgb: '234, 179, 8', color: '#facc15', className: 'camera' };
    }
    return { rgb: '161, 161, 170', color: '#a1a1aa', className: '' };
  }

  function statusMeta(status) {
    const normalized = String(status || 'pending');
    if (normalized === 'completed') return { label: 'Completed', className: 'completed' };
    if (normalized === 'failed') return { label: 'Failed', className: 'failed' };
    if (normalized === 'running' || normalized === 'claimed') {
      return { label: normalized === 'claimed' ? 'Claimed' : 'Running', className: normalized };
    }
    if (normalized === 'cancelled') return { label: 'Cancelled', className: 'cancelled' };
    return { label: 'Pending', className: 'pending' };
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

  function normalizeJobList(result) {
    const items = Array.isArray(result) ? result : result?.jobs || [];
    return items
      .filter((job) => job && typeof job === 'object' && job.id)
      .map((job) => ({ ...job }))
      .sort(compareJobs);
  }

  function promptText(job) {
    return String(job?.prompt || job?.direction || '').trim();
  }

  function rootPromptSnippet(job, maxLen = 40) {
    return App.truncate(promptText(job) || 'No prompt provided', maxLen);
  }

  function exactDateLabel(value) {
    if (!value) return '-';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  function computeChainStatus(statuses) {
    if (!statuses.length) return 'pending';
    if (statuses.includes('failed')) return 'failed';
    if (statuses.some((status) => status === 'running' || status === 'claimed')) return 'running';
    if (statuses.includes('pending')) {
      if (statuses.some((status) => status === 'completed' || status === 'cancelled')) {
        return 'running';
      }
      return 'pending';
    }
    if (statuses.every((status) => status === 'cancelled')) return 'cancelled';
    return 'completed';
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

  function buildChainSummary(chainId, jobs, detail = {}) {
    const sortedJobs = [...jobs].sort(compareJobs);
    const rootJob = pickRootJob(sortedJobs);
    const statuses = sortedJobs.map((job) => String(job.status || 'pending'));
    const detailProgress = detail?.progress || null;

    return {
      id: String(detail?.id || detail?.chain_id || chainId || ''),
      profile:
        detail?.profile ||
        rootJob?.profile ||
        sortedJobs.find((job) => job.profile)?.profile ||
        '',
      created_at:
        detail?.created_at ||
        rootJob?.created_at ||
        sortedJobs[0]?.created_at ||
        '',
      status: detail?.status || computeChainStatus(statuses),
      progress: {
        completed:
          detailProgress?.completed ??
          statuses.filter((status) => status === 'completed').length,
        total: detailProgress?.total ?? sortedJobs.length,
      },
      root_prompt: rootJob ? rootPromptSnippet(rootJob, 40) : 'No root prompt',
      total_jobs: detailProgress?.total ?? sortedJobs.length,
      jobs: sortedJobs,
    };
  }

  function mergeChainSummary(base, extra) {
    if (!base) return extra;
    if (!extra) return base;

    const mergedJobs = Array.isArray(extra.jobs) && extra.jobs.length
      ? extra.jobs
      : Array.isArray(base.jobs) ? base.jobs : [];

    return {
      ...base,
      ...extra,
      profile: extra.profile || base.profile || '',
      created_at: extra.created_at || base.created_at || '',
      status: extra.status || base.status || 'pending',
      progress: extra.progress || base.progress || { completed: 0, total: mergedJobs.length },
      root_prompt: extra.root_prompt || base.root_prompt || 'No root prompt',
      total_jobs: extra.total_jobs || base.total_jobs || mergedJobs.length,
      jobs: mergedJobs,
    };
  }

  function normalizeChainList(result) {
    const items = Array.isArray(result) ? result : result?.chains || [];
    return items
      .map((item) => {
        if (typeof item === 'string') {
          return { id: item, status: 'pending', progress: { completed: 0, total: 0 }, jobs: [] };
        }

        if (!item || typeof item !== 'object') return null;
        const id = item.id || item.chain_id;
        if (!id) return null;

        return {
          id: String(id),
          profile: item.profile || '',
          created_at: item.created_at || item.createdAt || '',
          status: item.status || 'pending',
          progress: item.progress || { completed: 0, total: Array.isArray(item.jobs) ? item.jobs.length : 0 },
          root_prompt: item.root_prompt || item.prompt || '',
          total_jobs: item.total_jobs || item.progress?.total || (Array.isArray(item.jobs) ? item.jobs.length : 0),
          jobs: Array.isArray(item.jobs) && item.jobs.every((job) => job && typeof job === 'object')
            ? item.jobs
            : [],
        };
      })
      .filter(Boolean);
  }

  function groupJobsByChain(jobs) {
    const grouped = new Map();

    jobs.forEach((job) => {
      const chainId = String(job?.chain_id || '').trim();
      if (!chainId) return;
      if (!grouped.has(chainId)) grouped.set(chainId, []);
      grouped.get(chainId).push(job);
    });

    return Array.from(grouped.entries()).map(([chainId, chainJobs]) => buildChainSummary(chainId, chainJobs));
  }

  function sortChains(chains) {
    return [...chains].sort((a, b) => {
      const createdDiff = safeDateValue(b.created_at) - safeDateValue(a.created_at);
      if (createdDiff !== 0) return createdDiff;
      return String(a.id || '').localeCompare(String(b.id || ''));
    });
  }

  function syncSelectedSummary() {
    const summary = state.chains.find((chain) => chain.id === state.selectedChainId) || null;
    state.selectedSummary = summary;
    state.selectedJobs = Array.isArray(summary?.jobs) ? [...summary.jobs] : [];
  }

  async function loadChainList() {
    const requestId = ++state.listRequestId;
    state.loadingList = true;
    state.listError = '';
    renderPage();

    try {
      const [chainsResult, jobsResult] = await Promise.allSettled([
        API.chains.list(),
        API.jobs.list(),
      ]);

      let summaries = [];

      if (chainsResult.status === 'fulfilled') {
        summaries = normalizeChainList(chainsResult.value);
      }

      if (jobsResult.status === 'fulfilled') {
        const groupedSummaries = groupJobsByChain(normalizeJobList(jobsResult.value));
        const merged = new Map(groupedSummaries.map((summary) => [summary.id, summary]));

        summaries.forEach((summary) => {
          merged.set(summary.id, mergeChainSummary(merged.get(summary.id), summary));
        });
        summaries = Array.from(merged.values());
      }

      if (!summaries.length && chainsResult.status === 'rejected' && jobsResult.status === 'rejected') {
        throw jobsResult.reason || chainsResult.reason;
      }

      if (requestId !== state.listRequestId) return;

      state.chains = sortChains(summaries);

      if (!state.selectedChainId || !state.chains.some((chain) => chain.id === state.selectedChainId)) {
        state.selectedChainId = state.chains[0]?.id || null;
      }

      syncSelectedSummary();
      renderPage();

      if (state.selectedChainId) {
        await loadSelectedChain();
      }
    } catch (error) {
      if (requestId !== state.listRequestId) return;
      state.chains = [];
      state.selectedChainId = null;
      state.selectedSummary = null;
      state.selectedJobs = [];
      state.listError = error?.message || 'Failed to load chains.';
      renderPage();
    } finally {
      if (requestId === state.listRequestId) {
        state.loadingList = false;
        renderPage();
      }
    }
  }

  async function loadSelectedChain() {
    const chainId = state.selectedChainId;
    if (!chainId) {
      state.selectedSummary = null;
      state.selectedJobs = [];
      state.detailError = '';
      renderPage();
      return;
    }

    const requestId = ++state.detailRequestId;
    state.loadingDetail = true;
    state.detailError = '';
    syncSelectedSummary();
    renderPage();

    try {
      const [detailResult, jobsResult] = await Promise.allSettled([
        API.chains.get(chainId),
        API.jobs.list({ chain_id: chainId }),
      ]);

      const detail = detailResult.status === 'fulfilled' && detailResult.value && typeof detailResult.value === 'object'
        ? detailResult.value
        : {};

      let jobs = [];
      if (Array.isArray(detail.jobs) && detail.jobs.every((job) => job && typeof job === 'object' && job.id)) {
        jobs = detail.jobs;
      } else if (jobsResult.status === 'fulfilled') {
        jobs = normalizeJobList(jobsResult.value).filter((job) => String(job.chain_id || '') === chainId);
      }

      if (!jobs.length && detailResult.status === 'rejected' && jobsResult.status === 'rejected') {
        throw jobsResult.reason || detailResult.reason;
      }

      if (requestId !== state.detailRequestId) return;

      const summary = buildChainSummary(chainId, jobs, detail);
      state.selectedSummary = summary;
      state.selectedJobs = summary.jobs;
      state.chains = state.chains.map((chain) => (
        chain.id === chainId ? mergeChainSummary(chain, summary) : chain
      ));
      renderPage();
    } catch (error) {
      if (requestId !== state.detailRequestId) return;
      state.detailError = error?.message || 'Failed to load chain detail.';
      syncSelectedSummary();
      renderPage();
    } finally {
      if (requestId === state.detailRequestId) {
        state.loadingDetail = false;
        renderPage();
      }
    }
  }

  function buildNodeTooltip(job) {
    const lines = [
      `Type: ${getJobTypeLabel(job.type)}`,
      `Status: ${statusMeta(job.status).label}`,
      `Prompt: ${promptText(job) || 'No prompt provided'}`,
      `Created: ${exactDateLabel(job.created_at)}`,
      `Updated: ${exactDateLabel(job.updated_at)}`,
    ];

    if (job.completed_at) {
      lines.push(`Completed: ${exactDateLabel(job.completed_at)}`);
    }

    return lines.join('\n');
  }

  function buildTreeLayout(jobs) {
    if (!jobs.length) return null;

    const nodesById = new Map();
    jobs.forEach((job) => {
      nodesById.set(job.id, {
        job,
        children: [],
        depth: 0,
        centerX: 0,
        left: 0,
        top: 0,
      });
    });

    const roots = [];
    nodesById.forEach((node) => {
      const parent = node.job.parent_job_id ? nodesById.get(node.job.parent_job_id) : null;
      if (parent) {
        parent.children.push(node);
      } else {
        roots.push(node);
      }
    });

    const sortNodes = (items) => items.sort((a, b) => compareJobs(a.job, b.job));
    sortNodes(roots);
    nodesById.forEach((node) => sortNodes(node.children));

    let leafIndex = 0;
    let maxDepth = 0;

    function assign(node, depth) {
      node.depth = depth;
      maxDepth = Math.max(maxDepth, depth);

      if (!node.children.length) {
        node.centerX = (leafIndex * LEAF_STEP) + NODE_WIDTH / 2;
        leafIndex += 1;
        return;
      }

      node.children.forEach((child) => assign(child, depth + 1));
      node.centerX = node.children.reduce((sum, child) => sum + child.centerX, 0) / node.children.length;
    }

    roots.forEach((rootNode, index) => {
      if (index > 0 && leafIndex > 0) leafIndex += 1;
      assign(rootNode, 0);
    });

    const nodes = Array.from(nodesById.values());
    const minCenter = Math.min(...nodes.map((node) => node.centerX));
    const maxCenter = Math.max(...nodes.map((node) => node.centerX));

    nodes.forEach((node) => {
      node.left = CANVAS_PADDING + (node.centerX - minCenter) - (NODE_WIDTH / 2);
      node.top = CANVAS_PADDING + (node.depth * LEVEL_GAP);
    });

    const edges = [];
    nodes.forEach((node) => {
      node.children.forEach((child) => {
        const startX = node.left + (NODE_WIDTH / 2);
        const startY = node.top + NODE_HEIGHT;
        const endX = child.left + (NODE_WIDTH / 2);
        const endY = child.top;
        const midY = startY + ((endY - startY) / 2);
        edges.push(`M ${startX} ${startY} V ${midY} H ${endX} V ${endY}`);
      });
    });

    return {
      width: Math.max((maxCenter - minCenter) + NODE_WIDTH + (CANVAS_PADDING * 2), NODE_WIDTH + (CANVAS_PADDING * 2)),
      height: (maxDepth * LEVEL_GAP) + NODE_HEIGHT + (CANVAS_PADDING * 2),
      nodes,
      edges,
      roots: roots.length,
    };
  }

  function renderChainList() {
    if (state.loadingList && !state.chains.length) {
      return '<div class="loading-center"><div class="spinner spinner-lg"></div></div>';
    }

    if (state.listError && !state.chains.length) {
      return `
        <div class="empty-state" style="padding: 40px 16px;">
          <span class="material-icons">error_outline</span>
          <h3>Failed to load chains</h3>
          <p>${App.escapeHtml(state.listError)}</p>
        </div>
      `;
    }

    if (!state.chains.length) {
      return `
        <div class="empty-state" style="padding: 40px 16px;">
          <span class="material-icons">account_tree</span>
          <h3>No chains found</h3>
          <p>No jobs with a <code>chain_id</code> are available yet.</p>
        </div>
      `;
    }

    return `
      <div class="chain-tree-list">
        ${state.chains.map((chain) => {
          const selected = chain.id === state.selectedChainId;
          const status = statusMeta(chain.status);
          return `
            <button
              type="button"
              class="chain-tree-list-item ${selected ? 'selected' : ''}"
              data-chain-id="${escapeAttr(chain.id)}"
            >
              <div class="chain-tree-list-top">
                <div class="chain-tree-list-type">
                  <div class="job-type-icon t2v" aria-hidden="true">
                    <span class="material-icons">account_tree</span>
                  </div>
                  <div style="min-width: 0;">
                    <div class="chain-tree-list-title">Chain ${App.escapeHtml(App.truncate(chain.id, 18))}</div>
                    <div class="chain-tree-list-id">${App.escapeHtml(App.formatDate(chain.created_at))}</div>
                  </div>
                </div>
                <span class="chain-tree-status ${status.className}">${App.escapeHtml(status.label)}</span>
              </div>
              <div class="chain-tree-prompt">${App.escapeHtml(chain.root_prompt || 'No root prompt')}</div>
              <div class="chain-tree-list-meta">
                <span class="chain-tree-meta-item">
                  <span class="material-icons">schedule</span>
                  ${App.escapeHtml(exactDateLabel(chain.created_at))}
                </span>
                <span class="chain-tree-meta-item">
                  <span class="material-icons">task_alt</span>
                  ${App.escapeHtml(String(chain.progress?.completed ?? 0))}/${App.escapeHtml(String(chain.progress?.total ?? chain.total_jobs ?? 0))}
                </span>
              </div>
            </button>
          `;
        }).join('')}
      </div>
    `;
  }

  function renderSummaryCard() {
    const summary = state.selectedSummary;
    if (!summary) {
      return `
        <div class="card">
          <div class="empty-state" style="padding: 48px 20px;">
            <span class="material-icons">account_tree</span>
            <h3>Select a chain</h3>
            <p>Choose a chain from the left rail to inspect its dependency tree.</p>
          </div>
        </div>
      `;
    }

    const status = statusMeta(summary.status);

    return `
      <div class="card chain-tree-summary-card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Chain ${App.escapeHtml(App.truncate(summary.id, 24))}</h3>
            <p class="form-hint">Interactive dependency view for the selected chain.</p>
          </div>
          <div class="section-actions">
            <button
              type="button"
              class="btn btn-sm btn-outline"
              id="chain-tree-refresh-selected"
              ${state.loadingDetail ? 'disabled' : ''}
            >
              ${
                state.loadingDetail
                  ? '<span class="spinner"></span> Refreshing...'
                  : '<span class="material-icons" style="font-size:16px">refresh</span> Refresh chain'
              }
            </button>
          </div>
        </div>

        ${state.detailError ? `
          <div class="chain-tree-banner warn" style="margin-bottom: 16px;">
            <span class="material-icons">warning</span>
            <div>${App.escapeHtml(state.detailError)}</div>
          </div>
        ` : ''}

        <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
          <span class="chain-tree-status ${status.className}">${App.escapeHtml(status.label)}</span>
          <code>${App.escapeHtml(summary.id)}</code>
        </div>

        <div class="chain-tree-summary-grid">
          <div class="chain-tree-kv">
            <div class="chain-tree-kv-label">Created</div>
            <div class="chain-tree-kv-value">${App.escapeHtml(exactDateLabel(summary.created_at))}</div>
          </div>
          <div class="chain-tree-kv">
            <div class="chain-tree-kv-label">Profile</div>
            <div class="chain-tree-kv-value">${App.escapeHtml(summary.profile || '-')}</div>
          </div>
          <div class="chain-tree-kv">
            <div class="chain-tree-kv-label">Progress</div>
            <div class="chain-tree-kv-value">${App.escapeHtml(String(summary.progress?.completed ?? 0))}/${App.escapeHtml(String(summary.progress?.total ?? summary.total_jobs ?? 0))} completed</div>
          </div>
          <div class="chain-tree-kv">
            <div class="chain-tree-kv-label">Root Prompt</div>
            <div class="chain-tree-kv-value">${App.escapeHtml(summary.root_prompt || 'No root prompt')}</div>
          </div>
        </div>
      </div>
    `;
  }

  function renderTreeCanvas() {
    if (state.loadingDetail && !state.selectedJobs.length) {
      return `
        <div class="card chain-tree-canvas-card">
          <div class="loading-center"><div class="spinner spinner-lg"></div></div>
        </div>
      `;
    }

    if (!state.selectedSummary) {
      return '';
    }

    if (!state.selectedJobs.length) {
      return `
        <div class="card chain-tree-canvas-card">
          <div class="empty-state" style="padding: 48px 20px;">
            <span class="material-icons">device_hub</span>
            <h3>No jobs on this chain</h3>
            <p>This chain has no visualizable jobs yet.</p>
          </div>
        </div>
      `;
    }

    const layout = buildTreeLayout(state.selectedJobs);
    if (!layout) {
      return `
        <div class="card chain-tree-canvas-card">
          <div class="empty-state" style="padding: 48px 20px;">
            <span class="material-icons">device_hub</span>
            <h3>Tree unavailable</h3>
            <p>The current chain could not be laid out.</p>
          </div>
        </div>
      `;
    }

    return `
      <div class="card chain-tree-canvas-card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Tree View</h3>
            <p class="chain-tree-help">Click a node to open <code>#job-detail/&lt;id&gt;</code>. Hover for full prompt and timestamps.</p>
          </div>
          <div class="form-hint">${layout.roots > 1 ? `${layout.roots} root nodes detected` : `${state.selectedJobs.length} jobs`}</div>
        </div>

        <div class="chain-tree-canvas-scroll">
          <div class="chain-tree-canvas" style="width:${layout.width}px; height:${layout.height}px;">
            <svg class="chain-tree-svg" viewBox="0 0 ${layout.width} ${layout.height}" preserveAspectRatio="xMinYMin meet" aria-hidden="true">
              ${layout.edges.map((path) => `<path class="chain-tree-edge" d="${path}"></path>`).join('')}
            </svg>

            ${layout.nodes.map((node) => {
              const job = node.job;
              const theme = typeTheme(job.type);
              const status = statusMeta(job.status);
              const label = getJobTypeLabel(job.type);
              const tooltip = buildNodeTooltip(job);
              return `
                <button
                  type="button"
                  class="chain-tree-node"
                  data-job-id="${escapeAttr(job.id)}"
                  title="${escapeAttr(tooltip)}"
                  style="left:${node.left}px; top:${node.top}px; --node-accent:${theme.color}; --node-accent-rgb:${theme.rgb};"
                >
                  <div class="chain-tree-node-top">
                    <div class="chain-tree-node-type">
                      <div class="job-type-icon ${theme.className} chain-tree-node-icon" aria-hidden="true">
                        <span class="material-icons">${App.escapeHtml(App.jobTypeIcon(job.type))}</span>
                      </div>
                      <div style="min-width: 0;">
                        <div class="chain-tree-node-title">${App.escapeHtml(label)}</div>
                        <div class="chain-tree-node-subtitle">${App.escapeHtml(App.formatDate(job.created_at))}</div>
                      </div>
                    </div>
                    <span class="chain-tree-status ${status.className}">${App.escapeHtml(status.label)}</span>
                  </div>

                  <div class="chain-tree-prompt" style="margin:0;">${App.escapeHtml(rootPromptSnippet(job, 40))}</div>

                  <div class="chain-tree-node-footer">
                    <div class="chain-tree-node-badges">
                      <span class="chain-tree-id-badge">${App.escapeHtml(App.truncate(job.id, 10))}</span>
                      <span class="chain-tree-level-badge">L${App.escapeHtml(String(job.job_level || '?'))}</span>
                    </div>
                  </div>
                </button>
              `;
            }).join('')}
          </div>
        </div>
      </div>
    `;
  }

  function renderPage() {
    if (!root) return;

    root.innerHTML = `
      <div class="chain-tree-shell">
        <aside class="card chain-tree-rail">
          <div class="section-header">
            <div>
              <h3 class="section-title">Chains</h3>
              <p class="form-hint">${state.chains.length} chain${state.chains.length === 1 ? '' : 's'} detected</p>
            </div>
            <button
              type="button"
              class="btn btn-sm btn-outline"
              id="chain-tree-refresh-list"
              ${state.loadingList ? 'disabled' : ''}
            >
              ${
                state.loadingList
                  ? '<span class="spinner"></span> Refreshing...'
                  : '<span class="material-icons" style="font-size:16px">refresh</span> Refresh'
              }
            </button>
          </div>

          ${state.listError && state.chains.length ? `
            <div class="chain-tree-banner warn" style="margin-bottom: 16px;">
              <span class="material-icons">warning</span>
              <div>${App.escapeHtml(state.listError)}</div>
            </div>
          ` : ''}

          ${renderChainList()}
        </aside>

        <section class="chain-tree-main">
          ${renderSummaryCard()}
          ${renderTreeCanvas()}
        </section>
      </div>
    `;
  }

  async function openJobDetailModal(jobId) {
    try {
      const job = await API.jobs.get(jobId);
      const outputLinks = (Array.isArray(job.output_files) ? job.output_files : [])
        .map((file) => {
          const normalized = String(file).replace(/\\/g, '/').replace(/^downloads\//i, '');
          const url = `/downloads/${encodeURI(normalized)}`;
          const name = normalized.split('/').pop() || normalized;
          return `
            <a class="btn btn-sm btn-outline" href="${escapeAttr(url)}" target="_blank" rel="noopener">
              <span class="material-icons" style="font-size:16px">open_in_new</span> ${App.escapeHtml(name)}
            </a>
          `;
        })
        .join('');

      App.openModal(
        `Job ${App.truncate(jobId, 12)}`,
        `
          <div style="display:grid; gap:16px;">
            ${outputLinks ? `
              <div>
                <div class="detail-label" style="margin-bottom:8px;">Outputs</div>
                <div style="display:flex; flex-wrap:wrap; gap:8px;">${outputLinks}</div>
              </div>
            ` : ''}
            <pre style="margin:0; padding:16px; border-radius:12px; background:#0a0a0c; border:1px solid var(--border); color:var(--text-secondary); font-size:12px; line-height:1.55; white-space:pre-wrap; overflow-wrap:anywhere;">${App.escapeHtml(JSON.stringify(job, null, 2))}</pre>
          </div>
        `
      );
    } catch (error) {
      App.toast(`Failed to load job: ${error.message}`, 'error');
    }
  }

  function installJobDetailBridge() {
    if (window.__chainTreeJobDetailBridgeInstalled) return;
    window.__chainTreeJobDetailBridgeInstalled = true;

    window.addEventListener('hashchange', () => {
      const hash = location.hash || '#home';
      if (!hash.startsWith('#job-detail/')) {
        lastNonDetailHash = hash;
        return;
      }

      const fallbackHash =
        lastNonDetailHash && !lastNonDetailHash.startsWith('#job-detail/')
          ? lastNonDetailHash
          : '#chain-tree';
      const jobId = decodeURIComponent(hash.slice('#job-detail/'.length));

      history.replaceState(null, '', fallbackHash);
      if (jobId) {
        void openJobDetailModal(jobId);
      }
    });
  }

  function selectChain(chainId) {
    if (!chainId || chainId === state.selectedChainId) return;
    state.selectedChainId = chainId;
    syncSelectedSummary();
    renderPage();
    void loadSelectedChain();
  }

  async function handleClick(event) {
    const chainButton = event.target.closest('[data-chain-id]');
    if (chainButton) {
      selectChain(chainButton.dataset.chainId);
      return;
    }

    const nodeButton = event.target.closest('[data-job-id]');
    if (nodeButton) {
      const jobId = nodeButton.dataset.jobId;
      if (jobId) {
        window.location.hash = `job-detail/${encodeURIComponent(jobId)}`;
      }
      return;
    }

    if (event.target.closest('#chain-tree-refresh-list')) {
      await loadChainList();
      return;
    }

    if (event.target.closest('#chain-tree-refresh-selected')) {
      await loadSelectedChain();
    }
  }

  installJobDetailBridge();

  const ChainTreePage = {
    name: 'chain-tree',
    title: 'Chain Tree',
    icon: 'account_tree',

    render() {
      return `<div id="${PAGE_ROOT_ID}"></div>`;
    },

    mount() {
      ensureStyles();
      installJobDetailBridge();

      root = document.getElementById(PAGE_ROOT_ID);
      if (!root) return;

      handlers = {
        click: (event) => { void handleClick(event); },
      };

      root.addEventListener('click', handlers.click);

      syncSelectedSummary();
      renderPage();
      void loadChainList();
    },

    destroy() {
      if (root && handlers) {
        root.removeEventListener('click', handlers.click);
      }
      root = null;
      handlers = null;
    },
  };

  App.register(ChainTreePage);
})();
