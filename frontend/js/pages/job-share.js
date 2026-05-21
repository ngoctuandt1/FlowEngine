/**
 * Job Share Page
 * Minimal share-link lifecycle controls for a single job.
 */
(() => {
  function routeJobId() {
    const parts = String(location.hash || '').replace(/^#/, '').split('/');
    return parts.length > 1 ? decodeURIComponent(parts[1] || '') : '';
  }

  function escapeHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  async function copyText(text) {
    if (!text) return;
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const input = document.createElement('input');
    input.value = text;
    document.body.appendChild(input);
    input.select();
    document.execCommand('copy');
    input.remove();
  }

  const page = {
    name: 'job-share',
    title: 'Job Share',
    icon: 'link',
    async render() {
      const jobId = routeJobId();
      if (!jobId) {
        return '<div class="empty-state">Missing job id.</div>';
      }

      return `
        <div class="page-section">
          <div class="page-header">
            <div>
              <h2>Share job</h2>
              <p class="muted">Mint, copy, or revoke public share link.</p>
            </div>
          </div>
          <div class="card" data-job-share-card data-job-id="${escapeHtml(jobId)}">
            <div class="form-row">
              <label>Job ID</label>
              <code>${escapeHtml(jobId)}</code>
            </div>
            <div class="form-row">
              <label>Share URL</label>
              <input data-share-url readonly placeholder="No active share link" />
            </div>
            <div class="button-row">
              <button class="btn primary" data-share-mint>Mint / copy link</button>
              <button class="btn" data-share-copy disabled>Copy</button>
              <button class="btn danger" data-share-revoke>Revoke</button>
            </div>
            <p class="muted" data-share-status></p>
          </div>
        </div>
      `;
    },
    async mounted() {
      const card = document.querySelector('[data-job-share-card]');
      if (!card) return;
      const jobId = card.dataset.jobId;
      const input = card.querySelector('[data-share-url]');
      const status = card.querySelector('[data-share-status]');
      const copyBtn = card.querySelector('[data-share-copy]');

      const setStatus = (message, type = 'info') => {
        if (status) status.textContent = message;
        App.toast?.(message, type);
      };
      const setUrl = (url) => {
        input.value = url || '';
        copyBtn.disabled = !url;
      };

      card.querySelector('[data-share-mint]')?.addEventListener('click', async () => {
        try {
          const result = await API.fetch(`/api/jobs/${encodeURIComponent(jobId)}/share`, { method: 'POST' });
          setUrl(result.share_url || '');
          await copyText(result.share_url || '');
          setStatus('Share link ready and copied.', 'success');
        } catch (err) {
          setStatus(err.message || 'Failed to mint share link.', 'error');
        }
      });

      copyBtn?.addEventListener('click', async () => {
        await copyText(input.value);
        setStatus('Share link copied.', 'success');
      });

      card.querySelector('[data-share-revoke]')?.addEventListener('click', async () => {
        try {
          await API.fetch(`/api/jobs/${encodeURIComponent(jobId)}/share`, { method: 'DELETE' });
          setUrl('');
          setStatus('Share link revoked.', 'success');
        } catch (err) {
          setStatus(err.message || 'Failed to revoke share link.', 'error');
        }
      });
    },
  };

  App.register(page);
})();

