/**
 * FlowEngine API Client
 * Handles all REST API communication with the backend.
 */
const API = {
  baseUrl: '',

  /**
   * Core fetch wrapper with error handling.
   */
  async fetch(path, options = {}) {
    const url = `${this.baseUrl}${path}`;
    const defaults = {
      headers: {
        'Content-Type': 'application/json',
      },
    };

    const config = {
      ...defaults,
      ...options,
      headers: { ...defaults.headers, ...options.headers },
    };

    // Don't set Content-Type for FormData
    if (config.body instanceof FormData) {
      delete config.headers['Content-Type'];
    }

    try {
      const response = await fetch(url, config);

      if (!response.ok) {
        let errorMsg = `HTTP ${response.status}`;
        try {
          const errorData = await response.json();
          errorMsg = errorData.detail || errorData.message || errorMsg;
        } catch {
          // response wasn't JSON
        }
        throw new APIError(errorMsg, response.status);
      }

      // Handle 204 No Content
      if (response.status === 204) {
        return null;
      }

      return await response.json();
    } catch (err) {
      if (err instanceof APIError) throw err;
      throw new APIError(`Network error: ${err.message}`, 0);
    }
  },

  // ---- Jobs ----

  jobs: {
    /**
     * List jobs with optional filters.
     * @param {Object} filters - { status, type, profile, q, limit, offset }
     */
    async list(filters = {}) {
      const params = new URLSearchParams();
      Object.entries(filters).forEach(([key, val]) => {
        if (val !== undefined && val !== null && val !== '') {
          params.append(key, val);
        }
      });
      const query = params.toString();
      return API.fetch(`/api/jobs${query ? '?' + query : ''}`);
    },

    /**
     * Get a single job by ID.
     */
    async get(id) {
      return API.fetch(`/api/jobs/${encodeURIComponent(id)}`);
    },

    /**
     * Create a new job.
     * @param {Object} data - Job creation payload
     */
    async create(data) {
      return API.fetch('/api/jobs', {
        method: 'POST',
        body: JSON.stringify(data),
      });
    },

    /**
     * Delete a job by ID.
     */
    async delete(id) {
      return API.fetch(`/api/jobs/${encodeURIComponent(id)}`, {
        method: 'DELETE',
      });
    },

    /**
     * Requeue a failed or cancelled job.
     */
    async requeue(id) {
      return API.fetch(`/api/jobs/${encodeURIComponent(id)}/requeue`, {
        method: 'POST',
      });
    },

    /**
     * Get job counts grouped by status.
     */
    async counts() {
      return API.fetch('/api/jobs/counts');
    },

    /**
     * Recover stale jobs stuck in claimed/running.
     */
    async recover() {
      return API.fetch('/api/jobs/recover', { method: 'POST' });
    },
  },

  // ---- Projects ----

  projects: {
    /**
     * List all projects for the home gallery.
     */
    async list() {
      return API.fetch('/api/projects');
    },

    /**
     * Create a new project row.
     * @param {Object} data - { name }
     */
    async create(data) {
      return API.fetch('/api/projects', {
        method: 'POST',
        body: JSON.stringify(data),
      });
    },
  },

  // ---- Chains ----

  chains: {
    /**
     * Create a chain of jobs.
     * @param {Object} data - { steps: [...] }
     */
    async create(data) {
      return API.fetch('/api/chains', {
        method: 'POST',
        body: JSON.stringify(data),
      });
    },

    /**
     * Get chain details.
     *
     * Note: there is intentionally no `list()` here — the backend does not
     * expose `GET /api/chains`. Callers that need a chain overview should
     * group `API.jobs.list({...})` results by `chain_id` (see home.js).
     */
    async get(id) {
      return API.fetch(`/api/chains/${encodeURIComponent(id)}`);
    },
  },

  // ---- Profiles ----

  profiles: {
    /**
     * List all profiles.
     */
    async list() {
      return API.fetch('/api/profiles');
    },

    /**
     * Create a new profile.
     * @param {Object} data - { name, google_account, locale, tier }
     */
    async create(data) {
      return API.fetch('/api/profiles', {
        method: 'POST',
        body: JSON.stringify(data),
      });
    },

    /**
     * Update a profile.
     */
    async update(id, data) {
      return API.fetch(`/api/profiles/${encodeURIComponent(id)}`, {
        method: 'PUT',
        body: JSON.stringify(data),
      });
    },

    /**
     * Reload profiles from Google Sheet credentials.
     */
    async reload(apiKey) {
      return API.fetch('/api/profiles/reload', {
        method: 'POST',
        headers: { 'X-Worker-API-Key': apiKey || '' },
      });
    },

    /**
     * Quarantine a profile.
     */
    async quarantine(id) {
      return API.fetch(`/api/profiles/${encodeURIComponent(id)}/quarantine`, {
        method: 'POST',
      });
    },

    /**
     * Activate a profile.
     */
    async activate(id) {
      return API.fetch(`/api/profiles/${encodeURIComponent(id)}/activate`, {
        method: 'POST',
      });
    },
  },

  // ---- Uploads ----

  uploads: {
    async create(file) {
      const form = new FormData();
      form.append('file', file);
      return API.fetch('/api/uploads', {
        method: 'POST',
        body: form,
      });
    },
  },
};


