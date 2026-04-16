/**
 * Profiles Page
 * List, create, quarantine/activate profiles.
 */
(() => {
  const TIERS = ['free', 'standard', 'pro'];
  const LOCALES = ['en-US', 'en-GB', 'vi-VN', 'ja-JP', 'ko-KR', 'zh-CN', 'de-DE', 'fr-FR'];

  function getInitials(name) {
    if (!name) return '?';
    return name
      .split(/[\s_-]+/)
      .slice(0, 2)
      .map((w) => w[0]?.toUpperCase() || '')
      .join('');
  }

  function renderProfileCard(profile) {
    const name = profile.name || profile.profile_name || 'Unknown';
    const status = profile.status || (profile.quarantined ? 'quarantined' : 'active');
    const avatarClass = status === 'quarantined' ? 'quarantined' : 'active';
    const tier = profile.tier || 'free';
    const locale = profile.locale || '-';
    const account = profile.google_account || profile.email || '-';
    const currentJob = profile.current_job || profile.current_job_id;

    return `
      <div class="profile-card" data-profile-id="${App.escapeHtml(profile.id || profile.profile_id || name)}">
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
          ${
            status === 'quarantined'
              ? `<button class="btn btn-success btn-sm profile-activate" data-id="${App.escapeHtml(
                  profile.id || profile.profile_id || name
                )}">
                  <span class="material-icons" style="font-size:14px">check</span> Activate
                </button>`
              : `<button class="btn btn-danger btn-sm profile-quarantine" data-id="${App.escapeHtml(
                  profile.id || profile.profile_id || name
                )}">
                  <span class="material-icons" style="font-size:14px">block</span> Quarantine
                </button>`
          }
        </div>
      </div>
    `;
  }

  function renderAddForm() {
    const tierOptions = TIERS.map(
      (t) => `<option value="${t}">${t.charAt(0).toUpperCase() + t.slice(1)}</option>`
    ).join('');

    const localeOptions = LOCALES.map(
      (l) => `<option value="${l}">${l}</option>`
    ).join('');

    return `
      <div class="card" style="margin-bottom: 24px;">
        <h3 style="margin-bottom: 16px; font-size: 16px; font-weight: 600;">
          <span class="material-icons" style="font-size:18px; vertical-align:middle;">person_add</span>
          Add New Profile
        </h3>
        <div class="form-row">
          <div class="form-group">
            <label class="form-label">Profile Name <span class="required">*</span></label>
            <input type="text" class="form-input" id="profile-name" placeholder="e.g. worker-01">
          </div>
          <div class="form-group">
            <label class="form-label">Google Account <span class="required">*</span></label>
            <input type="email" class="form-input" id="profile-account" placeholder="user@gmail.com">
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label class="form-label">Locale</label>
            <select class="form-select" id="profile-locale">${localeOptions}</select>
          </div>
          <div class="form-group">
            <label class="form-label">Tier</label>
            <select class="form-select" id="profile-tier">${tierOptions}</select>
          </div>
        </div>
        <button class="btn btn-primary" id="profile-submit">
          <span class="material-icons">add</span> Add Profile
        </button>
      </div>
    `;
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
      // Add profile
      document.getElementById('profile-submit')?.addEventListener('click', async () => {
        const name = document.getElementById('profile-name')?.value?.trim();
        const account = document.getElementById('profile-account')?.value?.trim();
        const locale = document.getElementById('profile-locale')?.value;
        const tier = document.getElementById('profile-tier')?.value;

        if (!name) {
          App.toast('Profile name is required.', 'warning');
          return;
        }
        if (!account) {
          App.toast('Google account is required.', 'warning');
          return;
        }

        const btn = document.getElementById('profile-submit');
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> Adding...';

        try {
          await API.profiles.create({
            name,
            google_account: account,
            locale,
            tier,
          });
          App.toast('Profile added!', 'success');
          App._loadPage('profiles');
        } catch (err) {
          App.toast('Failed to add profile: ' + err.message, 'error');
          btn.disabled = false;
          btn.innerHTML = '<span class="material-icons">add</span> Add Profile';
        }
      });

      // Quarantine / Activate buttons
      const grid = document.getElementById('profiles-grid');
      if (grid) {
        grid.addEventListener('click', async (e) => {
          const quarantineBtn = e.target.closest('.profile-quarantine');
          const activateBtn = e.target.closest('.profile-activate');

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
