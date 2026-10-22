/* MITOM browser Sky Star / Moon Phase. No CEP, Node, credentials, or remote API.
 * Catalog: the same D3-Celestial assets bundled in the user's panel.
 * Moon ephemeris: pinned SunCalc 2.1.1 (Meeus); NASA LROC surface map.
 * See assets/sky-moon/SOURCES.md for attribution and model limitations. */
import {getMoonIllumination, getMoonPosition} from './assets/sky-moon/suncalc-2.1.1.js';
import OfflineGeocoder from './assets/sky-moon/skymoon-offline-geocoder.js';

const ASSETS = new URL('./assets/sky-moon/', import.meta.url);
const RAD = Math.PI / 180, DAY = 86400000;
const PHASE_NAMES = ['New Moon', 'Waxing Crescent', 'First Quarter', 'Waxing Gibbous', 'Full Moon', 'Waning Gibbous', 'Last Quarter', 'Waning Crescent'];
let catalogPromise, placePromise, texturePromise;
const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
const wrap = (x) => ((x % 360) + 360) % 360;

function finite(value, lo, hi, label) {
  if (value === '' || value === null || value === undefined || !Number.isFinite(Number(value)) || Number(value) < lo || Number(value) > hi) throw new Error(`${label} phải từ ${lo} đến ${hi}.`);
  return Number(value);
}
function integer(value, lo, hi, label) {
  const x = finite(value, lo, hi, label);
  if (!Number.isInteger(x)) throw new Error(`${label} phải là số nguyên.`);
  return x;
}
function color(value, fallback) {
  value = value ?? fallback;
  if (typeof value !== 'string' || !/^#[a-f\d]{6}$/i.test(value)) throw new Error('Màu phải theo định dạng #RRGGBB.');
  return value;
}

/** Strict civil date plus explicit HOURS east of UTC. No machine timezone/DST.
 * Supports 1900..2100, including real Gregorian leap days. */
export function parseInstant(date, time = '12:00', utcOffset = 0) {
  if (typeof date !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(date)) throw new Error('Ngày phải theo YYYY-MM-DD; không tự đảo ngày/tháng.');
  if (typeof time !== 'string' || !/^\d{2}:\d{2}(?::\d{2})?$/.test(time)) throw new Error('Giờ phải theo HH:mm hoặc HH:mm:ss.');
  const [y, m, d] = date.split('-').map(Number), [h, min, sec = 0] = time.split(':').map(Number);
  finite(y, 1900, 2100, 'Năm'); finite(m, 1, 12, 'Tháng'); finite(d, 1, 31, 'Ngày');
  finite(h, 0, 23, 'Giờ'); finite(min, 0, 59, 'Phút'); finite(sec, 0, 59, 'Giây');
  const wall = new Date(Date.UTC(y, m - 1, d, h, min, sec));
  if (wall.getUTCFullYear() !== y || wall.getUTCMonth() !== m - 1 || wall.getUTCDate() !== d) throw new Error('Ngày không tồn tại trong lịch Gregorian.');
  const offset = finite(utcOffset, -14, 14, 'UTC offset (giờ)');
  if (!Number.isInteger(offset * 60)) throw new Error('UTC offset phải là số phút nguyên (ví dụ 5.5 hoặc 5.75 giờ).');
  return new Date(wall.getTime() - offset * 3600000);
}

export function meanSiderealDegreesUtc(instant) {
  const jd = instant.getTime() / DAY + 2440587.5, t = (jd - 2451545) / 36525;
  return wrap(280.46061837 + 360.98564736629 * (jd - 2451545) + 0.000387933 * t * t - t * t * t / 38710000);
}

/** IAU-1976 precession from the catalog's J2000 equatorial coordinates. */
export function precessJ2000(raDegrees, decDegrees, instant) {
  const t = (instant.getTime() / DAY + 2440587.5 - 2451545) / 36525;
  const zeta = (2306.2181 * t + .30188 * t * t + .017998 * t * t * t) / 3600 * RAD;
  const z = (2306.2181 * t + 1.09468 * t * t + .018203 * t * t * t) / 3600 * RAD;
  const theta = (2004.3109 * t - .42665 * t * t - .041833 * t * t * t) / 3600 * RAD;
  const ra = raDegrees * RAD, dec = decDegrees * RAD;
  const a = Math.cos(dec) * Math.sin(ra + zeta);
  const b = Math.cos(theta) * Math.cos(dec) * Math.cos(ra + zeta) - Math.sin(theta) * Math.sin(dec);
  const c = Math.sin(theta) * Math.cos(dec) * Math.cos(ra + zeta) + Math.cos(theta) * Math.sin(dec);
  return [wrap((Math.atan2(a, b) + z) / RAD), Math.asin(clamp(c, -1, 1)) / RAD];
}

/** North-up sky chart as seen looking UP: east is left, west is right.
 * Orthographic upper-hemisphere projection. z is sin(geometric altitude). */
export function projectEquatorial(ra, dec, lat, lst) {
  const h = (lst - ra) * RAD, d = dec * RAD, p = lat * RAD;
  return {x: Math.cos(d) * Math.sin(h), y: Math.cos(p) * Math.sin(d) - Math.sin(p) * Math.cos(d) * Math.cos(h),
    z: Math.sin(p) * Math.sin(d) + Math.cos(p) * Math.cos(d) * Math.cos(h)};
}

async function boundedAsset(name, maxBytes = 2 * 1024 * 1024) {
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 45000);
  try {
  const response = await fetch(new URL(name, ASSETS), {credentials: 'omit', cache: 'force-cache', signal: controller.signal});
  if (!response.ok) throw new Error(`Không tải được dữ liệu ${name} (${response.status}).`);
  const declared = Number(response.headers.get('content-length') || 0);
  if (declared > maxBytes) throw new Error(`Dữ liệu ${name} vượt giới hạn.`);
  // Bound streamed bytes as well as Content-Length (some hosts omit it).
  if (response.body?.getReader) {
    const reader = response.body.getReader(), chunks = []; let size = 0;
    while (true) {
      const next = await reader.read(); if (next.done) break;
      size += next.value.byteLength;
      if (size > maxBytes) { await reader.cancel(); throw new Error(`Dữ liệu ${name} vượt giới hạn.`); }
      chunks.push(next.value);
    }
    const bytes = new Uint8Array(size); let at = 0;
    chunks.forEach(chunk => { bytes.set(chunk, at); at += chunk.byteLength; }); return bytes;
  }
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.byteLength > maxBytes) throw new Error(`Dữ liệu ${name} vượt giới hạn.`);
  return bytes;
  } finally { clearTimeout(timer); }
}
async function catalogs() {
  if (!catalogPromise) catalogPromise = Promise.all(['stars.6.json', 'constellations.lines.json'].map(async name => {
    const value = JSON.parse(new TextDecoder().decode(await boundedAsset(name)));
    if (value.type !== 'FeatureCollection' || !Array.isArray(value.features) || !value.features.length) throw new Error(`Catalog ${name} không hợp lệ.`);
    return value;
  })).then(([stars, lines]) => ({stars, lines})).catch(error => { catalogPromise = null; throw error; });
  return catalogPromise;
}

