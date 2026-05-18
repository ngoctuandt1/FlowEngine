/**
 * FlowEngine SPA Router & App Initialization
 */
try {
  localStorage.removeItem('flowengine.workerApiKey');
} catch (_) {
  // Ignore storage access failures during early boot.
}

const ROUTE_ALIASES = { 'chain-builder': 'chains' };
const LAZY_MODULE_MAP = {
  dashboard: 'dashboard',
  'engine-status': 'engine-status',
  create: 'create-job',
  'create-job': 'create-job',
  'chain-builder': 'chain-builder',
  'chain-tree': 'chain-tree',
  characters: 'characters',
  profiles: 'profiles',
  settings: 'settings',
  tts: 'tts',
  workflows: 'workflows',
  'media-tools': 'media-tools',
  gallery: 'gallery',
  'project-view': 'project-view',
  'job-detail': 'job-detail',
  jobs: 'jobs',
  'batch-queue': 'batch-queue',
  chains: 'chain-builder',
};
const SKELETON_ROUTES = new Set(['dashboard', 'gallery', 'jobs', 'profiles', 'characters', 'media-tools']);

const App = {
  currentPage: null,
  pages: {},
  lazyModuleLoads: {},
  lazyModuleReady: {},
  lazyModuleVersion: null,
  modalOpenerEl: null,

  /**
   * Register a page module.
   * Each page must have: { name, title, icon, render(), destroy?() }
   */
  register(page) {
    this.pages[page.name] = page;
  },

  /**
   * Initialize the application.
   */
  init() {
    // Listen for hash changes
    window.addEventListener('hashchange', () => this._onRoute());

    // Menu toggle (drawer-style sidebar)
    const toggle = document.getElementById('menu-toggle');
    const sidebar = document.getElementById('sidebar');
    const scrim = document.getElementById('sidebar-scrim');
    const closeDrawer = () => {
      sidebar?.classList.remove('open');
      scrim?.classList.remove('open');
    };
    if (toggle && sidebar) {
      toggle.addEventListener('click', () => {
        const open = sidebar.classList.toggle('open');
        scrim?.classList.toggle('open', open);
      });
      scrim?.addEventListener('click', closeDrawer);
    }

    // Refresh button
    const refreshBtn = document.getElementById('refresh-btn');
    if (refreshBtn) {
      refreshBtn.addEventListener('click', () => this._refreshCurrentPage());
    }

    // Modal close
    const modalOverlay = document.getElementById('modal-overlay');
    const modalClose = modalOverlay?.querySelector('.modal-close');
    if (modalClose) {
      modalClose.addEventListener('click', () => App.closeModal());
    }
    if (modalOverlay) {
      modalOverlay.addEventListener('click', (e) => {
        if (e.target === modalOverlay) App.closeModal();
      });
      modalOverlay.addEventListener('keydown', (e) => App._trapModalFocus(e));
    }

    // ESC to close modal
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') App.closeModal();
    });

    // Connect WebSocket
    WS.connect();

    // Navigate to initial route
    this._onRoute();
  },

  _resolvePageName(name) {
    const alias = ROUTE_ALIASES[name];
    return alias && this.pages[alias] ? alias : name;
  },

  _getLazyModuleVersion() {
    if (this.lazyModuleVersion !== null) return this.lazyModuleVersion;

    const eagerScript = document.querySelector('script[src*="/js/app.js"]');
    const src = eagerScript?.getAttribute('src') || '';
    const match = src.match(/[?&]v=([^&]+)/);
    this.lazyModuleVersion = match ? decodeURIComponent(match[1]) : '';
    return this.lazyModuleVersion;
  },

  _fallbackToHome() {
    if (location.hash === '#home') {
      this._loadPage('home');
      return;
    }
    location.hash = '#home';
  },

  _ensureLazyPage(name) {
    const fileStem = LAZY_MODULE_MAP[name];
    if (!fileStem) return Promise.resolve(false);
    if (this.pages[name] || this.lazyModuleReady[fileStem]) return Promise.resolve(false);
    if (this.lazyModuleLoads[fileStem]) return this.lazyModuleLoads[fileStem];

    const version = this._getLazyModuleVersion();
    const query = version ? `?v=${encodeURIComponent(version)}` : '';
    const script = document.createElement('script');
    const loadingToast = this.toast('Loading…', 'info', 0);

    script.src = `/js/pages/${fileStem}.js${query}`;
    script.async = true;
    script.dataset.lazyPageModule = fileStem;

    const loadPromise = new Promise((resolve, reject) => {
      script.onload = () => {
        this.lazyModuleReady[fileStem] = true;
        delete this.lazyModuleLoads[fileStem];
        this.dismissToast(loadingToast);
        resolve(true);
      };

      script.onerror = () => {
        delete this.lazyModuleLoads[fileStem];
        delete this.lazyModuleReady[fileStem];
        script.remove();
        this.dismissToast(loadingToast);
        this.toast(`Failed to load ${name}`, 'error');
        reject(new Error(`Failed to load ${name}`));
      };
    });

    this.lazyModuleLoads[fileStem] = loadPromise;
    document.body.appendChild(script);
    return loadPromise;
  },

  mountSkeleton(container, count = 6) {
    if (!container) return;

    const cardCount = Math.max(1, Number(count) || 6);
    container.setAttribute('aria-busy', 'true');
    const cards = Array.from({ length: cardCount }, () => `
      <div class="skeleton-card" aria-hidden="true">
        <div class="skeleton-line skeleton-line--wide"></div>
        <div class="skeleton-line"></div>
        <div class="skeleton-line skeleton-line--short"></div>
      </div>
    `).join('');

    container.innerHTML = `
      <div class="skeleton-grid" role="status" aria-label="Loading content">
        ${cards}
      </div>
    `;
  },

  /**
   * Handle route changes.
   */
  _onRoute() {
    const hash = location.hash.slice(1) || 'home';
    // Strip query string before splitting (`#chain-builder?parent=…`).
    const hashPath = hash.split('?')[0];
    const segments = hashPath.split('/');
    let pageName = segments[0];

    // Hard redirect: legacy #job-detail/<id> → #project-view/<id> so every
    // click on a job lands on the DAG canvas (the new project view) instead
    // of the legacy single-job detail page. Preserves the id and any tail.
    if (pageName === 'job-detail' && segments[1]) {
      const tail = segments.slice(1).join('/');
      const query = hash.includes('?') ? '?' + hash.split('?').slice(1).join('?') : '';
      location.replace(`#project-view/${tail}${query}`);
      return;
    }

    pageName = this._resolvePageName(pageName);

    const lazyFile = LAZY_MODULE_MAP[pageName];
    if (lazyFile && !this.pages[pageName]) {
      if (!this.lazyModuleLoads[lazyFile] && !this.lazyModuleReady[lazyFile]) {
        this._ensureLazyPage(pageName)
          .then(() => this._onRoute())
          .catch(() => this._fallbackToHome());
      }
      return;
    }

    if (!this.pages[pageName]) {
      this._fallbackToHome();
      return;
    }

    this._loadPage(pageName);
  },

  /**
   * Load and render a page.
   */
  async _loadPage(name) {
    const resolvedName = this._resolvePageName(name);
    const page = this.pages[resolvedName];
    if (!page) {
      const lazyFile = LAZY_MODULE_MAP[resolvedName] || LAZY_MODULE_MAP[name];
      if (!lazyFile) return;

      try {
        if (!this.lazyModuleReady[lazyFile]) {
          await this._ensureLazyPage(name);
        }

        const retryName = this._resolvePageName(name);
        if (!this.pages[retryName]) {
          throw new Error(`Page '${name}' did not register after loading`);
        }

        return this._loadPage(retryName);
      } catch (err) {
        console.error(`[App] Failed to lazy-load page '${name}':`, err);
        this._fallbackToHome();
        return;
      }
    }

    // Destroy current page if it has cleanup
    if (this.currentPage && this.pages[this.currentPage]?.destroy) {
      this.pages[this.currentPage].destroy();
    }

    this.currentPage = resolvedName;

    // Update nav (sidebar + appbar)
    document.querySelectorAll('.nav-link, .appbar-link').forEach((link) => {
      link.classList.toggle('active', link.dataset.page === resolvedName);
    });

    // Update title
    const titleEl = document.getElementById('page-title');
    if (titleEl) titleEl.textContent = page.title;

    // Mark route on body for route-scoped CSS (e.g. hide top-bar on #home)
    document.body.className = document.body.className
      .split(/\s+/)
      .filter((c) => !c.startsWith('route-'))
      .concat(`route-${resolvedName}`)
      .join(' ').trim();

    // Close drawer
    document.getElementById('sidebar')?.classList.remove('open');
    document.getElementById('sidebar-scrim')?.classList.remove('open');

    // Render page
    const container = document.getElementById('page-container');
    if (SKELETON_ROUTES.has(resolvedName)) {
      this.mountSkeleton(container, resolvedName === 'dashboard' ? 4 : 6);
    } else {
      container.setAttribute('aria-busy', 'false');
      container.innerHTML = '<div class="loading-center"><div class="spinner spinner-lg"></div></div>';
    }
    container.classList.remove('page-enter');

    try {
      const html = await page.render();
      container.innerHTML = html;
      container.setAttribute('aria-busy', 'false');
      container.classList.add('page-enter');

      // Call mount if page has post-render logic
      if (page.mount) page.mount();
    } catch (err) {
      console.error(`[App] Failed to render page '${resolvedName}':`, err);
      container.setAttribute('aria-busy', 'false');
      container.innerHTML = `
        <div class="empty-state">
          <span class="material-icons">error_outline</span>
          <h3>Failed to load page</h3>
          <p>${App.escapeHtml(err.message)}</p>
        </div>
      `;
    }
  },

  /**
   * Refresh the current page.
   */
  _refreshCurrentPage() {
    if (this.currentPage) {
      this._loadPage(this.currentPage);
    }
  },

  // ---- Modal ----

  _getModalContent() {
    const modalOverlay = document.getElementById('modal-overlay');
    return modalOverlay?.querySelector('.modal-content') || null;
  },

  _isModalFocusableVisible(element) {
    return Boolean(element.offsetWidth || element.offsetHeight || element.getClientRects().length);
  },

  _getModalFocusable() {
    const modalContent = this._getModalContent();
    if (!modalContent) return [];

    const selector = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

    return Array.from(modalContent.querySelectorAll(selector))
      .filter((el) => this._isModalFocusableVisible(el) && typeof el.focus === 'function');
  },

  _getModalFallbackFocusTarget() {
    const modalContent = this._getModalContent();
    if (!modalContent) return null;

    const modalClose = modalContent.querySelector('.modal-close');
    if (
      modalClose
      && !modalClose.disabled
      && this._isModalFocusableVisible(modalClose)
      && typeof modalClose.focus === 'function'
    ) {
      return modalClose;
    }

    if (typeof modalContent.focus !== 'function') return null;
    if (!modalContent.hasAttribute('tabindex')) {
      modalContent.setAttribute('tabindex', '-1');
    }
    return modalContent;
  },

  _focusModalInitial() {
    const modalContent = this._getModalContent();
    if (!modalContent) return;

    const autofocusEl = modalContent.querySelector('[autofocus]');
    const focusable = this._getModalFocusable();
    const target = focusable.includes(autofocusEl) ? autofocusEl : focusable[0] || this._getModalFallbackFocusTarget();
    target?.focus();
  },

  _trapModalFocus(event) {
    if (event.key !== 'Tab') return;

    const modalOverlay = document.getElementById('modal-overlay');
    if (!modalOverlay || modalOverlay.classList.contains('hidden')) return;

    const focusable = this._getModalFocusable();
    if (!focusable.length) {
      event.preventDefault();
      this._getModalFallbackFocusTarget()?.focus();
      return;
    }

    const first = focusable[0];
    const last = focusable[focusable.length - 1];

    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
      return;
    }

    if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  },

  /**
   * Open the modal with content.
   */
  openModal(title, html) {
    this.modalOpenerEl = document.activeElement;
    document.getElementById('modal-title').textContent = title;
    document.getElementById('modal-body').innerHTML = html;
    document.getElementById('modal-overlay').classList.remove('hidden');
    document.body.style.overflow = 'hidden';
    this._focusModalInitial();
  },

  /**
   * Close the modal.
   */
  closeModal() {
    const modalOverlay = document.getElementById('modal-overlay');
    const wasOpen = modalOverlay && !modalOverlay.classList.contains('hidden');
    modalOverlay?.classList.add('hidden');
    document.body.style.overflow = '';

    if (wasOpen && this.modalOpenerEl && document.contains(this.modalOpenerEl)) {
      this.modalOpenerEl.focus();
    }
    this.modalOpenerEl = null;
  },

  mountSecretField(input, toggleBtn) {
    if (!input || !toggleBtn || toggleBtn.dataset.secretMounted === 'true') return;

    const icon = toggleBtn.querySelector('.material-icons');
    const label = (toggleBtn.getAttribute('aria-label') || 'Secret').replace(/^(Show|Hide)\s+/, '');
    const sync = () => {
      const visible = input.type === 'text';
      if (icon) icon.textContent = visible ? 'visibility_off' : 'visibility';
      toggleBtn.setAttribute('aria-label', `${visible ? 'Hide' : 'Show'} ${label}`);
      toggleBtn.setAttribute('aria-pressed', visible ? 'true' : 'false');
    };

    toggleBtn.dataset.secretMounted = 'true';
    sync();
    toggleBtn.addEventListener('click', () => {
      input.type = input.type === 'password' ? 'text' : 'password';
      sync();
    });
  },

  // ---- Toast Notifications ----

  /**
   * Show a toast notification.
   * @param {string} message
   * @param {'success'|'error'|'info'|'warning'} type
   * @param {number} duration - ms before auto-dismiss
   */
  toast(message, type = 'info', duration = 4000) {
    const container = document.getElementById('toast-container');
    const icons = {
      success: 'check_circle',
      error: 'error',
      info: 'info',
      warning: 'warning',
    };

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
      <span class="material-icons">${icons[type] || 'info'}</span>
      <span class="toast-message">${App.escapeHtml(message)}</span>
      <button class="toast-close" onclick="this.parentElement.remove()">&times;</button>
    `;

    container.appendChild(toast);

    if (duration > 0) {
      setTimeout(() => {
        this.dismissToast(toast);
      }, duration);
    }

    return toast;
  },

  dismissToast(toast) {
    if (!toast?.isConnected) return;
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(40px)';
    toast.style.transition = 'all 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  },

  // ---- Utilities ----

  /**
   * Escape HTML for text and quoted attribute contexts.
   */
  escapeHtml(text) {
    if (text === null || text === undefined) return '';
    const chars = {
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
      '/': '&#x2F;',
    };
    return String(text).replace(/[&<>"'\/]/g, (char) => chars[char]);
  },

  safeHref(url) {
    const raw = String(url || '');
    if (raw === '') return '';
    // Reject C0 controls (tab/newline/etc.) and backslash before parsing —
    // browsers strip these and can promote a "/path" into "//evil.com".
    if (/[\x00-\x1F\x7F\\]/.test(raw)) {
      console.warn('[FlowEngine] Blocked href with control chars or backslash', url);
      return '#';
    }
    let resolved;
    try {
      resolved = new URL(raw, window.location.origin);
    } catch (_) {
      console.warn('[FlowEngine] Blocked unparseable href', url);
      return '#';
    }
    // Allow only: same-origin https labs.google flow URLs, or same-origin app URLs.
    if (resolved.protocol === 'https:' && resolved.hostname === 'labs.google') return resolved.href;
    if (resolved.origin === window.location.origin) return resolved.pathname + resolved.search + resolved.hash;
    console.warn('[FlowEngine] Blocked unsafe href', url);
    return '#';
  },

  /**
   * Format a date string for display.
   */
  formatDate(dateStr) {
    if (!dateStr) return '-';
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now - date;
    const diffMin = Math.floor(diffMs / 60000);
    const diffHr = Math.floor(diffMs / 3600000);

    if (diffMin < 1) return 'Just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    if (diffHr < 24) return `${diffHr}h ago`;

    return date.toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  },

  /**
   * Truncate text to a maximum length.
   */
  truncate(text, maxLen = 100) {
    if (!text) return '';
    return text.length > maxLen ? text.slice(0, maxLen) + '...' : text;
  },

  /**
   * Get the material icon for a job type.
   */
  jobTypeIcon(type) {
    const icons = {
      'text-to-video': 'videocam',
      'frames-to-video': 'image',
      'ingredients-to-video': 'photo_library',
      'text-to-image': 'photo',
      't2v': 'videocam',
      'extend': 'add_to_queue',
      'extend-video': 'add_to_queue',
      'insert': 'add_box',
      'insert-object': 'add_box',
      'remove': 'delete_sweep',
      'remove-object': 'delete_sweep',
      'camera': 'videocam_off',
      'camera-move': 'videocam_off',
    };
    return icons[type] || 'work';
  },

  /**
   * Get CSS class for a job type icon.
   */
  jobTypeClass(type) {
    const classes = {
      'text-to-video': 't2v',
      'frames-to-video': 't2v',
      'ingredients-to-video': 't2v',
      'text-to-image': 'insert',
      't2v': 't2v',
      'extend': 'extend',
      'extend-video': 'extend',
      'insert': 'insert',
      'insert-object': 'insert',
      'remove': 'remove',
      'remove-object': 'remove',
      'camera': 'camera',
      'camera-move': 'camera',
    };
    return classes[type] || '';
  },

  /**
   * Get the badge class for a status.
   */
  statusBadge(status) {
    return `badge badge-${this.escapeHtml(status || 'pending')}`;
  },
};


// ---- Boot ----

document.addEventListener('DOMContentLoaded', () => {
  App.init();
});
