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
          <span class="detail-value">${label === 'Status' ? val : App.escapeHtml(String(val))
          }</span>
        </div>
      `
      )
      .join('');

    const outputs = Array.isArray(job.output_files) ? job.output_files : [];
    let downloadsHtml = '';
    if (outputs.length) {
      const links = outputs
        .map((p) => {
          const base = String(p).split(/[\\/]/).pop();
          const url = '/downloads/' + encodeURIComponent(base);
          const ext = base.split('.').pop().toLowerCase();
          const isVideo = ['mp4', 'webm', 'mov'].includes(ext);
          const isImage = ['jpg', 'jpeg', 'png', 'webp', 'gif'].includes(ext);

          let previewHtml = '';
          if (isVideo) {
            previewHtml = `<video src="${url}" controls autoplay loop muted style="width: 100%; border-radius: 12px; margin-bottom: 16px; background: #0f172a; aspect-ratio: 16/9; object-fit: contain; box-shadow: 0 8px 24px rgba(0,0,0,0.12);"></video>`;
          } else if (isImage) {
            previewHtml = `<img src="${url}" style="width: 100%; border-radius: 12px; margin-bottom: 16px; background: #0f172a; aspect-ratio: 16/9; object-fit: contain; box-shadow: 0 8px 24px rgba(0,0,0,0.12);">`;
          }

          return `
            <div style="width: 100%; margin-bottom: 24px; padding: 16px; background: #f8fafc; border-radius: 16px; border: 1px solid #e2e8f0; box-shadow: 0 4px 6px rgba(0,0,0,0.02);">
              ${previewHtml}
              <div style="display: flex; gap: 8px; justify-content: flex-end;">
                <a class="btn btn-sm btn-primary" href="${url}" target="_blank" rel="noopener">
                  <span class="material-icons" style="font-size:16px">open_in_new</span>
                  Open
                </a>
                <a class="btn btn-sm btn-secondary" href="${url}" download="${App.escapeHtml(base)}" target="_blank" rel="noopener">
                  <span class="material-icons" style="font-size:16px">download</span>
                  Download
                </a>
              </div>
            </div>
          `;
        })
        .join('');
      downloadsHtml = `
        <div class="detail-row" style="align-items:flex-start">
          <span class="detail-label">Output</span>
          <span class="detail-value" style="display:flex;flex-direction:column;gap:6px">${links}</span>
        </div>
      `;
    }

    return `
      <div class="detail-list">${rows}${downloadsHtml}</div>
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
            <div class="section-actions">
              <button class="btn btn-sm btn-outline" id="recover-btn" title="Reset stale claimed/running jobs back to pending">
                <span class="material-icons" style="font-size:16px">healing</span> Recover Stale
              </button>
            </div>
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

      // Recover stale jobs button
      const recoverBtn = document.getElementById('recover-btn');
      if (recoverBtn) {
        recoverBtn.addEventListener('click', async () => {
          try {
            recoverBtn.disabled = true;
            recoverBtn.textContent = 'Recovering...';
            const result = await API.jobs.recover();
            const count = result.recovered || 0;
            App.toast(
              count > 0 ? `Recovered ${count} stale job(s)` : 'No stale jobs found',
              count > 0 ? 'success' : 'info'
            );
            if (count > 0) App._loadPage('dashboard');
          } catch (err) {
            App.toast('Recovery failed: ' + err.message, 'error');
          } finally {
            recoverBtn.disabled = false;
            recoverBtn.innerHTML = '<span class="material-icons" style="font-size:16px">healing</span> Recover Stale';
          }
        });
      }

      // WebSocket listeners for real-time updates
      const refresh = () => {
        if (App.currentPage === 'dashboard') {
          App._loadPage('dashboard');
        }
      };

      const unsub1 = WS.on('job_update', refresh);
      const unsub2 = WS.on('connected', refresh);
      wsCleanup = [unsub1, unsub2];
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
