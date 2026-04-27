/**
 * Shared frontend constants. Single source of truth for job types,
 * models, aspect ratios, and camera presets. Must load before
 * create-job.js / chain-builder.js / dashboard.js.
 *
 * Mirrors:
 *   server/models/job.py         JobType enum + JobCreate defaults
 *   flow/model_selector.py       MODEL_MAP + DEFAULT_MODEL
 *   flow/camera_move.py (presets are Flow UI labels)
 */
const CONST = (() => {
  // Keep in sync with JobType enum in server/models/job.py.
  const JOB_TYPES = [
    { id: 'text-to-video', label: 'Text to Video', icon: 'videocam', shortLabel: 'T2V' },
    { id: 'frames-to-video', label: 'Frames to Video', icon: 'image', shortLabel: 'Frames' },
    { id: 'audio-to-video', label: 'Audio to Video', icon: 'mic', shortLabel: 'A2V' },
    { id: 'ingredients-to-video', label: 'Ingredients to Video', icon: 'photo_library', shortLabel: 'Refs' },
    { id: 'text-to-image', label: 'Text to Image', icon: 'photo', shortLabel: 'T2I' },
    { id: 'extend-video', label: 'Extend', icon: 'add_to_queue', shortLabel: 'Extend' },
    { id: 'insert-object', label: 'Insert', icon: 'add_box', shortLabel: 'Insert' },
    { id: 'remove-object', label: 'Remove', icon: 'delete_sweep', shortLabel: 'Remove' },
    { id: 'camera-move', label: 'Camera', icon: 'videocam_off', shortLabel: 'Camera' },
  ];

  // Veo 3.1 family exposed by Flow's model dropdown. LP = Lower Priority
  // (free tier). Default matches flow/model_selector.py DEFAULT_MODEL.
  const MODELS = [
    { value: 'veo-3.1-fast-lp', label: 'Veo 3.1 - Fast [Lower Priority]' },
    { value: 'veo-3.1-lite-lp', label: 'Veo 3.1 - Lite [Lower Priority]' },
    { value: 'veo-3.1-lite', label: 'Veo 3.1 - Lite' },
    { value: 'veo-3.1-fast', label: 'Veo 3.1 - Fast [paid]' },
    { value: 'veo-3.1-quality', label: 'Veo 3.1 - Quality [paid]' },
  ];
  const DEFAULT_MODEL = 'veo-3.1-fast-lp';
  const IMAGE_MODELS = [
    { value: 'nano-banana-pro', label: 'Nano Banana Pro' },
    { value: 'nano-banana-2', label: 'Nano Banana 2' },
    { value: 'imagen-4', label: 'Imagen 4' },
  ];
  const DEFAULT_IMAGE_MODEL = 'nano-banana-pro';

  // Flow supports only Landscape + Portrait. No square.
  const ASPECT_RATIOS = [
    { value: '16:9', label: '16:9 (Landscape)' },
    { value: '9:16', label: '9:16 (Portrait)' },
  ];
  const DEFAULT_ASPECT = '16:9';
  const ASPECT_RATIOS_IMAGE = [
    { value: '16:9', label: '16:9 (Landscape)' },
    { value: '4:3', label: '4:3' },
    { value: '1:1', label: '1:1 (Square)' },
    { value: '3:4', label: '3:4' },
    { value: '9:16', label: '9:16 (Portrait)' },
  ];

  // Camera move presets - Flow UI labels, passed through as `direction`.
  const CAMERA_PRESETS = [
    'Orbit Left', 'Orbit Right',
    'Pan Left', 'Pan Right',
    'Pedestal Up', 'Pedestal Down',
    'Tilt Up', 'Tilt Down',
    'Zoom In', 'Zoom Out',
    'Dolly In', 'Dolly Out',
    'Crane Up',
    'Roll CW', 'Roll CCW',
  ];

  // Which types accept a prompt. `remove-object` is bbox-only,
  // `camera-move` is direction-only.
  const TYPES_WITH_PROMPT = new Set(['text-to-video', 'frames-to-video', 'ingredients-to-video', 'text-to-image', 'extend-video', 'insert-object']);

  // Types that need a bbox (normalized 0-1 per server/models/job.py BBox).
  const TYPES_WITH_BBOX = new Set(['insert-object', 'remove-object']);

  // Types where model / aspect matter at creation time. Aspect is
  // inherited from the L1 project for L2+ ops, so only t2v asks for it.
  const TYPES_WITH_MODEL = new Set(['text-to-video', 'frames-to-video', 'ingredients-to-video', 'text-to-image', 'extend-video', 'insert-object']);
  const TYPES_WITH_ASPECT = new Set(['text-to-video', 'frames-to-video', 'ingredients-to-video', 'text-to-image']);
  const TYPES_WITH_IMAGES = new Set(['frames-to-video', 'text-to-image']);
  const TYPES_WITH_INGREDIENTS = new Set(['ingredients-to-video']);

  return {
    JOB_TYPES, MODELS, DEFAULT_MODEL,
    IMAGE_MODELS, DEFAULT_IMAGE_MODEL,
    ASPECT_RATIOS, ASPECT_RATIOS_IMAGE, DEFAULT_ASPECT,
    CAMERA_PRESETS,
    TYPES_WITH_PROMPT, TYPES_WITH_BBOX,
    TYPES_WITH_MODEL, TYPES_WITH_ASPECT, TYPES_WITH_IMAGES, TYPES_WITH_INGREDIENTS,
    typeMeta(id) { return JOB_TYPES.find((t) => t.id === id); },
  };
})();