function context(canvas, width, height) {
  if (!canvas || typeof canvas.getContext !== 'function') throw new Error('Thiếu canvas làm việc.');
  if (width * height > 16777216) throw new Error('Canvas vượt 16 megapixel. Giảm size hoặc số cột.');
  canvas.width = width; canvas.height = height;
  const ctx = canvas.getContext('2d'); if (!ctx) throw new Error('Trình duyệt không hỗ trợ Canvas 2D.');
  ctx.clearRect(0, 0, width, height); return ctx;
}
function lineBetween(ctx, a, b, project, cx, cy, radius) {
  const ar = a[0] * RAD, ad = a[1] * RAD, br = b[0] * RAD, bd = b[1] * RAD;
  const av = [Math.cos(ad) * Math.cos(ar), Math.cos(ad) * Math.sin(ar), Math.sin(ad)];
  const bv = [Math.cos(bd) * Math.cos(br), Math.cos(bd) * Math.sin(br), Math.sin(bd)];
  const angle = Math.acos(clamp(av.reduce((sum, n, i) => sum + n * bv[i], 0), -1, 1));
  const steps = Math.max(2, Math.ceil(angle / (.5 * RAD))), denominator = Math.sin(angle);
  let down = false;
  for (let i = 0; i <= steps; i++) {
    const t = i / steps, wa = angle < 1e-8 ? 1 - t : Math.sin((1 - t) * angle) / denominator, wb = angle < 1e-8 ? t : Math.sin(t * angle) / denominator;
    const v = av.map((n, j) => wa * n + wb * bv[j]);
    const p = project(Math.atan2(v[1], v[0]) / RAD, Math.atan2(v[2], Math.hypot(v[0], v[1])) / RAD);
    if (p.z >= 0) { const x = cx + p.x * radius, y = cy - p.y * radius; if (down) ctx.lineTo(x, y); else ctx.moveTo(x, y); down = true; }
    else down = false;
  }
}

