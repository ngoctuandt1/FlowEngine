/**
 * Chain Tree Page
 * Friendlier multi-level chain visualization with collapsible subtrees.
 */
(() => {
  const PAGE_ROOT_ID = 'chain-tree-page';
  const PAGE_STYLE_ID = 'chain-tree-page-styles';
  const COLLAPSE_STORAGE_PREFIX = 'flowengine:chain-tree:collapsed:';
  const HIDE_ORPHANS_STORAGE_KEY = 'chainTree.hideOrphans';
  const VIDEO_EXTENSIONS = new Set(['mp4', 'webm', 'mov', 'm4v']);
  const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp']);
  const ACTIVE_STATUSES = new Set(['pending', 'claimed', 'running']);
  const BACKEND_GAP_WARNED = new Set();

  let root = null;
  let handlers = null;

  const state = {
    chains: [],
    selectedChainId: null,
    selectedSummary: null,
    selectedJobs: [],
    collapsedByChain: {},
    errorOpenByChain: {},
    hideOrphans: readHideOrphansState(),
    deletingJobIds: new Set(),
    deletingCancelled: false,
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

  function debugBadgesEnabled() {
    try {
      return localStorage.getItem('FLOW_DEBUG_BADGES') === '1';
    } catch (_) {
      return false;
    }
  }

  function readHideOrphansState() {
    try {
      return localStorage.getItem(HIDE_ORPHANS_STORAGE_KEY) === '1';
    } catch (_) {
      return false;
    }
  }

  function persistHideOrphansState() {
    try {
      localStorage.setItem(HIDE_ORPHANS_STORAGE_KEY, state.hideOrphans ? '1' : '0');
    } catch (_) {
      // Ignore local storage failures.
    }
  }

  function warnBackendGap({ field, jobId, fallbackUsed }) {
    const key = `${field}|${jobId || ''}|${fallbackUsed}`;
    if (BACKEND_GAP_WARNED.has(key)) return;
    BACKEND_GAP_WARNED.add(key);
    console.warn('[backend-gap]', {
      page: 'chain-tree',
      field,
      jobId: jobId || '',
      fallbackUsed,
    });
  }

  function renderDebugBadges(items, className = 'chain-tree-stat-chip') {
    if (!debugBadgesEnabled() || !Array.isArray(items) || !items.length) return '';
    return items.map((item) => `
      <span
        class="${className}"
        title="${escapeAttr(`${item.field} -> ${item.fallbackUsed}`)}"
        style="opacity:0.65;"
      >
        ${App.escapeHtml(`gap:${item.field}`)}
      </span>
    `).join('');
  }

  function ensureStyles() {
    if (document.getElementById(PAGE_STYLE_ID)) return;

    const style = document.createElement('style');
    style.id = PAGE_STYLE_ID;
    style.textContent = `
      .chain-tree-shell {
        display: grid;
        grid-template-columns: minmax(300px, 31%) minmax(0, 1fr);
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
        background: rgba(255, 255, 255, 0.015);
        border: 1px solid var(--border);
        border-radius: 14px;
        cursor: pointer;
        text-align: left;
        transition: background var(--transition), border-color var(--transition), transform var(--transition), box-shadow var(--transition);
      }

      .chain-tree-list-item:hover {
        background: var(--bg-card-hover);
        border-color: var(--border-light);
        transform: translateY(-1px);
      }

      .chain-tree-list-item.selected {
        background:
          radial-gradient(circle at top right, rgba(124, 92, 255, 0.12), transparent 16rem),
          rgba(124, 92, 255, 0.06);
        border-color: var(--accent-border);
        box-shadow: inset 0 0 0 1px rgba(124, 92, 255, 0.18);
      }

      .chain-tree-list-top {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 12px;
      }

      .chain-tree-list-title-wrap {
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

      .chain-tree-list-prompt {
        margin: 12px 0 10px;
        color: var(--text-secondary);
        font-size: 13px;
        line-height: 1.5;
        overflow-wrap: anywhere;
      }

      .chain-tree-list-chip-row,
      .chain-tree-summary-chip-row,
      .chain-tree-node-chip-row {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
      }

      .chain-tree-stat-chip,
      .chain-tree-mini-chip,
      .chain-tree-chain-chip {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        min-height: 24px;
        padding: 0 9px;
        color: var(--text-secondary);
        background: rgba(10, 10, 12, 0.58);
        border: 1px solid var(--border);
        border-radius: 999px;
        font-size: 11px;
        font-weight: 600;
        line-height: 1;
      }

      .chain-tree-stat-chip.ok {
        color: #bbf7d0;
        border-color: rgba(34, 197, 94, 0.32);
      }

      .chain-tree-stat-chip.fail {
        color: #fecaca;
        border-color: rgba(239, 68, 68, 0.32);
      }

      .chain-tree-stat-chip.active {
        color: #fde68a;
        border-color: rgba(234, 179, 8, 0.32);
      }

      .chain-tree-stat-chip.mono,
      .chain-tree-mini-chip.mono,
      .chain-tree-chain-chip {
        font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      }

      .chain-tree-main {
        display: grid;
        gap: 16px;
        min-width: 0;
      }

      .chain-tree-summary-card,
      .chain-tree-tree-card {
        min-width: 0;
      }

      .chain-tree-summary-hero {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 16px;
        margin-bottom: 16px;
      }

      .chain-tree-summary-eyebrow {
        color: var(--text-muted);
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
      }

      .chain-tree-summary-copy {
        margin-top: 8px;
        color: var(--text-secondary);
        font-size: 14px;
        line-height: 1.55;
      }

      .chain-tree-summary-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
        gap: 12px;
        margin-top: 16px;
      }

      .chain-tree-summary-stat {
        display: grid;
        gap: 6px;
        padding: 14px;
        background: rgba(255, 255, 255, 0.02);
        border: 1px solid var(--border);
        border-radius: 12px;
      }

      .chain-tree-summary-label {
        color: var(--text-muted);
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.06em;
        text-transform: uppercase;
      }

      .chain-tree-summary-value {
        color: var(--text-primary);
        font-size: 16px;
        font-weight: 700;
        overflow-wrap: anywhere;
      }

      .chain-tree-summary-subvalue {
        color: var(--text-secondary);
        font-size: 12px;
        overflow-wrap: anywhere;
      }

      .chain-tree-banner {
        display: flex;
        align-items: flex-start;
        gap: 10px;
        padding: 12px 14px;
        border: 1px solid var(--border);
        border-radius: 12px;
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

      .chain-tree-tree-header {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 16px;
        margin-bottom: 16px;
      }

      .chain-tree-toolbar {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        margin-bottom: 14px;
      }

      .chain-tree-toggle {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        color: var(--text-secondary);
        font-size: 13px;
        cursor: pointer;
        user-select: none;
      }

      .chain-tree-toggle input {
        accent-color: var(--color-warn, var(--warning));
      }

      .chain-tree-help {
        margin-top: 6px;
        color: var(--text-secondary);
        font-size: 13px;
      }

      .chain-tree-tree-scroll {
        overflow: auto;
        padding-bottom: 6px;
        border-radius: 16px;
      }

      .chain-tree-tree-stage {
        min-width: 100%;
        padding: 4px 4px 8px;
        background:
          radial-gradient(circle at top left, rgba(124, 92, 255, 0.12), transparent 24rem),
          linear-gradient(180deg, rgba(255, 255, 255, 0.015), rgba(255, 255, 255, 0)),
          rgba(6, 6, 8, 0.42);
        border: 1px solid rgba(255, 255, 255, 0.04);
        border-radius: 16px;
      }

      .chain-tree-root-forest {
        display: inline-flex;
        align-items: flex-start;
        gap: 44px;
        min-width: 100%;
        padding: 12px 8px 16px;
      }

      .chain-tree-root-group {
        min-width: fit-content;
      }

      .chain-tree-tree,
      .chain-tree-tree ul {
        display: flex;
        justify-content: center;
        gap: 18px;
        margin: 0;
        padding-left: 0;
        list-style: none;
      }

      .chain-tree-tree {
        align-items: flex-start;
      }

      .chain-tree-tree ul {
        position: relative;
        padding-top: 28px;
      }

      .chain-tree-tree ul::before {
        content: '';
        position: absolute;
        top: 0;
        left: 50%;
        width: 2px;
        height: 28px;
        background: rgba(161, 161, 170, 0.32);
        transform: translateX(-50%);
      }

      .chain-tree-branch {
        position: relative;
        display: flex;
        flex-direction: column;
        align-items: center;
        padding: 28px 12px 0;
        list-style: none;
      }

      .chain-tree-branch::before,
      .chain-tree-branch::after {
        content: '';
        position: absolute;
        top: 0;
        width: 50%;
        height: 28px;
        border-top: 2px solid rgba(161, 161, 170, 0.32);
      }

      .chain-tree-branch::before {
        right: 50%;
      }

      .chain-tree-branch::after {
        left: 50%;
        border-left: 2px solid rgba(161, 161, 170, 0.32);
      }

      .chain-tree-branch:only-child {
        padding-top: 0;
      }

      .chain-tree-branch:only-child::before,
      .chain-tree-branch:only-child::after {
        display: none;
      }

      .chain-tree-branch:first-child::before,
      .chain-tree-branch:last-child::after {
        border: 0;
      }

      .chain-tree-branch:last-child::before {
        border-right: 2px solid rgba(161, 161, 170, 0.32);
        border-radius: 0 16px 0 0;
      }

      .chain-tree-branch:first-child::after {
        border-radius: 16px 0 0 0;
      }

      .chain-tree-node {
        position: relative;
        width: 292px;
        color: var(--text-primary);
        background:
          linear-gradient(180deg, rgba(var(--node-accent-rgb), 0.14), rgba(15, 15, 18, 0.96) 24%),
          var(--bg-card);
        border: 1px solid rgba(var(--node-accent-rgb), 0.26);
        border-left: 4px solid var(--node-accent);
        border-radius: 18px;
        box-shadow: 0 18px 36px rgba(0, 0, 0, 0.26);
        transition: transform var(--transition), box-shadow var(--transition), border-color var(--transition);
      }

      .chain-tree-node:hover {
        transform: translateY(-2px);
        border-color: rgba(var(--node-accent-rgb), 0.42);
        box-shadow: 0 24px 48px rgba(0, 0, 0, 0.34);
      }

      .chain-tree-node.root {
        width: 344px;
        border-left-width: 5px;
        box-shadow: 0 22px 44px rgba(0, 0, 0, 0.32);
      }

      .chain-tree-node.root .chain-tree-node-link {
        min-height: 244px;
      }

      .chain-tree-node.failed {
        border-color: rgba(239, 68, 68, 0.44);
        border-left-color: #ef4444;
      }

      .chain-tree-node.cycle {
        box-shadow:
          0 0 0 1px rgba(239, 68, 68, 0.22),
          0 18px 36px rgba(0, 0, 0, 0.26);
      }

      .chain-tree-node.is-active::after {
        content: '';
        position: absolute;
        inset: -1px;
        border-radius: inherit;
        border: 1px solid rgba(var(--node-accent-rgb), 0.18);
        animation: chain-tree-node-pulse 2.4s ease-in-out infinite;
        pointer-events: none;
      }

      @keyframes chain-tree-node-pulse {
        0%, 100% {
          opacity: 0.38;
          transform: scale(0.992);
        }
        50% {
          opacity: 0.82;
          transform: scale(1);
        }
      }

      .chain-tree-node-topbar {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 12px;
        padding: 12px 12px 0;
      }

      .chain-tree-node-badges {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
      }

      .chain-tree-root-badge,
      .chain-tree-level-badge {
        display: inline-flex;
        align-items: center;
        height: 24px;
        padding: 0 9px;
        border-radius: 999px;
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.06em;
        text-transform: uppercase;
      }

      .chain-tree-root-badge {
        color: #fef3c7;
        background: rgba(245, 158, 11, 0.16);
        border: 1px solid rgba(245, 158, 11, 0.36);
      }

      .chain-tree-level-badge {
        color: var(--text-muted);
        background: rgba(10, 10, 12, 0.52);
        border: 1px solid var(--border);
      }

      .chain-tree-status {
        display: inline-flex;
        align-items: center;
        height: 24px;
        padding: 0 9px;
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

      .chain-tree-status.orphan {
        color: var(--color-warn, var(--warning));
        background: color-mix(in srgb, var(--color-warn, var(--warning)) 18%, transparent);
        border-color: color-mix(in srgb, var(--color-err, var(--error)) 42%, transparent);
        text-transform: none;
      }

      .chain-tree-node-actions {
        display: inline-flex;
        align-items: center;
        gap: 6px;
      }

      .chain-tree-icon-btn.delete {
        color: var(--color-err, var(--error));
      }

      .chain-tree-icon-btn.delete:hover:not(:disabled) {
        border-color: color-mix(in srgb, var(--color-err, var(--error)) 45%, transparent);
        background: color-mix(in srgb, var(--color-err, var(--error)) 12%, transparent);
      }

      .chain-tree-icon-btn,
      .chain-tree-inline-link {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 6px;
        color: var(--text-secondary);
        background: rgba(10, 10, 12, 0.58);
        border: 1px solid var(--border);
        border-radius: 999px;
        cursor: pointer;
        transition: background var(--transition), border-color var(--transition), color var(--transition);
      }

      .chain-tree-icon-btn:hover,
      .chain-tree-inline-link:hover {
        color: var(--text-primary);
        background: rgba(255, 255, 255, 0.06);
        border-color: var(--border-light);
      }

      .chain-tree-icon-btn {
        width: 32px;
        height: 32px;
        padding: 0;
      }

      .chain-tree-icon-btn:disabled,
      .chain-tree-icon-btn:disabled:hover {
        cursor: not-allowed;
        opacity: 0.55;
      }

      .chain-tree-inline-link {
        min-height: 28px;
        padding: 0 10px;
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-transform: uppercase;
      }

      .chain-tree-node-link {
        display: grid;
        gap: 12px;
        width: 100%;
        min-height: 218px;
        padding: 12px;
        color: inherit;
        background: transparent;
        border: 0;
        cursor: pointer;
        text-align: left;
      }

      .chain-tree-node-main {
        display: grid;
        grid-template-columns: 112px minmax(0, 1fr);
        gap: 12px;
        align-items: start;
      }

      .chain-tree-node.root .chain-tree-node-main {
        grid-template-columns: 134px minmax(0, 1fr);
      }

      .chain-tree-node-thumb-media {
        width: 112px;
        border-radius: 12px;
        overflow: hidden;
        border: 1px solid rgba(255, 255, 255, 0.06);
        box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.02);
      }

      .chain-tree-node.root .chain-tree-node-thumb-media {
        width: 134px;
      }

      .chain-tree-node-thumb-media img {
        position: absolute;
        inset: 0;
        width: 100%;
        height: 100%;
        object-fit: cover;
        background: #000;
      }

      .chain-tree-node-thumb-placeholder {
        display: grid;
        place-items: center;
      }

      .chain-tree-node-thumb-placeholder .material-icons {
        color: rgba(255, 255, 255, 0.48);
        font-size: 30px;
      }

      .chain-tree-node-thumb-media.tile-thumb--broken {
        display: grid;
        place-items: center;
        background:
          linear-gradient(135deg, rgba(239, 68, 68, 0.16), rgba(12, 12, 14, 1)),
          #0a0a0c;
      }

      .chain-tree-node-thumb-media.tile-thumb--broken::after {
        content: 'broken_image';
        font-family: 'Material Icons';
        font-size: 28px;
        color: rgba(255, 255, 255, 0.5);
      }

      .chain-tree-node-copy {
        min-width: 0;
        display: grid;
        gap: 10px;
      }

      .chain-tree-node-title-row {
        display: flex;
        align-items: flex-start;
        gap: 10px;
        min-width: 0;
      }

      .chain-tree-node-type-icon {
        flex: 0 0 34px;
        width: 34px;
        height: 34px;
      }

      .chain-tree-node-title-block {
        min-width: 0;
      }

      .chain-tree-node-title {
        color: var(--text-primary);
        font-size: 15px;
        font-weight: 700;
        line-height: 1.28;
      }

      .chain-tree-node-timestamp {
        margin-top: 3px;
        color: var(--text-muted);
        font-size: 12px;
      }

      .chain-tree-node-snippet {
        color: var(--text-secondary);
        font-size: 13px;
        line-height: 1.5;
        overflow-wrap: anywhere;
      }

      .chain-tree-node-id {
        color: var(--text-muted);
        font-size: 11px;
        font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      }

      .chain-tree-node-bottom {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        padding: 0 12px 12px;
      }

      .chain-tree-node-hint {
        color: var(--text-muted);
        font-size: 11px;
        line-height: 1.4;
      }

      .chain-tree-node-error,
      .chain-tree-node-warning {
        margin: 0 12px 12px;
        padding: 10px 12px;
        border-radius: 12px;
        font-size: 12px;
        line-height: 1.55;
      }

      .chain-tree-node-error {
        color: #fecaca;
        background: rgba(239, 68, 68, 0.12);
        border: 1px solid rgba(239, 68, 68, 0.28);
      }

      .chain-tree-node-warning {
        color: #fde68a;
        background: rgba(234, 179, 8, 0.10);
        border: 1px solid rgba(234, 179, 8, 0.26);
      }

      @media (max-width: 1160px) {
        .chain-tree-shell {
          grid-template-columns: 1fr;
        }

        .chain-tree-rail {
          position: static;
          max-height: none;
        }
      }

      @media (max-width: 760px) {
        .chain-tree-summary-hero,
        .chain-tree-tree-header {
          flex-direction: column;
        }

        .chain-tree-root-forest {
          gap: 24px;
        }

        .chain-tree-node {
          width: 272px;
        }

        .chain-tree-node.root {
          width: 304px;
        }

        .chain-tree-node-main {
          grid-template-columns: 96px minmax(0, 1fr);
        }

        .chain-tree-node.root .chain-tree-node-main {
          grid-template-columns: 114px minmax(0, 1fr);
        }

        .chain-tree-node-thumb-media {
          width: 96px;
        }

        .chain-tree-node.root .chain-tree-node-thumb-media {
          width: 114px;
        }
      }
    `;
    document.head.appendChild(style);
  }

  function safeDateValue(value) {
    if (!value) return 0;
    const time = new Date(value).getTime();
    return Number.isFinite(time) ? time : 0;
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
      second: '2-digit',
    });
  }

  function relativeDateLabel(value) {
    if (!value) return '-';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return App.formatDate(value);
  }

  function getJobTypeLabel(type) {
    const meta = typeof CONST?.typeMeta === 'function' ? CONST.typeMeta(type) : null;
    return meta?.label || String(type || 'Unknown').replace(/-/g, ' ');
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

  function parentFailedReason(error) {
    const text = String(error || '').trim();
    if (!text.startsWith('parent_failed:')) return '';
    return text.slice('parent_failed:'.length).trim() || 'Parent job failed.';
  }

  function isCancelledJob(job) {
    return String(job?.status || '') === 'cancelled';
  }

  function isParentFailedCancellation(job) {
    return isCancelledJob(job) && Boolean(parentFailedReason(job?.error));
  }

  function nodeStatusMeta(job) {
    if (isParentFailedCancellation(job)) {
      return { label: 'Orphan (parent failed)', className: 'orphan' };
    }
    return statusMeta(job?.status);
  }

  function isActiveStatus(status) {
    return ACTIVE_STATUSES.has(String(status || 'pending'));
  }

  function compareJobs(a, b) {
    const levelDiff = (Number(a?.job_level) || 0) - (Number(b?.job_level) || 0);
    if (levelDiff !== 0) return levelDiff;

    const createdDiff = safeDateValue(a?.created_at) - safeDateValue(b?.created_at);
    if (createdDiff !== 0) return createdDiff;

    return String(a?.id || '').localeCompare(String(b?.id || ''));
  }

  function compareJobsByCreatedAt(a, b) {
    const createdDiff = safeDateValue(a?.created_at) - safeDateValue(b?.created_at);
    if (createdDiff !== 0) return createdDiff;
    return compareJobs(a, b);
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

  function shortId(value, maxLen = 14) {
    return App.truncate(String(value || ''), maxLen);
  }

  function rootPromptSnippet(job, maxLen = 64) {
    return App.truncate(promptText(job) || 'No prompt provided', maxLen);
  }

  function computeChainStatus(statuses) {
    if (!statuses.length) return 'pending';
    if (statuses.includes('failed')) return 'failed';
    if (statuses.some((status) => status === 'running' || status === 'claimed')) return 'running';
    if (statuses.includes('pending')) return 'pending';
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

  function pickEarliestJob(jobs) {
    return [...jobs].sort(compareJobsByCreatedAt)[0] || null;
  }

  function latestTimestampValue(jobs) {
    return jobs.reduce((latest, job) => {
      const candidate = job?.updated_at || job?.completed_at || job?.claimed_at || job?.created_at || '';
      return safeDateValue(candidate) > safeDateValue(latest) ? candidate : latest;
    }, '');
  }

  function computeSummaryStats(jobs) {
    const stats = {
      total: jobs.length,
      completed: 0,
      failed: 0,
      pending: 0,
      cancelled: 0,
    };

    jobs.forEach((job) => {
      const status = String(job?.status || 'pending');
      if (status === 'completed') {
        stats.completed += 1;
        return;
      }
      if (status === 'failed') {
        stats.failed += 1;
        return;
      }
      if (status === 'cancelled') {
        stats.cancelled += 1;
        return;
      }
      stats.pending += 1;
    });

    return stats;
  }

  function buildChainSummary(chainId, jobs, detail = {}) {
    const sortedJobs = [...jobs].sort(compareJobs);
    const rootJob = pickRootJob(sortedJobs);
    const earliestJob = pickEarliestJob(sortedJobs) || rootJob;
    const statuses = sortedJobs.map((job) => String(job.status || 'pending'));
    const detailProgress = detail?.progress || null;
    const stats = computeSummaryStats(sortedJobs);

    return {
      id: String(detail?.id || detail?.chain_id || chainId || ''),
      profile:
        detail?.profile ||
        rootJob?.profile ||
        earliestJob?.profile ||
        sortedJobs.find((job) => job.profile)?.profile ||
        '',
      created_at:
        detail?.created_at ||
        earliestJob?.created_at ||
        rootJob?.created_at ||
        sortedJobs[0]?.created_at ||
        '',
      updated_at:
        detail?.updated_at ||
        latestTimestampValue(sortedJobs) ||
        detail?.created_at ||
        '',
      status: detail?.status || computeChainStatus(statuses),
      progress: {
        completed:
          detailProgress?.completed ??
          statuses.filter((status) => status === 'completed').length,
        total: detailProgress?.total ?? sortedJobs.length,
      },
      root_prompt: detail?.root_prompt || rootPromptSnippet(earliestJob || rootJob, 64),
      total_jobs: detailProgress?.total ?? sortedJobs.length,
      stats,
      jobs: sortedJobs,
      debugBadges: Array.isArray(detail?.debugBadges) ? detail.debugBadges : [],
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
      updated_at: extra.updated_at || base.updated_at || '',
      status: extra.status || base.status || 'pending',
      progress: extra.progress || base.progress || { completed: 0, total: mergedJobs.length },
      root_prompt: extra.root_prompt || base.root_prompt || 'No root prompt',
      total_jobs: extra.total_jobs || base.total_jobs || mergedJobs.length,
      stats: extra.stats || base.stats || computeSummaryStats(mergedJobs),
      jobs: mergedJobs,
      debugBadges: Array.isArray(extra.debugBadges) && extra.debugBadges.length
        ? extra.debugBadges
        : Array.isArray(base.debugBadges) ? base.debugBadges : [],
    };
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
      const updatedDiff = safeDateValue(b.updated_at || b.created_at) - safeDateValue(a.updated_at || a.created_at);
      if (updatedDiff !== 0) return updatedDiff;
      return String(a.id || '').localeCompare(String(b.id || ''));
    });
  }

  function mediaTypeFromFile(file) {
    const normalized = String(file || '').replace(/\\/g, '/');
    const filename = normalized.split('/').pop() || normalized;
    const extension = filename.includes('.') ? filename.split('.').pop().toLowerCase() : '';
    if (VIDEO_EXTENSIONS.has(extension)) return 'video';
    if (IMAGE_EXTENSIONS.has(extension)) return 'image';
    return null;
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

  function thumbnailMedia(job) {
    const files = renderableFiles(job);
    const image = files.find((file) => file.kind === 'image');
    if (image) return image;
    return null;
  }

  function collapseStorageKey(chainId) {
    return `${COLLAPSE_STORAGE_PREFIX}${chainId}`;
  }

  function readCollapsedState(chainId) {
    try {
      const raw = sessionStorage.getItem(collapseStorageKey(chainId));
      if (!raw) return new Set();
      const parsed = JSON.parse(raw);
      return new Set(Array.isArray(parsed) ? parsed.map((item) => String(item || '').trim()).filter(Boolean) : []);
    } catch (_) {
      return new Set();
    }
  }

  function persistCollapsedState(chainId) {
    if (!chainId) return;
    try {
      const ids = [...(state.collapsedByChain[chainId] || new Set())];
      sessionStorage.setItem(collapseStorageKey(chainId), JSON.stringify(ids));
    } catch (_) {
      // Ignore session storage failures.
    }
  }

  function ensureChainUiState(chainId) {
    if (!chainId) return;
    if (!(state.collapsedByChain[chainId] instanceof Set)) {
      state.collapsedByChain[chainId] = readCollapsedState(chainId);
    }
    if (!(state.errorOpenByChain[chainId] instanceof Set)) {
      state.errorOpenByChain[chainId] = new Set();
    }
  }

  function pruneChainUiState(chainId, jobs) {
    if (!chainId) return;
    ensureChainUiState(chainId);

    const validIds = new Set(jobs.map((job) => String(job.id || '')));
    state.collapsedByChain[chainId] = new Set(
      [...state.collapsedByChain[chainId]].filter((id) => validIds.has(id))
    );
    state.errorOpenByChain[chainId] = new Set(
      [...state.errorOpenByChain[chainId]].filter((id) => validIds.has(id))
    );
    persistCollapsedState(chainId);
  }

  function getCollapsedSet(chainId) {
    ensureChainUiState(chainId);
    return state.collapsedByChain[chainId] || new Set();
  }

  function getErrorOpenSet(chainId) {
    ensureChainUiState(chainId);
    return state.errorOpenByChain[chainId] || new Set();
  }

  function isNodeCollapsed(chainId, nodeId) {
    return getCollapsedSet(chainId).has(String(nodeId || ''));
  }

  function isErrorOpen(chainId, nodeId) {
    return getErrorOpenSet(chainId).has(String(nodeId || ''));
  }

  function cancelledDescendants(model) {
    if (!model) return [];
    return Array.from(model.nodesById.values())
      .map((node) => node.job)
      .filter((job) => isCancelledJob(job) && (job.parent_job_id || Number(job.job_level) > 1))
      .sort((a, b) => (Number(b?.job_level) || 0) - (Number(a?.job_level) || 0));
  }

  async function deleteJob(jobId) {
    const id = String(jobId || '').trim();
    if (!id) return;
    if (!confirm(`Delete job ${id}? This cannot be undone.`)) return;

    state.deletingJobIds.add(id);
    renderPage();

    try {
      await API.jobs.delete(id);
      App.toast(`Deleted job ${shortId(id, 18)}`, 'success');
      await loadChainList();
    } catch (error) {
      App.toast(error?.message || 'Failed to delete job.', 'error');
    } finally {
      state.deletingJobIds.delete(id);
      renderPage();
    }
  }

  async function deleteAllCancelledInChain() {
    const chainId = state.selectedChainId;
    if (!chainId || !state.selectedJobs.length) return;

    const jobs = cancelledDescendants(buildTreeModel(state.selectedJobs));
    if (!jobs.length) {
      App.toast('No cancelled jobs to delete.', 'info');
      return;
    }

    const countLabel = jobs.length === 1 ? '1 cancelled job' : `${jobs.length} cancelled jobs`;
    if (!confirm(`Delete ${countLabel} in this chain? This cannot be undone.`)) return;

    state.deletingCancelled = true;
    jobs.forEach((job) => state.deletingJobIds.add(String(job.id || '')));
    renderPage();

    const failed = [];
    for (const job of jobs) {
      const id = String(job.id || '').trim();
      if (!id) continue;
      try {
        await API.jobs.delete(id);
      } catch (error) {
        failed.push({ id, error });
      }
    }

    try {
      await loadChainList();
    } finally {
      state.deletingCancelled = false;
      jobs.forEach((job) => state.deletingJobIds.delete(String(job.id || '')));
      renderPage();
    }

    if (failed.length) {
      App.toast(`Deleted ${jobs.length - failed.length}/${jobs.length}; ${failed.length} failed.`, 'warning');
    } else {
      App.toast(`Deleted ${countLabel}.`, 'success');
    }
  }

  function syncSelectedSummary() {
    const summary = state.chains.find((chain) => chain.id === state.selectedChainId) || null;
    state.selectedSummary = summary;
    state.selectedJobs = Array.isArray(summary?.jobs) ? [...summary.jobs] : [];
    if (state.selectedChainId) {
      ensureChainUiState(state.selectedChainId);
      pruneChainUiState(state.selectedChainId, state.selectedJobs);
    }
  }

  async function loadChainList() {
    const requestId = ++state.listRequestId;
    state.loadingList = true;
    state.listError = '';
    renderPage();

    try {
      const jobsResult = await API.jobs.list({ limit: 1000 });
      const summaries = groupJobsByChain(normalizeJobList(jobsResult));

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

    ensureChainUiState(chainId);

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
      const debugBadges = [];
      if (Array.isArray(detail.jobs) && detail.jobs.every((job) => job && typeof job === 'object' && job.id)) {
        jobs = detail.jobs;
      } else if (jobsResult.status === 'fulfilled') {
        warnBackendGap({
          field: 'detail.jobs',
          jobId: chainId,
          fallbackUsed: '/api/jobs?chain_id',
        });
        debugBadges.push({ field: 'detail.jobs', fallbackUsed: '/api/jobs?chain_id' });
        jobs = normalizeJobList(jobsResult.value).filter((job) => String(job.chain_id || '') === chainId);
      }

      if (!jobs.length && detailResult.status === 'rejected' && jobsResult.status === 'rejected') {
        throw jobsResult.reason || detailResult.reason;
      }

      if (requestId !== state.detailRequestId) return;

      const summary = buildChainSummary(chainId, jobs, { ...detail, debugBadges });
      state.selectedSummary = summary;
      state.selectedJobs = summary.jobs;
      pruneChainUiState(chainId, summary.jobs);
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
      `Status: ${nodeStatusMeta(job).label}`,
      `Prompt: ${promptText(job) || 'No prompt provided'}`,
      `Created: ${exactDateLabel(job.created_at)}`,
      `Updated: ${exactDateLabel(job.updated_at || job.created_at)}`,
    ];

    const parentFailed = parentFailedReason(job.error);
    if (parentFailed) {
      lines.push(`Parent failed: ${parentFailed}`);
    }

    if (job.completed_at) {
      lines.push(`Completed: ${exactDateLabel(job.completed_at)}`);
    }

    if (job.profile) {
      lines.push(`Profile: ${job.profile}`);
    }

    if (job.media_id) {
      lines.push(`Media: ${job.media_id}`);
    }

    return lines.join('\n');
  }

  function errorExcerpt(error, maxLen = 220) {
    const text = String(error || '').trim().replace(/\s+/g, ' ');
    if (!text) return 'Unknown failure.';
    return App.truncate(text, maxLen);
  }

  function nodeErrorText(job) {
    const parentFailed = parentFailedReason(job?.error);
    if (parentFailed) {
      return `Parent failed: ${parentFailed}`;
    }
    return errorExcerpt(job?.error);
  }

  function detectCycleNodeIds(nodesById) {
    const visitState = new Map();
    const visitStack = [];
    const stackIndexes = new Map();
    const cycleNodeIds = new Set();

    function visit(node) {
      const nodeId = node.job.id;
      const nodeState = visitState.get(nodeId) || 0;

      if (nodeState === 1) {
        const startIndex = stackIndexes.get(nodeId) ?? 0;
        for (let index = startIndex; index < visitStack.length; index += 1) {
          cycleNodeIds.add(visitStack[index].job.id);
        }
        cycleNodeIds.add(nodeId);
        return;
      }

      if (nodeState === 2) return;

      visitState.set(nodeId, 1);
      stackIndexes.set(nodeId, visitStack.length);
      visitStack.push(node);

      if (node.parent) {
        visit(node.parent);
      }

      visitStack.pop();
      stackIndexes.delete(nodeId);
      visitState.set(nodeId, 2);
    }

    nodesById.forEach((node) => visit(node));
    return cycleNodeIds;
  }

  function buildTreeModel(jobs) {
    if (!jobs.length) return null;

    const nodesById = new Map();
    jobs.forEach((job) => {
      nodesById.set(job.id, {
        job,
        children: [],
        parent: null,
        descendantCount: 0,
      });
    });

    let missingParentCount = 0;
    nodesById.forEach((node) => {
      const parentId = String(node.job.parent_job_id || '').trim();
      if (!parentId) return;
      const parent = nodesById.get(parentId);
      if (!parent) {
        missingParentCount += 1;
        return;
      }
      node.parent = parent;
      parent.children.push(node);
    });

    const orderedNodes = Array.from(nodesById.values()).sort((a, b) => compareJobs(a.job, b.job));
    orderedNodes.forEach((node) => node.children.sort((a, b) => compareJobs(a.job, b.job)));

    const cycleNodeIds = detectCycleNodeIds(nodesById);
    const roots = orderedNodes.filter((node) => !node.parent);
    if (!roots.length && orderedNodes.length) {
      roots.push(orderedNodes[0]);
    }

    const covered = new Set();
    function markReachable(node, trail = new Set()) {
      const nodeId = node.job.id;
      if (covered.has(nodeId) || trail.has(nodeId)) return;
      covered.add(nodeId);
      const nextTrail = new Set(trail);
      nextTrail.add(nodeId);
      node.children.forEach((child) => markReachable(child, nextTrail));
    }

    roots.forEach((rootNode) => markReachable(rootNode));
    orderedNodes.forEach((node) => {
      if (!covered.has(node.job.id)) {
        roots.push(node);
        markReachable(node);
      }
    });

    function assignDescendantCount(node, trail = new Set()) {
      const nodeId = node.job.id;
      if (trail.has(nodeId)) return 0;
      const nextTrail = new Set(trail);
      nextTrail.add(nodeId);

      let total = 0;
      node.children.forEach((child) => {
        total += 1;
        total += assignDescendantCount(child, nextTrail);
      });
      node.descendantCount = total;
      return total;
    }

    roots.forEach((rootNode) => assignDescendantCount(rootNode));

    return {
      roots,
      nodesById,
      cycleNodeIds,
      hasCycles: cycleNodeIds.size > 0,
      missingParentCount,
      hasMissingParents: missingParentCount > 0,
      rootCount: roots.length,
      hasCancelledJobs: orderedNodes.some((node) => isCancelledJob(node.job)),
    };
  }

  function renderNodeThumbnail(job, label) {
    const thumb = thumbnailMedia(job);
    if (thumb?.url) {
      return `
        <div class="tile-thumb chain-tree-node-thumb-media">
          <img
            src="${escapeAttr(thumb.url)}"
            alt="${escapeAttr(label)}"
            loading="lazy"
            decoding="async"
            data-chain-tree-thumb="1"
          >
        </div>
      `;
    }

    return `
      <div class="tile-thumb chain-tree-node-thumb-media chain-tree-node-thumb-placeholder">
        <span class="material-icons">${App.escapeHtml(App.jobTypeIcon(job.type))}</span>
      </div>
    `;
  }

  function renderNodeBranch(node, model, chainId, trail = new Set(), isRoot = false) {
    const job = node.job;
    if (state.hideOrphans && isCancelledJob(job)) {
      return '';
    }

    const nodeId = String(job.id || '');
    const cycleInTrail = trail.has(nodeId);
    const nextTrail = new Set(trail);
    nextTrail.add(nodeId);

    const status = nodeStatusMeta(job);
    const theme = typeTheme(job.type);
    const label = getJobTypeLabel(job.type);
    const snippet = rootPromptSnippet(job, isRoot ? 92 : 72);
    const collapsed = isNodeCollapsed(chainId, nodeId);
    const errorOpen = isErrorOpen(chainId, nodeId);
    const deleting = state.deletingJobIds.has(nodeId);
    const showDeleteAction = model.hasCancelledJobs;
    const hasChildren = node.children.length > 0;
    const showChildren = hasChildren && !collapsed && !cycleInTrail;
    const cycleNode = cycleInTrail || model.cycleNodeIds.has(nodeId);
    const childCount = node.children.length;
    const descendantCopy = childCount === 1 ? '1 child' : `${childCount} children`;
    const descendantHint = node.descendantCount > childCount
      ? `${descendantCopy} / ${node.descendantCount} descendants`
      : descendantCopy;
    const profileLabel = job.profile ? App.truncate(job.profile, 18) : 'Unpinned';
    const mediaLabel = job.media_id ? App.truncate(job.media_id, 18) : 'No media';
    const nodeHint = hasChildren ? (collapsed ? `${node.descendantCount} hidden` : descendantHint) : 'Leaf node';

    return `
      <li class="chain-tree-branch">
        <article
          class="chain-tree-node ${isRoot ? 'root' : ''} ${job.status === 'failed' ? 'failed' : ''} ${cycleNode ? 'cycle' : ''} ${isActiveStatus(job.status) ? 'is-active' : ''}"
          style="--node-accent:${theme.color}; --node-accent-rgb:${theme.rgb};"
        >
          <div class="chain-tree-node-topbar">
            <div class="chain-tree-node-badges">
              ${isRoot ? '<span class="chain-tree-root-badge">Root</span>' : ''}
              <span class="chain-tree-level-badge">L${App.escapeHtml(String(job.job_level || '?'))}</span>
              <span class="chain-tree-status ${status.className}">${App.escapeHtml(status.label)}</span>
            </div>
            <div${model.hasCancelledJobs ? ' class="chain-tree-node-actions"' : ''}>
              ${showDeleteAction ? `
                <button
                  type="button"
                  class="chain-tree-icon-btn delete"
                  data-delete-job="${escapeAttr(nodeId)}"
                  title="Delete this job"
                  ${deleting ? 'disabled' : ''}
                >
                  <span class="material-icons">${deleting ? 'hourglass_empty' : 'delete'}</span>
                </button>
              ` : ''}
              ${hasChildren ? `
                <button
                  type="button"
                  class="chain-tree-icon-btn"
                  data-toggle-node="${escapeAttr(nodeId)}"
                  aria-expanded="${showChildren ? 'true' : 'false'}"
                  title="${collapsed ? 'Expand subtree' : 'Collapse subtree'}"
                >
                  <span class="material-icons">${collapsed ? 'chevron_right' : 'expand_more'}</span>
                </button>
              ` : ''}
            </div>
          </div>

          <button
            type="button"
            class="chain-tree-node-link"
            data-job-id="${escapeAttr(nodeId)}"
            title="${escapeAttr(buildNodeTooltip(job))}"
          >
            <div class="chain-tree-node-main">
              ${renderNodeThumbnail(job, snippet || label)}
              <div class="chain-tree-node-copy">
                <div class="chain-tree-node-title-row">
                  <div class="job-type-icon ${theme.className} chain-tree-node-type-icon" aria-hidden="true">
                    <span class="material-icons">${App.escapeHtml(App.jobTypeIcon(job.type))}</span>
                  </div>
                  <div class="chain-tree-node-title-block">
                    <div class="chain-tree-node-title">${App.escapeHtml(label)}</div>
                    <div class="chain-tree-node-timestamp" title="${escapeAttr(exactDateLabel(job.updated_at || job.created_at))}">
                      ${App.escapeHtml(relativeDateLabel(job.updated_at || job.created_at))}
                    </div>
                  </div>
                </div>

                <div class="chain-tree-node-snippet">${App.escapeHtml(snippet)}</div>

                <div class="chain-tree-node-chip-row">
                  <span class="chain-tree-mini-chip">${App.escapeHtml(profileLabel)}</span>
                  <span class="chain-tree-mini-chip mono">${App.escapeHtml(mediaLabel)}</span>
                </div>

                <div class="chain-tree-node-id">job/${App.escapeHtml(shortId(nodeId, 14))}</div>
              </div>
            </div>
          </button>

          <div class="chain-tree-node-bottom">
            <div class="chain-tree-node-hint">${App.escapeHtml(nodeHint)}</div>
            ${job.error ? `
              <button
                type="button"
                class="chain-tree-inline-link"
                data-toggle-error="${escapeAttr(nodeId)}"
              >
                ${errorOpen ? 'Hide error' : 'Show error'}
              </button>
            ` : ''}
          </div>

          ${job.error && errorOpen ? `
            <div class="chain-tree-node-error">${App.escapeHtml(nodeErrorText(job))}</div>
          ` : ''}

          ${cycleNode ? `
            <div class="chain-tree-node-warning">
              Cycle detected on parent linkage. Descendants are clipped for a safe best-effort render.
            </div>
          ` : ''}
        </article>

        ${showChildren ? `
          <ul>
            ${node.children.map((child) => renderNodeBranch(child, model, chainId, nextTrail, false)).join('')}
          </ul>
        ` : ''}
      </li>
    `;
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
          const stats = chain.stats || computeSummaryStats(chain.jobs || []);
          return `
            <button
              type="button"
              class="chain-tree-list-item ${selected ? 'selected' : ''}"
              data-chain-id="${escapeAttr(chain.id)}"
            >
              <div class="chain-tree-list-top">
                <div class="chain-tree-list-title-wrap">
                  <div class="chain-tree-list-title">Chain ${App.escapeHtml(App.truncate(chain.id, 22))}</div>
                  <div class="chain-tree-list-id" title="${escapeAttr(chain.id)}">${App.escapeHtml(exactDateLabel(chain.updated_at || chain.created_at))}</div>
                </div>
                <span class="chain-tree-status ${status.className}">${App.escapeHtml(status.label)}</span>
              </div>

              <div class="chain-tree-list-prompt">${App.escapeHtml(chain.root_prompt || 'No root prompt')}</div>

              <div class="chain-tree-list-chip-row">
                <span class="chain-tree-stat-chip">${App.escapeHtml(String(stats.total))} nodes</span>
                <span class="chain-tree-stat-chip ok">${App.escapeHtml(String(stats.completed))} done</span>
                <span class="chain-tree-stat-chip fail">${App.escapeHtml(String(stats.failed))} failed</span>
                <span class="chain-tree-stat-chip active">${App.escapeHtml(String(stats.pending))} active</span>
                ${chain.profile ? `<span class="chain-tree-stat-chip">${App.escapeHtml(App.truncate(chain.profile, 18))}</span>` : ''}
                ${renderDebugBadges(chain.debugBadges)}
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
    const stats = summary.stats || computeSummaryStats(summary.jobs || []);

    return `
      <div class="card chain-tree-summary-card" style="padding: 20px;">
        <div class="chain-tree-summary-hero">
          <div>
            <div class="chain-tree-summary-eyebrow">Chain Explorer</div>
            <h3 class="section-title" style="margin-top: 6px;">Chain ${App.escapeHtml(App.truncate(summary.id, 30))}</h3>
            <p class="chain-tree-summary-copy">${App.escapeHtml(summary.root_prompt || 'No root prompt')}</p>
          </div>

          <div class="section-actions">
            <a href="#jobs/${encodeURIComponent(summary.id)}" class="btn btn-sm btn-outline">
              <span class="material-icons" style="font-size:16px">list</span> Open jobs
            </a>
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

        <div class="chain-tree-summary-chip-row">
          <span class="chain-tree-status ${status.className}">${App.escapeHtml(status.label)}</span>
          <span class="chain-tree-chain-chip">${App.escapeHtml(summary.id)}</span>
          ${summary.profile ? `<span class="chain-tree-chain-chip">${App.escapeHtml(summary.profile)}</span>` : ''}
          ${renderDebugBadges(summary.debugBadges, 'chain-tree-chain-chip')}
        </div>

        <div class="chain-tree-summary-grid">
          <div class="chain-tree-summary-stat">
            <div class="chain-tree-summary-label">Total Nodes</div>
            <div class="chain-tree-summary-value">${App.escapeHtml(String(stats.total))}</div>
            <div class="chain-tree-summary-subvalue">${App.escapeHtml(String(summary.progress?.completed ?? 0))}/${App.escapeHtml(String(summary.progress?.total ?? stats.total))} completed</div>
          </div>
          <div class="chain-tree-summary-stat">
            <div class="chain-tree-summary-label">Completed</div>
            <div class="chain-tree-summary-value">${App.escapeHtml(String(stats.completed))}</div>
            <div class="chain-tree-summary-subvalue">Successful terminal jobs</div>
          </div>
          <div class="chain-tree-summary-stat">
            <div class="chain-tree-summary-label">Failed</div>
            <div class="chain-tree-summary-value">${App.escapeHtml(String(stats.failed))}</div>
            <div class="chain-tree-summary-subvalue">Inline error excerpts available</div>
          </div>
          <div class="chain-tree-summary-stat">
            <div class="chain-tree-summary-label">Pending / Active</div>
            <div class="chain-tree-summary-value">${App.escapeHtml(String(stats.pending))}</div>
            <div class="chain-tree-summary-subvalue">Pending, claimed, and running jobs</div>
          </div>
          <div class="chain-tree-summary-stat">
            <div class="chain-tree-summary-label">Last Updated</div>
            <div class="chain-tree-summary-value">${App.escapeHtml(relativeDateLabel(summary.updated_at || summary.created_at))}</div>
            <div class="chain-tree-summary-subvalue">${App.escapeHtml(exactDateLabel(summary.updated_at || summary.created_at))}</div>
          </div>
        </div>
      </div>
    `;
  }

  function renderTreeCanvas() {
    if (state.loadingDetail && !state.selectedJobs.length) {
      return `
        <div class="card chain-tree-tree-card" style="padding: 20px;">
          <div class="loading-center"><div class="spinner spinner-lg"></div></div>
        </div>
      `;
    }

    if (!state.selectedSummary) {
      return '';
    }

    if (!state.selectedJobs.length) {
      return `
        <div class="card chain-tree-tree-card" style="padding: 20px;">
          <div class="empty-state" style="padding: 48px 20px;">
            <span class="material-icons">device_hub</span>
            <h3>No jobs on this chain</h3>
            <p>This chain has no visualizable jobs yet.</p>
          </div>
        </div>
      `;
    }

    const chainId = state.selectedSummary.id;
    const model = buildTreeModel(state.selectedJobs);
    if (!model) {
      return `
        <div class="card chain-tree-tree-card" style="padding: 20px;">
          <div class="empty-state" style="padding: 48px 20px;">
            <span class="material-icons">device_hub</span>
            <h3>Tree unavailable</h3>
            <p>The current chain could not be laid out.</p>
          </div>
        </div>
      `;
    }

    const hasExpandableNodes = Array.from(model.nodesById.values()).some((node) => node.children.length > 0);
    const cancelledJobs = cancelledDescendants(model);
    const cancelledCount = cancelledJobs.length;
    const showCleanupToolbar = model.hasCancelledJobs || state.deletingCancelled;
    const renderedRootGroups = model.roots
      .map((rootNode) => {
        const branch = renderNodeBranch(rootNode, model, chainId, new Set(), true);
        return branch.trim() ? `
          <div class="chain-tree-root-group">
            <ul class="chain-tree-tree">
              ${branch}
            </ul>
          </div>
        ` : '';
      })
      .join('');

    return `
      <div class="card chain-tree-tree-card" style="padding: 20px;">
        <div class="chain-tree-tree-header">
          <div>
            <h3 class="section-title">Multi-Level Tree</h3>
            <p class="chain-tree-help">
              Use the chevrons to collapse branches. Click a node card to open <code>#job-detail/&lt;id&gt;</code>.
            </p>
          </div>

          <div class="section-actions">
            <button
              type="button"
              class="btn btn-sm btn-outline"
              data-tree-action="expand-all"
              ${hasExpandableNodes ? '' : 'disabled'}
            >
              <span class="material-icons" style="font-size:16px">unfold_more</span> Expand all
            </button>
            <button
              type="button"
              class="btn btn-sm btn-outline"
              data-tree-action="collapse-all"
              ${hasExpandableNodes ? '' : 'disabled'}
            >
              <span class="material-icons" style="font-size:16px">unfold_less</span> Collapse all
            </button>
          </div>
        </div>

        ${showCleanupToolbar ? `
          <div class="chain-tree-toolbar">
            <label class="chain-tree-toggle" for="chain-tree-hide-orphans">
              <input
                type="checkbox"
                id="chain-tree-hide-orphans"
                ${state.hideOrphans ? 'checked' : ''}
              >
              <span>Hide cancelled / orphan jobs</span>
            </label>
            <button
              type="button"
              class="btn btn-sm btn-danger"
              data-tree-action="delete-cancelled"
              ${cancelledCount && !state.deletingCancelled ? '' : 'disabled'}
              title="Delete cancelled descendants in this chain"
            >
              <span class="material-icons" style="font-size:16px">${state.deletingCancelled ? 'hourglass_empty' : 'delete_sweep'}</span>
              Delete all cancelled in this chain${cancelledCount ? ` (${App.escapeHtml(String(cancelledCount))})` : ''}
            </button>
          </div>
        ` : ''}

        ${model.hasMissingParents ? `
          <div class="chain-tree-banner warn" style="margin-bottom: 12px;">
            <span class="material-icons">warning</span>
            <div>Server backfill pending - partial tree. ${App.escapeHtml(String(model.missingParentCount))} job(s) reference a parent that was not returned.</div>
          </div>
        ` : ''}

        ${model.hasCycles ? `
          <div class="chain-tree-banner warn" style="margin-bottom: 12px;">
            <span class="material-icons">warning</span>
            <div>Cycle detected in <code>parent_job_id</code>. Rendering is clipped to keep the tree readable.</div>
          </div>
        ` : ''}

        ${model.rootCount > 1 ? `
          <div class="chain-tree-banner warn" style="margin-bottom: 16px;">
            <span class="material-icons">call_split</span>
            <div>${App.escapeHtml(String(model.rootCount))} root groups are being rendered side-by-side.</div>
          </div>
        ` : ''}

        <div class="chain-tree-tree-scroll">
          <div class="chain-tree-tree-stage">
            <div class="chain-tree-root-forest">
              ${renderedRootGroups.trim() ? renderedRootGroups : `
                <div class="empty-state" style="padding: 40px 20px;">
                  <span class="material-icons">visibility_off</span>
                  <h3>Cancelled jobs hidden</h3>
                  <p>Turn off the filter to show cancelled and orphan jobs.</p>
                </div>
              `}
            </div>
          </div>
        </div>
      </div>
    `;
  }

  function renderPage() {
    if (!root) return;

    root.innerHTML = `
      <div class="chain-tree-shell">
        <aside class="card chain-tree-rail" style="padding: 18px;">
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
            <div class="chain-tree-banner warn" style="margin: 16px 0;">
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

  function selectChain(chainId) {
    if (!chainId || chainId === state.selectedChainId) return;
    state.selectedChainId = chainId;
    ensureChainUiState(chainId);
    syncSelectedSummary();
    renderPage();
    void loadSelectedChain();
  }

  function toggleNodeCollapsed(nodeId) {
    const chainId = state.selectedChainId;
    if (!chainId || !nodeId) return;
    ensureChainUiState(chainId);

    const collapsed = new Set(getCollapsedSet(chainId));
    if (collapsed.has(nodeId)) {
      collapsed.delete(nodeId);
    } else {
      collapsed.add(nodeId);
    }

    state.collapsedByChain[chainId] = collapsed;
    persistCollapsedState(chainId);
    renderPage();
  }

  function toggleErrorOpen(nodeId) {
    const chainId = state.selectedChainId;
    if (!chainId || !nodeId) return;
    ensureChainUiState(chainId);

    const opened = new Set(getErrorOpenSet(chainId));
    if (opened.has(nodeId)) {
      opened.delete(nodeId);
    } else {
      opened.add(nodeId);
    }
    state.errorOpenByChain[chainId] = opened;
    renderPage();
  }

  function setAllCollapsed(shouldCollapse) {
    const chainId = state.selectedChainId;
    if (!chainId || !state.selectedJobs.length) return;
    ensureChainUiState(chainId);

    const model = buildTreeModel(state.selectedJobs);
    if (!model) return;

    const rootIds = new Set(model.roots.map((node) => node.job.id));
    const next = shouldCollapse
      ? Array.from(model.nodesById.values())
        .filter((node) => node.children.length > 0 && !rootIds.has(node.job.id))
        .map((node) => node.job.id)
      : [];

    state.collapsedByChain[chainId] = new Set(next);
    persistCollapsedState(chainId);
    renderPage();
  }

  function setHideOrphans(checked) {
    state.hideOrphans = Boolean(checked);
    persistHideOrphansState();
    renderPage();
  }

  async function handleClick(event) {
    const chainButton = event.target.closest('[data-chain-id]');
    if (chainButton) {
      selectChain(chainButton.dataset.chainId);
      return;
    }

    const treeAction = event.target.closest('[data-tree-action]');
    if (treeAction) {
      if (treeAction.dataset.treeAction === 'expand-all') {
        setAllCollapsed(false);
      }
      if (treeAction.dataset.treeAction === 'collapse-all') {
        setAllCollapsed(true);
      }
      if (treeAction.dataset.treeAction === 'delete-cancelled') {
        await deleteAllCancelledInChain();
      }
      return;
    }

    const toggleButton = event.target.closest('[data-toggle-node]');
    if (toggleButton) {
      toggleNodeCollapsed(toggleButton.dataset.toggleNode);
      return;
    }

    const errorButton = event.target.closest('[data-toggle-error]');
    if (errorButton) {
      toggleErrorOpen(errorButton.dataset.toggleError);
      return;
    }

    const deleteButton = event.target.closest('[data-delete-job]');
    if (deleteButton) {
      await deleteJob(deleteButton.dataset.deleteJob);
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

  function handleChange(event) {
    const hideToggle = event.target.closest('#chain-tree-hide-orphans');
    if (hideToggle) {
      setHideOrphans(hideToggle.checked);
    }
  }

  function handleMediaError(event) {
    const img = event.target instanceof Element
      ? event.target.closest('img[data-chain-tree-thumb]')
      : null;
    if (!img) return;

    img.parentElement?.classList.add('tile-thumb--broken');
    img.remove();
  }

  const ChainTreePage = {
    name: 'chain-tree',
    title: 'Chain Tree',
    icon: 'account_tree',

    render() {
      return `<div id="${PAGE_ROOT_ID}"></div>`;
    },

    mount() {
      ensureStyles();

      root = document.getElementById(PAGE_ROOT_ID);
      if (!root) return;

      handlers = {
        click: (event) => { void handleClick(event); },
        change: handleChange,
        error: handleMediaError,
      };

      root.addEventListener('click', handlers.click);
      root.addEventListener('change', handlers.change);
      root.addEventListener('error', handlers.error, true);

      syncSelectedSummary();
      renderPage();
      void loadChainList();
    },

    destroy() {
      if (root && handlers) {
        root.removeEventListener('click', handlers.click);
        root.removeEventListener('change', handlers.change);
        root.removeEventListener('error', handlers.error, true);
      }
      root = null;
      handlers = null;
    },
  };

  App.register(ChainTreePage);
})();
