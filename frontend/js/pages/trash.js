/**
 * Trash Page - soft-deleted jobs and projects.
 */
(() => {
  let root = null;
  let state = {
    items: [],
    loading: false,
    error: '',
  };

  function escape(value) {
    return App.escapeHtml(value == null ? '' : String(value));
  }

  async function loadTrash() {
    state.loading = true;
    state.error = '';
    renderIntoRoot();
    try {
      const data = await API.fetch('/api/trash');
      state.items = Array.isArray(data?.items) ? data.items : [];
    } catch (err) {
      state.error = err.message || 'Failed to load trash';
    } finally {
      state.loading = false;
      renderIntoRoot();
    }
  }

  async function mutate(action) {
    const isDelete = action === 'delete-all';
    const message = isDelete
      ? 'Permanently delete all trash items? Active rows are not affected.'
      : 'Restore all trash items?';
    if (!window.confirm(message)) return;

    state.loading = true;
    state.error = '';
    renderIntoRoot();
    try {
      await API.fetch(isDelete ? '/api/trash/permanent' : '/api/trash/restore', {
        method: isDelete ? 'DELETE' : 'POST',
        body: JSON.stringify({ all: true }),
      });
      await loadTrash();
    } catch (err) {
      state.error = err.message || 'Trash mutation failed';
      state.loading = false;
      renderIntoRoot();
    }
  }

  function itemTitle(item) {
    if (item.type === 'project') return item.name || item.project_id || 'Project';
    return item.prompt || item.job_id || 'Job';
  }

  function renderItems() {
    if (state.loading) {
      return '<div class="empty-state"><span class="material-icons">hourglass_empty</span><p>Loading trash...</p></div>';
    }
    if (!state.items.length) {
      return '<div class="empty-state"><span class="material-icons">delete_outline</span><p>Trash is empty.</p></div>';
    }
    return `
      <div class="jobs-table-wrap">
        <table class="jobs-table">
          <thead>
            <tr>
              <th>Type</th>
              <th>Name / prompt</th>
              <th>Project ID</th>
              <th>Job ID</th>
              <th>Deleted</th>
            </tr>
          </thead>
          <tbody>
            ${state.items.map((item) => `
              <tr>
                <td>${escape(item.type)}</td>
                <td>${escape(itemTitle(item))}</td>
                <td><code>${escape(item.project_id || '')}</code></td>
                <td><code>${escape(item.job_id || '')}</code></td>
                <td>${escape(App.formatTileDate ? App.formatTileDate(item.deleted_at) : item.deleted_at)}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    `;
  }

  function renderContent() {
    return `
      <div class="page-header">
        <div>
          <h2>Trash</h2>
          <p class="muted">Soft-deleted jobs and projects. Permanent delete only affects trash rows.</p>
        </div>
        <div class="page-actions">
          <button class="btn btn-secondary" data-action="refresh" ${state.loading ? 'disabled' : ''}>Refresh</button>
          <button class="btn btn-outline" data-action="restore-all" ${state.loading || !state.items.length ? 'disabled' : ''}>Restore All</button>
          <button class="btn btn-danger" data-action="delete-all" ${state.loading || !state.items.length ? 'disabled' : ''}>Delete All</button>
        </div>
      </div>
      ${state.error ? `<div class="alert alert-error">${escape(state.error)}</div>` : ''}
      ${renderItems()}
    `;
  }

  function renderIntoRoot() {
    if (!root) return;
    root.innerHTML = renderContent();
  }

  const page = {
    name: 'trash',
    title: 'Trash',
    icon: 'delete',
    async render() {
      return '<div id="trash-page"></div>';
    },
    mount() {
      root = document.getElementById('trash-page');
      renderIntoRoot();
      loadTrash();
      root?.addEventListener('click', (event) => {
        const button = event.target.closest('[data-action]');
        if (!button) return;
        const action = button.dataset.action;
        if (action === 'refresh') loadTrash();
        if (action === 'restore-all') mutate('restore-all');
        if (action === 'delete-all') mutate('delete-all');
      });
    },
    destroy() {
      root = null;
    },
  };

  App.register(page);
})();
