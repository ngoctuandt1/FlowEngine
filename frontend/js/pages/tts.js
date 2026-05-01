/**
 * TTS Page
 * Generate speech from text with voice, rate, and pitch controls.
 */
(() => {
  const LOCALE_ORDER = ['vi-VN', 'en-US', 'en-GB', 'ja-JP', 'ko-KR'];
  const LOCALE_LABELS = {
    'vi-VN': 'Vietnamese (Vietnam)',
    'en-US': 'English (United States)',
    'en-GB': 'English (United Kingdom)',
    'ja-JP': 'Japanese (Japan)',
    'ko-KR': 'Korean (South Korea)',
  };
  // Keep this list aligned with server/routes/tts.py VOICE_PREFIXES.
  const VOICES = [
    { voice: 'vi-VN-HoaiMyNeural', locale: 'vi-VN', label: 'HoaiMy Neural' },
    { voice: 'en-US-JennyNeural', locale: 'en-US', label: 'Jenny Neural' },
    { voice: 'en-GB-SoniaNeural', locale: 'en-GB', label: 'Sonia Neural' },
    { voice: 'ja-JP-NanamiNeural', locale: 'ja-JP', label: 'Nanami Neural' },
    { voice: 'ko-KR-SunHiNeural', locale: 'ko-KR', label: 'SunHi Neural' },
  ];

  let cleanupFns = [];

  function guessLocale(voice) {
    const match = /^([a-z]{2}-[A-Z]{2})-/.exec(String(voice || ''));
    return match ? match[1] : '';
  }

  function humanizeVoiceName(voice) {
    const tail = String(voice || '').split('-').slice(2).join('-') || String(voice || '');
    return tail
      .replace(/([a-z])([A-Z])/g, '$1 $2')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function localeRank(locale) {
    const index = LOCALE_ORDER.indexOf(locale);
    return index === -1 ? Number.MAX_SAFE_INTEGER : index;
  }

  function localeLabel(locale) {
    return LOCALE_LABELS[locale] || locale || 'Unknown';
  }

  function groupVoices(voices) {
    const grouped = new Map();

    voices.forEach((voice) => {
      const locale = voice.locale || guessLocale(voice.voice) || 'unknown';
      if (!grouped.has(locale)) grouped.set(locale, []);
      grouped.get(locale).push({
        ...voice,
        locale,
      });
    });

    return Array.from(grouped.entries()).sort((a, b) => {
      const localeDiff = localeRank(a[0]) - localeRank(b[0]);
      if (localeDiff !== 0) return localeDiff;
      return a[0].localeCompare(b[0]);
    });
  }

  function renderLanguageOptions(voices, selectedLocale) {
    return groupVoices(voices)
      .map(([locale, items]) => `
        <option value="${App.escapeHtml(locale)}" ${locale === selectedLocale ? 'selected' : ''}>
          ${App.escapeHtml(localeLabel(locale))} (${items.length})
        </option>
      `)
      .join('');
  }

  function renderVoiceOptions(voices, selectedVoice) {
    return groupVoices(voices)
      .map(([locale, items]) => `
        <optgroup label="${App.escapeHtml(localeLabel(locale))}">
          ${items
            .map((item) => `
              <option
                value="${App.escapeHtml(item.voice)}"
                data-locale="${App.escapeHtml(locale)}"
                ${item.voice === selectedVoice ? 'selected' : ''}
              >
                ${App.escapeHtml(item.label)}
              </option>
            `)
            .join('')}
        </optgroup>
      `)
      .join('');
  }

  function resolveAudioUrl(outputPath) {
    const normalized = String(outputPath || '').replace(/\\/g, '/').trim();
    if (!normalized) return '';
    if (/^https?:\/\//i.test(normalized)) return normalized;

    if (/^\/?downloads\//i.test(normalized)) {
      const relative = normalized.replace(/^\/?downloads\//i, '');
      return `/downloads/${encodeURI(relative)}`;
    }

    const downloadMarkerIndex = normalized.toLowerCase().lastIndexOf('/downloads/');
    if (downloadMarkerIndex !== -1) {
      const relative = normalized.slice(downloadMarkerIndex + '/downloads/'.length);
      return `/downloads/${encodeURI(relative)}`;
    }

    if (/^tts\//i.test(normalized)) {
      return `/downloads/${encodeURI(normalized)}`;
    }

    return '';
  }

  function formatSignedValue(rawValue, suffix) {
    const value = Number(rawValue) || 0;
    return `${value >= 0 ? '+' : ''}${value}${suffix}`;
  }

  function getVoiceLocale(voiceValue, voices) {
    const matched = voices.find((voice) => voice.voice === voiceValue);
    return matched?.locale || guessLocale(voiceValue);
  }

  function getVoiceCountLabel(count) {
    return `${count} voice${count === 1 ? '' : 's'} loaded`;
  }

  function setBanner(elementId, message, type) {
    const el = document.getElementById(elementId);
    if (!el) return;

    if (!message) {
      el.style.display = 'none';
      el.innerHTML = '';
      return;
    }

    const palette = {
      error: {
        border: 'var(--error)',
        background: 'rgba(231,76,60,0.08)',
        icon: 'error',
      },
      warning: {
        border: '#eab308',
        background: 'rgba(234,179,8,0.08)',
        icon: 'warning',
      },
      info: {
        border: 'var(--accent-border)',
        background: 'rgba(124,92,255,0.08)',
        icon: 'info',
      },
    };
    const theme = palette[type] || palette.info;

    el.style.display = 'block';
    el.style.borderColor = theme.border;
    el.style.background = theme.background;
    el.innerHTML = `
      <div style="display:flex; gap:10px; align-items:flex-start;">
        <span class="material-icons" aria-hidden="true">${theme.icon}</span>
        <div>${App.escapeHtml(message)}</div>
      </div>
    `;
  }

  function updateRangeLabel(inputId, outputId, suffix) {
    const input = document.getElementById(inputId);
    const output = document.getElementById(outputId);
    if (!input || !output) return;
    output.textContent = formatSignedValue(input.value, suffix);
  }

  function updateCharacterCount() {
    const textArea = document.getElementById('tts-text');
    const counter = document.getElementById('tts-char-count');
    if (!textArea || !counter) return;
    counter.textContent = `${textArea.value.length} / 5000 characters`;
  }

  function renderResultIdle() {
    const container = document.getElementById('tts-result');
    if (!container) return;
    container.innerHTML = `
      <div class="empty-state" style="padding: 24px 12px;">
        <span class="material-icons">headphones</span>
        <h3>No audio yet</h3>
        <p>Generate a TTS clip to preview it here and download the mp3.</p>
      </div>
    `;
  }

  function renderResultLoading() {
    const container = document.getElementById('tts-result');
    if (!container) return;
    container.innerHTML = '<div class="loading-center"><div class="spinner spinner-lg"></div></div>';
  }

  function renderResultSuccess(result) {
    const container = document.getElementById('tts-result');
    if (!container) return;

    const audioUrl = resolveAudioUrl(result?.output_path);
    if (!audioUrl) {
      renderResultIdle();
      setBanner('tts-error-banner', 'TTS completed, but the returned audio path could not be resolved.', 'error');
      return;
    }

    const duration = Number(result?.duration_seconds_estimate);
    const durationLabel = Number.isFinite(duration) ? `${duration.toFixed(2)}s` : '-';
    const fileName = String(result.output_path || '').replace(/\\/g, '/').split('/').pop() || 'tts.mp3';

    container.innerHTML = `
      <div class="detail-list" style="margin-bottom: 16px;">
        <div class="detail-row">
          <span class="detail-label">Voice</span>
          <span class="detail-value">${App.escapeHtml(result.voice || '-')}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">Estimated Duration</span>
          <span class="detail-value">${App.escapeHtml(durationLabel)}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">Output Path</span>
          <span class="detail-value"><code style="font-size:12px">${App.escapeHtml(result.output_path || '-')}</code></span>
        </div>
      </div>
      <audio id="tts-audio-player" controls style="width: 100%; margin-bottom: 16px;">
        <source src="${App.escapeHtml(audioUrl)}" type="audio/mpeg">
      </audio>
      <a class="btn btn-primary" id="tts-download-link" href="${App.escapeHtml(audioUrl)}" download="${App.escapeHtml(fileName)}">
        <span class="material-icons">download</span> Download MP3
      </a>
    `;
  }

  function setVoiceControlsEnabled(enabled) {
    const languageSelect = document.getElementById('tts-language');
    const voiceSelect = document.getElementById('tts-voice');
    const submitButton = document.getElementById('tts-submit');

    if (languageSelect) languageSelect.disabled = !enabled;
    if (voiceSelect) voiceSelect.disabled = !enabled;
    if (submitButton) submitButton.disabled = !enabled;
  }

  function populateVoiceControls(voices, selectedVoice) {
    const languageSelect = document.getElementById('tts-language');
    const voiceSelect = document.getElementById('tts-voice');
    const voiceStatus = document.getElementById('tts-voice-status');
    if (!languageSelect || !voiceSelect || !voiceStatus) return;

    const fallbackVoice = voices[0]?.voice || '';
    const activeVoice = voices.some((voice) => voice.voice === selectedVoice) ? selectedVoice : fallbackVoice;
    const activeLocale = getVoiceLocale(activeVoice, voices) || voices[0]?.locale || '';

    languageSelect.innerHTML = renderLanguageOptions(voices, activeLocale);
    voiceSelect.innerHTML = renderVoiceOptions(voices, activeVoice);
    voiceStatus.textContent = getVoiceCountLabel(voices.length);
    setVoiceControlsEnabled(voices.length > 0);
  }

  function loadVoices() {
    populateVoiceControls(VOICES, 'vi-VN-HoaiMyNeural');
    const status = document.getElementById('tts-voice-status');
    if (status) status.textContent = getVoiceCountLabel(VOICES.length);
  }

  function syncLanguageSelectToVoice() {
    const languageSelect = document.getElementById('tts-language');
    const voiceSelect = document.getElementById('tts-voice');
    if (!languageSelect || !voiceSelect) return;

    const selectedOption = voiceSelect.selectedOptions[0];
    const locale = selectedOption?.dataset?.locale || '';
    if (locale) languageSelect.value = locale;
  }

  function selectFirstVoiceForLanguage(locale) {
    const voiceSelect = document.getElementById('tts-voice');
    if (!voiceSelect) return;

    const option = Array.from(voiceSelect.options).find((item) => item.value && item.dataset.locale === locale);
    if (option) {
      voiceSelect.value = option.value;
      syncLanguageSelectToVoice();
    }
  }

  function formatSubmitError(err) {
    if (!err) return 'Request failed.';
    if (err.status === 422) {
      return 'The TTS request was rejected. Check the text length and selected voice, then try again.';
    }
    return err.message || 'Request failed.';
  }

  async function handleSubmit(event) {
    event.preventDefault();

    const textArea = document.getElementById('tts-text');
    const voiceSelect = document.getElementById('tts-voice');
    const rateInput = document.getElementById('tts-rate');
    const pitchInput = document.getElementById('tts-pitch');
    const submitButton = document.getElementById('tts-submit');

    const text = textArea?.value?.trim() || '';
    const voice = voiceSelect?.value || '';

    if (!text) {
      setBanner('tts-error-banner', 'Text is required.', 'error');
      textArea?.focus();
      return;
    }

    if (!voice) {
      setBanner('tts-error-banner', 'Select a voice before generating audio.', 'error');
      voiceSelect?.focus();
      return;
    }

    setBanner('tts-error-banner', '', 'error');
    renderResultLoading();

    if (submitButton) {
      submitButton.disabled = true;
      submitButton.innerHTML = '<span class="spinner"></span> Generating...';
    }

    try {
      const response = await API.fetch('/api/tts', {
        method: 'POST',
        body: JSON.stringify({
          text,
          voice,
          rate: formatSignedValue(rateInput?.value, '%'),
          pitch: formatSignedValue(pitchInput?.value, 'Hz'),
        }),
      });

      renderResultSuccess(response);
      App.toast('Audio generated.', 'success');
    } catch (err) {
      renderResultIdle();
      setBanner('tts-error-banner', formatSubmitError(err), 'error');
    } finally {
      if (submitButton) {
        submitButton.disabled = false;
        submitButton.innerHTML = '<span class="material-icons">graphic_eq</span> Generate Audio';
      }
    }
  }

  const TtsPage = {
    name: 'tts',
    title: 'Text to Speech',
    icon: 'record_voice_over',

    render() {
      return `
        <div class="settings-grid">
          <div class="settings-card">
            <h3><span class="material-icons">record_voice_over</span> Text to Speech</h3>
            <div id="tts-error-banner" class="card" style="display:none; margin-bottom: 16px; padding: 12px 14px;"></div>

            <form id="tts-form">
              <div class="form-group">
                <label class="form-label" for="tts-text">Text <span class="required">*</span></label>
                <textarea
                  class="form-textarea"
                  id="tts-text"
                  maxlength="5000"
                  placeholder="Type the script you want to synthesize."
                ></textarea>
                <span class="form-hint" id="tts-char-count">0 / 5000 characters</span>
              </div>

              <div class="form-row">
                <div class="form-group">
                  <label class="form-label" for="tts-language">Language</label>
                  <select class="form-select" id="tts-language" disabled>
                    <option value="">Loading languages...</option>
                  </select>
                </div>
                <div class="form-group">
                  <label class="form-label" for="tts-voice">Voice <span class="required">*</span></label>
                  <select class="form-select" id="tts-voice" disabled>
                    <option value="">Loading voices...</option>
                  </select>
                  <span class="form-hint" id="tts-voice-status">Loading voices...</span>
                </div>
              </div>

              <div class="form-row">
                <div class="form-group">
                  <label class="form-label" for="tts-rate">Rate <span id="tts-rate-value">+0%</span></label>
                  <input type="range" id="tts-rate" min="-50" max="50" step="5" value="0" style="width: 100%;">
                  <span class="form-hint">Adjust speaking speed.</span>
                </div>
                <div class="form-group">
                  <label class="form-label" for="tts-pitch">Pitch <span id="tts-pitch-value">+0Hz</span></label>
                  <input type="range" id="tts-pitch" min="-50" max="50" step="5" value="0" style="width: 100%;">
                  <span class="form-hint">Adjust the voice pitch.</span>
                </div>
              </div>

              <button class="btn btn-primary" id="tts-submit" type="submit" disabled>
                <span class="material-icons">graphic_eq</span> Generate Audio
              </button>
            </form>
          </div>

          <div class="settings-card">
            <h3><span class="material-icons">headphones</span> Preview</h3>
            <div id="tts-result"></div>
          </div>
        </div>
      `;
    },

    mount() {
      cleanupFns.forEach((cleanup) => cleanup());
      cleanupFns = [];

      const form = document.getElementById('tts-form');
      const languageSelect = document.getElementById('tts-language');
      const voiceSelect = document.getElementById('tts-voice');
      const rateInput = document.getElementById('tts-rate');
      const pitchInput = document.getElementById('tts-pitch');
      const textArea = document.getElementById('tts-text');

      renderResultIdle();
      updateCharacterCount();
      updateRangeLabel('tts-rate', 'tts-rate-value', '%');
      updateRangeLabel('tts-pitch', 'tts-pitch-value', 'Hz');

      if (form) {
        form.addEventListener('submit', handleSubmit);
        cleanupFns.push(() => form.removeEventListener('submit', handleSubmit));
      }

      if (languageSelect) {
        const onLanguageChange = () => selectFirstVoiceForLanguage(languageSelect.value);
        languageSelect.addEventListener('change', onLanguageChange);
        cleanupFns.push(() => languageSelect.removeEventListener('change', onLanguageChange));
      }

      if (voiceSelect) {
        const onVoiceChange = () => syncLanguageSelectToVoice();
        voiceSelect.addEventListener('change', onVoiceChange);
        cleanupFns.push(() => voiceSelect.removeEventListener('change', onVoiceChange));
      }

      if (rateInput) {
        const onRateInput = () => updateRangeLabel('tts-rate', 'tts-rate-value', '%');
        rateInput.addEventListener('input', onRateInput);
        cleanupFns.push(() => rateInput.removeEventListener('input', onRateInput));
      }

      if (pitchInput) {
        const onPitchInput = () => updateRangeLabel('tts-pitch', 'tts-pitch-value', 'Hz');
        pitchInput.addEventListener('input', onPitchInput);
        cleanupFns.push(() => pitchInput.removeEventListener('input', onPitchInput));
      }

      if (textArea) {
        textArea.addEventListener('input', updateCharacterCount);
        cleanupFns.push(() => textArea.removeEventListener('input', updateCharacterCount));
      }

      loadVoices();
    },

    destroy() {
      cleanupFns.forEach((cleanup) => cleanup());
      cleanupFns = [];
      const audio = document.getElementById('tts-audio-player');
      if (audio) audio.pause();
    },
  };

  App.register(TtsPage);
})();
