/* Exact date-only protocol used by the installed panel. Metadata only: this
 * module never downloads, proxies, rehosts or reads back provider image bytes.
 * The API receives the chosen civil date; it does not accept an hour argument.
 * The caller must disclose that request and label local NASA PNGs separately. */
const API_ORIGIN='https://moonphases.co.uk';
const MAX_JSON_BYTES=2*1024*1024;
const REQUEST_TIMEOUT_MS=30000;
const PHASE_NAMES=new Set(['New Moon','Waxing Crescent','First Quarter','Waxing Gibbous','Full Moon','Waning Gibbous','Last Quarter','Third Quarter','Waning Crescent']);
function failure(code,message,cause){const error=new Error(message);error.code=code;if(cause)error.cause=cause;return error;}
function cancelError(){const error=failure('CANCELLED','Đã hủy lấy dữ liệu MoonPhases.');error.name='AbortError';return error;}
function civilDate(value){
  if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(value))throw failure('MOON_PROVIDER_DATE','Ngày MoonPhases phải là ISO YYYY-MM-DD đã được đọc từ MM/DD/YYYY.');
  const[year,month,day]=value.split('-').map(Number),date=new Date(Date.UTC(year,month-1,day));
  if(year<1950||year>2050||date.getUTCFullYear()!==year||date.getUTCMonth()!==month-1||date.getUTCDate()!==day)throw failure('MOON_PROVIDER_DATE','MoonPhases chỉ nhận ngày hợp lệ trong 1950–2050.');
  const mm=String(month).padStart(2,'0'),dd=String(day).padStart(2,'0');
  return{year,month,day,iso:value,serial:`${year}${mm}${dd}`,display:`${mm}/${dd}/${year}`};
}
function finiteNumber(value,min,max,label){
  if((typeof value!=='number'&&typeof value!=='string')||String(value).trim()===''||!Number.isFinite(Number(value))||Number(value)<min||Number(value)>max)throw failure('MOON_PROVIDER_INVALID_RESPONSE',`MoonPhases trả ${label} không hợp lệ.`);
  return Number(value);
}
export function moonProviderAPIURL(dateISO){const date=civilDate(dateISO);return`${API_ORIGIN}/service/getMoonDetails.php?day=${date.day}&month=${date.month}&year=${date.year}&lat=51.4809181&lng=-0.0207105&len=1&tz=0`;}
export function normalizeMoonProviderMetadata(payload,dateISO){
  const date=civilDate(dateISO),days=payload?.days,item=Array.isArray(days)&&days.length===1?days[0]:null;
  if(!item||typeof item!=='object'||Array.isArray(item))throw failure('MOON_PROVIDER_INVALID_RESPONSE','MoonPhases không trả đúng một ngày như yêu cầu.');
  if(typeof item.date_serial!=='string'||item.date_serial!==date.serial)throw failure('MOON_PROVIDER_DATE_MISMATCH',`MoonPhases trả sai ngày: cần ${date.serial}, nhận ${String(item.date_serial??'trống').slice(0,30)}.`);
  if(typeof item.phase_img!=='string'||!/^(?:[0-9]|1[0-5])\.png$/.test(item.phase_img))throw failure('MOON_PROVIDER_INVALID_RESPONSE','MoonPhases trả tên ảnh ngoài bộ 0.png–15.png.');
  const phaseName=String(item.phase_name??'').trim();if(!PHASE_NAMES.has(phaseName))throw failure('MOON_PROVIDER_INVALID_RESPONSE','MoonPhases trả tên pha không được nhận diện.');
  const illumination=finiteNumber(item.illumination,0,100,'tỷ lệ chiếu sáng'),providerTilt=finiteNumber(item.tilt,-180,180,'góc xoay'),phase=finiteNumber(item.phase,0,1,'pha');
  return{dateISO:date.iso,displayMMDD:date.display,sourceImageURL:`${API_ORIGIN}/images/moons/big/${item.phase_img}`,phaseName,illumination,providerTilt,phaseImage:item.phase_img,sourceDateSerial:item.date_serial,apiURL:moonProviderAPIURL(dateISO),phase,waxing:phase<.5};
}
function untilAbort(promise,signal){
  if(signal.aborted)return Promise.reject(cancelError());
  return new Promise((resolve,reject)=>{let settled=false;const cleanup=()=>signal.removeEventListener('abort',onAbort);function onAbort(){if(settled)return;settled=true;cleanup();reject(cancelError());}signal.addEventListener('abort',onAbort,{once:true});Promise.resolve(promise).then(value=>{if(settled)return;settled=true;cleanup();resolve(value);},error=>{if(settled)return;settled=true;cleanup();reject(error);});});
}
async function boundedJSON(response,signal){
  const declared=response.headers?.get('content-length');
  if(declared&&(!/^\d+$/.test(declared)||Number(declared)>MAX_JSON_BYTES))throw failure('MOON_PROVIDER_INVALID_RESPONSE','Dữ liệu MoonPhases vượt giới hạn 2 MB.');
  if(!response.body?.getReader)throw failure('MOON_PROVIDER_INVALID_RESPONSE','Trình duyệt chưa hỗ trợ đọc dữ liệu MoonPhases có giới hạn.');
  const reader=response.body.getReader(),chunks=[];let bytes=0,completed=false;
  try{while(true){const next=await untilAbort(reader.read(),signal);if(next.done){completed=true;break;}if(!(next.value instanceof Uint8Array))throw failure('MOON_PROVIDER_INVALID_RESPONSE','MoonPhases trả luồng dữ liệu không hợp lệ.');bytes+=next.value.byteLength;if(bytes>MAX_JSON_BYTES)throw failure('MOON_PROVIDER_INVALID_RESPONSE','Dữ liệu MoonPhases vượt giới hạn 2 MB.');chunks.push(next.value);}const all=new Uint8Array(bytes);let at=0;for(const chunk of chunks){all.set(chunk,at);at+=chunk.byteLength;}try{return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(all));}catch(error){throw failure('MOON_PROVIDER_INVALID_RESPONSE','MoonPhases không trả JSON UTF-8 hợp lệ.',error);}}
  finally{if(!completed){try{Promise.resolve(reader.cancel()).catch(()=>{});}catch{}}try{reader.releaseLock();}catch{}}
}
export async function fetchMoonProviderMetadata(dateISO,{signal,fetchImpl}={}){
  const url=moonProviderAPIURL(dateISO),fetcher=fetchImpl??globalThis.fetch;
  if(typeof fetcher!=='function')throw failure('MOON_PROVIDER_NETWORK','Trình duyệt không hỗ trợ Fetch.');
  if(signal?.aborted)throw cancelError();
  const controller=new AbortController();let timedOut=false;
  const forwardAbort=()=>controller.abort();signal?.addEventListener('abort',forwardAbort,{once:true});
  const timer=setTimeout(()=>{timedOut=true;controller.abort();},REQUEST_TIMEOUT_MS);
  try{
    const response=await untilAbort(fetcher(url,{method:'GET',mode:'cors',credentials:'omit',redirect:'error',cache:'no-store',referrerPolicy:'no-referrer',signal:controller.signal,headers:{Accept:'application/json'}}),controller.signal);
    if(!response||!response.ok)throw failure('MOON_PROVIDER_HTTP',`Không lấy được MoonPhases (HTTP ${Number(response?.status)||'không xác định'}). Không dùng kết quả giả thay thế.`);
    const payload=await boundedJSON(response,controller.signal);if(controller.signal.aborted)throw cancelError();return normalizeMoonProviderMetadata(payload,dateISO);
  }catch(error){
    if(timedOut)throw failure('MOON_PROVIDER_TIMEOUT','MoonPhases chưa phản hồi sau 30 giây. Hãy thử lại; không dùng dữ liệu ngày khác.');
    if(signal?.aborted||controller.signal.aborted)throw cancelError();
    if(error?.code?.startsWith('MOON_PROVIDER_'))throw error;
    throw failure('MOON_PROVIDER_NETWORK','Không đọc được MoonPhases. Kiểm tra mạng / quyền CORS; không tự đổi sang dữ liệu khác.',error);
  }finally{clearTimeout(timer);signal?.removeEventListener('abort',forwardAbort);}
}
