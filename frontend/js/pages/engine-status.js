/**
 * Engine Status Page
 * Live ops dashboard for workers, profiles, queue health, and failures.
 */
(() => {
  const REFRESH_MS = 5000;
  const ACTIVE_HEARTBEAT_MS = 60000;
  const FAILED_LOG_LIMIT = 10;

  const state = {
    snapshot: null,
    requestId: 0,
    refreshInterval: null,
    liveRefreshTimer: null,
    socketListener: null,
    socketTarget: null,
    wsUnsubs: [],
    root: null,
    clickHandler: null,
  };

  function normalizeJobList(result) {
    return Array.isArray(result) ? result : result?.jobs || [];
  }

  function normalizeProfiles(result) {
    return Array.isArray(result) ? result : result?.profiles || [];
  }

  function normalizeWorkers(result) {
    if (!result) return [];
    if (Array.isArray(result)) {
      return result
        .map((worker) => ({
          id: worker?.id || worker?.worker_id || worker?.name || '',
          lastHeartbeat: worker?.last_heartbeat || worker?.lastHeartbeat || worker?.heartbeat || null,
        }))
        .filter((worker) => worker.id);
    }

    if (typeof result === 'object') {
      return Object.entries(result)
        .map(([id, lastHeartbeat]) => ({ id, lastHeartbeat }))
        .filter((worker) => worker.id);
    }

    return [];
  }

  function coerceDate(value) {
    if (!value) return null;
    const date = value instanceof Date ? value : new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function formatAge(value) {
    const date = coerceDate(value);
    if (!date) return '-';

    const diffMs = Math.max(0, Date.now() - date.getTime());
    const diffSec = Math.floor(diffMs / 1000);

    if (diffSec < 5) return 'Just now';
    if (diffSec < 60) return `${diffSec}s ago`;

    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin}m ago`;

    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;

    const diffDay = Math.floor(diffHr / 24);
    return `${diffDay}d ago`;
  }

  function formatElapsed(ms) {
    if (!Number.isFinite(ms) || ms < 0) return '-';

    const totalSec = Math.floor(ms / 1000);
    const days = Math.floor(totalSec / 86400);
    const hours = Math.floor((totalSec % 86400) / 3600);
    const minutes = Math.floor((totalSec % 3600) / 60);
    const seconds = totalSec % 60;

    if (days > 0) return `${days}d ${hours}h`;
    if (hours > 0) return `${hours}h ${minutes}m`;
    if (minutes > 0) return `${minutes}m ${seconds}s`;
    return `${seconds}s`;
  }

  function resolveUptime(health) {
    if (!health || typeof health !== 'object') return null;

    if (typeof health.uptime === 'string' && health.uptime.trim()) {
      return health.uptime.trim();
    }

    if (Number.isFinite(health.uptime_seconds)) {
      return formatElapsed(health.uptime_seconds * 1000);
    }

    if (Number.isFinite(health.uptime_ms)) {
      return formatElapsed(health.uptime_ms);
    }

    const startedAt = coerceDate(health.started_at || health.startedAt);
    if (startedAt) {
      return formatElapsed(Date.now() - startedAt.getTime());
    }

    return null;
  }

  function escapeAttr(value) {
    return App.escapeHtml(String(value || ''));
  }

  function unique(values) {
    return Array.from(new Set(values.filter(Boolean)));
  }

  function truncateId(id) {
    return App.truncate(String(id || ''), 12);
  }

  function renderJobLink(jobId) {
    if (!jobId) return '<span>-</span>';
    const safeId = String(jobId);
    return `
      <a
        href="#job-detail/${encodeURIComponent(safeId)}"
        class="engine-status-job-link"
        data-job-id="${escapeAttr(safeId)}"
        title="${escapeAttr(safeId)}"
        style="color: var(--accent-hover); text-decoration: none;"
      >
        <code>${App.escapeHtml(truncateId(safeId))}</code>
      </a>
    `;
  }

  function sortByCreatedAsc(jobs) {
    return [...jobs].sort((a, b) => {
      const left = coerceDate(a?.created_at || a?.createdAt)?.getTime() || 0;
      const right = coerceDate(b?.created_at || b?.createdAt)?.getTime() || 0;
      return left - right;
    });
  }

  function sortByRecentFailure(jobs) {
    return [...jobs].sort((a, b) => {
      const left = coerceDate(a?.completed_at || a?.updated_at || a?.created_at)?.getTime() || 0;
      const right = coerceDate(b?.completed_at || b?.updated_at || b?.created_at)?.getTime() || 0;
      return right - left;
    });
  }

  function profileStats(profiles) {
    return profiles.reduce(
      (stats, profile) => {
        const status = profile?.status || (profile?.quarantined ? 'quarantined' : 'available');

        if (status === 'quarantined') {
          stats.quarantined += 1;
        } else if (status === 'busy' || profile?.current_job_id) {
          stats.busy += 1;
        } else {
          stats.idle += 1;
        }

        return stats;
      },
      { idle: 0, busy: 0, quarantined: 0 }
    );
  }

  function buildWorkerRows(workers, profiles, activeJobs) {
    const workerProfiles = new Map();
    const workerJobs = new Map();
    const knownWorkerIds = new Set();

    profiles.forEach((profile) => {
      const workerId = profile?.worker_id;
      if (!workerId) return;
      knownWorkerIds.add(workerId);
      if (!workerProfiles.has(workerId)) workerProfiles.set(workerId, []);
      workerProfiles.get(workerId).push(profile);
    });

    activeJobs.forEach((job) => {
      const workerId = job?.worker_id;
      if (!workerId) return;
      knownWorkerIds.add(workerId);
      if (!workerJobs.has(workerId)) workerJobs.set(workerId, []);
      workerJobs.get(workerId).push(job);
    });

    workers.forEach((worker) => {
      if (worker?.id) knownWorkerIds.add(worker.id);
    });

    const heartbeatByWorker = new Map(
      workers.map((worker) => [worker.id, worker.lastHeartbeat])
    );

    return Array.from(knownWorkerIds)
      .map((workerId) => {
        const assignedProfiles = workerProfiles.get(workerId) || [];
        const jobs = workerJobs.get(workerId) || [];
        const pinnedProfiles = unique([
          ...assignedProfiles.map((profile) => profile?.name || profile?.profile_name || ''),
          ...jobs.map((job) => job?.profile || ''),
        ]).sort((a, b) => a.localeCompare(b));
        const currentJobs = unique([
          ...assignedProfiles.map((profile) => profile?.current_job_id || ''),
          ...jobs.map((job) => job?.id || ''),
        ]);
        const heartbeat = heartbeatByWorker.get(workerId) || null;
        const heartbeatDate = coerceDate(heartbeat);
        const isActive = heartbeatDate
          ? Date.now() - heartbeatDate.getTime() <= ACTIVE_HEARTBEAT_MS
          : null;

        return {
          id: workerId,
          heartbeat,
          isActive,
          pinnedProfiles,
          currentJobs,
        };
      })
      .sort((a, b) => {
        const leftActive = a.isActive ? 1 : 0;
        const rightActive = b.isActive ? 1 : 0;
        if (leftActive !== rightActive) return rightActive - leftActive;

        const leftTime = coerceDate(a.heartbeat)?.getTime() || 0;
        const rightTime = coerceDate(b.heartbeat)?.getTime() || 0;
        if (leftTime !== rightTime) return rightTime - leftTime;

        return a.id.localeCompare(b.id);
      });
  }

  function deriveCounts(countsResult, pendingJobs, claimedJobs, runningJobs, failedJobs) {
    const counts = countsResult && typeof countsResult === 'object' ? countsResult : {};
    return {
      pending: Number.isFinite(counts.pending) ? counts.pending : pendingJobs.length,
      claimed: Number.isFinite(counts.claimed) ? counts.claimed : claimedJobs.length,
      running: Number.isFinite(counts.running) ? counts.running : runningJobs.length,
      failed: Number.isFinite(counts.failed) ? counts.failed : failedJobs.length,
    };
  }

  function buildSnapshot(raw) {
    const profiles = normalizeProfiles(raw.profiles?.value);
    const pendingJobs = sortByCreatedAsc(normalizeJobList(raw.pending?.value));
    const claimedJobs = normalizeJobList(raw.claimed?.value);
    const runningJobs = normalizeJobList(raw.running?.value);
    const failedAll = sortByRecentFailure(normalizeJobList(raw.failed?.value));
    const failedJobs = failedAll.slice(0, FAILED_LOG_LIMIT);
    const activeJobs = [...claimedJobs, ...runningJobs];
    const workers = normalizeWorkers(raw.workers?.value);
    const counts = deriveCounts(raw.counts?.value, pendingJobs, claimedJobs, runningJobs, failedAll);
    const health = raw.health?.value && typeof raw.health.value === 'object' ? raw.health.value : null;
    const uptime = resolveUptime(health);
    const pStats = profileStats(profiles);
    const workerRows = buildWorkerRows(workers, profiles, activeJobs);
    const workersAvailable = raw.workers?.status === 'fulfilled';
    const activeWorkerCount = workersAvailable
      ? workerRows.filter((worker) => worker.isActive).length
      : null;

    return {
      capturedAt: Date.now(),
      health: {
        ok: health?.status === 'ok',
        label: health?.status === 'ok' ? 'Healthy' : (health?.status ? String(health.status) : 'Unknown'),
        uptime,
      },
      workers: {
        available: workersAvailable,
        total: workersAvailable ? workers.length : null,
        active: workersAvailable ? activeWorkerCount : null,
        rows: workerRows,
      },
      profiles: {
        total: profiles.length,
        idle: pStats.idle,
        busy: pStats.busy,
        quarantined: pStats.quarantined,
      },
      jobs: {
        pending: counts.pending,
        claimed: counts.claimed,
        running: counts.running,
        failed: counts.failed,
        inFlight: counts.pending + counts.claimed + counts.running,
        queue: pendingJobs,
        failedRecent: failedJobs,
      },
      meta: {
        workersEndpointError: raw.workers?.status === 'rejected' ? raw.workers.reason : null,
      },
    };
  }

  function renderStatCard({ icon, label, value, detail, statusClass, dotColor = null }) {
    const dotHtml = dotColor
      ? `<span style="display:inline-block; width:10px; height:10px; border-radius:999px; background:${dotColor};"></span>`
      : '';

    return `
      <div class="stat-card">
        <div class="stat-icon ${statusClass}">
          <span class="material-icons">${icon}</span>
        </div>
        <div class="stat-info" style="min-width:0;">
          <h4>${App.escapeHtml(label)}</h4>
          <div class="stat-number" style="display:flex; align-items:center; gap:10px; min-width:0;">
            ${dotHtml}
            <span style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${App.escapeHtml(String(value))}</span>
          </div>
          <div style="margin-top:8px; color: var(--text-muted); font-size: 12px; line-height: 1.45;">
            ${App.escapeHtml(detail)}
          </div>
        </div>
      </div>
    `;
  }

  function renderWorkersPanel(snapshot) {
    const { workers } = snapshot;

    if (!workers.rows.length) {
      const message = workers.available
        ? 'No worker heartbeats or active assignments yet.'
        : 'Worker heartbeat endpoint unavailable in this environment.';

      return `
        <div class="table-container">
          <div class="section-header" style="padding: 18px 20px 0;">
            <div>
              <h3 class="section-title">Active Workers</h3>
              <p style="margin-top:4px; color: var(--text-muted); font-size: 13px;">
                Heartbeats in the last 60 seconds count as active.
              </p>
            </div>
          </div>
          <div class="empty-state" style="padding: 28px 20px 24px;">
            <span class="material-icons">hub</span>
            <h3>No worker data</h3>
            <p>${App.escapeHtml(message)}</p>
          </div>
        </div>
      `;
    }

    const rows = workers.rows
      .map((worker) => {
        const profileLabel = worker.pinnedProfiles.length
          ? App.escapeHtml(worker.pinnedProfiles.join(', '))
          : '-';
        const currentJobHtml = worker.currentJobs.length
          ? worker.currentJobs.map(renderJobLink).join('<span style="color: var(--text-muted);">, </span>')
          : '<span>-</span>';
        const heartbeatStatus = worker.isActive === null
          ? '-'
          : worker.isActive
            ? 'Active'
            : 'Stale';
        const heartbeatBadge = worker.isActive === null
          ? '<span style="color: var(--text-muted);">-</span>'
          : `<span class="${App.statusBadge(worker.isActive ? 'completed' : 'failed')}">${App.escapeHtml(heartbeatStatus)}</span>`;

        return `
          <tr>
            <td title="${escapeAttr(worker.id)}">
              <div style="display:flex; flex-direction:column; gap:4px;">
                <code>${App.escapeHtml(worker.id)}</code>
                <span style="color: var(--text-muted); font-size: 12px;">${heartbeatBadge}</span>
              </div>
            </td>
            <td title="${escapeAttr(profileLabel)}">${profileLabel}</td>
            <td>${currentJobHtml}</td>
            <td title="${escapeAttr(worker.heartbeat || '')}">${App.escapeHtml(formatAge(worker.heartbeat))}</td>
          </tr>
        `;
      })
      .join('');

    return `
      <div class="table-container">
        <div class="section-header" style="padding: 18px 20px 0;">
          <div>
            <h3 class="section-title">Active Workers</h3>
            <p style="margin-top:4px; color: var(--text-muted); font-size: 13px;">
              Name, pinned profiles, current job, and last heartbeat age.
            </p>
          </div>
        </div>
        <table>
          <thead>
            <tr>
              <th>Worker</th>
              <th>Profile Pinned</th>
              <th>Current Job</th>
              <th>Heartbeat Age</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }

  function renderQueuePanel(snapshot) {
    const jobs = snapshot.jobs.queue;

    if (!jobs.length) {
      return `
        <div class="table-container">
          <div class="section-header" style="padding: 18px 20px 0;">
            <div>
              <h3 class="section-title">Job Queue</h3>
              <p style="margin-top:4px; color: var(--text-muted); font-size: 13px;">
                Pending jobs sorted oldest-first.
              </p>
            </div>
          </div>
          <div class="empty-state" style="padding: 28px 20px 24px;">
            <span class="material-icons">inbox</span>
            <h3>Queue is empty</h3>
            <p>No pending jobs are waiting to be claimed.</p>
          </div>
        </div>
      `;
    }

    const rows = jobs
      .map((job) => {
        const profile = job.profile || 'Unpinned';
        const type = (job.type || 'unknown').replace(/-/g, ' ');

        return `
          <tr>
            <td>${renderJobLink(job.id)}</td>
            <td title="${escapeAttr(profile)}">${App.escapeHtml(profile)}</td>
            <td>
              <div style="display:flex; align-items:center; gap:8px;">
                <span class="material-icons" style="font-size:18px; color: var(--accent-hover);">
                  ${App.escapeHtml(App.jobTypeIcon(job.type))}
                </span>
                <span>${App.escapeHtml(type)}</span>
              </div>
            </td>
            <td title="${escapeAttr(job.created_at || job.createdAt || '')}">${App.escapeHtml(formatAge(job.created_at || job.createdAt))}</td>
          </tr>
        `;
      })
      .join('');

    return `
      <div class="table-container">
        <div class="section-header" style="padding: 18px 20px 0;">
          <div>
            <h3 class="section-title">Job Queue</h3>
            <p style="margin-top:4px; color: var(--text-muted); font-size: 13px;">
              Pending jobs sorted oldest-first.
            </p>
          </div>
        </div>
        <table>
          <thead>
            <tr>
              <th>Job</th>
              <th>Profile</th>
              <th>Type</th>
              <th>Age</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }

  function renderFailureStrip(snapshot) {
    const jobs = snapshot.jobs.failedRecent;

    if (!jobs.length) {
      return `
        <div class="card">
          <div class="section-header" style="margin-bottom: 12px;">
            <div>
              <h3 class="section-title">Last Errors</h3>
              <p style="margin-top:4px; color: var(--text-muted); font-size: 13px;">
                Last 10 failed jobs and their surfaced error text.
              </p>
            </div>
          </div>
          <div class="empty-state" style="padding: 22px 20px 12px;">
            <span class="material-icons">task_alt</span>
            <h3>No recent failures</h3>
            <p>The failed-job strip is clear right now.</p>
          </div>
        </div>
      `;
    }

    const items = jobs
      .map((job) => {
        const errorText = job.error ? App.truncate(String(job.error), 180) : 'No error text captured.';
        const when = formatAge(job.completed_at || job.updated_at || job.created_at);

        return `
          <div style="display:grid; grid-template-columns: minmax(140px, 180px) 1fr auto; gap:12px; align-items:start; padding: 14px 0; border-top: 1px solid var(--border);">
            <div style="display:flex; flex-direction:column; gap:6px;">
              <div>${renderJobLink(job.id)}</div>
              <span class="${App.statusBadge('failed')}">failed</span>
            </div>
            <div style="color: var(--text-secondary); font-size: 13px; line-height: 1.55;">
              ${App.escapeHtml(errorText)}
            </div>
            <div style="color: var(--text-muted); font-size: 12px; white-space: nowrap;">
              ${App.escapeHtml(when)}
            </div>
          </div>
        `;
      })
      .join('');

    return `
      <div class="card">
        <div class="section-header" style="margin-bottom: 2px;">
          <div>
            <h3 class="section-title">Last Errors</h3>
            <p style="margin-top:4px; color: var(--text-muted); font-size: 13px;">
              Last 10 failed jobs and their surfaced error text.
            </p>
          </div>
        </div>
        <div>${items}</div>
      </div>
    `;
  }

  function renderContent(snapshot) {
    const workersValue = snapshot.workers.available
      ? `${snapshot.workers.active} / ${snapshot.workers.total}`
      : '-';
    const workersDetail = snapshot.workers.available
      ? 'active / total heartbeats in the last 60s'
      : 'Worker heartbeat API unavailable';
    const profilesValue = String(snapshot.profiles.idle);
    const profilesDetail = `${snapshot.profiles.busy} busy | ${snapshot.profiles.quarantined} quarantined`;
    const jobsValue = String(snapshot.jobs.inFlight);
    const jobsDetail = `${snapshot.jobs.running} running | ${snapshot.jobs.pending} pending | ${snapshot.jobs.claimed} claimed`;
    const serverValue = snapshot.health.label;
    const serverDetail = `Uptime ${snapshot.health.uptime || '-'}`;

    return `
      <div style="display:grid; gap:16px;">
        <div class="section-header">
          <div>
            <h3 class="section-title">Live Engine Health</h3>
            <p style="margin-top:4px; color: var(--text-muted); font-size: 13px;">
              Polling every 5 seconds with WebSocket-triggered refreshes for job state changes.
            </p>
          </div>
          <div style="color: var(--text-muted); font-size: 12px;">
            Refreshed ${App.escapeHtml(formatAge(snapshot.capturedAt))}
          </div>
        </div>

        <div class="stats-grid">
          ${renderStatCard({
            icon: 'monitor_heart',
            label: 'Server Status',
            value: serverValue,
            detail: serverDetail,
            statusClass: snapshot.health.ok ? 'completed' : 'pending',
            dotColor: snapshot.health.ok ? 'var(--success)' : 'var(--warning)',
          })}
          ${renderStatCard({
            icon: 'hub',
            label: 'Workers',
            value: workersValue,
            detail: workersDetail,
            statusClass: snapshot.workers.available ? 'running' : 'pending',
          })}
          ${renderStatCard({
            icon: 'people',
            label: 'Profiles',
            value: `${profilesValue} idle`,
            detail: profilesDetail,
            statusClass: snapshot.profiles.quarantined > 0
              ? 'pending'
              : snapshot.profiles.busy > 0
                ? 'running'
                : 'completed',
          })}
          ${renderStatCard({
            icon: 'dns',
            label: 'Jobs In Flight',
            value: jobsValue,
            detail: jobsDetail,
            statusClass: snapshot.jobs.failed > 0 ? 'pending' : 'running',
          })}
        </div>

        <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); gap:16px;">
          ${renderWorkersPanel(snapshot)}
          ${renderQueuePanel(snapshot)}
        </div>

        ${renderFailureStrip(snapshot)}
      </div>
    `;
  }

  function renderSnapshot() {
    if (!state.root || !state.snapshot) return;
    state.root.innerHTML = renderContent(state.snapshot);
  }

  async function fetchSnapshot() {
    const requestId = ++state.requestId;

    const [health, counts, profiles, workers, pending, claimed, running, failed] = await Promise.allSettled([
      API.fetch('/health'),
      API.jobs.counts(),
      API.profiles.list(),
      API.fetch('/api/worker/workers'),
      API.jobs.list({ status: 'pending' }),
      API.jobs.list({ status: 'claimed' }),
      API.jobs.list({ status: 'running' }),
      API.jobs.list({ status: 'failed' }),
    ]);

    if (requestId !== state.requestId) return state.snapshot;

    state.snapshot = buildSnapshot({
      health,
      counts,
      profiles,
      workers,
      pending,
      claimed,
      running,
      failed,
    });

    return state.snapshot;
  }

  async function refreshSnapshot() {
    try {
      await fetchSnapshot();
      if (App.currentPage === 'engine-status') {
        renderSnapshot();
      }
    } catch (_) {
      // Promise.allSettled keeps endpoint failures local; only unexpected
      // render-time exceptions would land here, and the page should stay quiet.
    }
  }

  function scheduleLiveRefresh() {
    if (App.currentPage !== 'engine-status') return;
    if (state.liveRefreshTimer) return;

    state.liveRefreshTimer = setTimeout(() => {
      state.liveRefreshTimer = null;
      refreshSnapshot();
    }, 250);
  }

  function handleSocketMessage(event) {
    try {
      const message = JSON.parse(event.data);
      const eventName = message.event || message.type;
      const payload = message.data || message.payload;
      if (eventName === 'job_update' && payload?.id) {
        scheduleLiveRefresh();
      }
    } catch (_) {
      // Ignore malformed messages from other sources.
    }
  }

  function attachSocketListener() {
    if (!state.socketListener) {
      state.socketListener = handleSocketMessage;
    }

    const socket = WS.socket;
    if (!socket || typeof socket.addEventListener !== 'function' || socket === state.socketTarget) {
      return;
    }

    if (state.socketTarget && typeof state.socketTarget.removeEventListener === 'function') {
      state.socketTarget.removeEventListener('message', state.socketListener);
    }

    state.socketTarget = socket;
    socket.addEventListener('message', state.socketListener);
  }

  function detachSocketListener() {
    if (state.socketTarget && state.socketListener && typeof state.socketTarget.removeEventListener === 'function') {
      state.socketTarget.removeEventListener('message', state.socketListener);
    }
    state.socketTarget = null;
  }

  function renderJobModal(job) {
    const fields = [
      ['ID', job.id || '-'],
      ['Status', job.status || '-'],
      ['Type', job.type || '-'],
      ['Profile', job.profile || '-'],
      ['Worker', job.worker_id || '-'],
      ['Created', job.created_at || '-'],
      ['Updated', job.updated_at || '-'],
      ['Completed', job.completed_at || '-'],
      ['Chain ID', job.chain_id || '-'],
      ['Parent Job', job.parent_job_id || '-'],
      ['Project URL', job.project_url || '-'],
      ['Error', job.error || '-'],
    ];

    return `
      <div class="detail-list">
        ${fields
          .map(([label, value]) => `
            <div class="detail-row">
              <span class="detail-label">${App.escapeHtml(label)}</span>
              <span class="detail-value">
                ${label === 'Status'
                  ? `<span class="${App.statusBadge(value)}">${App.escapeHtml(String(value))}</span>`
                  : App.escapeHtml(String(value))}
              </span>
            </div>
          `)
          .join('')}
      </div>
      <pre style="margin-top:16px; padding:16px; border-radius: 12px; background: #0a0a0c; border: 1px solid var(--border); color: var(--text-secondary); font-size: 12px; line-height: 1.55; white-space: pre-wrap; overflow-wrap: anywhere;">${App.escapeHtml(JSON.stringify(job, null, 2))}</pre>
    `;
  }

  async function showJobDetail(jobId) {
    try {
      const job = await API.jobs.get(jobId);
      App.openModal(`Job ${truncateId(jobId)}`, renderJobModal(job));
    } catch (err) {
      App.toast('Failed to load job details: ' + err.message, 'error');
    }
  }

  const EngineStatusPage = {
    name: 'engine-status',
    title: 'Engine Status',

    async render() {
      await fetchSnapshot();
      return `<div id="engine-status-root">${renderContent(state.snapshot)}</div>`;
    },

    mount() {
      state.root = document.getElementById('engine-status-root');

      if (state.root) {
        state.clickHandler = (event) => {
          const link = event.target.closest('.engine-status-job-link');
          if (!link) return;
          event.preventDefault();
          const jobId = link.dataset.jobId;
          if (jobId) showJobDetail(jobId);
        };

        state.root.addEventListener('click', state.clickHandler);
      }

      if (state.refreshInterval) {
        clearInterval(state.refreshInterval);
      }
      state.refreshInterval = setInterval(() => {
        refreshSnapshot();
      }, REFRESH_MS);

      attachSocketListener();
      state.wsUnsubs.push(
        WS.on('connected', () => {
          attachSocketListener();
          scheduleLiveRefresh();
        })
      );
    },

    destroy() {
      state.requestId += 1;

      if (state.refreshInterval) {
        clearInterval(state.refreshInterval);
        state.refreshInterval = null;
      }

      if (state.liveRefreshTimer) {
        clearTimeout(state.liveRefreshTimer);
        state.liveRefreshTimer = null;
      }

      state.wsUnsubs.forEach((unsubscribe) => {
        try {
          unsubscribe?.();
        } catch (_) {
          // Ignore cleanup failures.
        }
      });
      state.wsUnsubs = [];

      detachSocketListener();

      if (state.root && state.clickHandler) {
        state.root.removeEventListener('click', state.clickHandler);
      }

      state.root = null;
      state.clickHandler = null;
    },
  };

  App.register(EngineStatusPage);
})();
