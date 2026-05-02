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
     * @param {Object} filters - { status, type, profile, limit, offset }
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
      return API.fetch(`/api/jobs/${id}`);
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
      return API.fetch(`/api/jobs/${id}`, {
        method: 'DELETE',
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
     * List all chains.
     */
    async list() {
      return API.fetch('/api/chains');
    },

    /**
     * Get chain details.
     */
    async get(id) {
      return API.fetch(`/api/chains/${id}`);
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
      return API.fetch(`/api/profiles/${id}`, {
        method: 'PUT',
        body: JSON.stringify(data),
      });
    },

    /**
     * Quarantine a profile.
     */
    async quarantine(id) {
      return API.fetch(`/api/profiles/${id}/quarantine`, {
        method: 'POST',
      });
    },

    /**
     * Activate a profile.
     */
    async activate(id) {
      return API.fetch(`/api/profiles/${id}/activate`, {
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

const TILE_MEDIA_ERROR_HANDLER = "this.closest('.tile-thumb')?.classList.add('tile-thumb--broken'); this.remove();";

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
    return `<img src="${App.escapeHtml(imageSrc)}" alt="${App.escapeHtml(alt || '')}" loading="lazy" decoding="async" onerror="${TILE_MEDIA_ERROR_HANDLER}" style="width:100%; height:100%; object-fit:cover; display:block;">`;
  },

  videoTag({ src, poster, alt } = {}) {
    const posterAttr = poster ? ` poster="${App.escapeHtml(bustMediaCache(poster))}"` : '';
    const ariaAttr = alt ? ` aria-label="${App.escapeHtml(alt)}"` : '';
    const finalSrc = bustMediaCache(src || '');
    return `<video class="tile-video" src="${App.escapeHtml(finalSrc)}"${posterAttr}${ariaAttr} muted loop playsinline preload="auto" onloadeddata="this.currentTime=0.1" onerror="${TILE_MEDIA_ERROR_HANDLER}" onmouseenter="this.play().catch(()=>{})" onmouseleave="this.pause(); this.currentTime=0;"></video>`;
  },
};

window.MediaUtil = MediaTile;

document.addEventListener('DOMContentLoaded', () => {
  if (typeof App !== 'undefined') {
    App.api = API;
    App.mediaTile = MediaTile;
    App.formatTileDate = formatTileDate;
  }
});
