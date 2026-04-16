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

    // Menu toggle for mobile
    const toggle = document.getElementById('menu-toggle');
    const sidebar = document.getElementById('sidebar');
    if (toggle && sidebar) {
      toggle.addEventListener('click', () => {
        sidebar.classList.toggle('open');
      });

      // Close sidebar on outside click (mobile)
      document.addEventListener('click', (e) => {
        if (
          sidebar.classList.contains('open') &&
          !sidebar.contains(e.target) &&
          e.target !== toggle &&
          !toggle.contains(e.target)
        ) {
          sidebar.classList.remove('open');
        }
      });
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
    const hash = location.hash.slice(1) || 'dashboard';
    const pageName = hash.split('/')[0];

    if (!this.pages[pageName]) {
      location.hash = '#dashboard';
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

    // Update nav
    document.querySelectorAll('.nav-link').forEach((link) => {
      link.classList.toggle('active', link.dataset.page === name);
    });

    // Update title
    const titleEl = document.getElementById('page-title');
    if (titleEl) titleEl.textContent = page.title;

    // Close mobile sidebar
    document.getElementById('sidebar')?.classList.remove('open');

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
      't2v': 'videocam',
      'extend': 'add_to_queue',
      'insert': 'add_box',
      'remove': 'delete_sweep',
      'camera': 'videocam_off',
    };
    return icons[type] || 'work';
  },

  /**
   * Get CSS class for a job type icon.
   */
  jobTypeClass(type) {
    const classes = {
      'text-to-video': 't2v',
      't2v': 't2v',
      'extend': 'extend',
      'insert': 'insert',
      'remove': 'remove',
      'camera': 'camera',
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
