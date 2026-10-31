export const THEMES = Object.freeze(['studio', 'christmas', 'floral', 'memorial', 'wedding', 'nursery', 'coastal', 'rustic', 'sports']);
const forbidden = new Set(['__proto__', 'prototype', 'constructor']);

function object(value, allowed, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${label} must be an object`);
  for (const key of Object.keys(value)) {
    if (forbidden.has(key) || !allowed.includes(key)) throw new Error(`Unknown ${label} field: ${key}`);
  }
  return value;
}
function number(value, fallback, min, max, label, integer = false) {
  const result = value === undefined ? fallback : value;
  if (typeof result !== 'number' || !Number.isFinite(result) || result < min || result > max || (integer && !Number.isInteger(result))) throw new Error(`Invalid ${label}`);
  return result;
}
function choice(value, fallback, allowed, label) {
  const result = value === undefined ? fallback : value;
  if (!allowed.includes(result)) throw new Error(`Invalid ${label}`);
  return result;
}
function asset(value, required, label) {
  if (value === undefined && !required) return undefined;
  if (typeof value !== 'string' || !value || value.length > 4096 || /[\u0000-\u001f]/.test(value)) throw new Error(`Invalid ${label}`);
  // Input scenes refer only to local image paths. The adapter replaces these
  // with validated data images before they are passed to Chromium.
  if (/^[a-z][a-z0-9+.-]*:/i.test(value) && !/^[a-z]:[\\/]/i.test(value)) throw new Error(`${label} must be a local image`);
  if (/^(?:\\\\|\/\/)/.test(value)) throw new Error(`${label} cannot use a network share`);
  return value;
}

export function normalizeScene(value) {
  const v = object(value, ['schemaVersion', 'aspectRatio', 'durationSeconds', 'fps', 'resolution', 'seed', 'product', 'background', 'motion', 'lighting'], 'scene');
  if (v.schemaVersion !== 1) throw new Error('Unsupported scene schema');
  const product = object(v.product, ['src', 'scale', 'anchor', 'material'], 'product');
  const background = object(v.background ?? {}, ['src', 'videoSrc', 'theme', 'palette'], 'background');
  const motion = object(v.motion ?? {}, ['orbitDegrees', 'swayDegrees', 'floatPixels', 'zoom', 'cycles', 'direction'], 'motion');
  const lighting = object(v.lighting ?? {}, ['warmth', 'shadow', 'glint'], 'lighting');
  let palette;
  if (background.palette !== undefined) {
    if (!Array.isArray(background.palette) || background.palette.length !== 3 || !background.palette.every(c => typeof c === 'string' && /^#[a-f0-9]{6}$/i.test(c))) throw new Error('Invalid background palette');
    palette = [...background.palette];
  }
  const bgSrc = asset(background.src, false, 'background.src');
  const videoSrc = asset(background.videoSrc, false, 'background.videoSrc');
  return {
    schemaVersion: 1,
    aspectRatio: choice(v.aspectRatio, '16:9', ['16:9', '9:16'], 'aspectRatio'),
    durationSeconds: number(v.durationSeconds, 8, 5, 15, 'durationSeconds'),
    fps: choice(v.fps, 30, [24, 30], 'fps'),
    resolution: choice(v.resolution, 1080, [720, 1080], 'resolution'),
    seed: number(v.seed, 42, 0, 2147483647, 'seed', true),
    product: {
      src: asset(product.src, true, 'product.src'),
      scale: number(product.scale, 0.76, 0.3, 0.9, 'product.scale'),
      anchor: choice(product.anchor, 'hanging', ['hanging', 'standing'], 'product.anchor'),
      material: choice(product.material, 'acrylic', ['acrylic', 'ceramic', 'wood', 'paper'], 'product.material'),
    },
    background: {...(bgSrc ? {src: bgSrc} : {}), ...(videoSrc ? {videoSrc} : {}), theme: choice(background.theme, 'studio', THEMES, 'background.theme'), ...(palette ? {palette} : {})},
    motion: {
      orbitDegrees: number(motion.orbitDegrees, 38, 0, 55, 'motion.orbitDegrees'),
      swayDegrees: number(motion.swayDegrees, 3, 0, 10, 'motion.swayDegrees'),
      floatPixels: number(motion.floatPixels, 9, 0, 30, 'motion.floatPixels'),
      zoom: number(motion.zoom, 0.045, 0, 0.15, 'motion.zoom'),
      cycles: number(motion.cycles, 1, 0.5, 3, 'motion.cycles'),
      direction: choice(motion.direction, 'left', ['left', 'right'], 'motion.direction'),
    },
    lighting: {
      warmth: number(lighting.warmth, 0.18, -1, 1, 'lighting.warmth'),
      shadow: number(lighting.shadow, 0.55, 0, 1, 'lighting.shadow'),
      glint: number(lighting.glint, 0.2, 0, 1, 'lighting.glint'),
    },
  };
}

export function sceneDimensions(scene) {
  const short = scene.resolution;
  const long = short === 1080 ? 1920 : 1280;
  return scene.aspectRatio === '9:16' ? {width: short, height: long} : {width: long, height: short};
}

export function seeded(seed) {
  let x = seed >>> 0;
  return () => {
    x += 0x6D2B79F5;
    let t = Math.imul(x ^ (x >>> 15), x | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function motionAtFrame(frame, scene) {
  const frames = Math.round(scene.durationSeconds * scene.fps);
  const t = Math.max(0, Math.min(frame / frames, 1));
  const phase = t * Math.PI * 2 * scene.motion.cycles;
  const sign = scene.motion.direction === 'left' ? -1 : 1;
  const hanging = scene.product.anchor === 'hanging';
  return {
    yaw: Math.sin(phase) * scene.motion.orbitDegrees * sign,
    roll: Math.sin(phase + 0.3) * scene.motion.swayDegrees * (hanging ? 1 : 0.35),
    pitch: Math.sin(phase * 2) * Math.min(scene.motion.orbitDegrees * 0.13, 2.5),
    y: Math.sin(phase + 0.7) * scene.motion.floatPixels * (hanging ? 1 : 0.25),
    x: Math.sin(phase) * scene.motion.floatPixels * 0.6 * sign,
    scale: 1 + (0.5 - 0.5 * Math.cos(t * Math.PI * 2)) * scene.motion.zoom,
    backgroundX: -Math.sin(phase) * 8 * sign,
    highlight: (0.5 + 0.5 * Math.sin(phase - 0.6)) * scene.lighting.glint,
    phase,
  };
}
