/**
 * FlowEngine WebSocket Client
 * Real-time job status updates with auto-reconnect.
 */
const WS = {
  socket: null,
  url: null,
  reconnectTimer: null,
  reconnectDelay: 1000,
  maxReconnectDelay: 30000,
  listeners: new Map(),
  isConnecting: false,

  /**
   * Connect to WebSocket server.
   * @param {string} host - Optional host override (defaults to current location)
   */
  connect(host) {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) return;
    if (this.isConnecting) return;

    const wsProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    this.url = host || `${wsProtocol}//${location.host}/ws/jobs`;

    this.isConnecting = true;
    this._updateStatus('connecting');

    try {
      this.socket = new WebSocket(this.url);
    } catch (err) {
      console.error('[WS] Failed to create WebSocket:', err);
      this.isConnecting = false;
      this._scheduleReconnect();
      return;
    }

    this.socket.onopen = () => {
      console.log('[WS] Connected to', this.url);
      this.isConnecting = false;
      this.reconnectDelay = 1000;
      this._updateStatus('connected');
      this._emit('connected');
    };

    this.socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        this._handleMessage(data);
      } catch (err) {
        console.warn('[WS] Failed to parse message:', err);
      }
    };

    this.socket.onclose = (event) => {
      console.log('[WS] Disconnected, code:', event.code);
      this.isConnecting = false;
      this._updateStatus('disconnected');
      this._emit('disconnected');
      this._scheduleReconnect();
    };

    this.socket.onerror = (err) => {
      console.error('[WS] Error:', err);
      this.isConnecting = false;
    };
  },

  /**
   * Disconnect from WebSocket server.
   */
  disconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.socket) {
      this.socket.onclose = null; // Prevent auto-reconnect
      this.socket.close();
      this.socket = null;
    }
    this._updateStatus('disconnected');
  },

  /**
   * Register an event listener.
   * Events: connected, disconnected, job_created, job_updated,
   *         job_completed, job_failed, job_deleted, status_change
   */
  on(event, callback) {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, new Set());
    }
    this.listeners.get(event).add(callback);
    return () => this.off(event, callback);
  },

  /**
   * Remove an event listener.
   */
  off(event, callback) {
    const cbs = this.listeners.get(event);
    if (cbs) cbs.delete(callback);
  },

  /**
   * Send a message to the server.
   */
  send(data) {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(data));
    }
  },

  // ---- Private ----

  _handleMessage(data) {
    const { type, payload } = data;
    if (!type) return;

    // Emit specific event type
    this._emit(type, payload);

    // Also emit generic 'message' for any listener
    this._emit('message', data);
  },

  _emit(event, data) {
    const cbs = this.listeners.get(event);
    if (cbs) {
      cbs.forEach((cb) => {
        try {
          cb(data);
        } catch (err) {
          console.error(`[WS] Listener error for '${event}':`, err);
        }
      });
    }
  },

  _scheduleReconnect() {
    if (this.reconnectTimer) return;

    console.log(`[WS] Reconnecting in ${this.reconnectDelay / 1000}s...`);
    this._updateStatus('connecting');

    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, this.reconnectDelay);

    // Exponential backoff capped at max
    this.reconnectDelay = Math.min(
      this.reconnectDelay * 2,
      this.maxReconnectDelay
    );
  },

  _updateStatus(status) {
    const el = document.getElementById('ws-status');
    if (!el) return;

    el.className = `ws-indicator ${status}`;
    const label = el.querySelector('.ws-label');
    if (label) {
      const labels = {
        connected: 'Connected',
        connecting: 'Connecting...',
        disconnected: 'Disconnected',
      };
      label.textContent = labels[status] || status;
    }
  },
};
