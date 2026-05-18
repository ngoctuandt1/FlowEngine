/**
 * Media Tools Timeline Editor Page
 * Visual-only multi-track shell with local drag/drop state.
 */
(() => {
  const VIDEO_EXTENSIONS = new Set(['mp4', 'webm', 'mov', 'm4v']);
  const AUDIO_EXTENSIONS = new Set(['mp3', 'wav', 'm4a', 'aac', 'ogg', 'flac']);
  const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp']);
  const TRACK_LABEL_WIDTH = 156;
  const BASE_PIXELS_PER_SECOND = 74;
  const DEFAULT_CLIP_DURATION_SEC = 5;
  const MIN_CLIP_DURATION_SEC = 0.5;
  const PLAYBACK_FPS = 30;
  const RENDER_POLL_INTERVAL_MS = 2000;
  const RENDER_MAX_TRACKS = 10;
  const RENDER_MAX_CLIPS = 100;
  const RENDER_MAX_DURATION_SEC = 600;
  const ASPECT_OPTIONS = ['9:16', '16:9', '1:1'];
  const TRACK_DEFS = [
    { id: 'track-1', label: 'Track 1', kind: 'text' },
    { id: 'track-2', label: 'Track 2', kind: 'video' },
    { id: 'track-3', label: 'Track 3', kind: 'audio' },
    { id: 'track-4', label: 'Track 4', kind: 'overlay' },
    { id: 'track-5', label: 'Track 5', kind: 'overlay' },
  ];

  let resourceCounter = 0;
  let clipCounter = 0;
  let rootEl = null;
  let seedPromise = null;
  let playbackFrameId = 0;
  let lastPlaybackTs = 0;
  let renderedPreviewClipId = '';
  let activeDropLane = null;
  let renderPollTimerId = 0;
  let renderGeneration = 0;

  const state = {
    resources: [],
    tracks: buildTracks(),
    playheadSec: 0,
    totalDurationSec: 30,
    selectedTrackId: 'track-3',
    selectedClipId: '',
    editingClipId: '',
    zoom: 1,
    aspectRatio: '9:16',
    isPlaying: false,
    seedLoaded: false,
    seedError: '',
    render: {
      renderId: '',
      status: '',
      progress: 0,
      outputUrl: '',
      error: '',
      isPolling: false,
    },
  };

  function buildTracks() {
    return TRACK_DEFS.map((track) => ({
      ...track,
      visible: true,
      clips: [],
    }));
  }

  function escapeAttr(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  function clamp(value, min, max) {
    return Math.min(Math.max(value, min), max);
  }

  function roundTimeline(value) {
    return Math.round(Number(value || 0) * 20) / 20;
  }

  function pixelsPerSecond() {
    return BASE_PIXELS_PER_SECOND * state.zoom;
  }

  function timelineWidthPx() {
    return Math.max(1, Math.round(state.totalDurationSec * pixelsPerSecond()));
  }

  function playheadLeftPx() {
    return TRACK_LABEL_WIDTH + (state.playheadSec * pixelsPerSecond());
  }

  function pad2(value) {
    return String(value).padStart(2, '0');
  }

  function formatTimecode(seconds) {
    const safe = clamp(Number(seconds || 0), 0, state.totalDurationSec);
    const wholeSeconds = Math.floor(safe);
    const hours = Math.floor(wholeSeconds / 3600);
    const minutes = Math.floor((wholeSeconds % 3600) / 60);
    const secs = wholeSeconds % 60;
    const frames = Math.floor((safe - wholeSeconds) * PLAYBACK_FPS + 1e-6);
    return `${pad2(hours)}:${pad2(minutes)}:${pad2(secs)}.${pad2(frames)}`;
  }

  function formatShortSeconds(seconds) {
    const safe = Math.max(0, Number(seconds || 0));
    return `${safe.toFixed(1).replace(/\.0$/, '')}s`;
  }

  function formatKindLabel(kind) {
    return String(kind || 'video').toUpperCase();
  }

  function normalizePath(path) {
    return String(path || '').replace(/\\/g, '/').trim();
  }

  function basename(path) {
    const normalized = normalizePath(path);
    return normalized.split('/').pop() || normalized || 'Untitled asset';
  }

  function extensionFromName(name) {
    const base = basename(name);
    if (!base.includes('.')) return '';
    return base.split('.').pop().toLowerCase();
  }

  function kindFromValue(nameOrType) {
    const value = String(nameOrType || '').toLowerCase();
    if (!value) return null;
    if (value.startsWith('video/')) return 'video';
    if (value.startsWith('audio/')) return 'audio';
    if (value.startsWith('image/')) return 'image';
    const extension = value.includes('/') && !value.includes('.') ? '' : extensionFromName(value);
    if (VIDEO_EXTENSIONS.has(extension)) return 'video';
    if (AUDIO_EXTENSIONS.has(extension)) return 'audio';
    if (IMAGE_EXTENSIONS.has(extension)) return 'image';
    return null;
  }

  function mediaUrl(path) {
    const normalized = normalizePath(path);
    if (!normalized) return '';
    if (/^(blob:|https?:)/i.test(normalized)) return normalized;
    if (normalized.startsWith('/downloads/') || normalized.startsWith('/uploads/')) {
      return normalized;
    }
    const lower = normalized.toLowerCase();
    const downloadsIndex = lower.lastIndexOf('/downloads/');
    if (downloadsIndex >= 0) {
      return `/downloads/${encodeURI(normalized.slice(downloadsIndex + '/downloads/'.length))}`;
    }
    const uploadsIndex = lower.lastIndexOf('/uploads/');
    if (uploadsIndex >= 0) {
      return `/uploads/${encodeURI(normalized.slice(uploadsIndex + '/uploads/'.length))}`;
    }
    if (lower.startsWith('downloads/')) {
      return `/downloads/${encodeURI(normalized.slice('downloads/'.length))}`;
    }
    if (lower.startsWith('uploads/')) {
      return `/uploads/${encodeURI(normalized.slice('uploads/'.length))}`;
    }
    return `/downloads/${encodeURI(normalized)}`;
  }

  function createResource({
    id,
    key,
    name,
    kind,
    src,
    poster = '',
    localObjectUrl = '',
    assetPath = '',
  }) {
    return {
      id: id || `asset-${++resourceCounter}`,
      key: key || `${kind}:${name}:${src}`,
      name: name || 'Untitled asset',
      kind: kind || 'video',
      src: src || '',
      poster: poster || '',
      localObjectUrl: localObjectUrl || '',
      assetPath: assetPath || '',
    };
  }

  function createClip({
    assetId = '',
    name,
    kind = 'video',
    startSec,
    durationSec = DEFAULT_CLIP_DURATION_SEC,
    trimStartSec = 0,
    trimEndSec = durationSec,
  }) {
    return {
      id: `clip-${++clipCounter}`,
      assetId,
      name: name || 'Untitled clip',
      kind,
      startSec: roundTimeline(startSec),
      durationSec: roundTimeline(durationSec),
      trimStartSec: roundTimeline(trimStartSec),
      trimEndSec: roundTimeline(trimEndSec),
    };
  }

  function normalizeJobList(result) {
    return Array.isArray(result) ? result : (result?.jobs || []);
  }

  function findTrack(trackId) {
    return state.tracks.find((track) => track.id === trackId) || null;
  }

  function findResource(resourceId) {
    return state.resources.find((resource) => resource.id === resourceId) || null;
  }

  function findClipContext(clipId) {
    if (!clipId) return null;
    for (const track of state.tracks) {
      const index = track.clips.findIndex((clip) => clip.id === clipId);
      if (index >= 0) {
        return {
          track,
          index,
          clip: track.clips[index],
        };
      }
    }
    return null;
  }

  function selectedClipContext() {
    return findClipContext(state.selectedClipId);
  }

  function editingClipContext() {
    return findClipContext(state.editingClipId);
  }

  function sortTrackClips(track) {
    track.clips.sort((left, right) => {
      if (left.startSec !== right.startSec) return left.startSec - right.startSec;
      return left.id.localeCompare(right.id);
    });
  }

  function normalizeSelection() {
    if (!findTrack(state.selectedTrackId)) {
      state.selectedTrackId = state.tracks[0]?.id || '';
    }
    if (state.selectedClipId && !findClipContext(state.selectedClipId)) {
      state.selectedClipId = '';
    }
    if (state.editingClipId && !findClipContext(state.editingClipId)) {
      state.editingClipId = '';
    }
  }

  function activePreviewClip() {
    const videoTrack = findTrack('track-2');
    if (!videoTrack || !videoTrack.visible) return null;
    return videoTrack.clips.find((clip) => {
      if (clip.kind !== 'video') return false;
      return state.playheadSec >= clip.startSec && state.playheadSec < (clip.startSec + clip.durationSec);
    }) || null;
  }

  function collectSeedResources(jobs) {
    const nextResources = [];
    const existingKeys = new Set(state.resources.map((resource) => resource.key));

    jobs.forEach((job, jobIndex) => {
      const outputFiles = Array.isArray(job?.output_files) ? job.output_files : [];
      outputFiles.forEach((file, fileIndex) => {
        const kind = kindFromValue(file);
        if (!kind) return;
        const normalized = normalizePath(file);
        const key = `seed:${normalized.toLowerCase()}`;
        if (existingKeys.has(key)) return;
        existingKeys.add(key);
        nextResources.push(createResource({
          key,
          name: basename(file),
          kind,
          src: mediaUrl(file),
          assetPath: normalized,
          id: `seed-${jobIndex + 1}-${fileIndex + 1}-${++resourceCounter}`,
        }));
      });
    });

    return nextResources;
  }

  async function ensureSeedResources() {
    if (state.seedLoaded) return;
    if (seedPromise) {
      await seedPromise;
      return;
    }

    seedPromise = (async () => {
      try {
        const result = await API.fetch('/api/jobs?status=completed&limit=20');
        const jobs = normalizeJobList(result);
        const seedResources = collectSeedResources(jobs);
        if (seedResources.length) {
          state.resources = [...state.resources, ...seedResources];
        }
        state.seedError = '';
      } catch (err) {
        state.seedError = err.message || 'Failed to load completed jobs.';
      } finally {
        state.seedLoaded = true;
      }
    })();

    try {
      await seedPromise;
    } finally {
      seedPromise = null;
    }
  }

  function serverAssetIdFromResource(resource) {
    const candidate = normalizePath(resource?.assetPath || resource?.src || '');
    if (!candidate || /^blob:/i.test(candidate) || /^https?:/i.test(candidate)) return '';

    const lower = candidate.toLowerCase();
    const downloadsIndex = lower.lastIndexOf('/downloads/');
    if (downloadsIndex >= 0) {
      return `downloads/${candidate.slice(downloadsIndex + '/downloads/'.length)}`;
    }

    const uploadsIndex = lower.lastIndexOf('/uploads/');
    if (uploadsIndex >= 0) {
      return `uploads/${candidate.slice(uploadsIndex + '/uploads/'.length)}`;
    }

    if (lower.startsWith('downloads/') || lower.startsWith('uploads/')) return candidate;
    return candidate.startsWith('/') ? candidate.slice(1) : candidate;
  }

  function timelineClipCount() {
    return state.tracks.reduce((total, track) => total + track.clips.length, 0);
  }

  function buildRenderPayload() {
    if (state.tracks.length > RENDER_MAX_TRACKS) {
      throw new Error(`Render supports max ${RENDER_MAX_TRACKS} tracks.`);
    }

    const clipCount = timelineClipCount();
    if (clipCount <= 0) {
      throw new Error('Add at least one timeline clip before rendering.');
    }
    if (clipCount > RENDER_MAX_CLIPS) {
      throw new Error(`Render supports max ${RENDER_MAX_CLIPS} clips total.`);
    }
    if (state.totalDurationSec > RENDER_MAX_DURATION_SEC) {
      throw new Error(`Render supports max ${RENDER_MAX_DURATION_SEC}s timelines.`);
    }

    const tracks = [];
    state.tracks.forEach((track) => {
      const clips = [];
      track.clips.forEach((clip) => {
        if (clip.kind === 'text') {
          throw new Error('Text clips are not renderable yet. Remove text clips before rendering.');
        }

        const resource = findResource(clip.assetId);
        const assetId = serverAssetIdFromResource(resource);
        if (!resource || !assetId) {
          throw new Error(`${clip.name || 'Clip'} is not a server asset. Use completed outputs or uploaded assets.`);
        }

        clips.push({
          asset_id: assetId,
          start_sec: roundTimeline(clip.startSec),
          duration_sec: roundTimeline(clip.durationSec),
          trim_in: roundTimeline(clip.trimStartSec || 0),
        });
      });

      if (clips.length) {
        tracks.push({
          kind: track.kind === 'audio' ? 'audio' : 'video',
          clips,
        });
      }
    });

    return {
      ratio: state.aspectRatio,
      tracks,
      total_duration_sec: roundTimeline(state.totalDurationSec),
    };
  }

  function clearRenderPolling() {
    if (renderPollTimerId) {
      window.clearTimeout(renderPollTimerId);
      renderPollTimerId = 0;
    }
  }

  function invalidateRenderPolling() {
    renderGeneration += 1;
    clearRenderPolling();
  }

  function isRenderGenerationActive(renderId, generation) {
    return generation === renderGeneration && state.render.isPolling && state.render.renderId === renderId;
  }

  function renderErrorMessage(err) {
    if (err?.status === 422) return 'Timeline exceeds render bounds or has invalid clips.';
    if (err?.status === 429) return 'Render queue is full. Wait for an active render to finish.';
    return err?.message || 'Render request failed.';
  }

  function setRenderState(nextState) {
    state.render = {
      ...state.render,
      ...nextState,
    };
    rerender({ preserveScroll: true });
  }

  async function pollRenderStatus(renderId, generation = renderGeneration) {
    clearRenderPolling();
    if (!isRenderGenerationActive(renderId, generation)) return;

    try {
      const result = await API.fetch(`/api/render/${encodeURIComponent(renderId)}`);
      if (!isRenderGenerationActive(renderId, generation)) return;
      const progress = clamp(Number(result?.progress || 0), 0, 100);
      state.render = {
        ...state.render,
        status: result?.status || state.render.status,
        progress,
        outputUrl: result?.output_url || '',
        error: result?.error || '',
      };

      if (result?.status === 'completed') {
        state.render.isPolling = false;
        rerender({ preserveScroll: true });
        App.toast('Render complete', 'success');
        return;
      }

      if (result?.status === 'failed') {
        state.render.isPolling = false;
        rerender({ preserveScroll: true });
        App.toast(result?.error || 'Render failed', 'error');
        return;
      }

      rerender({ preserveScroll: true });
      renderPollTimerId = window.setTimeout(
        () => pollRenderStatus(renderId, generation),
        RENDER_POLL_INTERVAL_MS
      );
    } catch (err) {
      if (generation !== renderGeneration) return;
      setRenderState({ isPolling: false, status: 'failed', error: renderErrorMessage(err) });
      App.toast(renderErrorMessage(err), 'error');
    }
  }

  async function startRender() {
    if (state.render.isPolling) return;

    let payload;
    try {
      payload = buildRenderPayload();
    } catch (err) {
      App.toast(err.message || 'Timeline is not renderable.', 'error');
      return;
    }

    const generation = renderGeneration + 1;
    renderGeneration = generation;
    setRenderState({ renderId: '', status: 'queued', progress: 0, outputUrl: '', error: '', isPolling: true });

    try {
      const result = await API.fetch('/api/render/timeline', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      if (generation !== renderGeneration) return;
      const renderId = String(result?.render_id || '').trim();
      if (!renderId) throw new Error('Render response missing render_id.');
      state.render = {
        ...state.render,
        renderId,
        status: result?.status || 'queued',
        progress: 0,
        isPolling: true,
      };
      rerender({ preserveScroll: true });
      App.toast('Rendering started', 'info');
      renderPollTimerId = window.setTimeout(
        () => pollRenderStatus(renderId, generation),
        RENDER_POLL_INTERVAL_MS
      );
    } catch (err) {
      if (generation !== renderGeneration) return;
      clearRenderPolling();
      setRenderState({ isPolling: false, status: 'failed', progress: 0, error: renderErrorMessage(err) });
      App.toast(renderErrorMessage(err), 'error');
    }
  }

  function cancelRenderPolling() {
    invalidateRenderPolling();
    state.render.isPolling = false;
    state.render.status = state.render.status || 'queued';
    rerender({ preserveScroll: true });
    App.toast('Stopped render polling. Server render continues.', 'info');
  }

  function insertResourceClips(trackId, resourceId, startSec) {
    const track = findTrack(trackId);
    const resource = findResource(resourceId);
    if (!track || !resource) return;

    const clampedStart = clamp(
      roundTimeline(startSec),
      0,
      Math.max(0, state.totalDurationSec - DEFAULT_CLIP_DURATION_SEC)
    );

    track.clips.push(createClip({
      assetId: resource.id,
      name: resource.name,
      kind: resource.kind,
      startSec: clampedStart,
    }));
    sortTrackClips(track);
    state.selectedTrackId = trackId;
    state.playheadSec = clampedStart;
  }

  function addTextClip() {
    const track = findTrack('track-1');
    if (!track) return;

    const startSec = clamp(
      roundTimeline(state.playheadSec),
      0,
      Math.max(0, state.totalDurationSec - 4)
    );

    const clip = createClip({
      assetId: '',
      name: 'Text Overlay',
      kind: 'text',
      startSec,
      durationSec: 4,
      trimEndSec: 4,
    });

    track.clips.push(clip);
    sortTrackClips(track);
    state.selectedTrackId = track.id;
    state.selectedClipId = clip.id;
    state.editingClipId = clip.id;
    App.toast('Text clip added', 'success');
  }

  function removeSelectedClip() {
    const context = selectedClipContext();
    if (!context) {
      App.toast('Select a clip first.', 'warning');
      return;
    }

    context.track.clips.splice(context.index, 1);
    if (state.selectedClipId === context.clip.id) state.selectedClipId = '';
    if (state.editingClipId === context.clip.id) state.editingClipId = '';
    App.toast('Clip removed', 'success');
  }

  function removeResource(resourceId) {
    const resource = findResource(resourceId);
    if (!resource) return;

    state.tracks.forEach((track) => {
      track.clips = track.clips.filter((clip) => clip.assetId !== resourceId);
    });

    state.resources = state.resources.filter((item) => item.id !== resourceId);
    if (resource.localObjectUrl) {
      URL.revokeObjectURL(resource.localObjectUrl);
    }

    normalizeSelection();
    App.toast('Resource removed', 'success');
  }

  function cutSelectedClip() {
    const context = selectedClipContext();
    if (!context) {
      App.toast('Select a clip first.', 'warning');
      return;
    }

    const { clip, track, index } = context;
    const cutOffset = roundTimeline(state.playheadSec - clip.startSec);
    if (cutOffset <= MIN_CLIP_DURATION_SEC || cutOffset >= (clip.durationSec - MIN_CLIP_DURATION_SEC)) {
      App.toast('Move the playhead inside the clip before cutting.', 'warning');
      return;
    }

    const leftDuration = roundTimeline(cutOffset);
    const rightDuration = roundTimeline(clip.durationSec - cutOffset);
    const leftTrimStart = roundTimeline(clip.trimStartSec || 0);
    const leftTrimEnd = roundTimeline(Math.min(clip.trimEndSec || clip.durationSec, leftTrimStart + leftDuration));
    const rightTrimStart = roundTimeline((clip.trimStartSec || 0) + leftDuration);
    const rightTrimEnd = roundTimeline(Math.min(clip.trimEndSec || clip.durationSec, rightTrimStart + rightDuration));

    const leftClip = {
      ...clip,
      durationSec: leftDuration,
      trimStartSec: leftTrimStart,
      trimEndSec: Math.max(leftTrimStart, leftTrimEnd),
    };

    const rightClip = {
      ...clip,
      id: `clip-${++clipCounter}`,
      startSec: roundTimeline(clip.startSec + leftDuration),
      durationSec: rightDuration,
      trimStartSec: rightTrimStart,
      trimEndSec: Math.max(rightTrimStart, rightTrimEnd),
    };

    track.clips.splice(index, 1, leftClip, rightClip);
    sortTrackClips(track);
    state.selectedClipId = rightClip.id;
    state.editingClipId = rightClip.id;
    App.toast('Clip cut at playhead', 'success');
  }

  function clipMarkers() {
    const values = new Set([0, state.totalDurationSec]);
    state.tracks.forEach((track) => {
      track.clips.forEach((clip) => {
        values.add(roundTimeline(clip.startSec));
        values.add(roundTimeline(clip.startSec + clip.durationSec));
      });
    });
    return Array.from(values).sort((left, right) => left - right);
  }

  function jumpToPreviousMarker() {
    const markers = clipMarkers();
    const previous = [...markers].reverse().find((marker) => marker < (state.playheadSec - 0.01));
    state.playheadSec = previous == null ? 0 : previous;
  }

  function jumpToNextMarker() {
    const markers = clipMarkers();
    const next = markers.find((marker) => marker > (state.playheadSec + 0.01));
    state.playheadSec = next == null ? state.totalDurationSec : next;
  }

  function setPlayhead(seconds) {
    state.playheadSec = clamp(roundTimeline(seconds), 0, state.totalDurationSec);
  }

  function timeFromPointer(event, laneElement) {
    const rect = laneElement.getBoundingClientRect();
    return clamp((event.clientX - rect.left) / pixelsPerSecond(), 0, state.totalDurationSec);
  }

  function stopPlayback({ rerenderView = true } = {}) {
    state.isPlaying = false;
    lastPlaybackTs = 0;
    if (playbackFrameId) {
      cancelAnimationFrame(playbackFrameId);
      playbackFrameId = 0;
    }
    if (rerenderView && rootEl) {
      rerender({ preserveScroll: true });
    } else {
      syncPreviewVideo();
    }
  }

  function startPlayback() {
    if (state.playheadSec >= state.totalDurationSec) {
      state.playheadSec = 0;
    }
    state.isPlaying = true;
    lastPlaybackTs = 0;
    rerender({ preserveScroll: true, scrollToPlayhead: true });
    playbackFrameId = requestAnimationFrame(tickPlayback);
  }

  function tickPlayback(timestamp) {
    if (!state.isPlaying) return;

    if (!lastPlaybackTs) {
      lastPlaybackTs = timestamp;
    }

    const delta = (timestamp - lastPlaybackTs) / 1000;
    lastPlaybackTs = timestamp;
    state.playheadSec = clamp(state.playheadSec + delta, 0, state.totalDurationSec);
    updateLiveUi();

    if (state.playheadSec >= state.totalDurationSec) {
      stopPlayback({ rerenderView: true });
      return;
    }

    playbackFrameId = requestAnimationFrame(tickPlayback);
  }

  function togglePlayback() {
    if (state.isPlaying) {
      stopPlayback({ rerenderView: true });
      return;
    }
    startPlayback();
  }

  function maybeScrollPlayheadIntoView() {
    const scroller = rootEl?.querySelector('[data-tl-scroll]');
    if (!scroller) return;

    const playheadLeft = playheadLeftPx();
    const minVisible = scroller.scrollLeft + TRACK_LABEL_WIDTH + 24;
    const maxVisible = scroller.scrollLeft + scroller.clientWidth - 28;

    if (playheadLeft < minVisible) {
      scroller.scrollLeft = Math.max(0, playheadLeft - TRACK_LABEL_WIDTH - 24);
    } else if (playheadLeft > maxVisible) {
      scroller.scrollLeft = Math.max(0, playheadLeft - scroller.clientWidth + 28);
    }
  }

  function syncPreviewVideo() {
    if (!rootEl) return;
    const previewClip = activePreviewClip();
    const video = rootEl.querySelector('#tl-preview-video');

    if (!previewClip || !video) return;

    const desiredTime = clamp(
      (previewClip.trimStartSec || 0) + (state.playheadSec - previewClip.startSec),
      0,
      Math.max(previewClip.trimEndSec || previewClip.durationSec, previewClip.durationSec)
    );

    const seek = () => {
      if (Number.isFinite(desiredTime) && Math.abs((video.currentTime || 0) - desiredTime) > 0.2) {
        try {
          video.currentTime = desiredTime;
        } catch (err) {
          console.debug('[MediaTools] preview seek skipped:', err.message);
        }
      }
      if (state.isPlaying) {
        video.play().catch(() => {});
      } else {
        video.pause();
      }
    };

    if (video.readyState >= 1) {
      seek();
    } else {
      video.addEventListener('loadedmetadata', seek, { once: true });
    }
  }

  function updateLiveUi() {
    if (!rootEl) return;

    const previewClip = activePreviewClip();
    const previewClipId = previewClip?.id || '';
    if (previewClipId !== renderedPreviewClipId) {
      rerender({ preserveScroll: true, scrollToPlayhead: true });
      return;
    }

    rootEl.querySelectorAll('.tl-playhead').forEach((playhead) => {
      playhead.style.left = `${playheadLeftPx()}px`;
    });

    const timecodeEl = rootEl.querySelector('.tl-timecode');
    if (timecodeEl) {
      timecodeEl.textContent = `${formatTimecode(state.playheadSec)} / ${formatTimecode(state.totalDurationSec)}`;
    }

    maybeScrollPlayheadIntoView();
    syncPreviewVideo();
  }

  function renderResourceThumb(resource) {
    if (resource.kind === 'image') {
      return `<img class="tl-resource-thumb" src="${escapeAttr(resource.src)}" alt="${escapeAttr(resource.name)}" loading="lazy">`;
    }
    if (resource.kind === 'video') {
      return `
        <video
          class="tl-resource-thumb"
          src="${escapeAttr(resource.src)}"
          poster="${escapeAttr(resource.poster || '')}"
          muted
          playsinline
          preload="metadata"
          data-resource-preview-video="1"
        ></video>
      `;
    }
    return `
      <div class="tl-resource-thumb tl-resource-thumb--audio" aria-hidden="true">
        <span class="material-icons">music_note</span>
      </div>
    `;
  }

  function renderResourceCard(resource) {
    return `
      <div class="tl-resource-card" draggable="true" data-asset-id="${escapeAttr(resource.id)}" title="${escapeAttr(resource.name)}">
        ${renderResourceThumb(resource)}
        <div class="tl-resource-meta">
          <div class="tl-resource-name">${App.escapeHtml(resource.name)}</div>
          <div class="tl-resource-actions">
            <span class="tl-resource-kind">${App.escapeHtml(formatKindLabel(resource.kind))}</span>
            <button type="button" class="tl-resource-action" data-preview-resource="${escapeAttr(resource.id)}" aria-label="${escapeAttr(`Preview ${resource.name}`)}">
              <span class="material-icons">play_arrow</span>
            </button>
            <button type="button" class="tl-resource-action" data-delete-resource="${escapeAttr(resource.id)}" aria-label="${escapeAttr(`Remove ${resource.name}`)}">
              <span class="material-icons">delete_outline</span>
            </button>
          </div>
        </div>
      </div>
    `;
  }

  function renderPreviewPanel() {
    const previewClip = activePreviewClip();
    const aspectValue = state.aspectRatio === '16:9'
      ? '16 / 9'
      : state.aspectRatio === '1:1'
        ? '1 / 1'
        : '9 / 16';

    if (!previewClip) {
      return `
        <section class="tl-preview-panel">
          <div class="tl-panel-heading">PREVIEW</div>
          <div class="tl-preview-stage" style="aspect-ratio:${aspectValue};">
            <div class="tl-preview-empty">No preview clip at this playhead</div>
          </div>
        </section>
      `;
    }

    const resource = findResource(previewClip.assetId);
    if (!resource) {
      return `
        <section class="tl-preview-panel">
          <div class="tl-panel-heading">PREVIEW</div>
          <div class="tl-preview-stage" style="aspect-ratio:${aspectValue};">
            <div class="tl-preview-empty">No preview clip at this playhead</div>
          </div>
        </section>
      `;
    }

    return `
      <section class="tl-preview-panel">
        <div class="tl-panel-heading">PREVIEW</div>
        <div class="tl-preview-stage" style="aspect-ratio:${aspectValue};">
          <video
            id="tl-preview-video"
            src="${escapeAttr(resource.src)}"
            poster="${escapeAttr(resource.poster || '')}"
            muted
            playsinline
            preload="auto"
          ></video>
        </div>
        <div class="tl-preview-caption">
          ${App.escapeHtml(resource.name)} &middot; Track 2
        </div>
      </section>
    `;
  }

  function renderPropertiesPanel() {
    const context = editingClipContext();

    if (!context) {
      return `
        <aside class="tl-properties-panel">
          <div class="tl-panel-heading">ITEM PROPERTIES</div>
          <div class="tl-properties-empty">Double-click an item to edit</div>
        </aside>
      `;
    }

    const { track, clip } = context;
    const endSec = roundTimeline(clip.startSec + clip.durationSec);
    const trimIn = roundTimeline(clip.trimStartSec || 0);
    const trimOut = roundTimeline(clip.trimEndSec || clip.durationSec);

    return `
      <aside class="tl-properties-panel">
        <div class="tl-panel-heading">ITEM PROPERTIES</div>
        <div class="tl-properties-stack">
          <label class="tl-field">
            <span>Name</span>
            <input
              class="tl-input"
              type="text"
              value="${escapeAttr(clip.name)}"
              data-clip-field="name"
            >
          </label>
          <div class="tl-field-grid">
            <label class="tl-field">
              <span>Start</span>
              <input
                class="tl-input"
                type="number"
                min="0"
                max="${escapeAttr(state.totalDurationSec)}"
                step="0.1"
                value="${escapeAttr(clip.startSec)}"
                data-clip-field="startSec"
              >
            </label>
            <label class="tl-field">
              <span>End</span>
              <input
                class="tl-input"
                type="number"
                min="0"
                max="${escapeAttr(state.totalDurationSec)}"
                step="0.1"
                value="${escapeAttr(endSec)}"
                data-clip-field="endSec"
              >
            </label>
          </div>
          <div class="tl-field-grid">
            <label class="tl-field">
              <span>Trim In</span>
              <input
                class="tl-input"
                type="number"
                min="0"
                max="${escapeAttr(clip.durationSec)}"
                step="0.1"
                value="${escapeAttr(trimIn)}"
                data-clip-field="trimStartSec"
              >
            </label>
            <label class="tl-field">
              <span>Trim Out</span>
              <input
                class="tl-input"
                type="number"
                min="0"
                max="${escapeAttr(clip.durationSec)}"
                step="0.1"
                value="${escapeAttr(trimOut)}"
                data-clip-field="trimEndSec"
              >
            </label>
          </div>
          <div class="tl-properties-hint">
            ${App.escapeHtml(track.label)} &middot; ${App.escapeHtml(formatKindLabel(clip.kind))} &middot; ${App.escapeHtml(formatShortSeconds(clip.durationSec))}
          </div>
        </div>
      </aside>
    `;
  }

  function clipClassName(clip) {
    if (clip.kind === 'audio') return 'tl-clip--audio';
    if (clip.kind === 'text') return 'tl-clip--text';
    return 'tl-clip--video';
  }

  function renderClip(track, clip) {
    const left = Math.round(clip.startSec * pixelsPerSecond());
    const width = Math.max(68, Math.round(clip.durationSec * pixelsPerSecond()));
    const selectedClass = state.selectedClipId === clip.id ? ' tl-clip--selected' : '';
    return `
      <button
        type="button"
        class="tl-clip ${clipClassName(clip)}${selectedClass}"
        data-clip-id="${escapeAttr(clip.id)}"
        data-track-id="${escapeAttr(track.id)}"
        style="left:${left}px; width:${width}px;"
        title="${escapeAttr(clip.name)}"
      >
        <span class="tl-clip-badge">${App.escapeHtml(formatKindLabel(clip.kind))}</span>
        <span class="tl-clip-title">${App.escapeHtml(App.truncate(clip.name, 26))}</span>
      </button>
    `;
  }

  function renderTrackRow(track) {
    const selectedClass = state.selectedTrackId === track.id ? ' tl-track-row--selected' : '';
    const visibilityIcon = track.visible ? 'visibility' : 'visibility_off';
    return `
      <div class="tl-track-row${selectedClass}" data-track-row="${escapeAttr(track.id)}" style="width:${TRACK_LABEL_WIDTH + timelineWidthPx()}px;">
        <div class="tl-track-label">
          <button type="button" class="tl-track-visibility" data-toggle-track="${escapeAttr(track.id)}" aria-label="${escapeAttr(`Toggle ${track.label} visibility`)}">
            <span class="material-icons">${visibilityIcon}</span>
          </button>
          <div class="tl-track-label-copy">
            <strong>${App.escapeHtml(track.label)}</strong>
            <span>${App.escapeHtml(formatKindLabel(track.kind))}</span>
          </div>
        </div>
        <div class="tl-track-lane${track.visible ? '' : ' tl-track-lane--muted'}" data-track-id="${escapeAttr(track.id)}" style="width:${timelineWidthPx()}px;">
          ${track.clips.length
            ? track.clips.map((clip) => renderClip(track, clip)).join('')
            : '<div class="tl-track-placeholder">Drop assets here</div>'}
        </div>
      </div>
    `;
  }

  function renderRuler() {
    const pps = pixelsPerSecond();
    const majorMarks = [];
    const minorMarks = [];

    for (let second = 0; second <= state.totalDurationSec; second += 1) {
      const left = Math.round(second * pps);
      if (second % 5 === 0) {
        majorMarks.push(`
          <button
            type="button"
            class="tl-ruler-mark tl-ruler-mark--major"
            data-ruler-sec="${second}"
            style="left:${left}px;"
          >
            <span>${second}s</span>
          </button>
        `);
      } else {
        minorMarks.push(`<div class="tl-ruler-mark tl-ruler-mark--minor" style="left:${left}px;"></div>`);
      }
    }

    return `
      <div class="tl-ruler" data-ruler style="width:${TRACK_LABEL_WIDTH + timelineWidthPx()}px;">
        <div class="tl-ruler-spacer" style="width:${TRACK_LABEL_WIDTH}px;"></div>
        <div class="tl-ruler-scale" data-ruler-scale style="width:${timelineWidthPx()}px;">
          ${minorMarks.join('')}
          ${majorMarks.join('')}
        </div>
      </div>
    `;
  }

  function renderStatusBar() {
    const render = state.render;
    if (!render.renderId && !render.isPolling && !render.error) return '';

    const progress = clamp(Number(render.progress || 0), 0, 100);
    const statusLabel = render.isPolling
      ? `Rendering… ${progress}%`
      : render.status === 'completed'
        ? 'Render complete'
        : render.status === 'failed'
          ? 'Render failed'
          : `Render ${render.status || 'idle'}`;

    return `
      <div class="tl-render-status" style="display:flex; align-items:center; gap:10px; min-width:260px; color:#f7f2e8; font-size:13px;">
        <span>${App.escapeHtml(statusLabel)}</span>
        <progress value="${escapeAttr(progress)}" max="100" style="width:96px; accent-color:var(--tl-accent);"></progress>
        ${render.isPolling ? '<button type="button" class="tl-back" data-render-cancel style="min-width:76px; min-height:34px; padding:0 10px;">Cancel</button>' : ''}
        ${render.outputUrl ? `<a class="tl-back" href="${escapeAttr(render.outputUrl)}" download style="display:inline-flex; align-items:center; justify-content:center; min-width:92px; min-height:34px; padding:0 10px; text-decoration:none;">Download</a>` : ''}
        ${render.error ? `<span style="color:#ffb4a8; max-width:240px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${escapeAttr(render.error)}">${App.escapeHtml(render.error)}</span>` : ''}
      </div>
    `;
  }

  function renderPageContent() {
    normalizeSelection();
    const renderDisabled = state.render.isPolling ? 'disabled aria-disabled="true"' : '';
    const renderLabel = state.render.isPolling ? `Rendering… ${clamp(Number(state.render.progress || 0), 0, 100)}%` : 'Render';

    return `
      <div class="tl-topbar">
        <button type="button" class="tl-back" data-tl-back>&larr; Back</button>
        <div class="tl-topbar-actions">
          ${renderStatusBar()}
          <select class="tl-ratio" data-ratio-select>
            ${ASPECT_OPTIONS.map((option) => `
              <option value="${escapeAttr(option)}" ${state.aspectRatio === option ? 'selected' : ''}>${App.escapeHtml(option)}</option>
            `).join('')}
          </select>
          <button type="button" class="tl-render-btn" data-render-start ${renderDisabled}>${App.escapeHtml(renderLabel)}</button>
        </div>
      </div>

      <div class="tl-workspace">
        <aside class="tl-resources-panel">
          <div class="tl-panel-heading">RESOURCES</div>
          <button type="button" class="tl-resource-add" data-add-resource>+ Add image/video/audio</button>
          ${state.seedError ? `<div class="tl-resources-note">${App.escapeHtml(state.seedError)}</div>` : ''}
          <div class="tl-resources-list">
            ${state.resources.length
              ? state.resources.map(renderResourceCard).join('')
              : '<div class="tl-resources-empty">Completed job outputs and local uploads will appear here.</div>'}
          </div>
        </aside>

        ${renderPreviewPanel()}
        ${renderPropertiesPanel()}
      </div>

      <section class="tl-timeline">
        <div class="tl-toolbar">
          <label class="tl-zoom-group">
            <span>Zoom</span>
            <input
              class="tl-zoom-slider"
              type="range"
              min="0.6"
              max="2"
              step="0.1"
              value="${escapeAttr(state.zoom)}"
              data-zoom-slider
            >
          </label>
          <button type="button" class="tl-tool-btn" data-tool-action="prev" aria-label="Previous marker">
            <span class="material-icons">skip_previous</span>
          </button>
          <button type="button" class="tl-tool-btn" data-tool-action="next" aria-label="Next marker">
            <span class="material-icons">skip_next</span>
          </button>
          <button type="button" class="tl-tool-btn tl-tool-btn--outline" data-tool-action="text">+ Text</button>
          <button type="button" class="tl-tool-btn" data-tool-action="cut" aria-label="Cut clip">
            <span class="material-icons">content_cut</span>
          </button>
          <button type="button" class="tl-tool-btn" data-tool-action="delete" aria-label="Delete clip">
            <span class="material-icons">delete_outline</span>
          </button>
          <button type="button" class="tl-tool-btn" data-tool-action="fastforward" aria-label="Fast forward">
            <span class="material-icons">fast_forward</span>
          </button>
          <button type="button" class="tl-tool-btn tl-tool-btn--play" data-tool-action="play" aria-label="Play timeline">
            <span class="material-icons">${state.isPlaying ? 'pause' : 'play_arrow'}</span>
          </button>
          <div class="tl-timecode">${formatTimecode(state.playheadSec)} / ${formatTimecode(state.totalDurationSec)}</div>
        </div>

        <div class="tl-stage-viewport" data-tl-scroll>
          <div class="tl-stage-content" style="width:${TRACK_LABEL_WIDTH + timelineWidthPx()}px;">
            ${renderRuler()}
            <div class="tl-tracks">
              ${state.tracks.map(renderTrackRow).join('')}
            </div>
            <div class="tl-playhead" style="left:${playheadLeftPx()}px;"></div>
          </div>
        </div>
      </section>

      <input
        id="tl-file-input"
        type="file"
        accept="image/*,video/*,audio/*"
        multiple
        hidden
      >
    `;
  }

  function rerender({ preserveScroll = true, scrollToPlayhead = false } = {}) {
    if (!rootEl) return;

    const previousScroller = preserveScroll ? rootEl.querySelector('[data-tl-scroll]') : null;
    const scrollLeft = previousScroller ? previousScroller.scrollLeft : 0;
    rootEl.innerHTML = renderPageContent();
    renderedPreviewClipId = activePreviewClip()?.id || '';

    if (preserveScroll) {
      const nextScroller = rootEl.querySelector('[data-tl-scroll]');
      if (nextScroller) {
        nextScroller.scrollLeft = Math.min(
          scrollLeft,
          Math.max(0, nextScroller.scrollWidth - nextScroller.clientWidth)
        );
      }
    }

    if (scrollToPlayhead) {
      maybeScrollPlayheadIntoView();
    }

    syncPreviewVideo();
  }

  function previewResource(resourceId) {
    const resource = findResource(resourceId);
    if (!resource) return;

    if (resource.kind === 'image') {
      App.openModal(resource.name, `
        <div class="tl-modal-preview">
          <img src="${escapeAttr(resource.src)}" alt="${escapeAttr(resource.name)}" style="width:100%; border-radius:12px; display:block;">
        </div>
      `);
      return;
    }

    if (resource.kind === 'audio') {
      App.openModal(resource.name, `
        <div class="tl-modal-preview">
          <audio src="${escapeAttr(resource.src)}" controls autoplay style="width:100%;"></audio>
        </div>
      `);
      return;
    }

    App.openModal(resource.name, `
      <div class="tl-modal-preview">
        <video src="${escapeAttr(resource.src)}" controls autoplay playsinline style="width:100%; border-radius:12px; background:#000;"></video>
      </div>
    `);
  }

  async function handleFileInputChange(input) {
    const files = Array.from(input.files || []);
    if (!files.length) return;

    const addedResources = [];
    const existingKeys = new Set(state.resources.map((resource) => resource.key));

    files.forEach((file) => {
      const kind = kindFromValue(file.type) || kindFromValue(file.name);
      if (!kind) return;
      const key = `upload:${file.name}:${file.size}:${file.lastModified}:${kind}`;
      if (existingKeys.has(key)) return;
      existingKeys.add(key);
      const url = URL.createObjectURL(file);
      addedResources.push(createResource({
        key,
        name: file.name,
        kind,
        src: url,
        localObjectUrl: url,
      }));
    });

    if (addedResources.length) {
      state.resources = [...addedResources, ...state.resources];
      App.toast(`${addedResources.length} resource${addedResources.length === 1 ? '' : 's'} added`, 'success');
    }

    input.value = '';
    rerender({ preserveScroll: true });
  }

  function updateEditingClipField(field, rawValue) {
    const context = editingClipContext();
    if (!context) return;

    const { clip, track } = context;

    if (field === 'name') {
      const nextName = String(rawValue || '').trim();
      clip.name = nextName || clip.name;
      return;
    }

    const numeric = Number(rawValue);
    if (!Number.isFinite(numeric)) return;

    if (field === 'startSec') {
      clip.startSec = clamp(
        roundTimeline(numeric),
        0,
        Math.max(0, state.totalDurationSec - clip.durationSec)
      );
      sortTrackClips(track);
      return;
    }

    if (field === 'endSec') {
      const nextEnd = clamp(
        roundTimeline(numeric),
        clip.startSec + MIN_CLIP_DURATION_SEC,
        state.totalDurationSec
      );
      clip.durationSec = roundTimeline(nextEnd - clip.startSec);
      clip.trimStartSec = clamp(clip.trimStartSec || 0, 0, clip.durationSec);
      clip.trimEndSec = clamp(clip.trimEndSec || clip.durationSec, clip.trimStartSec, clip.durationSec);
      return;
    }

    if (field === 'trimStartSec') {
      clip.trimStartSec = clamp(roundTimeline(numeric), 0, clip.durationSec);
      clip.trimEndSec = clamp(clip.trimEndSec || clip.durationSec, clip.trimStartSec, clip.durationSec);
      return;
    }

    if (field === 'trimEndSec') {
      clip.trimEndSec = clamp(roundTimeline(numeric), clip.trimStartSec || 0, clip.durationSec);
    }
  }

  function clearDropHighlight() {
    if (activeDropLane) {
      activeDropLane.classList.remove('tl-track-lane--dropping');
      activeDropLane = null;
    }
  }

  function handleToolbarAction(action) {
    if (!action) return;

    if (action === 'play') {
      togglePlayback();
      return;
    }

    stopPlayback({ rerenderView: false });

    if (action === 'prev') {
      jumpToPreviousMarker();
      rerender({ preserveScroll: true, scrollToPlayhead: true });
      return;
    }

    if (action === 'next') {
      jumpToNextMarker();
      rerender({ preserveScroll: true, scrollToPlayhead: true });
      return;
    }

    if (action === 'text') {
      addTextClip();
      rerender({ preserveScroll: true, scrollToPlayhead: true });
      return;
    }

    if (action === 'cut') {
      cutSelectedClip();
      rerender({ preserveScroll: true, scrollToPlayhead: true });
      return;
    }

    if (action === 'delete') {
      removeSelectedClip();
      rerender({ preserveScroll: true, scrollToPlayhead: true });
      return;
    }

    if (action === 'fastforward') {
      setPlayhead(state.playheadSec + 5);
      rerender({ preserveScroll: true, scrollToPlayhead: true });
    }
  }

  function onClick(event) {
    const addResourceButton = event.target.closest('[data-add-resource]');
    if (addResourceButton) {
      rootEl.querySelector('#tl-file-input')?.click();
      return;
    }

    const previewButton = event.target.closest('[data-preview-resource]');
    if (previewButton) {
      previewResource(previewButton.dataset.previewResource);
      return;
    }

    const deleteResourceButton = event.target.closest('[data-delete-resource]');
    if (deleteResourceButton) {
      removeResource(deleteResourceButton.dataset.deleteResource);
      rerender({ preserveScroll: true });
      return;
    }

    if (event.target.closest('[data-tl-back]')) {
      stopPlayback({ rerenderView: false });
      if (window.history.length > 1) {
        window.history.back();
      } else {
        window.location.hash = '#workflows';
      }
      return;
    }

    if (event.target.closest('[data-render-start]')) {
      startRender();
      return;
    }

    if (event.target.closest('[data-render-cancel]')) {
      cancelRenderPolling();
      return;
    }

    const toolButton = event.target.closest('[data-tool-action]');
    if (toolButton) {
      handleToolbarAction(toolButton.dataset.toolAction);
      return;
    }

    const toggleTrackButton = event.target.closest('[data-toggle-track]');
    if (toggleTrackButton) {
      const track = findTrack(toggleTrackButton.dataset.toggleTrack);
      if (!track) return;
      track.visible = !track.visible;
      rerender({ preserveScroll: true });
      return;
    }

    const rulerMark = event.target.closest('[data-ruler-sec]');
    if (rulerMark) {
      stopPlayback({ rerenderView: false });
      setPlayhead(Number(rulerMark.dataset.rulerSec || 0));
      rerender({ preserveScroll: true, scrollToPlayhead: true });
      return;
    }

    const clipButton = event.target.closest('[data-clip-id]');
    if (clipButton) {
      state.selectedTrackId = clipButton.dataset.trackId || state.selectedTrackId;
      state.selectedClipId = clipButton.dataset.clipId || '';
      rerender({ preserveScroll: true });
      return;
    }

    const lane = event.target.closest('.tl-track-lane');
    if (lane) {
      stopPlayback({ rerenderView: false });
      state.selectedTrackId = lane.dataset.trackId || state.selectedTrackId;
      state.selectedClipId = '';
      state.editingClipId = '';
      setPlayhead(timeFromPointer(event, lane));
      rerender({ preserveScroll: true, scrollToPlayhead: true });
      return;
    }

    const trackRow = event.target.closest('[data-track-row]');
    if (trackRow) {
      state.selectedTrackId = trackRow.dataset.trackRow || state.selectedTrackId;
      state.selectedClipId = '';
      state.editingClipId = '';
      rerender({ preserveScroll: true });
      return;
    }

    const rulerScale = event.target.closest('[data-ruler-scale]');
    if (rulerScale) {
      stopPlayback({ rerenderView: false });
      setPlayhead(timeFromPointer(event, rulerScale));
      rerender({ preserveScroll: true, scrollToPlayhead: true });
    }
  }

  function onDoubleClick(event) {
    const clipButton = event.target.closest('[data-clip-id]');
    if (!clipButton) return;
    state.selectedTrackId = clipButton.dataset.trackId || state.selectedTrackId;
    state.selectedClipId = clipButton.dataset.clipId || '';
    state.editingClipId = clipButton.dataset.clipId || '';
    rerender({ preserveScroll: true });
  }

  function onChange(event) {
    const ratioSelect = event.target.closest('[data-ratio-select]');
    if (ratioSelect) {
      state.aspectRatio = ratioSelect.value;
      rerender({ preserveScroll: true });
      return;
    }

    const zoomSlider = event.target.closest('[data-zoom-slider]');
    if (zoomSlider) {
      state.zoom = clamp(Number(zoomSlider.value || 1), 0.6, 2);
      rerender({ preserveScroll: true, scrollToPlayhead: true });
      return;
    }

    if (event.target.id === 'tl-file-input') {
      handleFileInputChange(event.target);
      return;
    }

    const clipField = event.target.closest('[data-clip-field]');
    if (clipField) {
      updateEditingClipField(clipField.dataset.clipField, clipField.value);
      rerender({ preserveScroll: true });
    }
  }

  function onDragStart(event) {
    const resourceCard = event.target.closest('[data-asset-id]');
    if (!resourceCard || !event.dataTransfer) return;
    const assetId = resourceCard.dataset.assetId || '';
    event.dataTransfer.effectAllowed = 'copy';
    event.dataTransfer.setData('text/plain', assetId);
    event.dataTransfer.setData('application/x-flowengine-asset', assetId);
  }

  function onDragOver(event) {
    const lane = event.target.closest('.tl-track-lane');
    if (!lane) return;
    event.preventDefault();
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = 'copy';
    }
    if (activeDropLane !== lane) {
      clearDropHighlight();
      activeDropLane = lane;
      activeDropLane.classList.add('tl-track-lane--dropping');
    }
  }

  function onDragLeave(event) {
    const lane = event.target.closest('.tl-track-lane');
    if (!lane || !activeDropLane) return;
    const related = event.relatedTarget;
    if (related && lane.contains(related)) return;
    if (lane === activeDropLane) {
      clearDropHighlight();
    }
  }

  function onDrop(event) {
    const lane = event.target.closest('.tl-track-lane');
    if (!lane) return;

    event.preventDefault();
    clearDropHighlight();
    stopPlayback({ rerenderView: false });

    const assetId = event.dataTransfer?.getData('application/x-flowengine-asset')
      || event.dataTransfer?.getData('text/plain');
    if (!assetId) return;

    state.selectedClipId = '';
    state.editingClipId = '';
    insertResourceClips(lane.dataset.trackId, assetId, timeFromPointer(event, lane));
    rerender({ preserveScroll: true, scrollToPlayhead: true });
  }

  function onDragEnd() {
    clearDropHighlight();
  }

  function onMediaLoadedData(event) {
    const video = event.target instanceof Element
      ? event.target.closest('video[data-resource-preview-video]')
      : null;
    if (!video) return;

    try {
      video.currentTime = 0.1;
    } catch {
    }
  }

  function bindEvents() {
    if (!rootEl) return;
    rootEl.addEventListener('click', onClick);
    rootEl.addEventListener('dblclick', onDoubleClick);
    rootEl.addEventListener('change', onChange);
    rootEl.addEventListener('dragstart', onDragStart);
    rootEl.addEventListener('dragover', onDragOver);
    rootEl.addEventListener('dragleave', onDragLeave);
    rootEl.addEventListener('drop', onDrop);
    rootEl.addEventListener('dragend', onDragEnd);
    rootEl.addEventListener('loadeddata', onMediaLoadedData, true);
  }

  function unbindEvents() {
    if (!rootEl) return;
    rootEl.removeEventListener('click', onClick);
    rootEl.removeEventListener('dblclick', onDoubleClick);
    rootEl.removeEventListener('change', onChange);
    rootEl.removeEventListener('dragstart', onDragStart);
    rootEl.removeEventListener('dragover', onDragOver);
    rootEl.removeEventListener('dragleave', onDragLeave);
    rootEl.removeEventListener('drop', onDrop);
    rootEl.removeEventListener('dragend', onDragEnd);
    rootEl.removeEventListener('loadeddata', onMediaLoadedData, true);
  }

  const MediaToolsPage = {
    name: 'media-tools',
    title: 'Media Tools',
    icon: 'build',
    async render() {
      await ensureSeedResources();
      renderedPreviewClipId = activePreviewClip()?.id || '';
      return `<div id="media-tools-page" class="tl-canvas">${renderPageContent()}</div>`;
    },
    mount() {
      rootEl = document.getElementById('media-tools-page');
      bindEvents();
      maybeScrollPlayheadIntoView();
      syncPreviewVideo();
    },
    destroy() {
      stopPlayback({ rerenderView: false });
      invalidateRenderPolling();
      state.render.isPolling = false;
      clearDropHighlight();
      unbindEvents();
      rootEl = null;
    },
  };

  App.register(MediaToolsPage);
})();
