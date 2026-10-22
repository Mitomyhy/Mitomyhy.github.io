export const ORT_VERSION = '1.23.2';
export const PYODIDE_VERSION = '314.0.7';
export const PROFILE = 'cpu-standard-fp32-1225';
export const PYTHON_NAMES = new Set(['browser_postprocess.py','depth_math.py','geometry_validity.py','relief_detail.py','sculpt_relief.py','relief_balance.py','relief_transition.py','fullprint_detail.py','fullprint_prepare.py','da2_backend.py','relief_finish.py']);
export const MAX_MODEL_BYTES = 1_600_000_000;
export function check(condition, message) { if(!condition)throw new Error(message); }
export function size(width,height,maxPixels=40_000_000) { check(Number.isSafeInteger(width)&&Number.isSafeInteger(height)&&Math.min(width,height)>=8&&width*height<=maxPixels,'Kích thước ảnh không hợp lệ.'); }
export function resource(value,{max=MAX_MODEL_BYTES}={}) { check(value&&typeof value==='object'&&/^[a-f0-9]{64}$/.test(value.sha256)&&Number.isSafeInteger(value.bytes)&&value.bytes>0&&value.bytes<=max,'Tệp chưa có kích thước/SHA-256 hợp lệ.'); return value; }
export function trustedURL(raw, base, allowedOrigins=[]) {
  check(typeof raw==='string'&&raw.length<4096,'URL tài nguyên không hợp lệ.'); const url=new URL(raw,base), same=url.origin===new URL(base).origin;
  check(!url.username&&!url.password&&!url.hash&&(url.protocol==='https:'||(same&&url.protocol==='http:'&&['localhost','127.0.0.1','[::1]'].includes(url.hostname))),'Chỉ dùng HTTPS hoặc preview localhost.');
  check(same||allowedOrigins.includes(url.origin),'Nguồn tài nguyên chưa nằm trong manifest cho phép.'); return url.href;
}
export function validateManifest(manifest, base) {
  check(manifest&&manifest.schema===1&&manifest.profile===PROFILE&&manifest.models&&manifest.python,'Manifest CPU không đúng phiên bản.');
  const origins=manifest.modelOrigins||[];check(Array.isArray(origins)&&origins.length<=8&&origins.every(s=>typeof s==='string'&&new URL(s).origin===s&&s.startsWith('https://')),'Danh sách nguồn model không hợp lệ.');
  for(const [kind,side] of [['moge',490],['da2',1036]]) {
    const spec=resource(manifest.models[kind]);check(spec.side===side&&spec.dtype==='float32',`Model ${kind} không đúng FP32/kích thước.`);
    if(kind==='moge')check(spec.tokens===1225&&spec.queryChunk===64,'Không tự thay preset MoGe 1225/query64.');
    if(spec.parts){check(!spec.url&&Array.isArray(spec.parts)&&spec.parts.length>0&&spec.parts.length<=64,'Parts của model không hợp lệ.');let total=0;for(const part of spec.parts){resource(part,{max:48*1024*1024});trustedURL(part.url,base,origins);total+=part.bytes;}check(total===spec.bytes,'Tổng kích thước parts không khớp model.');}
    else trustedURL(spec.url,base,origins);
  }
  check(manifest.python.version===PYODIDE_VERSION&&Array.isArray(manifest.python.modules)&&manifest.python.modules.length===PYTHON_NAMES.size,'Bộ xử lý native Python không đầy đủ.');
  const names=new Set();for(const file of manifest.python.modules){resource(file,{max:1_000_000});check(PYTHON_NAMES.has(file.name)&&!names.has(file.name),'Tên module native không hợp lệ/lặp.');names.add(file.name);trustedURL(file.url,base);}
  return manifest;
}
