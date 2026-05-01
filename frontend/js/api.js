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
