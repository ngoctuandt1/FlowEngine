/**
 * FlowEngine Form Configuration
 * Shared constants + validation rules for the Create Job page and the
 * Chain Builder. Keeps the two surfaces in sync (prior drift: MODELS
 * dropdown had 5 options on Create and 4 on Chain Builder).
 */
(() => {
  const MODELS = [
    { value: '', label: 'Default' },
    { value: 'kling-v2.1', label: 'Kling v2.1' },
    { value: 'kling-v2.0', label: 'Kling v2.0' },
    { value: 'kling-v1.6', label: 'Kling v1.6' },
    { value: 'kling-v1.5', label: 'Kling v1.5' },
  ];

  const ASPECT_RATIOS = [
    { value: '', label: 'Default' },
    { value: '16:9', label: '16:9 (Landscape)' },
    { value: '9:16', label: '9:16 (Portrait)' },
    { value: '1:1', label: '1:1 (Square)' },
  ];

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

  // Type → list of fields that must be non-empty on the submitted payload.
  // Used by both Create Job validate() and Chain Builder validateChain().
  const REQUIRED_FIELDS = {
    'text-to-video': ['prompt'],
    'extend': [],
    'insert': ['prompt'],
    'remove': [],
    'camera': ['camera_direction'],
  };

  const FIELD_LABELS = {
    prompt: 'Prompt',
    camera_direction: 'Camera direction',
    parent_job_id: 'Parent Job ID',
    project_url: 'Project URL',
  };

  const TYPE_LABELS = {
    'text-to-video': 'Text-to-Video',
    'extend': 'Extend',
    'insert': 'Insert',
    'remove': 'Remove',
    'camera': 'Camera',
  };

  /**
   * Return the human label of the first missing required field for `type`,
   * or null if every required field is populated.
   */
  function missingRequiredLabel(type, data) {
    const fields = REQUIRED_FIELDS[type];
    if (!fields) return null;
    for (const f of fields) {
      const v = data ? data[f] : undefined;
      if (v === undefined || v === null || (typeof v === 'string' && !v.trim())) {
        return FIELD_LABELS[f] || f;
      }
    }
    return null;
  }

  window.FlowConfig = {
    MODELS,
    ASPECT_RATIOS,
    CAMERA_PRESETS,
    REQUIRED_FIELDS,
    FIELD_LABELS,
    TYPE_LABELS,
    missingRequiredLabel,
  };
})();
