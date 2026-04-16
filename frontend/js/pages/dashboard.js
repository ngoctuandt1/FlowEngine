/**
 * Dashboard Page
 * Shows job counts by status, recent jobs as cards, real-time updates.
 */
(() => {
  let wsCleanup = [];

  function renderStatCard(icon, label, count, statusClass) {
    return `
      <div class="stat-card">
        <div class="stat-icon ${statusClass}">
          <span class="material-icons">${icon}</span>
        </div>
        <div class="stat-info">
          <h4>${App.escapeHtml(label)}</h4>
          <div class="stat-number">${count}</div>
        </div>
      </div>
    `;
  }

  function renderJobCard(job) {
    const typeIcon = App.jobTypeIcon(job.type);
    const typeClass = App.jobTypeClass(job.type);
    const typeLabel = (job.type || 'unknown').replace(/-/g, ' ');
    const prompt = job.prompt || job.description;
    const promptHtml = prompt
      ? `<p class="job-prompt">${App.escapeHtml(App.truncate(prompt, 120))}</p>`
      : `<p class="job-prompt empty">No prompt</p>`;

    return `
      <div class="job-card" data-job-id="${App.escapeHtml(job.id || job.job_id || '')}">
        <div class="job-card-header">
          <div class="job-type">
            <div class="job-type-icon ${typeClass}">
              <span class="material-icons">${typeIcon}</span>
            </div>
            <span class="job-type-label">${App.escapeHtml(typeLabel)}</span>
          </div>
          <span class="${App.statusBadge(job.status)}">${App.escapeHtml(job.status || 'pending')}</span>
        </div>
        <div class="job-card-body">
          ${promptHtml}
        </div>
        <div class="job-card-footer">
          <div class="job-meta">
            ${job.profile ? `
              <span class="job-meta-item">
                <span class="material-icons">person</span>
                ${App.escapeHtml(App.truncate(job.profile, 16))}
              </span>
            ` : ''}
            ${job.model ? `
              <span class="job-meta-item">
                <span class="material-icons">memory</span>
                ${App.escapeHtml(job.model)}
              </span>
            ` : ''}
          </div>
          <span class="job-meta-item">
            <span class="material-icons">schedule</span>
            ${App.formatDate(job.created_at || job.createdAt)}
          </span>
        </div>
      </div>
    `;
  }

  function renderJobDetailModal(job) {
    const fields = [
      ['ID', job.id || job.job_id],
      ['Type', (job.type || '').replace(/-/g, ' ')],
      ['Status', `<span class="${App.statusBadge(job.status)}">${job.status}</span>`],
      ['Prompt', job.prompt || job.description || '-'],
      ['Model', job.model || '-'],
      ['Profile', job.profile || '-'],
      ['Aspect Ratio', job.aspect_ratio || '-'],
      ['Project URL', job.project_url || '-'],
      ['Parent Job', job.parent_job_id || '-'],
      ['Chain ID', job.chain_id || '-'],
      ['Created', job.created_at || job.createdAt || '-'],
      ['Updated', job.updated_at || job.updatedAt || '-'],
      ['Completed', job.completed_at || '-'],
      ['Error', job.error || '-'],
    ];

    const rows = fields
      .filter(([, val]) => val && val !== '-')
      .map(
        ([label, val]) => `
        <div class="detail-row">
          <span class="detail-label">${label}</span>
          <span class="detail-value">${
            label === 'Status' ? val : App.escapeHtml(String(val))
          }</span>
        </div>
      `
      )
      .join('');

    return `
      <div class="detail-list">${rows}</div>
      <div style="margin-top: 20px; display: flex; gap: 8px;">
        <button class="btn btn-danger btn-sm" onclick="DashboardPage._deleteJob('${App.escapeHtml(
          job.id || job.job_id || ''
        )}')">
          <span class="material-icons" style="font-size:16px">delete</span> Delete
        </button>
      </div>
    `;
  }

  const DashboardPage = {
    name: 'dashboard',
    title: 'Dashboard',
    icon: 'dashboard',

    async render() {
      let counts = { pending: 0, claimed: 0, running: 0, completed: 0, failed: 0 };
      let jobs = [];

      try {
        const [countsRes, jobsRes] = await Promise.allSettled([
          API.jobs.counts(),
          API.jobs.list({ limit: 20 }),
        ]);

        if (countsRes.status === 'fulfilled' && countsRes.value) {
          counts = { ...counts, ...countsRes.value };
        }
        if (jobsRes.status === 'fulfilled' && jobsRes.value) {
          jobs = Array.isArray(jobsRes.value) ? jobsRes.value : jobsRes.value.jobs || [];
        }
      } catch (err) {
        console.warn('[Dashboard] API not available, showing empty state:', err.message);
      }

      const totalPending = (counts.pending || 0) + (counts.claimed || 0);

      const statsHtml = `
        <div class="stats-grid">
          ${renderStatCard('hourglass_empty', 'Pending', totalPending, 'pending')}
          ${renderStatCard('play_circle', 'Running', counts.running || 0, 'running')}
          ${renderStatCard('check_circle', 'Completed', counts.completed || 0, 'completed')}
          ${renderStatCard('cancel', 'Failed', counts.failed || 0, 'failed')}
        </div>
      `;

      let jobsHtml;
      if (jobs.length === 0) {
        jobsHtml = `
          <div class="empty-state">
            <span class="material-icons">work_outline</span>
            <h3>No jobs yet</h3>
            <p>Create your first job to get started.</p>
            <a href="#create" class="btn btn-primary" style="margin-top:16px">
              <span class="material-icons">add</span> Create Job
            </a>
          </div>
        `;
      } else {
        jobsHtml = `
          <div class="section-header">
            <h3 class="section-title">Recent Jobs</h3>
          </div>
          <div class="jobs-grid" id="jobs-grid">
            ${jobs.map(renderJobCard).join('')}
          </div>
        `;
      }

      return statsHtml + jobsHtml;
    },

    mount() {
      // Bind click on job cards
      const grid = document.getElementById('jobs-grid');
      if (grid) {
        grid.addEventListener('click', (e) => {
          const card = e.target.closest('.job-card');
          if (!card) return;
          const jobId = card.dataset.jobId;
          if (jobId) DashboardPage._showJobDetail(jobId);
        });
      }

      // WebSocket listeners for real-time updates
      const refresh = () => {
        if (App.currentPage === 'dashboard') {
          App._loadPage('dashboard');
        }
      };

      const unsub1 = WS.on('job_created', refresh);
      const unsub2 = WS.on('job_updated', refresh);
      const unsub3 = WS.on('job_completed', refresh);
      const unsub4 = WS.on('job_failed', refresh);
      const unsub5 = WS.on('job_deleted', refresh);
      wsCleanup = [unsub1, unsub2, unsub3, unsub4, unsub5];
    },

    destroy() {
      wsCleanup.forEach((fn) => fn && fn());
      wsCleanup = [];
    },

    async _showJobDetail(jobId) {
      try {
        const job = await API.jobs.get(jobId);
        App.openModal('Job Details', renderJobDetailModal(job));
      } catch (err) {
        App.toast('Failed to load job details: ' + err.message, 'error');
      }
    },

    async _deleteJob(jobId) {
      if (!confirm('Delete this job? This cannot be undone.')) return;
      try {
        await API.jobs.delete(jobId);
        App.closeModal();
        App.toast('Job deleted', 'success');
        App._loadPage('dashboard');
      } catch (err) {
        App.toast('Failed to delete job: ' + err.message, 'error');
      }
    },
  };

  // Expose for modal button onclick
  window.DashboardPage = DashboardPage;

  App.register(DashboardPage);
})();