export async function renderSky(canvas, options = {}) {
  const date = parseInstant(options.date, options.time ?? '12:00', options.utcOffset ?? 0);
  const lat = finite(options.lat, -90, 90, 'Latitude'), lon = finite(options.lon, -180, 180, 'Longitude');
  const size = integer(options.size ?? 2400, 256, 4096, 'Kích thước');
  const bg = color(options.background, '#080d18'), star = color(options.starColor, '#ffffff'), line = color(options.lineColor, '#6686a8');
  const magnitude = finite(options.magnitude ?? 6, 0, 6, 'Độ sáng giới hạn');
  const data = await catalogs(), ctx = context(canvas, size, size), center = size / 2, radius = size * .455;
  if (!options.transparent) { ctx.fillStyle = bg; ctx.fillRect(0, 0, size, size); }
  const lst = wrap(meanSiderealDegreesUtc(date) + lon);
  const project = (ra, dec) => { const p = precessJ2000(ra, dec, date); return projectEquatorial(p[0], p[1], lat, lst); };
  ctx.save(); ctx.beginPath(); ctx.arc(center, center, radius, 0, Math.PI * 2); ctx.clip();
  if (options.grid) {
    ctx.strokeStyle = line; ctx.globalAlpha = .3; ctx.lineWidth = size / 2400;
    [0, 30, 60].forEach(alt => { ctx.beginPath(); ctx.arc(center, center, radius * Math.cos(alt * RAD), 0, Math.PI * 2); ctx.stroke(); });
    for (let a = 0; a < 360; a += 30) { ctx.beginPath(); ctx.moveTo(center, center); ctx.lineTo(center + radius * Math.sin(a * RAD), center - radius * Math.cos(a * RAD)); ctx.stroke(); }
    ctx.globalAlpha = 1;
  }
  if (options.constellations !== false) {
    ctx.beginPath(); ctx.strokeStyle = line; ctx.lineWidth = Math.max(.5, size / 1500); ctx.globalAlpha = .7;
    data.lines.features.forEach(feature => { const groups = feature.geometry.type === 'MultiLineString' ? feature.geometry.coordinates : [feature.geometry.coordinates];
      groups.forEach(group => { for (let i = 1; i < group.length; i++) lineBetween(ctx, group[i - 1], group[i], project, center, center, radius); }); });
    ctx.stroke(); ctx.globalAlpha = 1;
  }
  let count = 0; ctx.fillStyle = star;
  data.stars.features.forEach(feature => {
    const mag = Number(feature.properties.mag); if (!Number.isFinite(mag) || mag > magnitude) return;
    const [ra, dec] = feature.geometry.coordinates, p = project(ra, dec); if (p.z < 0) return;
    const r = clamp(3.4 * Math.pow(10, -.18 * (mag + 1.4)), .35, 4.5) * size / 1600;
    ctx.beginPath(); ctx.arc(center + p.x * radius, center - p.y * radius, r, 0, Math.PI * 2); ctx.fill(); count++;
  });
  ctx.restore(); ctx.strokeStyle = line; ctx.lineWidth = Math.max(1, size / 1200); ctx.beginPath(); ctx.arc(center, center, radius, 0, Math.PI * 2); ctx.stroke();
  if (options.labels !== false) {
    ctx.fillStyle = star; ctx.font = `${Math.round(size * .017)}px sans-serif`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    [['N', center, center - radius - size * .02], ['S', center, center + radius + size * .02], ['E', center - radius - size * .02, center], ['W', center + radius + size * .02, center]].forEach(v => ctx.fillText(...v));
  }
  return {kind: 'sky', utc: date.toISOString(), lat, lon, utcOffset: Number(options.utcOffset ?? 0), width: size, height: size, starsRendered: count, projection: 'orthographic-upper-hemisphere', catalog: 'D3-Celestial stars.6 (J2000)', localSiderealDegrees: lst,
    model: 'J2000 catalog + IAU-1976 precession + GMST; geometric horizon, no refraction/proper-motion', note: 'Web projection differs from panel stereographic rendering; not a random decorative star field.'};
}

