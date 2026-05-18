/**
 * Profiles Page
 * List, create, edit, reload, quarantine/activate profiles.
 */
(() => {
  const TIERS = ['free', 'standard', 'pro'];
  const LOCALES = ['en-US', 'en-GB', 'vi-VN', 'ja-JP', 'ko-KR', 'zh-CN', 'de-DE', 'fr-FR'];
  const WORKER_KEY_VISIBILITY_CLEAR_DELAY_MS = 5 * 60 * 1000;
  let cachedWorkerKey = null;
  let workerKeyVisibilityTimer = null;

  function clearCachedWorkerKey() {
    cachedWorkerKey = null;
  }

  function scheduleWorkerKeyVisibilityClear() {
    if (workerKeyVisibilityTimer) {
      clearTimeout(workerKeyVisibilityTimer);
      workerKeyVisibilityTimer = null;
    }

    if (document.visibilityState !== 'hidden' || !cachedWorkerKey) return;

    workerKeyVisibilityTimer = setTimeout(() => {
      if (document.visibilityState === 'hidden') clearCachedWorkerKey();
      workerKeyVisibilityTimer = null;
    }, WORKER_KEY_VISIBILITY_CLEAR_DELAY_MS);
  }

  document.addEventListener('visibilitychange', scheduleWorkerKeyVisibilityClear);

  function getInitials(name) {
    if (!name) return '?';
    return name
      .split(/[\s_-]+/)
      .slice(0, 2)
      .map((w) => w[0]?.toUpperCase() || '')
      .join('');
  }

  function escapeAttr(value) {
    return App.escapeHtml(String(value ?? '')).replace(/"/g, '&quot;');
  }

  function optionValues(values, currentValue) {
    const normalized = String(currentValue || '').trim();
    return normalized && !values.includes(normalized) ? [normalized, ...values] : values;
  }

  function renderSelectOptions(values, currentValue) {
    const selected = String(currentValue || '').trim();
    return optionValues(values, selected).map((value) => {
      return `<option value="${escapeAttr(value)}" ${selected === value ? 'selected' : ''}>${App.escapeHtml(value)}</option>`;
    }).join('');
  }

  function renderProfileFormFields(prefix, profile = {}, options = {}) {
    const name = profile.name || profile.profile_name || '';
    const account = profile.google_account || profile.email || '';
    const locale = profile.locale || LOCALES[0];
    const tier = profile.tier || TIERS[0];
    const nameAttrs = options.readonlyName ? 'readonly aria-readonly="true"' : '';

    return `
      <div class="form-row">
        <div class="form-group">
          <label class="form-label">Profile Name <span class="required">*</span></label>
          <input type="text" class="form-input" id="${prefix}-name" placeholder="e.g. worker-01" value="${escapeAttr(name)}" ${nameAttrs}>
        </div>
        <div class="form-group">
          <label class="form-label">Google Account <span class="required">*</span></label>
          <input type="email" class="form-input" id="${prefix}-account" placeholder="user@gmail.com" value="${escapeAttr(account)}">
        </div>
      </div>
      <div class="form-row">
        <div class="form-group">
          <label class="form-label">Locale</label>
          <select class="form-select" id="${prefix}-locale">${renderSelectOptions(LOCALES, locale)}</select>
        </div>
        <div class="form-group">
          <label class="form-label">Tier</label>
          <select class="form-select" id="${prefix}-tier">${renderSelectOptions(TIERS, tier)}</select>
        </div>
      </div>
    `;
  }

  function readProfileForm(prefix) {
    return {
      name: document.getElementById(`${prefix}-name`)?.value?.trim() || '',
      google_account: document.getElementById(`${prefix}-account`)?.value?.trim() || '',
      locale: document.getElementById(`${prefix}-locale`)?.value || '',
      tier: document.getElementById(`${prefix}-tier`)?.value || '',
    };
  }

  function validateProfileForm(data) {
    if (!data.name) return 'Profile name is required.';
    if (!data.google_account) return 'Google account is required.';
    return '';
  }

  function profileFromCard(card) {
    return {
      name: card?.dataset.profileName || '',
      google_account: card?.dataset.profileAccount || '',
      locale: card?.dataset.profileLocale || '',
      tier: card?.dataset.profileTier || '',
    };
  }

  function renderProfileCard(profile) {
    const name = profile.name || profile.profile_name || 'Unknown';
    const status = profile.status || (profile.quarantined ? 'quarantined' : 'active');
    const avatarClass = status === 'quarantined' ? 'quarantined' : 'active';
    const tier = profile.tier || 'free';
    const locale = profile.locale || '-';
    const account = profile.google_account || profile.email || '-';
    const currentJob = profile.current_job || profile.current_job_id;
    const actionId = profile.id || profile.profile_id || name;

    return `
      <div class="profile-card"
        data-profile-id="${escapeAttr(actionId)}"
        data-profile-name="${escapeAttr(name)}"
        data-profile-account="${escapeAttr(account === '-' ? '' : account)}"
        data-profile-locale="${escapeAttr(locale === '-' ? '' : locale)}"
        data-profile-tier="${escapeAttr(tier)}">
        <div class="profile-header">
          <div class="profile-avatar ${avatarClass}">${getInitials(name)}</div>
          <div>
            <div class="profile-name">${App.escapeHtml(name)}</div>
            <div class="profile-email">${App.escapeHtml(account)}</div>
          </div>
          <span class="${App.statusBadge(status)}" style="margin-left: auto;">
            ${App.escapeHtml(status)}
          </span>
        </div>
        <div class="profile-details">
          <div class="profile-detail">
            <span class="material-icons">star</span>
            Tier: <strong>${App.escapeHtml(tier)}</strong>
          </div>
          <div class="profile-detail">
            <span class="material-icons">language</span>
            Locale: ${App.escapeHtml(locale)}
          </div>
          ${currentJob ? `
            <div class="profile-detail">
              <span class="material-icons">work</span>
              Current job: <code style="font-size:12px">${App.escapeHtml(String(currentJob))}</code>
            </div>
          ` : ''}
        </div>
        <div class="profile-actions">
          <button class="btn btn-sm btn-outline profile-edit" data-name="${escapeAttr(name)}">
            <span class="material-icons" style="font-size:14px">edit</span> Edit
          </button>
          ${
            status === 'quarantined'
              ? `<button class="btn btn-success btn-sm profile-activate" data-id="${escapeAttr(actionId)}">
                  <span class="material-icons" style="font-size:14px">check</span> Activate
                </button>`
              : `<button class="btn btn-danger btn-sm profile-quarantine" data-id="${escapeAttr(actionId)}">
                  <span class="material-icons" style="font-size:14px">block</span> Quarantine
                </button>`
          }
        </div>
      </div>
    `;
  }

  function renderAddForm() {
    return `
      <div class="card" style="margin-bottom: 24px;">
        <div class="section-header">
          <h3 style="font-size: 16px; font-weight: 600; margin: 0;">
            <span class="material-icons" style="font-size:18px; vertical-align:middle;">person_add</span>
            Add New Profile
          </h3>
          <button class="btn btn-sm btn-outline" id="profiles-reload">
            <span class="material-icons" style="font-size:16px">sync</span> Reload from Google Sheet
          </button>
        </div>
        ${renderProfileFormFields('profile')}
        <button class="btn btn-primary" id="profile-submit">
          <span class="material-icons">add</span> Add Profile
        </button>
      </div>
    `;
  }

  function openEditProfileModal(profile) {
    App.openModal('Edit Profile', `
      ${renderProfileFormFields('profile-edit', profile, { readonlyName: true })}
      <div style="display:flex; justify-content:flex-end; gap:10px; margin-top:18px;">
        <button class="btn btn-outline" id="profile-edit-cancel">Cancel</button>
        <button class="btn btn-primary" id="profile-edit-submit">
          <span class="material-icons" style="font-size:16px">save</span> Save Changes
        </button>
      </div>
    `);

    document.getElementById('profile-edit-cancel')?.addEventListener('click', () => App.closeModal());
    document.getElementById('profile-edit-submit')?.addEventListener('click', async (event) => {
      const data = readProfileForm('profile-edit');
      const error = validateProfileForm(data);
      if (error) {
        App.toast(error, 'warning');
        return;
      }

      const button = event.currentTarget;
      button.disabled = true;
      button.innerHTML = '<span class="spinner"></span> Saving...';
      try {
        await API.profiles.update(data.name, {
          google_account: data.google_account,
          locale: data.locale,
          tier: data.tier,
        });
        App.toast('Profile updated.', 'success');
        App.closeModal();
        App._loadPage('profiles');
      } catch (err) {
        App.toast('Failed to update profile: ' + err.message, 'error');
        button.disabled = false;
        button.innerHTML = '<span class="material-icons" style="font-size:16px">save</span> Save Changes';
      }
    });
  }

  function promptForWorkerKey() {
    return new Promise((resolve) => {
      App.openModal('Worker API Key', `
        <p style="margin:0 0 14px; color: var(--text-muted);">
          Enter Worker API key to reload profiles from Google Sheet. Key is kept in memory only.
        </p>
        <div class="form-group">
          <label class="form-label" for="worker-api-key-input">Worker API Key</label>
          <div style="display:flex; gap:8px; align-items:center;">
            <input
              type="password"
              class="form-input"
              id="worker-api-key-input"
              placeholder="Worker API key"
              autocomplete="off"
              autocapitalize="off"
              spellcheck="false"
            >
            <button type="button" class="btn btn-icon btn-outline" id="worker-api-key-toggle" aria-label="Show worker API key">
              <span class="material-icons" aria-hidden="true">visibility</span>
            </button>
          </div>
        </div>
        <div style="display:flex; justify-content:flex-end; gap:10px; margin-top:18px;">
          <button class="btn btn-outline" id="worker-api-key-cancel">Cancel</button>
          <button class="btn btn-primary" id="worker-api-key-submit">
            <span class="material-icons" style="font-size:16px">sync</span> Reload
          </button>
        </div>
      `);

      const overlay = document.getElementById('modal-overlay');
      const input = document.getElementById('worker-api-key-input');
      const toggle = document.getElementById('worker-api-key-toggle');
      const cancel = document.getElementById('worker-api-key-cancel');
      const submit = document.getElementById('worker-api-key-submit');
      let settled = false;

      const finish = (value) => {
        if (settled) return;
        settled = true;
        observer.disconnect();
        App.closeModal();
        resolve(value);
      };

      const observer = new MutationObserver(() => {
        if (!overlay || overlay.classList.contains('hidden')) finish(null);
      });

      if (overlay) observer.observe(overlay, { attributes: true, attributeFilter: ['class'] });
      input?.focus();

      toggle?.addEventListener('click', () => {
        if (!input) return;
        const showKey = input.type === 'password';
        input.type = showKey ? 'text' : 'password';
        toggle.setAttribute('aria-label', showKey ? 'Hide worker API key' : 'Show worker API key');
        const icon = toggle.querySelector('.material-icons');
        if (icon) icon.textContent = showKey ? 'visibility_off' : 'visibility';
      });

      cancel?.addEventListener('click', () => finish(null));
      submit?.addEventListener('click', () => {
        const apiKey = input?.value.trim() || '';
        if (!apiKey) {
          App.toast('Worker API key is required.', 'warning');
          input?.focus();
          return;
        }
        finish(apiKey);
      });
      input?.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') submit?.click();
      });
    });
  }

  async function getWorkerKey(forcePrompt = false) {
    if (!forcePrompt && cachedWorkerKey) return cachedWorkerKey;
    const apiKey = await promptForWorkerKey();
    cachedWorkerKey = apiKey || null;
    return cachedWorkerKey;
  }

  async function callReloadProfiles(apiKey) {
    const result = await API.profiles.reload(apiKey);
    const count = result?.loaded ?? result?.profiles?.length ?? 0;
    App.toast(`Reloaded ${count} profile${count === 1 ? '' : 's'} from Google Sheet.`, 'success');
    App._loadPage('profiles');
  }

  async function reloadProfiles(button) {
    if (button.disabled) return;
    button.disabled = true;

    let apiKey = await getWorkerKey();
    if (!apiKey) {
      button.disabled = false;
      return;
    }

    button.innerHTML = '<span class="spinner"></span> Reloading...';
    try {
      await callReloadProfiles(apiKey);
    } catch (err) {
      if (err.status === 401) {
        clearCachedWorkerKey();
        App.toast('Worker API key rejected. Enter it again.', 'warning');
        apiKey = await getWorkerKey(true);
        if (apiKey) {
          try {
            await callReloadProfiles(apiKey);
            return;
          } catch (retryErr) {
            if (retryErr.status === 401) clearCachedWorkerKey();
            App.toast('Profile reload failed: ' + retryErr.message, 'error');
            return;
          }
        }
        return;
      }
      App.toast('Profile reload failed: ' + err.message, 'error');
    } finally {
      button.disabled = false;
      button.innerHTML = '<span class="material-icons" style="font-size:16px">sync</span> Reload from Google Sheet';
    }
  }

  const ProfilesPage = {
    name: 'profiles',
    title: 'Profiles',
    icon: 'people',

    async render() {
      let profiles = [];
      try {
        const result = await API.profiles.list();
        profiles = Array.isArray(result) ? result : result?.profiles || [];
      } catch (err) {
        console.warn('[Profiles] API not available:', err.message);
      }

      const addFormHtml = renderAddForm();

      let listHtml;
      if (profiles.length === 0) {
        listHtml = `
          <div class="empty-state">
            <span class="material-icons">people_outline</span>
            <h3>No profiles</h3>
            <p>Add your first profile using the form above.</p>
          </div>
        `;
      } else {
        listHtml = `
          <div class="section-header">
            <h3 class="section-title">Profiles (${profiles.length})</h3>
          </div>
          <div class="profiles-grid" id="profiles-grid">
            ${profiles.map(renderProfileCard).join('')}
          </div>
        `;
      }

      return addFormHtml + listHtml;
    },

    mount() {
      document.getElementById('profiles-reload')?.addEventListener('click', (event) => {
        reloadProfiles(event.currentTarget);
      });

      document.getElementById('profile-submit')?.addEventListener('click', async () => {
        const data = readProfileForm('profile');
        const error = validateProfileForm(data);
        if (error) {
          App.toast(error, 'warning');
          return;
        }

        const btn = document.getElementById('profile-submit');
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> Adding...';

        try {
          await API.profiles.create({
            name: data.name,
            google_account: data.google_account,
            locale: data.locale,
            tier: data.tier,
          });
          App.toast('Profile added!', 'success');
          App._loadPage('profiles');
        } catch (err) {
          App.toast('Failed to add profile: ' + err.message, 'error');
          btn.disabled = false;
          btn.innerHTML = '<span class="material-icons">add</span> Add Profile';
        }
      });

      const grid = document.getElementById('profiles-grid');
      if (grid) {
        grid.addEventListener('click', async (e) => {
          const editBtn = e.target.closest('.profile-edit');
          const quarantineBtn = e.target.closest('.profile-quarantine');
          const activateBtn = e.target.closest('.profile-activate');

          if (editBtn) {
            openEditProfileModal(profileFromCard(editBtn.closest('.profile-card')));
            return;
          }

          if (quarantineBtn) {
            const id = quarantineBtn.dataset.id;
            quarantineBtn.disabled = true;
            try {
              await API.profiles.quarantine(id);
              App.toast('Profile quarantined.', 'info');
              App._loadPage('profiles');
            } catch (err) {
              App.toast('Failed: ' + err.message, 'error');
              quarantineBtn.disabled = false;
            }
          }

          if (activateBtn) {
            const id = activateBtn.dataset.id;
            activateBtn.disabled = true;
            try {
              await API.profiles.activate(id);
              App.toast('Profile activated.', 'success');
              App._loadPage('profiles');
            } catch (err) {
              App.toast('Failed: ' + err.message, 'error');
              activateBtn.disabled = false;
            }
          }
        });
      }
    },
  };

  App.register(ProfilesPage);
})();
