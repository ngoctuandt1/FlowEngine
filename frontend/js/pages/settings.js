/**
 * Settings Page
 * IdeaStudio-style setup workspace for Gemini, Veo accounts, and Nano.
 */
(() => {
  const GEMINI_MODELS = [
    'gemini-2-flash-preview',
    'gemini-2-pro',
    'gemini-1.5-flash',
    'gemini-1.5-pro',
  ];

  const DEFAULT_GEMINI_MODEL = GEMINI_MODELS[0];

  let root = null;
  let handlers = null;
  let accountSeed = 1;
  let state = createInitialState();

  function createInitialState() {
    return {
      ai: {
        gemini_api_key: '',
        gemini_model: DEFAULT_GEMINI_MODEL,
        nano_api_key: '',
      },
      accounts: [],
      saving: false,
      deletingAccountUid: null,
      banner: null,
      showGeminiKey: false,
      showNanoKey: false,
    };
  }

  function firstDefined(...values) {
    for (const value of values) {
      if (value !== undefined && value !== null) return value;
    }
    return '';
  }

  function stringValue(value) {
    return value == null ? '' : String(value);
  }

  function trimmed(value) {
    return stringValue(value).trim();
  }

  function hasContent(account) {
    return Boolean(trimmed(account.name) || trimmed(account.token) || trimmed(account.cookie));
  }

  function nextAccountUid() {
    accountSeed += 1;
    return `veo-account-${Date.now()}-${accountSeed}`;
  }

  function accountKey(account) {
    if (account?.id != null && account.id !== '') {
      return `id:${account.id}`;
    }
    return `draft:${trimmed(account?.name)}|${trimmed(account?.token)}`;
  }

  function createAccountDraft(raw = {}, options = {}) {
    return {
      uid: options.uid || nextAccountUid(),
      id: firstDefined(raw.id, raw.account_id, raw.veo_account_id, raw.uuid, null),
      name: stringValue(firstDefined(raw.name, raw.account_name, raw.label, '')),
      token: stringValue(firstDefined(raw.token, raw.access_token, raw.api_token, '')),
      cookie: stringValue(firstDefined(raw.cookie, raw.cookies, raw.cookie_blob, raw.cookie_text, '')),
      expanded: options.expanded ?? true,
    };
  }

  function ensureExpandedAccount() {
    if (!state.accounts.length) return;
    if (state.accounts.some((account) => account.expanded)) return;
    state.accounts[0].expanded = true;
  }

  function normalizeAiSettings(payload) {
    const source = payload && typeof payload === 'object' ? payload : {};
    const gemini = source.gemini && typeof source.gemini === 'object' ? source.gemini : {};
    const nano = source.nano && typeof source.nano === 'object' ? source.nano : {};
    const model = stringValue(
      firstDefined(source.gemini_model, source.geminiModel, gemini.model, gemini.model_name, '')
    );

    state.ai = {
      gemini_api_key: stringValue(
        firstDefined(source.gemini_api_key, source.geminiApiKey, gemini.api_key, gemini.apiKey, '')
      ),
      gemini_model: model || DEFAULT_GEMINI_MODEL,
      nano_api_key: stringValue(
        firstDefined(source.nano_api_key, source.nanoApiKey, nano.api_key, nano.apiKey, '')
      ),
    };
  }

  function normalizeAccounts(payload) {
    const previousExpanded = new Map(state.accounts.map((account) => [accountKey(account), account.expanded]));
    const items = Array.isArray(payload)
      ? payload
      : Array.isArray(payload?.accounts)
        ? payload.accounts
        : Array.isArray(payload?.items)
          ? payload.items
          : [];

    state.accounts = items.map((item, index) => {
      const draft = createAccountDraft(item, { expanded: index === 0 });
      const expanded = previousExpanded.get(accountKey(draft));
      if (typeof expanded === 'boolean') {
        draft.expanded = expanded;
      }
      return draft;
    });

    ensureExpandedAccount();
  }

  async function fetchOptional(path, fallback) {
    try {
      return await API.fetch(path);
    } catch (err) {
      if (err?.status === 404) return fallback;
      throw err;
    }
  }

  async function loadSettings({ silent = false } = {}) {
    const [aiResult, accountsResult] = await Promise.allSettled([
      fetchOptional('/api/settings/ai', {}),
      fetchOptional('/api/settings/veo-accounts', []),
    ]);

    const loadErrors = [];

    if (aiResult.status === 'fulfilled') {
      normalizeAiSettings(aiResult.value);
    } else {
      loadErrors.push(`AI settings: ${aiResult.reason.message}`);
    }

    if (accountsResult.status === 'fulfilled') {
      normalizeAccounts(accountsResult.value);
    } else {
      loadErrors.push(`Veo accounts: ${accountsResult.reason.message}`);
    }

    if (loadErrors.length > 0 && !silent) {
      state.banner = {
        type: 'error',
        message: `Could not load saved settings. ${loadErrors.join(' ')}`,
      };
    }
  }

  function findAccount(uid) {
    return state.accounts.find((account) => account.uid === uid) || null;
  }

  function getModelOptions() {
    if (!state.ai.gemini_model || GEMINI_MODELS.includes(state.ai.gemini_model)) {
      return GEMINI_MODELS;
    }
    return [state.ai.gemini_model, ...GEMINI_MODELS];
  }

  function setBanner(type, message) {
    state.banner = message ? { type, message } : null;
  }

  function renderBanner() {
    if (!state.banner?.message) return '';

    return `
      <div class="settings-setup-banner settings-setup-banner--${App.escapeHtml(state.banner.type || 'info')}" role="alert">
        <span class="material-icons" aria-hidden="true">${
          state.banner.type === 'error' ? 'error_outline' : 'info'
        }</span>
        <div>${App.escapeHtml(state.banner.message)}</div>
      </div>
    `;
  }

  function renderSecretField({
    id,
    label,
    value,
    placeholder,
    visible,
    toggleKey,
    mono = false,
  }) {
    const safeId = App.escapeHtml(id);
    const safeLabel = App.escapeHtml(label);
    const safePlaceholder = App.escapeHtml(placeholder);
    const safeValue = App.escapeHtml(value);
    const ariaLabel = visible ? `Ẩn ${label}` : `Hiện ${label}`;

    return `
      <div class="form-group">
        <label class="form-label" for="${safeId}">${safeLabel}</label>
        <div class="settings-setup-secret-field">
          <input
            type="${visible ? 'text' : 'password'}"
            class="form-input ${mono ? 'settings-setup-mono' : ''}"
            id="${safeId}"
            placeholder="${safePlaceholder}"
            value="${safeValue}"
            autocapitalize="off"
            autocomplete="off"
            spellcheck="false"
          >
          <button
            type="button"
            class="settings-setup-eye-toggle"
            data-secret-toggle="${App.escapeHtml(toggleKey)}"
            aria-label="${App.escapeHtml(ariaLabel)}"
          >
            <span class="material-icons" aria-hidden="true">${visible ? 'visibility_off' : 'visibility'}</span>
          </button>
        </div>
      </div>
    `;
  }

  function renderGeminiCard() {
    const options = getModelOptions()
      .map((model) => {
        const selected = model === state.ai.gemini_model ? 'selected' : '';
        return `<option value="${App.escapeHtml(model)}" ${selected}>${App.escapeHtml(model)}</option>`;
      })
      .join('');

    return `
      <section class="settings-setup-card">
        <div class="settings-setup-card-header">
          <div>
            <h2 class="settings-setup-card-title">Thiết lập Gemini SDK</h2>
            <p class="settings-setup-card-copy">Khai báo khóa truy cập và chọn model Gemini mặc định cho hệ thống.</p>
          </div>
        </div>
        <div class="settings-setup-grid settings-setup-grid--two">
          ${renderSecretField({
            id: 'settings-gemini-api-key',
            label: 'Gemini API Key',
            value: state.ai.gemini_api_key,
            placeholder: 'AIzaSy...',
            visible: state.showGeminiKey,
            toggleKey: 'gemini',
            mono: true,
          })}
          <div class="form-group">
            <label class="form-label" for="settings-gemini-model">Gemini Model</label>
            <select class="form-select" id="settings-gemini-model">
              ${options}
            </select>
          </div>
        </div>
      </section>
    `;
  }

  function renderAccountRow(account, index) {
    const summary = trimmed(account.name) || 'Chưa điền Name';
    const deleteBusy = state.deletingAccountUid === account.uid;

    return `
      <article class="settings-setup-account-card${account.expanded ? '' : ' is-collapsed'}" data-account-card="${App.escapeHtml(account.uid)}">
        <button
          type="button"
          class="settings-setup-account-header"
          data-account-toggle="${App.escapeHtml(account.uid)}"
          aria-expanded="${account.expanded ? 'true' : 'false'}"
        >
          <div class="settings-setup-account-heading">
            <span class="settings-setup-account-title">Tài khoản ${index + 1}</span>
            <span class="settings-setup-account-summary">${App.escapeHtml(summary)}</span>
          </div>
          <span class="material-icons settings-setup-account-chevron" aria-hidden="true">
            ${account.expanded ? 'expand_less' : 'expand_more'}
          </span>
        </button>

        <div class="settings-setup-account-body" ${account.expanded ? '' : 'hidden'}>
          <div class="settings-setup-grid settings-setup-grid--two">
            <div class="form-group">
              <label class="form-label" for="account-name-${App.escapeHtml(account.uid)}">Name</label>
              <input
                type="text"
                class="form-input"
                id="account-name-${App.escapeHtml(account.uid)}"
                data-account-uid="${App.escapeHtml(account.uid)}"
                data-account-field="name"
                placeholder="e.g. vanan060417533"
                value="${App.escapeHtml(account.name)}"
                autocapitalize="off"
                autocomplete="off"
                spellcheck="false"
              >
            </div>
            <div class="form-group">
              <label class="form-label" for="account-token-${App.escapeHtml(account.uid)}">Token</label>
              <input
                type="text"
                class="form-input settings-setup-mono"
                id="account-token-${App.escapeHtml(account.uid)}"
                data-account-uid="${App.escapeHtml(account.uid)}"
                data-account-field="token"
                placeholder="Long access token"
                value="${App.escapeHtml(account.token)}"
                autocapitalize="off"
                autocomplete="off"
                spellcheck="false"
              >
            </div>
          </div>

          <div class="form-group">
            <label class="form-label" for="account-cookie-${App.escapeHtml(account.uid)}">Cookie</label>
            <textarea
              class="form-textarea settings-setup-mono settings-setup-cookie"
              id="account-cookie-${App.escapeHtml(account.uid)}"
              data-account-uid="${App.escapeHtml(account.uid)}"
              data-account-field="cookie"
              rows="5"
              placeholder="cookie_name=value; ..."
              autocapitalize="off"
              autocomplete="off"
              spellcheck="false"
            >${App.escapeHtml(account.cookie)}</textarea>
          </div>

          <div class="settings-setup-account-actions">
            <button
              type="button"
              class="settings-setup-delete-link"
              data-account-delete="${App.escapeHtml(account.uid)}"
              ${deleteBusy ? 'disabled' : ''}
            >
              ${deleteBusy ? 'Đang xoá...' : 'Xoá'}
            </button>
          </div>
        </div>
      </article>
    `;
  }

  function renderVeoCard() {
    const listHtml = state.accounts.length
      ? state.accounts.map(renderAccountRow).join('')
      : `
        <div class="settings-setup-empty">
          <span class="material-icons" aria-hidden="true">account_circle</span>
          <div>
            <strong>Chưa có tài khoản Veo</strong>
            <p>Bấm "Thêm tài khoản" để thêm thông tin Name, Token và Cookie.</p>
          </div>
        </div>
      `;

    return `
      <section class="settings-setup-card">
        <div class="settings-setup-card-header settings-setup-card-header--split">
          <div>
            <h2 class="settings-setup-card-title">Thiết lập tài khoản Veo</h2>
            <p class="settings-setup-card-copy">Quản lý nhiều tài khoản Veo và lưu token/cookie cho từng account.</p>
          </div>
          <button type="button" class="btn btn-outline settings-setup-add-account" id="settings-add-account">
            <span class="material-icons" aria-hidden="true">add</span>
            Thêm tài khoản
          </button>
        </div>
        <div class="settings-setup-account-list">
          ${listHtml}
        </div>
      </section>
    `;
  }

  function renderNanoCard() {
    return `
      <section class="settings-setup-card">
        <div class="settings-setup-card-header">
          <div>
            <h2 class="settings-setup-card-title">API KEY NANO</h2>
            <p class="settings-setup-card-copy">Lưu khóa Nano API dùng chung cho các module cần gọi dịch vụ NANO.</p>
          </div>
        </div>
        ${renderSecretField({
          id: 'settings-nano-api-key',
          label: 'Nano API Key',
          value: state.ai.nano_api_key,
          placeholder: 'nano_live_...',
          visible: state.showNanoKey,
          toggleKey: 'nano',
          mono: true,
        })}
      </section>
    `;
  }

  function renderPage() {
    if (!root) return;

    root.innerHTML = `
      <div class="settings-setup-page">
        <header class="settings-setup-header">
          <p class="settings-setup-kicker">System Configuration</p>
          <h1 class="settings-setup-title">⚙ Setup</h1>
          <p class="settings-setup-subtitle">Quản lý cấu hình hệ thống và thông tin kết nối cho các module AI.</p>
        </header>

        ${renderBanner()}

        <form id="settings-setup-form" class="settings-setup-form" novalidate>
          ${renderGeminiCard()}
          ${renderVeoCard()}
          ${renderNanoCard()}

          <div class="settings-setup-footer">
            <button type="submit" class="btn settings-setup-save" ${state.saving ? 'disabled' : ''}>
              ${
                state.saving
                  ? '<span class="spinner"></span> Saving...'
                  : 'Save Configuration'
              }
            </button>
          </div>
        </form>
      </div>
    `;
  }

  function syncSecretVisibility(toggleKey) {
    const keyMap = {
      gemini: {
        inputId: 'settings-gemini-api-key',
        visible: state.showGeminiKey,
        label: 'Gemini API Key',
      },
      nano: {
        inputId: 'settings-nano-api-key',
        visible: state.showNanoKey,
        label: 'Nano API Key',
      },
    };

    const config = keyMap[toggleKey];
    if (!config || !root) return;

    const input = root.querySelector(`#${config.inputId}`);
    const button = root.querySelector(`[data-secret-toggle="${toggleKey}"]`);
    const icon = button?.querySelector('.material-icons');

    if (input) {
      input.type = config.visible ? 'text' : 'password';
    }
    if (button) {
      button.setAttribute('aria-label', `${config.visible ? 'Ẩn' : 'Hiện'} ${config.label}`);
    }
    if (icon) {
      icon.textContent = config.visible ? 'visibility_off' : 'visibility';
    }
  }

  function syncAccountExpandedState(uid) {
    if (!root) return;

    const account = findAccount(uid);
    const card = root.querySelector(`[data-account-card="${uid}"]`);
    const header = root.querySelector(`[data-account-toggle="${uid}"]`);
    const body = card?.querySelector('.settings-setup-account-body');
    const icon = header?.querySelector('.settings-setup-account-chevron');

    if (!account || !card || !header || !body || !icon) return;

    card.classList.toggle('is-collapsed', !account.expanded);
    header.setAttribute('aria-expanded', account.expanded ? 'true' : 'false');
    body.hidden = !account.expanded;
    icon.textContent = account.expanded ? 'expand_less' : 'expand_more';
  }

  function updateAccountField(uid, field, value) {
    const account = findAccount(uid);
    if (!account || !field) return;
    account[field] = value;
  }

  function addAccount() {
    state.accounts.push(createAccountDraft({}, { expanded: true }));
    renderPage();
  }

  function toggleAccount(uid) {
    const account = findAccount(uid);
    if (!account) return;
    account.expanded = !account.expanded;
    syncAccountExpandedState(uid);
  }

  async function deleteAccount(uid) {
    const account = findAccount(uid);
    if (!account) return;

    if (!window.confirm(`Delete Tài khoản ${state.accounts.findIndex((item) => item.uid === uid) + 1}?`)) {
      return;
    }

    if (!account.id) {
      state.accounts = state.accounts.filter((item) => item.uid !== uid);
      ensureExpandedAccount();
      renderPage();
      return;
    }

    state.deletingAccountUid = uid;
    setBanner(null, '');
    renderPage();

    try {
      await API.fetch(`/api/settings/veo-accounts/${encodeURIComponent(String(account.id))}`, {
        method: 'DELETE',
      });
      state.accounts = state.accounts.filter((item) => item.uid !== uid);
      ensureExpandedAccount();
      state.deletingAccountUid = null;
      renderPage();
      App.toast('Deleted account.', 'success');
    } catch (err) {
      state.deletingAccountUid = null;
      setBanner('error', `Could not delete the Veo account. ${err.message}`);
      renderPage();
    }
  }

  function collectAccountsForSave() {
    const accountsToSave = [];

    for (const [index, account] of state.accounts.entries()) {
      const name = trimmed(account.name);
      const token = trimmed(account.token);
      const cookie = trimmed(account.cookie);
      const anyFilled = Boolean(name || token || cookie);

      if (!account.id && !anyFilled) {
        continue;
      }

      if (!name || !token || !cookie) {
        return {
          error: account.id
            ? `Tài khoản ${index + 1} is incomplete. Use "Xoá" to remove saved accounts, or fill all fields.`
            : `Complete Name, Token, and Cookie for Tài khoản ${index + 1}.`,
        };
      }

      accountsToSave.push({
        uid: account.uid,
        id: account.id,
        name,
        token,
        cookie,
      });
    }

    return { accountsToSave };
  }

  function applySavedAccountResponses(savedAccounts) {
    const savedMap = new Map(savedAccounts.map((item) => [item.uid, item.response]));

    state.accounts = state.accounts
      .filter((account) => account.id || hasContent(account))
      .map((account) => {
        const response = savedMap.get(account.uid);
        if (!response || typeof response !== 'object') return account;

        const payload = response.account || response.item || response;
        const normalized = createAccountDraft(payload, {
          uid: account.uid,
          expanded: account.expanded,
        });

        return {
          ...account,
          id: normalized.id,
          name: normalized.name,
          token: normalized.token,
          cookie: normalized.cookie,
        };
      });

    ensureExpandedAccount();
  }

  async function saveConfiguration() {
    const { accountsToSave, error } = collectAccountsForSave();
    if (error) {
      setBanner('error', error);
      renderPage();
      return;
    }

    state.saving = true;
    setBanner(null, '');
    renderPage();

    try {
      await API.fetch('/api/settings/ai', {
        method: 'POST',
        body: JSON.stringify({
          gemini_api_key: trimmed(state.ai.gemini_api_key),
          gemini_model: state.ai.gemini_model || DEFAULT_GEMINI_MODEL,
          nano_api_key: trimmed(state.ai.nano_api_key),
        }),
      });

      const savedAccounts = [];
      const accountErrors = [];

      for (const [index, account] of accountsToSave.entries()) {
        try {
          const response = await API.fetch(
            account.id
              ? `/api/settings/veo-accounts/${encodeURIComponent(String(account.id))}`
              : '/api/settings/veo-accounts',
            {
              method: account.id ? 'PUT' : 'POST',
              body: JSON.stringify({
                name: account.name,
                token: account.token,
                cookie: account.cookie,
              }),
            }
          );

          savedAccounts.push({ uid: account.uid, response });
        } catch (err) {
          accountErrors.push(`Tài khoản ${index + 1}: ${err.message}`);
        }
      }

      applySavedAccountResponses(savedAccounts);

      if (accountErrors.length > 0) {
        throw new Error(accountErrors.join(' '));
      }

      await loadSettings({ silent: true });
      state.accounts = state.accounts.filter((account) => account.id || hasContent(account));
      state.saving = false;
      setBanner(null, '');
      renderPage();
      App.toast('Saved', 'success');
    } catch (err) {
      state.saving = false;
      setBanner('error', `Could not save settings. ${err.message}`);
      renderPage();
    }
  }

  async function handleClick(event) {
    const addAccountButton = event.target.closest('#settings-add-account');
    if (addAccountButton) {
      addAccount();
      return;
    }

    const toggleButton = event.target.closest('[data-account-toggle]');
    if (toggleButton) {
      toggleAccount(toggleButton.dataset.accountToggle);
      return;
    }

    const deleteButton = event.target.closest('[data-account-delete]');
    if (deleteButton && !deleteButton.disabled) {
      await deleteAccount(deleteButton.dataset.accountDelete);
      return;
    }

    const secretToggle = event.target.closest('[data-secret-toggle]');
    if (!secretToggle) return;

    if (secretToggle.dataset.secretToggle === 'gemini') {
      state.showGeminiKey = !state.showGeminiKey;
      syncSecretVisibility('gemini');
      return;
    }

    if (secretToggle.dataset.secretToggle === 'nano') {
      state.showNanoKey = !state.showNanoKey;
      syncSecretVisibility('nano');
    }
  }

  function handleFieldChange(event) {
    const accountField = event.target.dataset.accountField;
    const accountUid = event.target.dataset.accountUid;

    if (accountField && accountUid) {
      updateAccountField(accountUid, accountField, event.target.value);
      return;
    }

    switch (event.target.id) {
      case 'settings-gemini-api-key':
        state.ai.gemini_api_key = event.target.value;
        break;
      case 'settings-gemini-model':
        state.ai.gemini_model = event.target.value || DEFAULT_GEMINI_MODEL;
        break;
      case 'settings-nano-api-key':
        state.ai.nano_api_key = event.target.value;
        break;
      default:
        break;
    }
  }

  async function handleSubmit(event) {
    if (event.target.id !== 'settings-setup-form') return;
    event.preventDefault();
    await saveConfiguration();
  }

  const SettingsPage = {
    name: 'settings',
    title: 'Setup',
    icon: 'settings',

    async render() {
      state = createInitialState();
      await loadSettings();
      return '<div id="settings-setup-root"></div>';
    },

    mount() {
      root = document.getElementById('settings-setup-root');
      if (!root) return;

      handlers = {
        click: (event) => { void handleClick(event); },
        input: handleFieldChange,
        change: handleFieldChange,
        submit: (event) => { void handleSubmit(event); },
      };

      root.addEventListener('click', handlers.click);
      root.addEventListener('input', handlers.input);
      root.addEventListener('change', handlers.change);
      root.addEventListener('submit', handlers.submit);

      renderPage();
    },

    destroy() {
      if (root && handlers) {
        root.removeEventListener('click', handlers.click);
        root.removeEventListener('input', handlers.input);
        root.removeEventListener('change', handlers.change);
        root.removeEventListener('submit', handlers.submit);
      }

      root = null;
      handlers = null;
      state = createInitialState();
    },
  };

  App.register(SettingsPage);
})();
