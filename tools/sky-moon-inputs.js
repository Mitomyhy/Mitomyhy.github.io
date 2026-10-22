/* Shared browser-only civil-date and IANA timezone handling. Never uses the
 * workstation's local timezone. Numeric year-last dates have an explicit order. */
const MONTHS = {jan:1,january:1,feb:2,february:2,mar:3,march:3,apr:4,april:4,may:5,jun:6,june:6,jul:7,july:7,aug:8,august:8,sep:9,sept:9,september:9,oct:10,october:10,nov:11,november:11,dec:12,december:12};
const pad = n => String(n).padStart(2, '0');
export function parseCivilDate(input, order = 'MDY') {
  if (!['MDY','DMY'].includes(order)) throw Error('Chọn thứ tự MM/DD/YYYY hoặc DD/MM/YYYY.');
  const value = String(input ?? '').trim().replace(/[\u2010-\u2015\u2212]/g,'-');
  let match, y, m, d;
  if ((match=/^(\d{4})[\/.\-](\d{1,2})[\/.\-](\d{1,2})(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?$/.exec(value))) [,y,m,d]=match;
  else if ((match=/^(\d{1,2})[\/.\-](\d{1,2})[\/.\-](\d{2}|\d{4})$/.exec(value))) {
    const a=Number(match[1]), b=Number(match[2]); y=Number(match[3]);
    if (match[3].length===2) y+=y<50?2000:1900;
    if(a>12) {d=a;m=b;} else if(b>12) {m=a;d=b;} else if(order==='DMY') {d=a;m=b;} else {m=a;d=b;}
  } else if ((match=/^([A-Za-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?\s*,?\s+(\d{4})$/i.exec(value))) {m=MONTHS[match[1].toLowerCase()];d=match[2];y=match[3];}
  else if ((match=/^(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\.?\s*,?\s+(\d{4})$/i.exec(value))) {d=match[1];m=MONTHS[match[2].toLowerCase()];y=match[3];}
  else if ((match=/^(?:ng[aà]y\s*)?(\d{1,2})\s*th[aá]ng\s*(\d{1,2})\s*n[aă]m\s*(\d{4})$/i.exec(value))) {d=match[1];m=match[2];y=match[3];}
  else throw Error(`Không nhận ra ngày “${value}”. Dán MM/DD/YYYY, DD/MM/YYYY, YYYY-MM-DD hoặc tên tháng tiếng Anh.`);
  y=Number(y);m=Number(m);d=Number(d);
  const check=new Date(Date.UTC(y,m-1,d));
  if(y<1900||y>2100||check.getUTCFullYear()!==y||check.getUTCMonth()!==m-1||check.getUTCDate()!==d) throw Error(`Ngày không tồn tại hoặc ngoài 1900–2100: ${value}.`);
  return {year:y,month:m,day:d,iso:`${y}-${pad(m)}-${pad(d)}`,display:`${pad(m)}/${pad(d)}/${y}`};
}
export function parseDateList(text, order='MDY') {
  const lines=Array.isArray(text)?text:String(text??'').split(/[\r\n;]+/).map(s=>s.trim()).filter(Boolean);
  if(!lines.length||lines.length>60) throw Error('Dán từ 1 đến 60 ngày, mỗi ngày một dòng.');
  return lines.map(line=>parseCivilDate(line,order));
}
function timeParts(time) {
  const match=/^(\d{1,2}):(\d{2})(?::(\d{2}))?$/.exec(String(time));
  if(!match||Number(match[1])>23||Number(match[2])>59||Number(match[3]??0)>59) throw Error('Giờ phải theo HH:mm (00:00–23:59).');
  return [Number(match[1]),Number(match[2]),Number(match[3]??0)];
}
export function zonedDateTime(date, time='12:00', zone='UTC') {
  const parts=typeof date==='string'?parseCivilDate(date):date;
  const [hour,minute,second]=timeParts(time), wall=Date.UTC(parts.year,parts.month-1,parts.day,hour,minute,second);
  let formatter;
  try {formatter=new Intl.DateTimeFormat('en-GB',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'});} catch {throw Error(`Múi giờ IANA không hợp lệ hoặc chưa được trình duyệt hỗ trợ: ${zone}.`);}
  const clock=instant=>{const p=Object.fromEntries(formatter.formatToParts(instant).filter(p=>p.type!=='literal').map(p=>[p.type,Number(p.value)]));return Date.UTC(p.year,p.month-1,p.day,p.hour,p.minute,p.second);};
  const candidates=new Set();
  // Inspect offsets on both sides of a transition. Reject gaps and repeated
  // hours rather than silently moving the user's civil date/time.
  for(const delta of [-2,-1,0,1,2]) {
    const probe=wall+delta*86400000, offset=clock(probe)-probe, candidate=wall-offset;
    if(clock(candidate)===wall) candidates.add(candidate);
  }
  if(candidates.size===0) throw Error('Giờ này không tồn tại do đổi giờ mùa hè. Chọn giờ khác hoặc dùng UTC offset trong phần nâng cao.');
  if(candidates.size>1) throw Error('Giờ này lặp lại do kết thúc giờ mùa hè. Dùng UTC offset trong phần nâng cao để xác định lần mong muốn.');
  const epoch=[...candidates][0], utcOffset=(wall-epoch)/3600000;
  return {instant:new Date(epoch),utcOffset,timeZone:zone};
}
