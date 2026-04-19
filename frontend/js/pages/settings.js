/**
 * Settings Page
 * Shows server health, configuration info, and admin actions.
 */
(() => {
  const SettingsPage = {
    name: 'settings',
    title: 'Settings',
    icon: 'settings',

    async render() {
      let health = {};
      let jobCounts = {};

      try {
        const [healthRes, countsRes] = await Promise.allSettled([
          API.fetch('/health'),
          API.jobs.counts(),
        ]);
        if (healthRes.status === 'fulfilled') health = healthRes.value;
        if (countsRes.status === 'fulfilled') jobCounts = countsRes.value;
      } catch (err) {
        console.warn('[Settings] API error:', err.message);
      }

      const totalJobs = Object.values(jobCounts).reduce((a, b) => a + b, 0);

      return `
        <div class="settings-grid">
          <div class="settings-card">
            <h3><span class="material-icons">dns</span> Server Status</h3>
            <div class="detail-list">
              <div class="detail-row">
                <span class="detail-label">Status</span>
                <span class="detail-value">
                  <span class="status-badge status-completed">${health.status || 'unknown'}</span>
                </span>
              </div>
              <div class="detail-row">
                <span class="detail-label">Total Jobs</span>
                <span class="detail-value">${totalJobs}</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">Pending</span>
                <span class="detail-value">${jobCounts.pending || 0}</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">Running</span>
                <span class="detail-value">${jobCounts.running || 0}</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">Completed</span>
                <span class="detail-value">${jobCounts.completed || 0}</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">Failed</span>
                <span class="detail-value">${jobCounts.failed || 0}</span>
              </div>
            </div>
          </div>

          <div class="settings-card">
            <h3><span class="material-icons">build</span> Admin Actions</h3>
            <div class="settings-actions">
              <button class="btn btn-primary" id="settings-recover-btn">
                <span class="material-icons">healing</span> Recover Stale Jobs
              </button>
              <p class="settings-hint">Reset jobs stuck in claimed/running for over 30 minutes</p>

              <button class="btn btn-danger" id="settings-clear-completed-btn">
                <span class="material-icons">delete_sweep</span> Clear Completed Jobs
              </button>
              <p class="settings-hint">Delete all completed jobs from the database</p>
            </div>
          </div>

          <div class="settings-card">
            <h3><span class="material-icons">info</span> About</h3>
            <div class="detail-list">
              <div class="detail-row">
                <span class="detail-label">App</span>
                <span class="detail-value">FlowEngine v0.1.0</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">Stack</span>
                <span class="detail-value">FastAPI + Playwright + Vanilla JS</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">Database</span>
                <span class="detail-value">SQLite (aiosqlite)</span>
              </div>
            </div>
          </div>
        </div>
      `;
    },

    mount() {
      // Recover button
      const recoverBtn = document.getElementById('settings-recover-btn');
      if (recoverBtn) {
        recoverBtn.addEventListener('click', async () => {
          try {
            recoverBtn.disabled = true;
            const result = await API.jobs.recover();
            const count = result.recovered || 0;
            App.toast(
              count > 0 ? `Recovered ${count} stale job(s)` : 'No stale jobs found',
              count > 0 ? 'success' : 'info'
            );
          } catch (err) {
            App.toast('Recovery failed: ' + err.message, 'error');
          } finally {
            recoverBtn.disabled = false;
          }
        });
      }

      // Clear completed button (P2b: single bulk DELETE, was N+1 loop)
      const clearBtn = document.getElementById('settings-clear-completed-btn');
      if (clearBtn) {
        clearBtn.addEventListener('click', async () => {
          if (!confirm('Delete ALL completed jobs? This cannot be undone.')) return;
          try {
            clearBtn.disabled = true;
            const result = await API.fetch('/api/jobs?status=completed', { method: 'DELETE' });
            const count = (result && typeof result.deleted === 'number') ? result.deleted : 0;
            App.toast(`Deleted ${count} completed job(s)`, 'success');
          } catch (err) {
            App.toast('Clear failed: ' + err.message, 'error');
          } finally {
            clearBtn.disabled = false;
          }
        });
      }
    },

    destroy() {},
  };

  App.register(SettingsPage);
})();