export function moonMetadata(date, time = '12:00', utcOffset = 0, lat = 51.4809181, lon = -.0207105) {
  const instant = parseInstant(date, time, utcOffset), latitude = finite(lat, -90, 90, 'Latitude'), longitude = finite(lon, -180, 180, 'Longitude');
  const illumination = getMoonIllumination(instant), position = getMoonPosition(instant, latitude, longitude);
  return {date, utc: instant.toISOString(), illumination: illumination.fraction * 100, fraction: illumination.fraction, phase: illumination.phase,
    phaseName: PHASE_NAMES[Math.round(illumination.phase * 8) % 8], waxing: illumination.waxing, brightLimbAngle: illumination.angle,
    parallacticAngle: position.parallacticAngle, distanceKm: position.distance, altitude: position.altitude, azimuth: position.azimuth, model: 'SunCalc 2.1.1 / Meeus'};
}

function texture() {
  if (!texturePromise) texturePromise = new Promise((resolve, reject) => {
    const image = new Image(); image.onload = () => {
      try { const source = document.createElement('canvas'); source.width = image.naturalWidth; source.height = image.naturalHeight;
        const ctx = source.getContext('2d', {willReadFrequently: true}); ctx.drawImage(image, 0, 0);
        resolve({width: source.width, height: source.height, pixels: ctx.getImageData(0, 0, source.width, source.height).data});
      } catch (error) { reject(error); }
    };
    image.onerror = () => reject(new Error('Không tải được texture Mặt Trăng NASA đã bundle.'));
    image.src = new URL('nasa-lroc-color-2k.jpg', ASSETS).href;
  }).catch(error => { texturePromise = null; throw error; });
  return texturePromise;
}

/** North-up near-side texture plus physically calculated illuminated hemisphere.
 * Appearance is illustrative: no libration, lunar-axis rotation, eclipse or relief. */
export function shadeMoonPixels(size, meta, surface) {
  integer(size, 1, 1200, 'Size mỗi moon');
  finite(meta?.fraction, 0, 1, 'Tỷ lệ chiếu sáng'); finite(meta?.brightLimbAngle, -360, 360, 'Góc chiếu sáng');
  if (!surface || !Number.isInteger(surface.width) || !Number.isInteger(surface.height) || surface.width < 1 || surface.height < 1 || surface.width * surface.height > 8388608 || !surface.pixels || surface.pixels.length !== surface.width * surface.height * 4) throw new Error('Texture Moon không hợp lệ.');
  const bytes = new Uint8ClampedArray(size * size * 4), lz = clamp(2 * meta.fraction - 1, -1, 1);
  const planar = Math.sqrt(Math.max(0, 1 - lz * lz)), angle = meta.brightLimbAngle * RAD;
  const lx = -Math.sin(angle) * planar, ly = Math.cos(angle) * planar;
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    const nx = (x + .5 - size / 2) / (size / 2), ny = (size / 2 - y - .5) / (size / 2), rr = nx * nx + ny * ny;
    if (rr >= 1) continue;
    const nz = Math.sqrt(1 - rr), dot = nx * lx + ny * ly + nz * lz;
    const longitude = Math.atan2(nx, nz), latitude = Math.asin(ny);
    const tx = Math.min(surface.width - 1, Math.max(0, Math.floor((.5 + longitude / (2 * Math.PI)) * surface.width)));
    const ty = Math.min(surface.height - 1, Math.max(0, Math.floor((.5 - latitude / Math.PI) * surface.height)));
    const sample = (ty * surface.width + tx) * 4, at = (y * size + x) * 4;
    const light = dot > 0 ? .08 + .92 * Math.pow(dot, .32) : .018;
    bytes[at] = surface.pixels[sample] * light; bytes[at + 1] = surface.pixels[sample + 1] * light; bytes[at + 2] = surface.pixels[sample + 2] * light;
    bytes[at + 3] = Math.round(255 * clamp((1 - Math.sqrt(rr)) * size / 2, 0, 1));
  }
  return bytes;
}

