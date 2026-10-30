/* MITOM browser Sky Star / Moon Phase. No CEP, Node, credentials, or remote API.
 * Catalog: the same D3-Celestial assets bundled in the user's panel.
 * Moon ephemeris: pinned SunCalc 2.1.1 (Meeus); NASA LROC surface map.
 * See assets/sky-moon/SOURCES.md for attribution and model limitations. */
import {getMoonIllumination, getMoonPosition} from './assets/sky-moon/suncalc-2.1.1.js';
import OfflineGeocoder from './assets/sky-moon/skymoon-offline-geocoder.js';
import {parseCivilDate, parseDateList, zonedDateTime} from './sky-moon-inputs.js?v=20261030-date-r79';
export {parseCivilDate, parseDate, parseDateList, parseMoonDates, createDateNormalizer, zonedDateTime} from './sky-moon-inputs.js?v=20261030-date-r79';

const ASSETS = new URL('./assets/sky-moon/', import.meta.url);
const RAD = Math.PI / 180, DAY = 86400000;
const PHASE_NAMES = ['New Moon', 'Waxing Crescent', 'First Quarter', 'Waxing Gibbous', 'Full Moon', 'Waning Gibbous', 'Last Quarter', 'Waning Crescent'];
let catalogPromise, placePromise, texturePromise, zonePromise;
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

/** Same upper-hemisphere stereographic projection family as the panel.
 * The zenith is the centre; the geometric horizon has radius one. */
