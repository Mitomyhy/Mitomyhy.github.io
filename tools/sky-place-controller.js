/* A pasted location resolves on-device. A stale lookup never replaces a newer paste. */
export function createPlaceResolver({search,onState=()=>{},delay=350,setTimer=setTimeout,clearTimer=clearTimeout}={}) {
  if(typeof search!=='function')throw Error('Thiếu bộ tra địa điểm.');
  let serial=0,timer=null,disposed=false,state={status:'idle',query:'',place:null,candidates:[],message:''};
  const emit=value=>{state={...state,...value};onState({...state,candidates:[...state.candidates]});};
  const query=value=>{const text=String(value??'').trim();if(text.length>200)throw Error('Địa điểm tối đa 200 ký tự.');return text;};
  function valid(place){if(!place||!Number.isFinite(place.lat)||Math.abs(place.lat)>90||!Number.isFinite(place.lon)||Math.abs(place.lon)>180||typeof place.label!=='string'||!place.label||typeof place.timeZone!=='string'||!place.timeZone)throw Error('Chưa xác định được thành phố và múi giờ. Dán tên thành phố kèm quốc gia.');try{new Intl.DateTimeFormat('en',{timeZone:place.timeZone});}catch{throw Error('Múi giờ của địa điểm chưa được trình duyệt hỗ trợ.');}return {...place};}
  async function lookup(text,id){
    if(disposed)throw Error('Bộ tra địa điểm đã đóng.');
    emit({status:'loading',query:text,place:null,candidates:[],message:'Đang nhận địa điểm và múi giờ…'});
    try{
      const found=await search(text);
      if(disposed||id!==serial)throw Object.assign(Error('Đã dán địa điểm mới; bỏ kết quả cũ.'),{code:'CANCELLED'});
      if(found?.status==='matched'&&found.result){const place=valid(found.result);emit({status:'matched',place,message:place.label+' · tự tính giờ mùa hè theo ngày.'});return place;}
      const candidates=(found?.candidates||[]).slice(0,16).map(valid);
      if(candidates.length){emit({status:'ambiguous',candidates,message:'Có nhiều địa điểm cùng tên. Chọn đúng thành phố bên dưới.'});throw Error(state.message);}
      throw Error('Chưa tìm thấy địa điểm. Dán tên thành phố và quốc gia, ví dụ Leeds, UK.');
    }catch(error){if(id===serial&&!disposed&&state.status!=='ambiguous')emit({status:'error',place:null,candidates:[],message:error.message});throw error;}
  }
  return {
    input(value){if(disposed)return;clearTimer(timer);const text=query(value),id=++serial;emit({status:'idle',query:text,place:null,candidates:[],message:text.length<2?'Dán thành phố và quốc gia.':'Đang chờ nhận địa điểm…'});if(text.length>=2)timer=setTimer(()=>lookup(text,id).catch(()=>{}),delay);},
    resolve(value=state.query){if(disposed)return Promise.reject(Error('Bộ tra địa điểm đã đóng.'));clearTimer(timer);const text=query(value);if(state.place&&(text===state.query||text===state.place.label))return Promise.resolve({...state.place});if(text.length<2)return Promise.reject(Error('Dán tên thành phố và quốc gia.'));return lookup(text,++serial);},
    choose(value){if(disposed)throw Error('Bộ tra địa điểm đã đóng.');const candidate=state.candidates.find(item=>item.geonameId===value?.geonameId&&item.lat===value?.lat&&item.lon===value?.lon&&item.label===value?.label);if(!candidate)throw Error('Chọn một địa điểm trong kết quả đang hiển thị.');clearTimer(timer);serial++;const place=valid(candidate);emit({status:'matched',query:place.label,place,candidates:[],message:place.label+' · đã tự nhận tọa độ và múi giờ.'});return place;},
    state(){return {...state,candidates:[...state.candidates]};},
    dispose(){clearTimer(timer);serial++;disposed=true;}
  };
}