export async function renderMoons(canvas, options = {}) {
  let dates = Array.isArray(options.dates) ? options.dates : String(options.dates ?? options.date ?? '').split(/[\n\r,;]+/).map(s => s.trim()).filter(Boolean);
  if (!dates.length || dates.length > 60) throw new Error('Nhập từ 1 đến 60 ngày YYYY-MM-DD, mỗi ngày một dòng.');
  const columns = integer(options.columns ?? 3, 1, 12, 'Số cột'), size = integer(options.size ?? 400, 100, 1200, 'Size mỗi moon');
  const time = options.time ?? '12:00', offset = options.utcOffset ?? 0, lat = options.lat ?? 51.4809181, lon = options.lon ?? -.0207105;
  const items = dates.map(date => moonMetadata(date, time, offset, lat, lon));
  const cols = Math.min(columns, items.length), rows = Math.ceil(items.length / cols), pad = Math.round(size * .11), labelH = options.showLabels === false ? 0 : Math.round(size * .19);
  const w = cols * (size + pad) + pad, h = rows * (size + pad + labelH) + pad;
  if (w * h > 16777216 || w > 8192 || h > 8192) throw new Error('PNG ghép quá lớn. Giảm size hoặc số ngày (tối đa 16 megapixel).');
  const bg = color(options.background, '#080d18'), label = color(options.labelColor, '#d8deec');
  const surface = await texture(), ctx = context(canvas, w, h);
  if (!options.transparent) { ctx.fillStyle = bg; ctx.fillRect(0, 0, w, h); }
  const tile = document.createElement('canvas'); tile.width = size; tile.height = size;
  const tctx = tile.getContext('2d');
  for (let i = 0; i < items.length; i++) {
    const x = pad + i % cols * (size + pad), y = pad + Math.floor(i / cols) * (size + pad + labelH);
    const image = tctx.createImageData(size, size); image.data.set(shadeMoonPixels(size, items[i], surface)); tctx.putImageData(image, 0, 0); ctx.drawImage(tile, x, y);
    if (labelH) { ctx.fillStyle = label; ctx.textAlign = 'center'; ctx.textBaseline = 'top'; ctx.font = `${Math.max(12, Math.round(size * .048))}px sans-serif`;
      ctx.fillText(items[i].date, x + size / 2, y + size + size * .025); ctx.font = `${Math.max(10, Math.round(size * .037))}px sans-serif`;
      ctx.fillText(`${items[i].illumination.toFixed(1)}% · ${items[i].phaseName}`, x + size / 2, y + size + size * .095); }
    await new Promise(resolve => setTimeout(resolve, 0));
  }
  return {kind: 'moon', items, width: w, height: h, model: 'SunCalc 2.1.1 / Meeus', texture: "NASA's Scientific Visualization Studio / LROC",
    note: 'Illumination/phase calculated for exact UTC. North-up illustrative texture: no libration, lunar-axis rotation or eclipses. Different engine from legacy panel API.'};
}

async function places() {
  if (!placePromise) placePromise = (async () => {
    if (typeof DecompressionStream !== 'function') throw new Error('Trình duyệt chưa hỗ trợ dữ liệu thành phố offline. Nhập trực tiếp latitude, longitude.');
    const bytes = await boundedAsset('cities500.json.gz', 24 * 1024 * 1024);
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
    const reader = stream.getReader(), chunks = []; let count = 0;
    while (true) { const next = await reader.read(); if (next.done) break; count += next.value.byteLength;
      if (count > 64 * 1024 * 1024) { await reader.cancel(); throw new Error('Dữ liệu địa điểm giải nén vượt giới hạn.'); } chunks.push(next.value); }
    const text = await new Blob(chunks).text(); return OfflineGeocoder.createLookup(JSON.parse(text));
  })().catch(error => { placePromise = null; throw error; });
  return placePromise;
}
export async function searchPlace(query) {
  if (typeof query !== 'string' || !query.trim() || query.length > 200) throw new Error('Nhập tên thành phố và quốc gia, hoặc latitude, longitude.');
  const coordinate = OfflineGeocoder.parseCoordinates(query);
  if (coordinate?.error) throw new Error(coordinate.error);
  if (coordinate) return {status: 'matched', result: coordinate};
  return (await places())(query);
}

export function getCapabilities() {
  return {sky: true, moon: true, placeSearch: 'bundled-GeoNames-exact-match', localOnly: true, browserOnly: true, remoteApiRequired: false,
    dateRange: [1900, 2100], utcOffsetUnit: 'hours-east-of-UTC', moonEngine: 'SunCalc 2.1.1', moonOrientation: 'north-up-illustrative-no-libration',
    skyProjection: 'orthographic-upper-hemisphere', catalog: 'same bundled panel D3-Celestial stars.6', pixelLimit: 16777216};
}
export const getcapabilities = getCapabilities;
