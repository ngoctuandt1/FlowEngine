/**
 * Characters Page
 * CRUD for reusable character records backed by /api/characters.
 */
(() => {
  const MAX_IMAGES = 10;

  let root = null;
  let handlers = null;
  let state = {
    characters: [],
    search: '',
    selectedId: null,
    draft: emptyDraft(),
    saving: false,
    deleting: false,
    uploading: false,
  };

  function emptyDraft() {
    return {
      id: '',
      name: '',
      description: '',
      image_paths: [],
      created_at: '',
      updated_at: '',
    };
  }

  function cloneCharacter(character) {
    return {
      id: character?.id || '',
      name: character?.name || '',
      description: character?.description || '',
      image_paths: Array.isArray(character?.image_paths) ? [...character.image_paths] : [],
      created_at: character?.created_at || '',
      updated_at: character?.updated_at || '',
    };
  }

  function sortCharacters(characters) {
    return [...characters].sort((a, b) => {
      const timeDiff = new Date(b.updated_at || 0).getTime() - new Date(a.updated_at || 0).getTime();
      if (timeDiff !== 0) return timeDiff;
      return String(a.name || '').localeCompare(String(b.name || ''));
    });
  }

  function currentCharacter() {
    return state.characters.find((character) => character.id === state.selectedId) || null;
  }

  function ensureSelection() {
    const existing = currentCharacter();
    if (existing) {
      state.draft = cloneCharacter(existing);
      return;
    }

    if (state.characters.length > 0) {
      state.selectedId = state.characters[0].id;
      state.draft = cloneCharacter(state.characters[0]);
      return;
    }

    state.selectedId = null;
    state.draft = emptyDraft();
  }

  async function loadCharacters() {
    const characters = await API.fetch('/api/characters');
    state.characters = sortCharacters(Array.isArray(characters) ? characters : []);
    ensureSelection();
  }

  function filteredCharacters() {
    const query = state.search.trim().toLowerCase();
    if (!query) return state.characters;

    return state.characters.filter((character) => {
      const haystack = [
        character.name,
        character.description,
        ...(Array.isArray(character.image_paths) ? character.image_paths : []),
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      return haystack.includes(query);
    });
  }

  function mediaTileHelper() {
    return App.mediaTile || window.MediaUtil;
  }

  function renderCharacterItem(character) {
    const selected = character.id === state.selectedId;
    const count = Array.isArray(character.image_paths) ? character.image_paths.length : 0;
    const cardStyle = selected
      ? 'border-color: var(--accent); background: var(--accent-muted); box-shadow: inset 0 0 0 1px var(--accent-border);'
      : '';

    return `
      <button
        type="button"
        class="card card-clickable"
        data-character-id="${App.escapeHtml(character.id)}"
        style="width: 100%; margin-bottom: 12px; padding: 16px; text-align: left; ${cardStyle}"
      >
        <div class="section-header" style="margin-bottom: 10px;">
          <div style="display: flex; align-items: center; gap: 10px; min-width: 0;">
            <div class="job-type-icon insert" aria-hidden="true">
              <span class="material-icons">face</span>
            </div>
            <div style="min-width: 0;">
              <div class="job-type-label" style="word-break: break-word;">${App.escapeHtml(character.name)}</div>
              <div class="form-hint">${count} image${count === 1 ? '' : 's'}</div>
            </div>
          </div>
          <span class="badge badge-completed">Saved</span>
        </div>
        <div class="job-prompt ${character.description ? '' : 'empty'}" style="min-height: 0;">
          ${character.description ? App.escapeHtml(character.description) : 'No description'}
        </div>
        <div class="profile-details" style="margin-top: 12px; padding-top: 12px;">
          <div class="profile-detail">
            <span class="material-icons">schedule</span>
            Updated ${App.escapeHtml(App.formatDate(character.updated_at))}
          </div>
        </div>
      </button>
    `;
  }

  function renderListPanel() {
    const characters = filteredCharacters();

    return `
      <div class="card">
        <div class="section-header">
          <div>
            <h3 class="section-title">Characters</h3>
            <p class="form-hint">${state.characters.length} total record${state.characters.length === 1 ? '' : 's'}</p>
          </div>
          <button type="button" class="btn btn-outline btn-sm" id="characters-new">
            <span class="material-icons">add</span> New
          </button>
        </div>

        <div class="form-group" style="margin-bottom: 20px;">
          <label class="form-label" for="characters-search">Search</label>
          <input
            type="search"
            class="form-input"
            id="characters-search"
            placeholder="Find by name, description, or upload path"
            value="${App.escapeHtml(state.search)}"
          >
        </div>

        <div>
          ${
            characters.length === 0
              ? `
                <div class="empty-state" style="padding: 40px 16px;">
                  <span class="material-icons">search_off</span>
                  <h3>${state.characters.length === 0 ? 'No characters yet' : 'No matches'}</h3>
                  <p>${
                    state.characters.length === 0
                      ? 'Create your first reusable character entry from the editor.'
                      : 'Try a different search term or clear the filter.'
                  }</p>
                </div>
              `
              : characters.map(renderCharacterItem).join('')
          }
        </div>
      </div>
    `;
  }

  function renderImageCard(path, index) {
    return `
      <div class="card" style="padding: 12px; position: relative;">
        <button
          type="button"
          class="icon-btn"
          data-remove-image-index="${index}"
          title="Remove image"
          style="position: absolute; top: 8px; right: 8px; width: 28px; height: 28px;"
        >
          <span class="material-icons" style="font-size: 18px;">close</span>
        </button>
        <div class="tile-thumb" style="height: 144px; border-radius: 10px; border: 1px solid var(--border);">
          ${mediaTileHelper().imgTag({
            src: `/${path}`,
            alt: `Character reference ${index + 1}`,
          })}
        </div>
        <div class="form-hint" style="margin-top: 8px; word-break: break-all;">${App.escapeHtml(path)}</div>
      </div>
    `;
  }

  function renderFormPanel() {
    const editing = !!state.selectedId;
    const imageCount = state.draft.image_paths.length;

    return `
      <div class="card">
        <div class="section-header">
          <div>
            <h3 class="section-title">${editing ? 'Edit Character' : 'Create Character'}</h3>
            <p class="form-hint">${
              editing
                ? 'Update the saved KOL reference and keep its upload paths in sync.'
                : 'Create a reusable KOL reference from one or more uploaded images.'
            }</p>
          </div>
          ${
            editing
              ? `<span class="badge badge-running">Selected</span>`
              : `<span class="badge badge-pending">New</span>`
          }
        </div>

        <form id="characters-form">
          <div class="form-group">
            <label class="form-label" for="character-name">Name <span class="required">*</span></label>
            <input
              type="text"
              class="form-input"
              id="character-name"
              data-field="name"
              maxlength="64"
              placeholder="e.g. Ivy Tran"
              value="${App.escapeHtml(state.draft.name)}"
            >
          </div>

          <div class="form-group">
            <label class="form-label" for="character-description">Description</label>
            <textarea
              class="form-textarea"
              id="character-description"
              data-field="description"
              rows="5"
              placeholder="Persona notes, visual traits, wardrobe, tone..."
            >${App.escapeHtml(state.draft.description)}</textarea>
          </div>

          <div class="form-group">
            <div class="section-header" style="margin-bottom: 12px;">
              <div>
                <label class="form-label" style="margin-bottom: 0;">Reference Images <span class="required">*</span></label>
                <p class="form-hint">${imageCount}/${MAX_IMAGES} uploaded image${imageCount === 1 ? '' : 's'}</p>
              </div>
              <button
                type="button"
                class="btn btn-outline btn-sm"
                id="character-upload-trigger"
                ${state.uploading || imageCount >= MAX_IMAGES ? 'disabled' : ''}
              >
                ${
                  state.uploading
                    ? '<span class="spinner"></span> Uploading...'
                    : '<span class="material-icons">upload</span> Upload Images'
                }
              </button>
            </div>
            <input
              type="file"
              id="character-upload-input"
              accept="image/png,image/jpeg,image/webp"
              multiple
              hidden
            >
            ${
              imageCount === 0
                ? `
                  <div class="empty-state" style="padding: 32px 16px;">
                    <span class="material-icons">portrait</span>
                    <h3>No reference images</h3>
                    <p>Upload 1-10 files. Each file is stored via <code>/api/uploads</code> and saved as an <code>image_paths</code> entry.</p>
                  </div>
                `
                : `
                  <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px;">
                    ${state.draft.image_paths.map(renderImageCard).join('')}
                  </div>
                `
            }
          </div>

          ${
            editing
              ? `
                <div class="form-row">
                  <div class="form-group">
                    <label class="form-label">Character ID</label>
                    <input type="text" class="form-input" value="${App.escapeHtml(state.draft.id)}" readonly>
                  </div>
                  <div class="form-group">
                    <label class="form-label">Updated</label>
                    <input type="text" class="form-input" value="${App.escapeHtml(App.formatDate(state.draft.updated_at))}" readonly>
                  </div>
                </div>
              `
              : ''
          }

          <div style="display: flex; flex-wrap: wrap; gap: 12px;">
            <button type="submit" class="btn btn-primary" id="characters-save" ${state.saving ? 'disabled' : ''}>
              ${
                state.saving
                  ? '<span class="spinner"></span> Saving...'
                  : `<span class="material-icons">save</span> ${editing ? 'Save Changes' : 'Create Character'}`
              }
            </button>
            ${
              editing
                ? `
                  <button type="button" class="btn btn-danger" id="characters-delete" ${state.deleting ? 'disabled' : ''}>
                    ${
                      state.deleting
                        ? '<span class="spinner"></span> Deleting...'
                        : '<span class="material-icons">delete</span> Delete'
                    }
                  </button>
                `
                : ''
            }
          </div>
        </form>
      </div>
    `;
  }

  function renderPage() {
    if (!root) return;

    root.innerHTML = `
      <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 24px; align-items: start;">
        ${renderListPanel()}
        ${renderFormPanel()}
      </div>
    `;
  }

  function selectCharacter(characterId) {
    const character = state.characters.find((item) => item.id === characterId);
    if (!character) return;
    state.selectedId = character.id;
    state.draft = cloneCharacter(character);
    renderPage();
  }

  function startNewCharacter() {
    state.selectedId = null;
    state.draft = emptyDraft();
    renderPage();
  }

  function validateDraft() {
    const name = state.draft.name.trim();
    if (!name) return 'Character name is required.';
    if (name.length > 64) return 'Character name must be 64 characters or fewer.';
    if (!Array.isArray(state.draft.image_paths) || state.draft.image_paths.length === 0) {
      return 'Upload at least one reference image.';
    }
    if (state.draft.image_paths.length > MAX_IMAGES) {
      return `A character can include at most ${MAX_IMAGES} images.`;
    }
    return null;
  }

  function buildPayload() {
    return {
      name: state.draft.name.trim(),
      description: state.draft.description.trim() || null,
      image_paths: [...state.draft.image_paths],
    };
  }

  function upsertCharacter(character) {
    const next = state.characters.filter((item) => item.id !== character.id);
    next.push(character);
    state.characters = sortCharacters(next);
    state.selectedId = character.id;
    state.draft = cloneCharacter(character);
  }

  async function saveCharacter() {
    const error = validateDraft();
    if (error) {
      App.toast(error, 'warning');
      return;
    }

    const wasEditing = !!state.selectedId;
    state.saving = true;
    renderPage();

    try {
      const payload = buildPayload();
      const character = wasEditing
        ? await API.fetch(`/api/characters/${state.selectedId}`, {
            method: 'PUT',
            body: JSON.stringify(payload),
          })
        : await API.fetch('/api/characters', {
            method: 'POST',
            body: JSON.stringify(payload),
          });

      upsertCharacter(character);
      state.saving = false;
      renderPage();
      App.toast(wasEditing ? 'Character saved.' : 'Character created.', 'success');
    } catch (err) {
      state.saving = false;
      renderPage();
      App.toast(`Failed to save character: ${err.message}`, 'error');
    }
  }

  async function deleteCharacter() {
    const character = currentCharacter();
    if (!character) return;

    if (!window.confirm(`Delete character "${character.name}"?`)) {
      return;
    }

    state.deleting = true;
    renderPage();

    try {
      await API.fetch(`/api/characters/${character.id}`, { method: 'DELETE' });
      state.characters = state.characters.filter((item) => item.id !== character.id);
      state.deleting = false;
      ensureSelection();
      renderPage();
      App.toast('Character deleted.', 'success');
    } catch (err) {
      state.deleting = false;
      renderPage();
      App.toast(`Failed to delete character: ${err.message}`, 'error');
    }
  }

  async function uploadImages(files) {
    if (!files.length) return;

    const remaining = MAX_IMAGES - state.draft.image_paths.length;
    if (remaining <= 0) {
      App.toast(`A character can include up to ${MAX_IMAGES} images.`, 'warning');
      return;
    }

    if (files.length > remaining) {
      App.toast(`Only ${remaining} more image${remaining === 1 ? '' : 's'} can be added.`, 'warning');
    }

    state.uploading = true;
    renderPage();

    try {
      const uploaded = [];
      for (const file of files.slice(0, remaining)) {
        const result = await API.uploads.create(file);
        if (!result?.path) {
          throw new Error('Upload completed without a path.');
        }
        uploaded.push(result.path);
        state.draft.image_paths.push(result.path);
      }
      App.toast(`${uploaded.length} image${uploaded.length === 1 ? '' : 's'} uploaded.`, 'success');
    } catch (err) {
      App.toast(`Image upload failed: ${err.message}`, 'error');
    }

    state.uploading = false;
    renderPage();
  }

  function removeImage(index) {
    state.draft.image_paths = state.draft.image_paths.filter((_, itemIndex) => itemIndex !== index);
    renderPage();
  }

  async function handleClick(event) {
    const newButton = event.target.closest('#characters-new');
    if (newButton) {
      startNewCharacter();
      return;
    }

    const item = event.target.closest('[data-character-id]');
    if (item) {
      selectCharacter(item.dataset.characterId);
      return;
    }

    const uploadTrigger = event.target.closest('#character-upload-trigger');
    if (uploadTrigger && !uploadTrigger.disabled) {
      root.querySelector('#character-upload-input')?.click();
      return;
    }

    const removeImageButton = event.target.closest('[data-remove-image-index]');
    if (removeImageButton) {
      removeImage(Number.parseInt(removeImageButton.dataset.removeImageIndex, 10));
      return;
    }

    const deleteButton = event.target.closest('#characters-delete');
    if (deleteButton && !deleteButton.disabled) {
      await deleteCharacter();
    }
  }

  function handleInput(event) {
    const searchInput = event.target.closest('#characters-search');
    if (searchInput) {
      const cursor = searchInput.selectionStart ?? searchInput.value.length;
      state.search = searchInput.value;
      renderPage();
      const nextSearch = root.querySelector('#characters-search');
      if (nextSearch) {
        nextSearch.focus();
        nextSearch.setSelectionRange(cursor, cursor);
      }
      return;
    }

    const field = event.target.dataset.field;
    if (field) {
      state.draft[field] = event.target.value;
    }
  }

  async function handleChange(event) {
    const uploadInput = event.target.closest('#character-upload-input');
    if (!uploadInput) return;

    const files = Array.from(uploadInput.files || []);
    uploadInput.value = '';
    await uploadImages(files);
  }

  async function handleSubmit(event) {
    if (event.target.id !== 'characters-form') return;
    event.preventDefault();
    await saveCharacter();
  }

  const CharactersPage = {
    name: 'characters',
    title: 'Characters',
    icon: 'face',

    async render() {
      await loadCharacters();
      return '<div id="characters-page"></div>';
    },

    mount() {
      root = document.getElementById('characters-page');
      if (!root) return;

      handlers = {
        click: (event) => { void handleClick(event); },
        input: handleInput,
        change: (event) => { void handleChange(event); },
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
    },
  };

  App.register(CharactersPage);
})();
