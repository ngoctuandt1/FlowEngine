/**
 * Media Tools Page
 * Exposes existing backend operations for cut, merge, download, and retarget.
 */
(() => {
  const { MODELS, DEFAULT_MODEL, ASPECT_RATIOS, DEFAULT_ASPECT } = CONST;

  const TAB_META = {
    cut: {
      icon: 'content_cut',
      label: 'Video Cut',
      submitLabel: 'Create Cut',
      loadingLabel: 'Cutting...',
    },
    merge: {
      icon: 'merge_type',
      label: 'Video Merge',
      submitLabel: 'Merge Videos',
      loadingLabel: 'Merging...',
    },
    download: {
      icon: 'download',
      label: 'Video Download',
      submitLabel: 'Fetch Video',
      loadingLabel: 'Fetching...',
    },
    retarget: {
      icon: 'center_focus_strong',
      label: 'Frame Retarget',
      submitLabel: 'Queue Retarget',
      loadingLabel: 'Queueing...',
    },
  };

  const DOWNLOAD_HEIGHTS = [
    { value: 1080, label: '1080p' },
    { value: 720, label: '720p' },
    { value: 480, label: '480p' },
    { value: 360, label: '360p' },
  ];

  let activeTab = 'cut';
  let profiles = [];
  let state = createInitialState();

  function createInitialState() {
    return {
      cut: {
        input_path: '',
        start_seconds: '0',
        end_seconds: '',
        error: '',
        result: null,
      },
      merge: {
        input_paths: '',
        output_name: '',
        error: '',
        result: null,
      },
      download: {
        url: '',
        max_height: 1080,
        error: '',
        result: null,
      },
      retarget: {
        reference_video_path: '',
        new_prompt: '',
        profile: '',
        aspect_ratio: DEFAULT_ASPECT,
        model: DEFAULT_MODEL,
        frame_seconds: '1',
        error: '',
        result: null,
      },
    };
  }

  function escapeAttr(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  function normalizePath(path) {
    return String(path || '')
      .trim()
      .replace(/\\/g, '/')
      .replace(/^\/+/, '');
  }

  function buildPublicUrl(root, relativePath) {
    let normalized = normalizePath(relativePath);
    if (!normalized) return '';
    if (normalized.startsWith(`${root}/`)) {
      normalized = normalized.slice(root.length + 1);
    }
    return new URL(`/${root}/${normalized}`, window.location.origin).href;
  }

  function formatSeconds(value) {
    if (typeof value !== 'number' || Number.isNaN(value)) return 'Unavailable';
    return `${value.toFixed(2).replace(/\.00$/, '').replace(/(\.\d)0$/, '$1')}s`;
  }

  function getProfileName(profile) {
    return profile?.name || profile?.profile_name || profile?.id || profile?.profile_id || '';
  }

  function renderOptions(items, selected, { includeBlank = false, blankLabel = '' } = {}) {
    const options = [];

    if (includeBlank) {
      options.push(`<option value="">${App.escapeHtml(blankLabel)}</option>`);
    }

    items.forEach((item) => {
      const value = typeof item === 'string' ? item : item.value;
      const label = typeof item === 'string' ? item : item.label;
      const isSelected = String(value) === String(selected) ? ' selected' : '';
      options.push(
        `<option value="${escapeAttr(value)}"${isSelected}>${App.escapeHtml(label)}</option>`
      );
    });

    return options.join('');
  }

  function renderProfileOptions(selected) {
    const seen = new Set();
    const options = ['<option value="">(any available)</option>'];

    profiles.forEach((profile) => {
      const name = getProfileName(profile);
      if (!name || seen.has(name)) return;
      seen.add(name);
      const isSelected = name === selected ? ' selected' : '';
      options.push(`<option value="${escapeAttr(name)}"${isSelected}>${App.escapeHtml(name)}</option>`);
    });

    return options.join('');
  }

  function renderTabs() {
    return Object.entries(TAB_META).map(([key, meta]) => `
      <button
        type="button"
        class="btn ${activeTab === key ? 'btn-primary' : 'btn-outline'} btn-sm"
        data-switch-tab="${key}"
      >
        <span class="material-icons">${meta.icon}</span>
        ${App.escapeHtml(meta.label)}
      </button>
    `).join('');
  }

  function renderFeedback(tabKey) {
    const tabState = state[tabKey];
    if (!tabState) return '';

    if (tabState.error) {
      return `
        <div class="card" style="margin-top: 16px; border-color: var(--error); background: rgba(231, 76, 60, 0.08);">
          <div style="display:flex; align-items:flex-start; gap:10px;">
            <span class="material-icons" style="color:var(--error);">error</span>
            <div>
              <strong>Request failed</strong>
              <div style="font-size:13px; color:var(--text-secondary); margin-top:4px;">
                ${App.escapeHtml(tabState.error)}
              </div>
            </div>
          </div>
        </div>
      `;
    }

    if (!tabState.result) return '';

    switch (tabKey) {
      case 'cut':
        return renderMediaResult({
          title: 'Cut created',
          description: `Duration: ${formatSeconds(tabState.result.duration_seconds)}`,
          relativePath: tabState.result.output_path,
          root: 'downloads',
          preview: 'video',
          details: [
            `Output path: ${tabState.result.output_path || '-'}`,
          ],
        });
      case 'merge':
        return renderMediaResult({
          title: 'Merge completed',
          description: `Sources: ${tabState.result.source_count || 0} | Duration: ${formatSeconds(tabState.result.duration_seconds)}`,
          relativePath: tabState.result.output_path,
          root: 'downloads',
          preview: 'video',
          details: [
            `Output path: ${tabState.result.output_path || '-'}`,
          ],
        });
      case 'download':
        return renderMediaResult({
          title: tabState.result.title || 'Video fetched',
          description: `Source URL: ${tabState.result.source_url || '-'}`,
          relativePath: tabState.result.output_path,
          root: 'downloads',
          preview: 'video',
          details: [
            `Duration: ${tabState.result.duration_seconds != null ? formatSeconds(Number(tabState.result.duration_seconds)) : 'Unavailable'}`,
            `Output path: ${tabState.result.output_path || '-'}`,
          ],
        });
      case 'retarget':
        return renderRetargetResult(tabState.result);
      default:
        return '';
    }
  }

  function renderMediaResult({ title, description, relativePath, root, preview, details }) {
    const url = buildPublicUrl(root, relativePath);
    const previewHtml = preview === 'video' && url
      ? `
        <video
          controls
          preload="metadata"
          src="${escapeAttr(url)}"
          style="width:100%; max-height:320px; margin-top:14px; border-radius:12px; border:1px solid var(--border-light); background:#000;"
        ></video>
      `
      : '';
    const detailRows = details.map((detail) => `
      <div style="font-size:12px; color:var(--text-secondary);">${App.escapeHtml(detail)}</div>
    `).join('');

    return `
      <div class="card" style="margin-top: 16px; border-color: var(--success); background: rgba(46, 204, 113, 0.08);">
        <div style="display:flex; align-items:flex-start; gap:10px;">
          <span class="material-icons" style="color:var(--success);">check_circle</span>
          <div style="flex:1; min-width:0;">
            <strong>${App.escapeHtml(title)}</strong>
            <div style="font-size:13px; color:var(--text-secondary); margin-top:4px;">
              ${App.escapeHtml(description)}
            </div>
            <div style="margin-top:12px; padding:12px; background:var(--bg-input); border:1px solid var(--border-light); border-radius:12px;">
              <div style="font-size:12px; color:var(--text-muted); margin-bottom:6px;">Public URL</div>
              <code style="display:block; word-break:break-all; font-size:12px;">${App.escapeHtml(url || '(unavailable)')}</code>
            </div>
            <div style="display:flex; flex-wrap:wrap; gap:8px; margin-top:12px;">
              <a class="btn btn-outline btn-sm" href="${escapeAttr(url)}" target="_blank" rel="noopener">
                <span class="material-icons">open_in_new</span> Open output
              </a>
              <button type="button" class="btn btn-outline btn-sm" data-copy="${escapeAttr(url)}" data-copy-label="Output URL">
                <span class="material-icons">content_copy</span> Copy URL
              </button>
            </div>
            <div style="display:grid; gap:4px; margin-top:12px;">
              ${detailRows}
            </div>
            ${previewHtml}
          </div>
        </div>
      </div>
    `;
  }

  function renderRetargetResult(result) {
    const frameUrl = buildPublicUrl('uploads', result?.frame_path);
    return `
      <div class="card" style="margin-top: 16px; border-color: var(--success); background: rgba(46, 204, 113, 0.08);">
        <div style="display:flex; align-items:flex-start; gap:10px;">
          <span class="material-icons" style="color:var(--success);">check_circle</span>
          <div style="flex:1; min-width:0;">
            <strong>Retarget job queued</strong>
            <div style="font-size:13px; color:var(--text-secondary); margin-top:4px;">
              ${App.escapeHtml(result?.message || 'Reference frame extracted and job submitted.')}
            </div>
            <div style="display:grid; gap:4px; margin-top:12px;">
              <div style="font-size:12px; color:var(--text-secondary);">Job ID: <code>${App.escapeHtml(result?.job_id || '-')}</code></div>
              <div style="font-size:12px; color:var(--text-secondary);">Frame path: ${App.escapeHtml(result?.frame_path || '-')}</div>
            </div>
            <div style="margin-top:12px; padding:12px; background:var(--bg-input); border:1px solid var(--border-light); border-radius:12px;">
              <div style="font-size:12px; color:var(--text-muted); margin-bottom:6px;">Frame URL</div>
              <code style="display:block; word-break:break-all; font-size:12px;">${App.escapeHtml(frameUrl || '(unavailable)')}</code>
            </div>
            <div style="display:flex; flex-wrap:wrap; gap:8px; margin-top:12px;">
              <a class="btn btn-outline btn-sm" href="${escapeAttr(frameUrl)}" target="_blank" rel="noopener">
                <span class="material-icons">open_in_new</span> Open frame
              </a>
              <button type="button" class="btn btn-outline btn-sm" data-copy="${escapeAttr(frameUrl)}" data-copy-label="Frame URL">
                <span class="material-icons">content_copy</span> Copy URL
              </button>
              <button type="button" class="btn btn-outline btn-sm" data-copy="${escapeAttr(result?.job_id || '')}" data-copy-label="Job ID">
                <span class="material-icons">badge</span> Copy Job ID
              </button>
            </div>
            ${frameUrl ? `
              <img
                src="${escapeAttr(frameUrl)}"
                alt="Extracted reference frame"
                style="width:100%; max-height:320px; object-fit:contain; margin-top:14px; border-radius:12px; border:1px solid var(--border-light); background:#000;"
              >
            ` : ''}
          </div>
        </div>
      </div>
    `;
  }

  function renderCutForm() {
    const tabState = state.cut;
    return `
      <form data-tool-tab="cut">
        <div class="section-header" style="margin-bottom: 20px; align-items:flex-start;">
          <div>
            <h3 class="section-title" style="display:flex; align-items:center; gap:8px;">
              <span class="material-icons">content_cut</span> Video Cut
            </h3>
            <p style="margin:6px 0 0; font-size:13px; color:var(--text-secondary);">
              Trim a file under <code>downloads/</code> or <code>uploads/</code> by start/end seconds.
            </p>
          </div>
        </div>
        <div class="form-group">
          <label class="form-label">Input Path <span class="required">*</span></label>
          <input
            type="text"
            class="form-input"
            data-state-tab="cut"
            data-state-field="input_path"
            value="${escapeAttr(tabState.input_path)}"
            placeholder="downloads/fetched/example.mp4"
          >
          <span class="form-hint">Remote URLs are not accepted here. Use Video Download first if needed.</span>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label class="form-label">Start Seconds <span class="required">*</span></label>
            <input
              type="number"
              class="form-input"
              data-state-tab="cut"
              data-state-field="start_seconds"
              value="${escapeAttr(tabState.start_seconds)}"
              min="0"
              step="0.01"
              placeholder="0"
            >
          </div>
          <div class="form-group">
            <label class="form-label">End Seconds <span class="required">*</span></label>
            <input
              type="number"
              class="form-input"
              data-state-tab="cut"
              data-state-field="end_seconds"
              value="${escapeAttr(tabState.end_seconds)}"
              min="0"
              step="0.01"
              placeholder="12.5"
            >
          </div>
        </div>
        <div style="display:flex; flex-wrap:wrap; gap:12px; margin-top:24px;">
          <button class="btn btn-primary" type="submit" id="media-tools-submit">
            <span class="material-icons">content_cut</span> Create Cut
          </button>
          <button class="btn btn-outline" type="button" data-reset-tab="cut">
            <span class="material-icons">refresh</span> Reset
          </button>
        </div>
      </form>
      ${renderFeedback('cut')}
    `;
  }

  function renderMergeForm() {
    const tabState = state.merge;
    return `
      <form data-tool-tab="merge">
        <div class="section-header" style="margin-bottom: 20px; align-items:flex-start;">
          <div>
            <h3 class="section-title" style="display:flex; align-items:center; gap:8px;">
              <span class="material-icons">merge_type</span> Video Merge
            </h3>
            <p style="margin:6px 0 0; font-size:13px; color:var(--text-secondary);">
              Concatenate 2-20 local files. Enter one path per line from <code>downloads/</code> or <code>uploads/</code>.
            </p>
          </div>
        </div>
        <div class="form-group">
          <label class="form-label">Input Paths <span class="required">*</span></label>
          <textarea
            class="form-textarea"
            data-state-tab="merge"
            data-state-field="input_paths"
            rows="7"
            placeholder="downloads/fetched/clip-1.mp4&#10;downloads/fetched/clip-2.mp4"
          >${App.escapeHtml(tabState.input_paths)}</textarea>
        </div>
        <div class="form-group">
          <label class="form-label">Output Name (optional)</label>
          <input
            type="text"
            class="form-input"
            data-state-tab="merge"
            data-state-field="output_name"
            value="${escapeAttr(tabState.output_name)}"
            placeholder="launch-recap.mp4"
          >
          <span class="form-hint">Accepted by the API, but the current backend still generates the final filename.</span>
        </div>
        <div style="display:flex; flex-wrap:wrap; gap:12px; margin-top:24px;">
          <button class="btn btn-primary" type="submit" id="media-tools-submit">
            <span class="material-icons">merge_type</span> Merge Videos
          </button>
          <button class="btn btn-outline" type="button" data-reset-tab="merge">
            <span class="material-icons">refresh</span> Reset
          </button>
        </div>
      </form>
      ${renderFeedback('merge')}
    `;
  }

  function renderDownloadForm() {
    const tabState = state.download;
    return `
      <form data-tool-tab="download">
        <div class="section-header" style="margin-bottom: 20px; align-items:flex-start;">
          <div>
            <h3 class="section-title" style="display:flex; align-items:center; gap:8px;">
              <span class="material-icons">download</span> Video Download
            </h3>
            <p style="margin:6px 0 0; font-size:13px; color:var(--text-secondary);">
              Fetch a remote HTTP/HTTPS video URL into <code>downloads/fetched/</code> so it can be reused by the other tools.
            </p>
          </div>
        </div>
        <div class="form-group">
          <label class="form-label">Source URL <span class="required">*</span></label>
          <input
            type="url"
            class="form-input"
            data-state-tab="download"
            data-state-field="url"
            value="${escapeAttr(tabState.url)}"
            placeholder="https://example.com/video.mp4"
          >
        </div>
        <div class="form-group" style="max-width: 280px;">
          <label class="form-label">Max Height</label>
          <select class="form-select" data-state-tab="download" data-state-field="max_height">
            ${renderOptions(DOWNLOAD_HEIGHTS, tabState.max_height)}
          </select>
        </div>
        <div style="display:flex; flex-wrap:wrap; gap:12px; margin-top:24px;">
          <button class="btn btn-primary" type="submit" id="media-tools-submit">
            <span class="material-icons">download</span> Fetch Video
          </button>
          <button class="btn btn-outline" type="button" data-reset-tab="download">
            <span class="material-icons">refresh</span> Reset
          </button>
        </div>
      </form>
      ${renderFeedback('download')}
    `;
  }

  function renderRetargetForm() {
    const tabState = state.retarget;
    return `
      <form data-tool-tab="retarget">
        <div class="section-header" style="margin-bottom: 20px; align-items:flex-start;">
          <div>
            <h3 class="section-title" style="display:flex; align-items:center; gap:8px;">
              <span class="material-icons">center_focus_strong</span> Frame Retarget
            </h3>
            <p style="margin:6px 0 0; font-size:13px; color:var(--text-secondary);">
              Extract a frame from a local reference video, then queue a <code>frames-to-video</code> job with new prompt/model settings.
            </p>
          </div>
        </div>
        <div class="form-group">
          <label class="form-label">Reference Video Path <span class="required">*</span></label>
          <input
            type="text"
            class="form-input"
            data-state-tab="retarget"
            data-state-field="reference_video_path"
            value="${escapeAttr(tabState.reference_video_path)}"
            placeholder="downloads/fetched/source.mp4"
          >
        </div>
        <div class="form-group">
          <label class="form-label">New Prompt <span class="required">*</span></label>
          <textarea
            class="form-textarea"
            data-state-tab="retarget"
            data-state-field="new_prompt"
            rows="5"
            placeholder="Describe how the retargeted video should look..."
          >${App.escapeHtml(tabState.new_prompt)}</textarea>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label class="form-label">Profile (optional)</label>
            <select class="form-select" data-state-tab="retarget" data-state-field="profile">
              ${renderProfileOptions(tabState.profile)}
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">Frame Seconds</label>
            <input
              type="number"
              class="form-input"
              data-state-tab="retarget"
              data-state-field="frame_seconds"
              value="${escapeAttr(tabState.frame_seconds)}"
              min="0"
              step="0.01"
              placeholder="1.0"
            >
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label class="form-label">Model</label>
            <select class="form-select" data-state-tab="retarget" data-state-field="model">
              ${renderOptions(MODELS, tabState.model)}
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">Aspect Ratio</label>
            <select class="form-select" data-state-tab="retarget" data-state-field="aspect_ratio">
              ${renderOptions(ASPECT_RATIOS, tabState.aspect_ratio)}
            </select>
          </div>
        </div>
        <div style="display:flex; flex-wrap:wrap; gap:12px; margin-top:24px;">
          <button class="btn btn-primary" type="submit" id="media-tools-submit">
            <span class="material-icons">center_focus_strong</span> Queue Retarget
          </button>
          <button class="btn btn-outline" type="button" data-reset-tab="retarget">
            <span class="material-icons">refresh</span> Reset
          </button>
        </div>
      </form>
      ${renderFeedback('retarget')}
    `;
  }

  function renderActiveTab() {
    if (activeTab === 'cut') return renderCutForm();
    if (activeTab === 'merge') return renderMergeForm();
    if (activeTab === 'download') return renderDownloadForm();
    return renderRetargetForm();
  }

  async function loadProfiles() {
    try {
      const result = await API.profiles.list();
      profiles = Array.isArray(result) ? result : (result?.profiles || []);
    } catch (err) {
      console.warn('[MediaTools] could not load profiles:', err.message);
      profiles = [];
    }
  }

  function resetTab(tabKey) {
    state[tabKey] = createInitialState()[tabKey];
  }

  function updateStateField(tabKey, field, value) {
    if (!state[tabKey] || !(field in state[tabKey])) return;
    state[tabKey][field] = tabKey === 'download' && field === 'max_height'
      ? Number(value)
      : value;
  }

  function listFromTextarea(text) {
    return String(text || '')
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);
  }

  function validateTab(tabKey) {
    const tabState = state[tabKey];

    if (tabKey === 'cut') {
      const start = Number(tabState.start_seconds);
      const end = Number(tabState.end_seconds);
      if (!tabState.input_path.trim()) return 'Input path is required.';
      if (!Number.isFinite(start) || start < 0) return 'Start seconds must be a number >= 0.';
      if (!Number.isFinite(end) || end <= 0) return 'End seconds must be a number > 0.';
      if (start >= end) return 'Start seconds must be less than end seconds.';
      return null;
    }

    if (tabKey === 'merge') {
      const inputPaths = listFromTextarea(tabState.input_paths);
      if (inputPaths.length < 2) return 'Enter at least 2 input paths, one per line.';
      if (inputPaths.length > 20) return 'Merge accepts at most 20 input paths.';
      return null;
    }

    if (tabKey === 'download') {
      if (!tabState.url.trim()) return 'Source URL is required.';
      try {
        const parsed = new URL(tabState.url);
        if (!['http:', 'https:'].includes(parsed.protocol)) {
          return 'Source URL must use http or https.';
        }
      } catch {
        return 'Source URL must be a valid absolute URL.';
      }
      return null;
    }

    if (!tabState.reference_video_path.trim()) return 'Reference video path is required.';
    if (!tabState.new_prompt.trim()) return 'New prompt is required.';
    const frameSeconds = Number(tabState.frame_seconds);
    if (!Number.isFinite(frameSeconds) || frameSeconds < 0) {
      return 'Frame seconds must be a number >= 0.';
    }
    return null;
  }

  function buildPayload(tabKey) {
    const tabState = state[tabKey];

    if (tabKey === 'cut') {
      return {
        input_path: tabState.input_path.trim(),
        start_seconds: Number(tabState.start_seconds),
        end_seconds: Number(tabState.end_seconds),
      };
    }

    if (tabKey === 'merge') {
      const payload = {
        input_paths: listFromTextarea(tabState.input_paths),
      };
      if (tabState.output_name.trim()) {
        payload.output_name = tabState.output_name.trim();
      }
      return payload;
    }

    if (tabKey === 'download') {
      return {
        url: tabState.url.trim(),
        max_height: Number(tabState.max_height),
      };
    }

    const payload = {
      reference_video_path: tabState.reference_video_path.trim(),
      new_prompt: tabState.new_prompt.trim(),
      aspect_ratio: tabState.aspect_ratio,
      model: tabState.model,
      frame_seconds: Number(tabState.frame_seconds),
    };
    if (tabState.profile) {
      payload.profile = tabState.profile;
    }
    return payload;
  }

  function getEndpoint(tabKey) {
    if (tabKey === 'cut') return '/api/media/cut';
    if (tabKey === 'merge') return '/api/media/merge';
    if (tabKey === 'download') return '/api/media/fetch-url';
    return '/api/retarget';
  }

  function setInlineError(tabKey, message) {
    state[tabKey].error = message;
    state[tabKey].result = null;
  }

  function clearFeedback(tabKey) {
    state[tabKey].error = '';
    state[tabKey].result = null;
  }

  async function copyText(text, label) {
    if (!text) {
      App.toast(`${label} is unavailable.`, 'warning');
      return;
    }

    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const temp = document.createElement('textarea');
        temp.value = text;
        temp.setAttribute('readonly', '');
        temp.style.position = 'absolute';
        temp.style.left = '-9999px';
        document.body.appendChild(temp);
        temp.select();
        document.execCommand('copy');
        temp.remove();
      }
      App.toast(`${label} copied`, 'success');
    } catch (err) {
      App.toast(`Failed to copy ${label.toLowerCase()}: ${err.message}`, 'error');
    }
  }

  async function rerender() {
    await App._loadPage('media-tools');
  }

  async function submitTab(tabKey) {
    const validationError = validateTab(tabKey);
    if (validationError) {
      setInlineError(tabKey, validationError);
      App.toast(validationError, 'warning');
      await rerender();
      return;
    }

    const submitButton = document.getElementById('media-tools-submit');
    const meta = TAB_META[tabKey];
    if (submitButton) {
      submitButton.disabled = true;
      submitButton.innerHTML = `<span class="spinner"></span> ${meta.loadingLabel}`;
    }

    clearFeedback(tabKey);

    try {
      const result = await API.fetch(getEndpoint(tabKey), {
        method: 'POST',
        body: JSON.stringify(buildPayload(tabKey)),
      });
      state[tabKey].result = result;
      App.toast(tabKey === 'retarget' ? 'Retarget job queued' : `${meta.label} completed`, 'success');
    } catch (err) {
      setInlineError(tabKey, err.message);
      App.toast(`${meta.label} failed: ${err.message}`, 'error');
    }

    await rerender();
  }

  function bindPageEvents() {
    const root = document.getElementById('media-tools-page');
    if (!root) return;

    root.addEventListener('click', async (event) => {
      const tabButton = event.target.closest('[data-switch-tab]');
      if (tabButton) {
        activeTab = tabButton.dataset.switchTab;
        await rerender();
        return;
      }

      const resetButton = event.target.closest('[data-reset-tab]');
      if (resetButton) {
        resetTab(resetButton.dataset.resetTab);
        await rerender();
        return;
      }

      const copyButton = event.target.closest('[data-copy]');
      if (copyButton) {
        await copyText(copyButton.dataset.copy || '', copyButton.dataset.copyLabel || 'Value');
      }
    });

    const syncState = (event) => {
      const field = event.target.closest('[data-state-tab][data-state-field]');
      if (!field) return;
      updateStateField(field.dataset.stateTab, field.dataset.stateField, field.value);
    };

    root.addEventListener('input', syncState);
    root.addEventListener('change', syncState);

    root.addEventListener('submit', async (event) => {
      const form = event.target.closest('form[data-tool-tab]');
      if (!form) return;
      event.preventDefault();
      await submitTab(form.dataset.toolTab);
    });
  }

  const MediaToolsPage = {
    name: 'media-tools',
    title: 'Media Tools',
    icon: 'build',

    async render() {
      await loadProfiles();
      return `
        <div id="media-tools-page" style="display:grid; gap:20px; max-width:960px;">
          <div class="card">
            <div class="section-header" style="margin-bottom:16px; align-items:flex-start;">
              <div>
                <h3 class="section-title" style="display:flex; align-items:center; gap:8px;">
                  <span class="material-icons">build</span> Media Tools
                </h3>
                <p style="margin:6px 0 0; font-size:13px; color:var(--text-secondary); line-height:1.6;">
                  Download accepts remote URLs. Cut, Merge, and Retarget operate on files inside
                  <code>downloads/</code> or <code>uploads/</code> and expose the existing backend endpoints directly.
                </p>
              </div>
            </div>
            <div style="display:flex; flex-wrap:wrap; gap:8px;">
              ${renderTabs()}
            </div>
          </div>

          <div class="card">
            ${renderActiveTab()}
          </div>
        </div>
      `;
    },

    mount() {
      bindPageEvents();
    },

    destroy() {},
  };

  App.register(MediaToolsPage);
})();
