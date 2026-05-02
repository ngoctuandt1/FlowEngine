/**
 * FlowEngine SPA Router & App Initialization
 */
const App = {
  currentPage: null,
  pages: {},

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

    // Aliases: semantic hashes that map to a registered page under a different key.
    const ROUTE_ALIASES = { 'chain-builder': 'chains' };
    if (ROUTE_ALIASES[pageName] && this.pages[ROUTE_ALIASES[pageName]]) {
      pageName = ROUTE_ALIASES[pageName];
    }

    if (!this.pages[pageName]) {
      location.hash = '#home';
      return;
    }

    this._loadPage(pageName);
  },

  /**
   * Load and render a page.
   */
  async _loadPage(name) {
    const page = this.pages[name];
    if (!page) return;

    // Destroy current page if it has cleanup
    if (this.currentPage && this.pages[this.currentPage]?.destroy) {
      this.pages[this.currentPage].destroy();
    }

    this.currentPage = name;

    // Update nav (sidebar + appbar)
    document.querySelectorAll('.nav-link, .appbar-link').forEach((link) => {
      link.classList.toggle('active', link.dataset.page === name);
    });

    // Update title
    const titleEl = document.getElementById('page-title');
    if (titleEl) titleEl.textContent = page.title;

    // Mark route on body for route-scoped CSS (e.g. hide top-bar on #home)
    document.body.className = document.body.className
      .split(/\s+/)
      .filter((c) => !c.startsWith('route-'))
      .concat(`route-${name}`)
      .join(' ').trim();

    // Close drawer
    document.getElementById('sidebar')?.classList.remove('open');
    document.getElementById('sidebar-scrim')?.classList.remove('open');

    // Render page
    const container = document.getElementById('page-container');
    container.innerHTML = '<div class="loading-center"><div class="spinner spinner-lg"></div></div>';
    container.classList.remove('page-enter');

    try {
      const html = await page.render();
      container.innerHTML = html;
      container.classList.add('page-enter');

      // Call mount if page has post-render logic
      if (page.mount) page.mount();
    } catch (err) {
      console.error(`[App] Failed to render page '${name}':`, err);
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

  /**
   * Open the modal with content.
   */
  openModal(title, html) {
    document.getElementById('modal-title').textContent = title;
    document.getElementById('modal-body').innerHTML = html;
    document.getElementById('modal-overlay').classList.remove('hidden');
    document.body.style.overflow = 'hidden';
  },

  /**
   * Close the modal.
   */
  closeModal() {
    document.getElementById('modal-overlay').classList.add('hidden');
    document.body.style.overflow = '';
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
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(40px)';
        toast.style.transition = 'all 0.3s ease';
        setTimeout(() => toast.remove(), 300);
      }, duration);
    }
  },

  // ---- Utilities ----

  /**
   * Escape HTML to prevent XSS.
   */
  escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
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
    return `badge badge-${status || 'pending'}`;
  },
};


// ---- Boot ----

document.addEventListener('DOMContentLoaded', () => {
  App.init();
});
