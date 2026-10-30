/* R79: customer-note dates -> civil parts -> MM/DD/YYYY. Never Date.parse,
 * workstation timezone, calendar rollover, or silently skipping a bad date.
 * Ambiguous numbers default to month/day; an explicit order or format label
 * resolves them. Existing two-digit-year policy: 00–49 -> 2000s, 50–99 -> 1900s. */
const MONTH_GROUPS = [
  'jan january janvier enero ene januar gennaio janeiro',
  'feb february fevrier febrero februar febbraio fevereiro',
  'mar march mars marzo marz maart marco',
  'apr april avril abril aprile',
  'may mai mayo maggio maio mei',
  'jun june juin junio juni giugno junho',
  'jul july juillet juil julio juli luglio julho',
  'aug august aout agosto',
  'sep sept september septembre septiembre setiembre settembre setembro',
  'oct october octobre octubre oktober ottobre outubro',
  'nov november novembre noviembre novembro',
  'dec december decembre diciembre dezember dicembre dezembro'
];
const MONTHS=Object.fromEntries(MONTH_GROUPS.flatMap((group,index)=>group.split(' ').map(name=>[name,index+1])));
const MONTH_PATTERN=Object.keys(MONTHS).sort((a,b)=>b.length-a.length).join('|');
const pad=n=>String(n).padStart(2,'0');
function dateError(message,code='DATE_INVALID'){const error=new Error(message);error.code=code;throw error;}
function folded(text){return text.normalize('NFKC').normalize('NFD').replace(/[\u0300-\u036f]/g,'').normalize('NFC').replace(/đ/g,'d').replace(/Đ/g,'D').replace(/[\u200b\ufeff]/g,'').replace(/[\u2010-\u2015\u2212]/g,'-').replace(/[\u2044\u2215]/g,'/').replace(/\r\n?/g,'\n');}
function settings(order){
  const value=typeof order==='object'&&order!==null?order:{order:order??'AUTO'};
  const preferred=value.dayFirst===true?'DMY':value.order??'AUTO';
  if(!['AUTO','MDY','DMY'].includes(preferred))dateError('Chọn Tự nhận dạng, MM/DD/YYYY hoặc DD/MM/YYYY.');
  return{order:preferred};
}
function formatHint(text){
  const mdy=/\bm{1,2}\s*[/.\-]\s*d{1,2}\s*[/.\-]\s*y{2,4}\b|\bmonth\s*[/-]\s*day\s*[/-]\s*year\b|\bmonth[- ]first\b/i.test(text);
  const dmy=/\bd{1,2}\s*[/.\-]\s*m{1,2}\s*[/.\-]\s*y{2,4}\b|\bday\s*[/-]\s*month\s*[/-]\s*year\b|\bday[- ]first\b/i.test(text);
  return mdy===dmy?null:mdy?'MDY':'DMY';
}
function calendar(parts,token){
  let{year,month,day}=parts;const short=String(year).length===2;
  year=Number(year);month=Number(month);day=Number(day);
  if(short)year+=year<50?2000:1900;
  const leap=year%4===0&&(year%100!==0||year%400===0),days=[31,leap?29:28,31,30,31,30,31,31,30,31,30,31];
  if(!Number.isInteger(year)||year<1900||year>2100||!Number.isInteger(month)||month<1||month>12||!Number.isInteger(day)||day<1||day>days[month-1])dateError(`Ngày không tồn tại hoặc ngoài 1900–2100: “${token.slice(0,100)}”.`);
  const display=`${pad(month)}/${pad(day)}/${year}`;
  return{year,month,day,iso:`${year}-${pad(month)}-${pad(day)}`,display,normalized:display,ambiguous:!!parts.ambiguous,sourceOrder:parts.sourceOrder||'TEXT',twoDigitYear:short,source:token};
}
function numeric(a,b,c,preferred,hint){
  if(a.length===4){if(b.length>2||c.length>2)dateError('Ngày và tháng chỉ dùng 1 hoặc 2 chữ số.');return{year:a,month:b,day:c,sourceOrder:'YMD'};}
  if(![2,4].includes(c.length)||a.length>2||b.length>2)dateError('Cần ngày, tháng và năm đầy đủ; năm dùng 2 hoặc 4 chữ số.');
  const first=Number(a),second=Number(b),order=first>12&&second<=12?'DMY':second>12&&first<=12?'MDY':hint||(preferred==='DMY'?'DMY':'MDY');
  return{year:c,month:order==='DMY'?b:a,day:order==='DMY'?a:b,sourceOrder:order,ambiguous:first>=1&&first<=12&&second>=1&&second<=12&&first!==second&&!hint&&preferred==='AUTO'};
}
function extract(text,order){
  if(typeof text!=='string'||!text.trim()||text.length>65536)dateError('Dán ngày hoặc note có ngày (tối đa 60 ngày).');
  const value=folded(text),config=settings(order),all=[],globalHint=formatHint(value);
  function collect(pattern,decode,priority=1,letters=false){
    let match;while((match=pattern.exec(value))){
      const start=match.index,end=start+match[0].length,before=value[start-1]||'',after=value[end]||'';
      if(/[0-9]/.test(before)||/[0-9]/.test(after)||letters&&(/[a-z]/i.test(before)||/[a-z]/i.test(after)))continue;
      const line=value.slice(value.lastIndexOf('\n',start-1)+1,value.indexOf('\n',end)<0?value.length:value.indexOf('\n',end));
      // Freeze captures: RegExp.exec reassigns match on its next iteration.
      const copy=Array.from(match);all.push({start,end,token:match[0],priority,decode:()=>decode(copy,formatHint(line)||globalHint)});
      if(all.length>240)dateError('Note có quá nhiều ngày; tối đa 60 ngày mỗi lần.');
    }
  }
  collect(/\b(\d{4})\s*(?:年|년)\s*(\d{1,2})\s*(?:月|월)\s*(\d{1,2})\s*(?:日|号|號|일)?/g,m=>({year:m[1],month:m[2],day:m[3],sourceOrder:'YMD'}),5);
  collect(/\b(\d{1,2})\s*(?:thang|thg)\.?\s*(\d{1,2})(?:\s*(?:nam|n)\.?\s*|\s*[,/-]\s*|\s+)(\d{4}|\d{2})\b/gi,m=>({year:m[3],month:m[2],day:m[1],sourceOrder:'DMY'}),5);
  collect(/\bthang\s*(\d{1,2})\s*ngay\s*(\d{1,2})\s*nam\s*(\d{4}|\d{2})\b/gi,m=>({year:m[3],month:m[1],day:m[2],sourceOrder:'MDY'}),5);
  collect(new RegExp('\\b(\\d{1,2})(?:st|nd|rd|th|er|e)?[\\s,./-]*(?:(?:of|de|del)\\s+)?('+MONTH_PATTERN+')\\.?[\\s,./-]*(?:de\\s+)?(\\d{4}|\\d{2})\\b','gi'),m=>({year:m[3],month:MONTHS[m[2].toLowerCase()],day:m[1]}),4,true);
  collect(new RegExp('\\b('+MONTH_PATTERN+')\\.?[\\s,./-]*(?:the\\s+)?(\\d{1,2})(?:(?:st|nd|rd|th|er|e)\\s*[,./-]?\\s*|[\\s,./-]+)(\\d{4}|\\d{2})\\b','gi'),m=>({year:m[3],month:MONTHS[m[1].toLowerCase()],day:m[2]}),4,true);
  collect(new RegExp('\\b(\\d{4})[\\s,./-]*('+MONTH_PATTERN+')\\.?[\\s,./-]*(\\d{1,2})(?:st|nd|rd|th)?\\b','gi'),m=>({year:m[1],month:MONTHS[m[2].toLowerCase()],day:m[3]}),4,true);
  collect(/\d{1,4}\s*[/.,\\-]\s*\d{1,2}\s*[/.,\\-]\s*\d{1,4}/g,(m,hint)=>{const parts=m[0].split(/\s*[/.,\\-]\s*/);return numeric(...parts,config.order,hint);},3);
  collect(/\b\d{4}[ \t]+\d{1,2}[ \t]+\d{1,2}\b|\b\d{1,2}[ \t]+\d{1,2}[ \t]+(?:\d{4}|\d{2})\b/g,(m,hint)=>numeric(...m[0].split(/[ \t]+/),config.order,hint),2);
  // Compact dates are accepted only as a whole date line or an explicit date
  // field. Do not mistake an ordinary eight-digit order/phone number for a date.
  collect(/\b\d{8}\b/g,(m,hint)=>{const token=m[0];return /^19|^20|^2100/.test(token)?numeric(token.slice(0,4),token.slice(4,6),token.slice(6),config.order,hint):numeric(token.slice(0,2),token.slice(2,4),token.slice(4),config.order,hint);},1);
  const ranked=all.filter(item=>{
    if(item.priority!==1)return true;
    const line=value.slice(value.lastIndexOf('\n',item.start-1)+1,value.indexOf('\n',item.end)<0?value.length:value.indexOf('\n',item.end)).trim();
    return line===item.token||/^(?:date|dob|birthday|birth date|ngay sinh|ngay)\s*[:=]/i.test(line);
  }).sort((a,b)=>b.priority-a.priority||(b.end-b.start)-(a.end-a.start)||a.start-b.start);
  const picked=[];for(const item of ranked)if(!picked.some(p=>item.start<p.end&&item.end>p.start))picked.push(item);
  picked.sort((a,b)=>a.start-b.start);
  // A malformed date elsewhere in a batch must not disappear just because a
  // different customer's date parsed. Broader numeric widths catch e.g.
  // 02/300/2026 without treating an ordinary order number as a date.
  for(const token of value.matchAll(/\d{1,6}\s*[/.,\\-]\s*\d{1,6}\s*[/.,\\-]\s*\d{1,6}/g)){
    const parts=token[0].split(/\s*[/.,\\-]\s*/),hasYear=parts.some(part=>part.length===4&&Number(part)>=1900&&Number(part)<=2100);
    if(hasYear&&!picked.some(item=>token.index>=item.start&&token.index+token[0].length<=item.end))dateError(`Chưa đọc được ngày “${token[0].slice(0,100)}”; kiểm tra đủ ngày, tháng, năm trước khi tạo.`);
  }
  if(!picked.length)dateError('Chưa nhận ra ngày đầy đủ trong note. Ví dụ: 10.24.2026, 24/10/2026, 2026-10-24 hoặc October 24, 2026.');
  if(picked.length>60)dateError('Tối đa 60 ngày mỗi lần.');
  return picked.map(item=>calendar(item.decode(),item.token));
}
export function parseCivilDate(input,order='AUTO'){
  const dates=extract(input,order);
  if(dates.length!==1)dateError('Sky Starmap cần đúng một ngày. Moon Phase có thể nhận nhiều ngày trong note.');
  return dates[0];
}
export function parseDate(input,options={}){return parseCivilDate(input,options);}
export function parseDateList(text,order='AUTO'){
  if(Array.isArray(text)){if(text.some(value=>typeof value!=='string'))dateError('Danh sách ngày chưa hợp lệ.');text=text.join('\n');}
  return extract(text,order);
}
export function parseMoonDates(text,order='AUTO'){return parseDateList(text,order);}
/** Keep raw note separately from its canonical display. Repeated Generate or
 * changing DMY preference must not parse the already-normalized value again. */
export function createDateNormalizer({multiple=false}={}){
  let previous=null;
  return{reset(){previous=null;},normalize(value,order='AUTO'){
    const original=previous&&value===previous.text?previous.original:value;
    const dates=multiple?parseMoonDates(original,order):[parseCivilDate(original,order)],text=dates.map(date=>date.display).join('\n');
    previous={original,text};return{original,text,dates,ambiguous:dates.some(date=>date.ambiguous),twoDigitYear:dates.some(date=>date.twoDigitYear)};
  }};
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
  if(candidates.size===0) throw Error('Giờ này không tồn tại do đổi giờ mùa hè. Chọn một giờ khác tại địa điểm này.');
  if(candidates.size>1) throw Error('Giờ này lặp lại do kết thúc giờ mùa hè. Chọn một giờ khác ngoài khoảng giờ lặp.');
  const epoch=[...candidates][0], utcOffset=(wall-epoch)/3600000;
  return {instant:new Date(epoch),utcOffset,timeZone:zone};
}