/**
 * Custom API error class.
 */
class APIError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'APIError';
    this.status = status;
  }
}

// Append a versioned query string so Cloudflare's edge serves a fresh
// response after the Cache-Control middleware change (CF was caching old
// responses without Accept-Ranges, breaking <video> playback). Stable per
// session — picked up at script load — so within one tab the URL doesn't
// flap and the browser still benefits from its own cache.
const MEDIA_CACHE_BUST = `_v=${Date.now().toString(36)}`;
function bustMediaCache(url) {
  if (!url) return url;
  if (url.startsWith('/downloads/') || url.startsWith('/uploads/')) {
    return url.includes('?') ? `${url}&${MEDIA_CACHE_BUST}` : `${url}?${MEDIA_CACHE_BUST}`;
  }
  return url;
}

function posterUrlFor(videoUrl) {
  if (!videoUrl) return '';
  const match = videoUrl.match(/^(\/downloads\/.+)\.mp4(\?.*)?$/i);
  if (!match) return '';
  return `${match[1]}.poster.jpg${match[2] || ''}`;
}

const TILE_DATE_FORMATTER = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: true,
});

function formatTileDate(dateStr) {
  if (!dateStr) return '-';

  const date = new Date(dateStr);
  if (Number.isNaN(date.getTime())) return '-';

  const parts = TILE_DATE_FORMATTER.formatToParts(date);
  const lookup = (type) => parts.find((part) => part.type === type)?.value || '';
  const month = lookup('month');
  const day = lookup('day');
  const hour = lookup('hour');
  const minute = lookup('minute');
  const dayPeriod = lookup('dayPeriod');

  if (month && day && hour && minute && dayPeriod) {
    return `${month} ${day}, ${hour}:${minute} ${dayPeriod}`;
  }

  return TILE_DATE_FORMATTER.format(date).replace(/\s+/g, ' ').trim();
}

