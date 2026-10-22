/* Pure pixel operations. No DOM, network, or source-file writes. */
export const MAX_PIXELS = 16_000_000;
export const MAX_SIDE = 8192;

export function checkSize(width, height) {
  if (!Number.isInteger(width) || !Number.isInteger(height) || width < 2 || height < 2 ||
      width > MAX_SIDE || height > MAX_SIDE || width * height > MAX_PIXELS) {
    throw new Error('Ảnh cần từ 2 px, tối đa 16 megapixel / 8192 px mỗi cạnh.');
  }
}

export function normalizedDepth(values, width, height) {
  checkSize(width, height);
  if (!values || values.length !== width * height) throw new Error('Kích thước depth không khớp ảnh.');
  let lo = Infinity, hi = -Infinity;
  for (const n of values) {
    if (!Number.isFinite(n)) throw new Error('Depth AI có giá trị không hợp lệ.');
    lo = Math.min(lo, n); hi = Math.max(hi, n);
  }
  const range = hi - lo, output = new Float32Array(values.length);
  for (let i = 0; i < output.length; i++) output[i] = range > 1e-12 ? (values[i] - lo) / range : 0.5;
  return output;
}

export function resizeDepth(depth, sw, sh, width, height) {
  checkSize(sw, sh); checkSize(width, height);
  if (!(depth instanceof Float32Array) || depth.length !== sw * sh) throw new Error('Depth array không hợp lệ.');
  if (sw === width && sh === height) return depth.slice();
  const output = new Float32Array(width * height);
  for (let y = 0; y < height; y++) {
    const sy = Math.max(0, Math.min(sh - 1, (y + 0.5) * sh / height - 0.5));
    const y0 = Math.floor(sy), y1 = Math.min(sh - 1, y0 + 1), fy = sy - y0;
    for (let x = 0; x < width; x++) {
      const sx = Math.max(0, Math.min(sw - 1, (x + 0.5) * sw / width - 0.5));
      const x0 = Math.floor(sx), x1 = Math.min(sw - 1, x0 + 1), fx = sx - x0;
      output[y * width + x] = (depth[y0 * sw + x0] * (1 - fx) + depth[y0 * sw + x1] * fx) * (1 - fy) +
        (depth[y1 * sw + x0] * (1 - fx) + depth[y1 * sw + x1] * fx) * fy;
    }
  }
  return output;
}

export function grayscaleDepth(rgba, width, height) {
  checkSize(width, height);
  if (!rgba || rgba.length !== width * height * 4) throw new Error('Ảnh depth không hợp lệ.');
  const depth = new Float32Array(width * height);
  // This is an explicitly supplied depth image, never an AI substitute.
  for (let i = 0; i < depth.length; i++) depth[i] =
    (0.2126 * rgba[i * 4] + 0.7152 * rgba[i * 4 + 1] + 0.0722 * rgba[i * 4 + 2]) / 255;
  return depth;
}

export function substrateMask(width, height, type) {
  checkSize(width, height);
  if (type === 'acrylic') return undefined;
  if (!['ceramic_circle', 'ceramic_heart'].includes(type)) throw new Error('Loại sản phẩm không hợp lệ.');
  const mask = new Uint8Array(width * height), scale = Math.min(width, height) * 0.45;
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const px = (x + 0.5 - width / 2) / scale;
    const py = -(y + 0.5 - height / 2) / scale;
    // Explicit geometric preview blank, independent from artwork transparency.
    // A circle/heart here is NOT a customer's authoritative Photoshop template.
    const heartX = px * 1.12, heartY = py * 1.12 + 0.12;
    const q = heartX * heartX + heartY * heartY - 1;
    const inside = type === 'ceramic_circle' ? px * px + py * py <= 1 :
      q * q * q - heartX * heartX * heartY * heartY * heartY <= 0;
    mask[y * width + x] = inside ? 255 : 0;
  }
  return mask;
}

const crcTable = new Uint32Array(256);
for (let i = 0; i < 256; i++) {
  let c = i;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  crcTable[i] = c >>> 0;
}
function u32(value) { return new Uint8Array([(value >>> 24) & 255, (value >>> 16) & 255, (value >>> 8) & 255, value & 255]); }
function concat(parts) {
  const output = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let offset = 0; for (const part of parts) { output.set(part, offset); offset += part.length; } return output;
}
function chunk(type, bytes) {
  const tag = Uint8Array.from(type, c => c.charCodeAt(0));
  let c = 0xffffffff;
  for (const part of [tag, bytes]) for (const b of part) c = crcTable[(c ^ b) & 255] ^ (c >>> 8);
  return concat([u32(bytes.length), tag, bytes, u32((c ^ 0xffffffff) >>> 0)]);
}
export function encodeGray16PNG(depth, width, height, alpha = null) {
  checkSize(width, height);
  if (!(depth instanceof Float32Array) || depth.length !== width * height) throw new Error('Chưa có depth để xuất.');
  if (alpha !== null && (!(alpha instanceof Uint8Array || alpha instanceof Uint8ClampedArray) || alpha.length !== width * height))
    throw new Error('Alpha của map không khớp canvas.');
  const scan = new Uint8Array(height * (width * (alpha === null ? 2 : 4) + 1));
  let offset = 0;
  for (let y = 0; y < height; y++) {
    scan[offset++] = 0;
    for (let x = 0; x < width; x++) {
      const n = depth[y * width + x];
      if (!Number.isFinite(n) || n < 0 || n > 1) throw new Error('Depth không hợp lệ.');
      const v = Math.round(n * 65535); scan[offset++] = v >>> 8; scan[offset++] = v & 255;
      // PNG color type4 preserves the scalar channel at16 bits. Replicating an
      // 8-bit alpha into both bytes maps0..255 exactly into0..65535; no Canvas
      // 8-bit RGB quantisation and no inferred white-background removal.
      if (alpha !== null) { scan[offset++] = alpha[y * width + x]; scan[offset++] = alpha[y * width + x]; }
    }
  }
  // RFC 1950 zlib + RFC 1951 stored blocks: deterministic 16-bit PNG, no
  // accidental Canvas 8-bit quantisation and no external compression library.
  const blocks = [new Uint8Array([0x78, 0x01])];
  for (let p = 0; p < scan.length; p += 65535) {
    const n = Math.min(65535, scan.length - p), last = p + n === scan.length;
    blocks.push(new Uint8Array([last ? 1 : 0, n & 255, n >>> 8, (~n) & 255, ((~n) >>> 8) & 255]), scan.subarray(p, p + n));
  }
  let a = 1, b = 0;
  for (const byte of scan) { a = (a + byte) % 65521; b = (b + a) % 65521; }
  blocks.push(u32(((b << 16) | a) >>> 0));
  const header = concat([u32(width), u32(height), new Uint8Array([16, alpha === null ? 0 : 4, 0, 0, 0])]);
  return concat([new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header), chunk('IDAT', concat(blocks)), chunk('IEND', new Uint8Array())]);
}

export function bytesDataURL(bytes) {
  let binary = '';
  for (let p = 0; p < bytes.length; p += 32768) binary += String.fromCharCode(...bytes.subarray(p, p + 32768));
  return 'data:image/png;base64,' + btoa(binary);
}