export function projectStereographic(ra, dec, lat, lst) {
  const p=projectEquatorial(ra,dec,lat,lst), denominator=1+p.z;
  return {x:p.x/denominator,y:p.y/denominator,z:p.z};
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
  const date = options.instant ? new Date(options.instant) : parseInstant(parseCivilDate(options.date,options.dateOrder??'MDY').iso, options.time ?? '12:00', options.utcOffset ?? 0);
  if(!Number.isFinite(date.getTime())) throw Error('Thời điểm Sky Star không hợp lệ.');
  const lat = finite(options.lat, -90, 90, 'Latitude'), lon = finite(options.lon, -180, 180, 'Longitude');
  const size = integer(options.size ?? 2400, 256, 4096, 'Kích thước');
  const bg = color(options.background, '#080d18'), star = color(options.starColor, '#ffffff'), line = color(options.lineColor, '#6686a8');
  const magnitude = finite(options.magnitude ?? 6, 0, 6, 'Độ sáng giới hạn');
  const data = await catalogs(), ctx = context(canvas, size, size), center = size / 2, radius = size * .455;
  if (!options.transparent) { ctx.fillStyle = bg; ctx.fillRect(0, 0, size, size); }
  const lst = wrap(meanSiderealDegreesUtc(date) + lon);
  const projection=options.projection??'stereographic';
  if(!['stereographic','orthographic'].includes(projection)) throw Error('Phép chiếu Sky Star không hợp lệ.');
  const project = (ra, dec) => { const p = precessJ2000(ra, dec, date); return (projection==='stereographic'?projectStereographic:projectEquatorial)(p[0], p[1], lat, lst); };
  ctx.save(); ctx.beginPath(); ctx.arc(center, center, radius, 0, Math.PI * 2); ctx.clip();
  if (options.grid) {
    ctx.strokeStyle = line; ctx.globalAlpha = .3; ctx.lineWidth = size / 2400;
    [0, 30, 60].forEach(alt => { ctx.beginPath(); ctx.arc(center, center, radius * (projection==='stereographic'?Math.cos(alt*RAD)/(1+Math.sin(alt*RAD)):Math.cos(alt * RAD)), 0, Math.PI * 2); ctx.stroke(); });
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
  return {kind: 'sky', utc: date.toISOString(), lat, lon, utcOffset: Number(options.utcOffset ?? 0), width: size, height: size, starsRendered: count, projection: `${projection}-upper-hemisphere`, catalog: 'D3-Celestial stars.6 (J2000)', localSiderealDegrees: lst,
    model: 'J2000 catalog + IAU-1976 precession + GMST; geometric horizon, no refraction/proper-motion', note: 'Same panel star catalog and stereographic projection family; browser renderer is not a Photoshop action or pixel-identical D3-Celestial capture.'};
}

/** Port of the panel's local Sky effect LUT + four-neighbour unsharp mask.
 * Reapply operates on an unchanged raw canvas; it never compounds effects. */
export function applySkyAppearance(target, raw, options={}) {
  const ctx=context(target,raw.width,raw.height);ctx.drawImage(raw,0,0);
  const image=ctx.getImageData(0,0,target.width,target.height), src=image.data;
  for(let at=0;at<src.length;at+=4) {
    let gray=Math.round(.299*src[at]+.587*src[at+1]+.114*src[at+2]);
    if(options.effect!==false) {let value=clamp((gray-10)/235,0,1);value=Math.pow(value,1/1.2);gray=Math.round(clamp(((value-.5)*1.15+.5)*1.05,0,1)*255);src[at]=src[at+1]=src[at+2]=gray;}
    if(options.transparent) src[at+3]=gray;
  }
  if(options.effect!==false) {
    const copy=new Uint8ClampedArray(src), width=target.width;
    for(let y=1;y<target.height-1;y++)for(let x=1;x<width-1;x++){const at=(y*width+x)*4;for(let c=0;c<3;c++)src[at+c]=clamp(copy[at+c]*2.2-.3*(copy[at-width*4+c]+copy[at-4+c]+copy[at+4+c]+copy[at+width*4+c]),0,255);}
  }
  ctx.putImageData(image,0,0);return target;
}

export function moonMetadata(date, time = '12:00', utcOffset = 0, lat = 51.4809181, lon = -.0207105, exactInstant=null) {
  const instant = exactInstant ? new Date(exactInstant) : parseInstant(date, time, utcOffset), latitude = finite(lat, -90, 90, 'Latitude'), longitude = finite(lon, -180, 180, 'Longitude');
  if(!Number.isFinite(instant.getTime())) throw Error('Thời điểm Moon Phase không hợp lệ.');
  const illumination = getMoonIllumination(instant), position = getMoonPosition(instant, latitude, longitude);
  return {date, utc: instant.toISOString(), illumination: illumination.fraction * 100, fraction: illumination.fraction, phase: illumination.phase,
    phaseName: moonPhaseName(illumination.phase), waxing: illumination.waxing, brightLimbAngle: illumination.angle,
    parallacticAngle: position.parallacticAngle, distanceKm: position.distance, altitude: position.altitude, azimuth: position.azimuth, model: 'SunCalc 2.1.1 / Meeus'};
}

/** Do not label an entire ~3.7-day nearest-eighth band "Full Moon". The
 * principal phase names apply only close to the corresponding phase instant;
 * crescent/gibbous remain meaningful on neighbouring high-illumination dates. */
export function moonPhaseName(phase) {
  phase=finite(phase,0,1,'Pha Mặt Trăng');
  if(phase<=.01||phase>=.99)return PHASE_NAMES[0];
  if(Math.abs(phase-.25)<=.01)return PHASE_NAMES[2];
  if(Math.abs(phase-.5)<=.01)return PHASE_NAMES[4];
  if(Math.abs(phase-.75)<=.01)return PHASE_NAMES[6];
  return phase<.25?PHASE_NAMES[1]:phase<.5?PHASE_NAMES[3]:phase<.75?PHASE_NAMES[5]:PHASE_NAMES[7];
}

function checkCancelled(signal) {
  if(signal?.aborted){const error=Error('Đã hủy tạo Moon Phase; không xuất kết quả dở dang.');error.code='CANCELLED';throw error;}
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
export function shadeMoonPixels(size, meta, surface, options={}) {
  integer(size, 1, 1200, 'Size mỗi moon');
  finite(meta?.fraction, 0, 1, 'Tỷ lệ chiếu sáng'); finite(meta?.brightLimbAngle, -360, 360, 'Góc chiếu sáng');
  if (!surface || !Number.isInteger(surface.width) || !Number.isInteger(surface.height) || surface.width < 1 || surface.height < 1 || surface.width * surface.height > 8388608 || !surface.pixels || surface.pixels.length !== surface.width * surface.height * 4) throw new Error('Texture Moon không hợp lệ.');
  const bytes = new Uint8ClampedArray(size * size * 4), lz = clamp(2 * meta.fraction - 1, -1, 1);
  const orientation=options.orientation??'north', appearance=options.appearance??'natural';
  if(!['north','observer','phase','provider'].includes(orientation)||!['natural','warm','grayscale'].includes(appearance))throw Error('Kiểu hiển thị Moon không hợp lệ.');
  const degrees=orientation==='observer'?meta.brightLimbAngle-Number(meta.parallacticAngle??0):['phase','provider'].includes(orientation)?(meta.waxing?-90:90):meta.brightLimbAngle;
  const rotation=orientation==='provider'?finite(meta.providerTilt,-180,180,'Góc xoay nguồn Moon')*RAD:0,cos=Math.cos(rotation),sin=Math.sin(rotation);
  const planar = Math.sqrt(Math.max(0, 1 - lz * lz)), angle = degrees * RAD;
  const lx = -Math.sin(angle) * planar, ly = Math.cos(angle) * planar;
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    const nx = (x + .5 - size / 2) / (size / 2), ny = (size / 2 - y - .5) / (size / 2), rr = nx * nx + ny * ny;
    if (rr >= 1) continue;
    // Provider rotates the complete disk clockwise in screen coordinates.
    const sx=cos*nx-sin*ny,sy=sin*nx+cos*ny;
    const nz = Math.sqrt(1 - rr), dot = sx * lx + sy * ly + nz * lz;
    const longitude = Math.atan2(sx, nz), latitude = Math.asin(sy);
    const tx = Math.min(surface.width - 1, Math.max(0, Math.floor((.5 + longitude / (2 * Math.PI)) * surface.width)));
    const ty = Math.min(surface.height - 1, Math.max(0, Math.floor((.5 - latitude / Math.PI) * surface.height)));
    const sample = (ty * surface.width + tx) * 4, at = (y * size + x) * 4;
    const light = dot > 0 ? .08 + .92 * Math.pow(dot, .32) : .018;
    bytes[at] = surface.pixels[sample] * light; bytes[at + 1] = surface.pixels[sample + 1] * light; bytes[at + 2] = surface.pixels[sample + 2] * light;
    if(appearance!=='natural') {
      const gray=.299*bytes[at]+.587*bytes[at+1]+.114*bytes[at+2];
      bytes[at]=gray;bytes[at+1]=gray*(appearance==='warm'?.94:1);bytes[at+2]=gray*(appearance==='warm'?.69:1);
    }
    bytes[at + 3] = Math.round(255 * clamp((1 - Math.sqrt(rr)) * size / 2, 0, 1));
  }
  return bytes;
}

export async function renderMoons(canvas, options = {}) {
  checkCancelled(options.signal);
  const civilDates=parseDateList(options.dates??options.date??'',options.dateOrder??'AUTO'),dates=civilDates.map(date=>date.iso);
  if(options.instants!==undefined&&(!Array.isArray(options.instants)||options.instants.length!==dates.length||options.instants.some(value=>!Number.isFinite(new Date(value).getTime()))))throw Error('Danh sách thời điểm không khớp các ngày Moon Phase.');
  const columns = integer(options.columns ?? 3, 1, 12, 'Số cột'), size = integer(options.size ?? 400, 100, 1200, 'Size mỗi moon');
  const time = options.time ?? '12:00', offset = options.utcOffset ?? 0, lat = options.lat ?? 51.4809181, lon = options.lon ?? -.0207105;
  const provider=options.providerMetadata;
  if(provider!==undefined&&(!Array.isArray(provider)||provider.length!==dates.length))throw Error('Dữ liệu nguồn Moon không khớp danh sách ngày.');
  const items = dates.map((date,i) => {
    const calculated=moonMetadata(date,time,offset,lat,lon,options.instants?.[i]);
    if(!provider)return{...calculated,display:civilDates[i].display};
    const source=provider[i];
    if(source?.dateISO!==date||source.sourceDateSerial!==date.replaceAll('-','')||(!PHASE_NAMES.includes(source.phaseName)&&source.phaseName!=='Third Quarter')||typeof source.waxing!=='boolean')throw Error('Nguồn Moon trả ngày/pha không khớp; không xuất ảnh ngày khác.');
    const illumination=finite(source.illumination,0,100,'Chiếu sáng nguồn'),providerTilt=finite(source.providerTilt,-180,180,'Góc xoay nguồn');
    return{...calculated,...source,date,display:civilDates[i].display,fraction:illumination/100,illumination,providerTilt,model:'MoonPhases date-only protocol / NASA illustrative raster'};
  });
  const cols = Math.min(columns, items.length), rows = Math.ceil(items.length / cols), labelH = options.showLabels === false ? 0 : Math.max(34,Math.round(size*.13));
  const w = cols*size, h = rows*(size+labelH);
  if (w * h > 16777216 || w > 8192 || h > 8192) throw new Error('PNG ghép quá lớn. Giảm size hoặc số ngày (tối đa 16 megapixel).');
  const bg = color(options.background, '#080d18'), label = color(options.labelColor, '#d8deec');
  const surface = await texture();checkCancelled(options.signal);const ctx = context(canvas, w, h);
  if (!options.transparent) { ctx.fillStyle = bg; ctx.fillRect(0, 0, w, h); }
  const tile = document.createElement('canvas'); tile.width = size; tile.height = size;
  const tctx = tile.getContext('2d');
  for (let i = 0; i < items.length; i++) {
    checkCancelled(options.signal);
    const x = i % cols * size, y = Math.floor(i / cols) * (size+labelH);
    const image = tctx.createImageData(size, size); image.data.set(shadeMoonPixels(size, items[i], surface,{appearance:options.appearance??'warm',orientation:options.orientation??'north'})); tctx.putImageData(image, 0, 0);
    const individual=document.createElement('canvas');individual.width=size;individual.height=size;individual.getContext('2d').drawImage(tile,0,0);
    items[i].canvas=individual;ctx.drawImage(tile,x+size*.03,y+size*.03,size*.94,size*.94);
    if (labelH) { ctx.fillStyle = label; ctx.textAlign = 'center'; ctx.textBaseline = 'top'; ctx.font = `${Math.max(12, Math.round(size * .048))}px sans-serif`;
      ctx.fillText(items[i].display, x + size / 2, y + size + size * .01);ctx.font=`${Math.max(10,Math.round(size*.03))}px sans-serif`;
      ctx.fillText(`${items[i].illumination.toFixed(1)}% · ${items[i].phaseName}`,x+size/2,y+size+Math.max(18,size*.075));}
    await new Promise(resolve => setTimeout(resolve, 0));
  }
  checkCancelled(options.signal);
  return {kind: 'moon', items, width: w, height: h, model:provider?'MoonPhases date-only verified metadata':'SunCalc 2.1.1 / Meeus', texture: "NASA's Scientific Visualization Studio / LROC",
    note:provider?'Date, illumination and disk tilt verified with the panel provider. PNG is a NASA reconstruction, not the provider photograph.':'Local computed PNG, not the panel provider 16-image lookup. No libration, lunar-axis rotation or eclipses; warm is an appearance preset, not different astronomical data.'};
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
  const found=(await places())(query),zones=await cityTimezones();
  const enrich=place=>({...place,timeZone:zones.get(place.geonameId)??null});
  if(found.result)found.result=enrich(found.result);if(found.candidates)found.candidates=found.candidates.map(enrich);
  return found;
}

async function cityTimezones() {
  if(!zonePromise)zonePromise=(async()=>{
    const bytes=await boundedAsset('city-timezones.json.gz',2*1024*1024),stream=new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
    const reader=stream.getReader(),chunks=[];let size=0;
    while(true){const next=await reader.read();if(next.done)break;size+=next.value.byteLength;if(size>8*1024*1024){await reader.cancel();throw Error('Dữ liệu múi giờ quá lớn.');}chunks.push(next.value);}
    const data=JSON.parse(await new Blob(chunks).text());
    if(data.schema!==1||!Array.isArray(data.zones)||data.zones.length>1000||!Array.isArray(data.cities)||data.cities.length>500000)throw Error('Dữ liệu múi giờ thành phố không hợp lệ.');
    return new Map(data.cities.map(row=>[row[0],data.zones[row[1]]]));
  })().catch(error=>{zonePromise=null;throw error;});return zonePromise;
}

export function getCapabilities() {
  return {sky: true, moon: true, placeSearch: 'bundled-GeoNames-exact-match', localOnly: true, browserOnly: true, remoteApiRequired: false,
    dateRange: [1900, 2100], utcOffsetUnit: 'hours-east-of-UTC', moonEngine: 'SunCalc 2.1.1', moonOrientation: 'north-up-illustrative-no-libration',
    skyProjection: 'stereographic-upper-hemisphere', cityTimezone:'GeoNames city ID / browser Intl IANA DST', catalog: 'same bundled panel D3-Celestial stars.6', pixelLimit: 16777216};
}
export const getcapabilities = getCapabilities;
