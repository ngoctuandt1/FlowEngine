/**
 * Profiles Page
 * List, create, edit, reload, quarantine/activate profiles.
 */
(() => {
  const TIERS = ['free', 'standard', 'pro'];
  const LOCALES = ['en-US', 'en-GB', 'vi-VN', 'ja-JP', 'ko-KR', 'zh-CN', 'de-DE', 'fr-FR'];
  const RELOAD_API_KEY_STORAGE = 'flowengine.workerApiKey';

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

  async function reloadProfiles(button) {
    let apiKey = localStorage.getItem(RELOAD_API_KEY_STORAGE) || '';
    if (!apiKey) {
      apiKey = prompt('Worker API key for Google Sheet reload') || '';
      apiKey = apiKey.trim();
      if (!apiKey) return;
      localStorage.setItem(RELOAD_API_KEY_STORAGE, apiKey);
    }

    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span> Reloading...';
    try {
      const result = await API.profiles.reload(apiKey);
      const count = result?.loaded ?? result?.profiles?.length ?? 0;
      App.toast(`Reloaded ${count} profile${count === 1 ? '' : 's'} from Google Sheet.`, 'success');
      App._loadPage('profiles');
    } catch (err) {
      if (err.status === 401) localStorage.removeItem(RELOAD_API_KEY_STORAGE);
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
