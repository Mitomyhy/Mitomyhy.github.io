// Browser counterpart of the panel's A–1 template → A–Z → layout workflow.
// Uses the unmodified panel layout/score engine. No Photoshop, server or AI API.
export const LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
export const TILE_SIZE = 800;
const MAX_IMAGE_BYTES = 12 * 1024 * 1024;
const MAX_IMAGE_PIXELS = 16 * 1024 * 1024;
const DEFAULT_STYLE = Object.freeze({ tile:'#e3c48b', ink:'#29251f', font:'Arial', letterSize:.62, scoreSize:.18, letterX:.48, letterY:.48, scoreX:.88, scoreY:.85, showScore:true, radius:.055 });
function bounded(value, min, max, label) { if (!Number.isFinite(value) || value < min || value > max) throw Error(`${label}: ngoài giới hạn ${min}–${max}.`); return value; }
export function normalizeStyle(values = {}) {
  const result = { ...DEFAULT_STYLE, ...values };
  for (const key of ['tile','ink']) if (!/^#[a-f0-9]{6}$/i.test(result[key])) throw Error('Màu ô/chữ phải là mã màu hợp lệ.');
  if (typeof result.font !== 'string' || !/^[A-Za-z0-9 _-]{1,100}$/.test(result.font)) throw Error('Tên font không hợp lệ.');
  for (const key of ['letterSize','scoreSize']) bounded(result[key], .05, .85, key);
  for (const key of ['letterX','letterY','scoreX','scoreY']) bounded(result[key], .05, .95, key);
  bounded(result.radius, 0, .2, 'Bo góc');
  result.showScore = Boolean(result.showScore);
  return result;
}
export function tileLetterFromFilename(name) {
  const match = String(name).match(/^(?:tile[-_ ]?)?([a-z])\.(png|jpe?g|webp)$/i);
  if (!match) throw Error(`“${name}”: đặt tên ô theo A.png … Z.png (hoặc tile-A.png).`);
  return match[1].toUpperCase();
}
export function requiredLetters(layout) { return [...new Set(layout.cells.map(cell => cell.letter))].sort(); }
export function missingLetters(layout, tiles) { return requiredLetters(layout).filter(letter => !tiles.has(letter)); }
export function outputDimensions(width, height, maxEdge) {
  bounded(width, 100, 30000, 'Rộng canvas'); bounded(height, 100, 30000, 'Cao canvas'); bounded(maxEdge, 100, 4000, 'Cạnh xuất');
  const factor = maxEdge / Math.max(width, height);
  return { width:Math.max(1,Math.round(width*factor)), height:Math.max(1,Math.round(height*factor)) };
}
function rounded(ctx, x, y, w, h, radius) {
  const r = Math.min(radius,w/2,h/2); ctx.beginPath(); ctx.moveTo(x+r,y); ctx.lineTo(x+w-r,y); ctx.quadraticCurveTo(x+w,y,x+w,y+r); ctx.lineTo(x+w,y+h-r); ctx.quadraticCurveTo(x+w,y+h,x+w-r,y+h); ctx.lineTo(x+r,y+h); ctx.quadraticCurveTo(x,y+h,x,y+h-r); ctx.lineTo(x,y+r); ctx.quadraticCurveTo(x,y,x+r,y); ctx.closePath();
}
function canvasBlob(canvas) { return new Promise((resolve,reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(Error('Không xuất được PNG.')), 'image/png')); }
export async function decodeLocalImage(file, dependencies = {}) {
  if (!file || !/\.(png|jpe?g|webp)$/i.test(file.name) || !Number.isSafeInteger(file.size) || file.size < 1 || file.size > MAX_IMAGE_BYTES) throw Error('Chọn PNG/JPG/WebP không quá 12 MB.');
  const urlAPI = dependencies.urlAPI || URL, ImageClass = dependencies.ImageClass || Image;
  const url = urlAPI.createObjectURL(file);
  try {
    const image = new ImageClass(); image.src = url; await image.decode();
    if (!Number.isInteger(image.naturalWidth) || !Number.isInteger(image.naturalHeight) || image.naturalWidth < 1 || image.naturalHeight < 1 || image.naturalWidth * image.naturalHeight > MAX_IMAGE_PIXELS || Math.max(image.naturalWidth,image.naturalHeight) > 8192) throw Error('Ảnh vượt giới hạn 16 megapixel / 8192 px.');
    return image;
  } finally { urlAPI.revokeObjectURL(url); }
}
export function createScrabbleBrowser({ engine, createCanvas = () => document.createElement('canvas') } = {}) {
  if (!engine?.fit || !engine?.letterScore || !engine?.validate) throw Error('Thiếu bộ Scrabble của panel.');
  let style = normalizeStyle(), backing = null, custom = new Map(), source = 'template', font = null;
  function makeTile(letter, side = TILE_SIZE) {
    if (!/^[A-Z]$/.test(letter)) throw Error('Ô chữ chỉ nhận A–Z.');
    if (source === 'images') { if (!custom.has(letter)) throw Error(`Thiếu ô nguồn ${letter}. Không tự thay font/kiểu ô.`); return custom.get(letter); }
    const canvas = createCanvas(); canvas.width = side; canvas.height = side; const ctx = canvas.getContext('2d');
    ctx.clearRect(0,0,side,side);
    ctx.save(); rounded(ctx,side*.015,side*.015,side*.97,side*.97,side*style.radius); ctx.clip();
    ctx.fillStyle = style.tile; ctx.fillRect(0,0,side,side);
    if (backing) ctx.drawImage(backing,0,0,side,side);
    ctx.restore();
    ctx.fillStyle = style.ink; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.font = `600 ${side*style.letterSize}px "${style.font}"`;
    // Width is measured before rendering: W/M must remain inside the tile too.
    const metrics = ctx.measureText(letter), allowed = side*.90, scale = Math.min(1,allowed/Math.max(1,metrics.width));
    if (scale < 1) ctx.font = `600 ${side*style.letterSize*scale}px "${style.font}"`;
    const letterWidth = ctx.measureText(letter).width;
    const letterX = Math.max(letterWidth/2+side*.025,Math.min(side-letterWidth/2-side*.025,side*style.letterX));
    ctx.fillText(letter,letterX,side*style.letterY);
    if (style.showScore) { ctx.textAlign = 'right'; ctx.font = `400 ${side*style.scoreSize}px "${style.font}"`; ctx.fillText(String(engine.letterScore(letter)),side*style.scoreX,side*style.scoreY); }
    return canvas;
  }
  function render(canvas, layout, options = {}) {
    const { width, height } = outputDimensions(options.width || 800,options.height || 800,options.maxEdge || 2048);
    const absent = source === 'images' ? missingLetters(layout,custom) : [];
    if (absent.length) throw Error(`Bộ ô đang thiếu ${absent.join(', ')}. Nạp đúng ô hoặc chuyển về template A–1; không tự thay kiểu.`);
    const fit = engine.fit(layout,{ bounds:[0,0,width,height], shape:options.shape || 'rect', gapRatio:options.gapRatio ?? .04, inset:options.inset ?? .04 });
    canvas.width = width; canvas.height = height; const ctx = canvas.getContext('2d'); ctx.clearRect(0,0,width,height);
    const cache = new Map();
    for (const cell of layout.cells) {
      if (!cache.has(cell.letter)) cache.set(cell.letter,makeTile(cell.letter,Math.min(800,Math.max(48,Math.ceil(fit.tileSize)))));
      const image = cache.get(cell.letter), x = fit.left+cell.col*fit.pitch, y = fit.top+cell.row*fit.pitch;
      ctx.drawImage(image,x,y,fit.tileSize,fit.tileSize);
    }
    return { width, height, fit, cells:layout.cells.length, source };
  }
  async function importTiles(files) {
    if (!files?.length || files.length > 26) throw Error('Chọn từ 1 đến 26 ảnh ô chữ có tên A.png … Z.png.');
    const accepted = new Map(); let total = 0;
    // Decode into a new map first: an invalid file never damages the active set.
    for (const file of files) {
      const letter = tileLetterFromFilename(file.name); if (accepted.has(letter)) throw Error(`Hai file cùng ô ${letter}; chỉ giữ một file mỗi chữ.`);
      total += file.size; if (total > 64*1024*1024) throw Error('Tổng bộ ô không quá 64 MB.');
      const image = await decodeLocalImage(file), ratio = image.naturalWidth/image.naturalHeight;
      if (ratio < .8 || ratio > 1.25) throw Error(`Ô ${letter} cần gần vuông (tỷ lệ 0.8–1.25), giống điều kiện nguồn của panel.`);
      // Retain normalized 800 px tiles rather than 26 full camera decodes.
      const tile = createCanvas(); tile.width = TILE_SIZE; tile.height = TILE_SIZE;
      tile.getContext('2d').drawImage(image,0,0,TILE_SIZE,TILE_SIZE); accepted.set(letter,tile);
    }
    custom = accepted; source = 'images'; return [...custom.keys()].sort();
  }
  async function loadFont(file) {
    if (!file || !/\.(ttf|otf|woff2?)$/i.test(file.name) || file.size < 1 || file.size > 10*1024*1024) throw Error('Font phải là TTF/OTF/WOFF/WOFF2 không quá 10 MB.');
    const family = `MITOMScrabble${Date.now()}`, face = new FontFace(family,await file.arrayBuffer()); await face.load();
    document.fonts.add(face); if (font) document.fonts.delete(font); font = face; style = normalizeStyle({ ...style,font:family }); return family;
  }
  async function alphabetZip() {
    if (source === 'images') { const absent = LETTERS.split('').filter(letter => !custom.has(letter)); if (absent.length) throw Error(`Bộ ô chưa đủ A–Z: thiếu ${absent.join(', ')}.`); }
    const entries = [];
    for (const letter of LETTERS) {
      const tile = makeTile(letter), canvas = createCanvas(); canvas.width = TILE_SIZE; canvas.height = TILE_SIZE; canvas.getContext('2d').drawImage(tile,0,0,TILE_SIZE,TILE_SIZE);
      entries.push({ name:`${letter}.png`, bytes:new Uint8Array(await (await canvasBlob(canvas)).arrayBuffer()) });
    }
    return makeStoredZip(entries);
  }
  return { render, tile:makeTile, alphabetZip, importTiles, loadFont,
    setStyle(values) { style = normalizeStyle({ ...style,...values }); return style; },
    setSource(value) { if (!['template','images'].includes(value)) throw Error('Nguồn ô không hợp lệ.'); source = value; },
    async setBacking(file) { backing = file ? await decodeLocalImage(file) : null; },
    state() { return { source, letters:[...custom.keys()].sort(), style:{...style}, hasBacking:Boolean(backing) }; }
  };
}
// Uncompressed ZIP: losslessly bundles PNGs, needs no CDN/library/backend.
const crcTable = new Uint32Array(256).map((_,n) => { let c=n; for(let i=0;i<8;i++)c=(c&1)?0xedb88320^(c>>>1):c>>>1;return c>>>0; });
function crc32(bytes) { let value=0xffffffff;for(const byte of bytes)value=crcTable[(value^byte)&255]^(value>>>8);return (value^0xffffffff)>>>0; }
export function makeStoredZip(entries,kind='alphabet') {
  if(!['alphabet','mockups'].includes(kind))throw Error('Loại ZIP không hợp lệ.');
  const allowedName=kind==='mockups'?/^Mockup-0[1-9]\.png$/:/^[A-Z]\.png$/;
  if (!Array.isArray(entries) || entries.length < 1 || entries.length > 27) throw Error('Số file ZIP không hợp lệ.');
  const encoder=new TextEncoder(),local=[],central=[];let offset=0,total=0;const seen=new Set();
  for(const entry of entries) {
    if (!allowedName.test(entry.name) || seen.has(entry.name) || !(entry.bytes instanceof Uint8Array) || !entry.bytes.length) throw Error('Nội dung ZIP PNG không hợp lệ.'); seen.add(entry.name);
    total+=entry.bytes.length;if(total>128*1024*1024)throw Error('Bộ ô vượt 128 MB.');
    const name=encoder.encode(entry.name),crc=crc32(entry.bytes),header=new Uint8Array(30+name.length),v=new DataView(header.buffer);
    v.setUint32(0,0x04034b50,true);v.setUint16(4,20,true);v.setUint16(10,0,true);v.setUint16(12,33,true);v.setUint32(14,crc,true);v.setUint32(18,entry.bytes.length,true);v.setUint32(22,entry.bytes.length,true);v.setUint16(26,name.length,true);header.set(name,30);local.push(header,entry.bytes);
    const record=new Uint8Array(46+name.length),c=new DataView(record.buffer);c.setUint32(0,0x02014b50,true);c.setUint16(4,20,true);c.setUint16(6,20,true);c.setUint16(14,33,true);c.setUint32(16,crc,true);c.setUint32(20,entry.bytes.length,true);c.setUint32(24,entry.bytes.length,true);c.setUint16(28,name.length,true);c.setUint32(42,offset,true);record.set(name,46);central.push(record);offset+=header.length+entry.bytes.length;
  }
  const centralSize=central.reduce((sum,record)=>sum+record.length,0),end=new Uint8Array(22),v=new DataView(end.buffer);v.setUint32(0,0x06054b50,true);v.setUint16(8,entries.length,true);v.setUint16(10,entries.length,true);v.setUint32(12,centralSize,true);v.setUint32(16,offset,true);
  return new Blob([...local,...central,end],{type:'application/zip'});
}