const MediaTile = {
  imgTag({ src, alt, posterFallback } = {}) {
    const imageSrc = bustMediaCache(src || posterFallback || '');
    return `<img src="${App.escapeHtml(imageSrc)}" alt="${App.escapeHtml(alt || '')}" loading="lazy" decoding="async" data-media-tile-image-fallback="1" style="width:100%; height:100%; object-fit:cover; display:block;">`;
  },

  markBroken(element) {
    element?.closest('.tile-thumb')?.classList.add('tile-thumb--broken');
    element?.remove();
    return false;
  },

  createVideoElement({ src, alt, preload = 'metadata', autoplay = false, seekPreview = false, resetOnLeave = false } = {}) {
    const finalSrc = bustMediaCache(src || '');
    const videoElement = document.createElement('video');
    videoElement.className = 'tile-video';
    videoElement.src = finalSrc;
    if (alt) {
      videoElement.setAttribute('aria-label', alt);
    }
    videoElement.muted = true;
    videoElement.loop = true;
    videoElement.playsInline = true;
    videoElement.preload = preload;
    videoElement.autoplay = autoplay;
    if (seekPreview) {
      videoElement.addEventListener('loadedmetadata', () => {
        try {
          videoElement.currentTime = 0.1;
        } catch {
          // Ignore preview seek failures and keep the first decoded frame.
        }
      }, { once: true });
    }
    videoElement.addEventListener('error', () => {
      MediaTile.markBroken(videoElement);
    }, { once: true });
    videoElement.addEventListener('mouseenter', () => {
      videoElement.play().catch(() => {});
    });
    videoElement.addEventListener('mouseleave', () => {
      videoElement.pause();
      if (resetOnLeave) {
        try {
          videoElement.currentTime = 0;
        } catch {
          // Ignore reset failures on partially buffered videos.
        }
      }
    });
    return videoElement;
  },

  handlePosterError(imgElement) {
    const videoSrc = imgElement?.dataset?.videoSrc || '';
    if (!videoSrc) {
      return MediaTile.markBroken(imgElement);
    }
    const videoElement = MediaTile.createVideoElement({
      src: videoSrc,
      alt: imgElement.getAttribute('alt') || '',
      preload: 'auto',
      seekPreview: true,
      resetOnLeave: true,
    });
    imgElement.replaceWith(videoElement);
    return false;
  },

  upgradeToVideo(imgElement) {
    const videoSrc = imgElement?.dataset?.videoSrc || '';
    if (!videoSrc || imgElement?.dataset?.videoUpgraded === '1') {
      return false;
    }
    imgElement.dataset.videoUpgraded = '1';
    const videoElement = MediaTile.createVideoElement({
      src: videoSrc,
      alt: imgElement.getAttribute('alt') || '',
      preload: 'metadata',
      autoplay: true,
    });
    imgElement.replaceWith(videoElement);
    videoElement.play().catch(() => {});
    return false;
  },

  videoTag({ src, poster, alt } = {}) {
    const rawSrc = src || '';
    const finalSrc = bustMediaCache(rawSrc);
    const derivedPoster = bustMediaCache(poster || '') || posterUrlFor(finalSrc) || '';
    const safeAlt = App.escapeHtml(alt || '');
    if (!derivedPoster) {
      const ariaAttr = alt ? ` aria-label="${safeAlt}"` : '';
      return `<video class="tile-video" src="${App.escapeHtml(finalSrc)}"${ariaAttr} muted loop playsinline preload="auto" data-media-tile-video="1" data-media-seek-preview="1" data-media-reset-on-leave="1"></video>`;
    }
    return `<img class="tile-video" src="${App.escapeHtml(derivedPoster)}" alt="${safeAlt}" loading="lazy" decoding="async" data-video-src="${App.escapeHtml(rawSrc)}" data-media-poster-fallback="1" data-media-poster-upgrade="1">`;
  },
};

function mediaDelegateTarget(event, selector) {
  const target = event.target;
  return target instanceof Element ? target.closest(selector) : null;
}

function seekPreviewFrame(videoElement) {
  try {
    videoElement.currentTime = 0.1;
  } catch {
  }
}

function resetVideoFrame(videoElement) {
  try {
    videoElement.currentTime = 0;
  } catch {
  }
}

function installMediaTileDelegates() {
  if (document.documentElement.dataset.mediaTileDelegates === '1') return;
  document.documentElement.dataset.mediaTileDelegates = '1';

  document.addEventListener('error', (event) => {
    const poster = mediaDelegateTarget(event, 'img[data-media-poster-fallback]');
    if (poster) {
      MediaTile.handlePosterError(poster);
      return;
    }

    const image = mediaDelegateTarget(event, 'img[data-media-tile-image-fallback]');
    if (image) {
      MediaTile.markBroken(image);
      return;
    }

    const video = mediaDelegateTarget(event, 'video[data-media-tile-video]');
    if (video) {
      MediaTile.markBroken(video);
    }
  }, true);

  document.addEventListener('loadedmetadata', (event) => {
    const video = mediaDelegateTarget(event, 'video[data-media-seek-preview]');
    if (video) seekPreviewFrame(video);
  }, true);

  document.addEventListener('mouseover', (event) => {
    const poster = mediaDelegateTarget(event, 'img[data-media-poster-upgrade]');
    if (poster) {
      MediaTile.upgradeToVideo(poster);
      return;
    }

    const video = mediaDelegateTarget(event, 'video[data-media-tile-video]');
    if (video) video.play().catch(() => {});
  });

  document.addEventListener('mouseout', (event) => {
    const video = mediaDelegateTarget(event, 'video[data-media-tile-video]');
    if (!video || (event.relatedTarget instanceof Node && video.contains(event.relatedTarget))) return;

    video.pause();
    if (video.dataset.mediaResetOnLeave === '1') resetVideoFrame(video);
  });
}

window.MediaUtil = MediaTile;
installMediaTileDelegates();

document.addEventListener('DOMContentLoaded', () => {
  if (typeof App !== 'undefined') {
    App.api = API;
    App.mediaTile = MediaTile;
    App.formatTileDate = formatTileDate;
  }
});
