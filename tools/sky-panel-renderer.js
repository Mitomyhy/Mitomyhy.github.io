/* Exact installed-panel D3-Celestial renderer, isolated from the MITOM page.
 * No approximate projection fallback, Photoshop, server or artwork upload. */
const SCHEMA='mitom.sky-panel.v1',RENDER_SIZE=2400;
const ASSETS=new URL('./vendor/sky-panel/',import.meta.url);
const FRAME=new URL('./sky-panel-frame.html',import.meta.url);
const FILES=['mw.json','constellations.json','constellations.borders.json','constellations.lines.json','stars.6.json','starnames.json','dsos.bright.json','dsonames.json','planets.json'];
const LIBRARIES=['d3.min.js','d3.geo.projection.min.js','celestial.min.js'];
function check(value,message){if(!value)throw Error(message);}
function number(value,lo,hi,name){check(value!==''&&value!==null&&value!==undefined&&Number.isFinite(Number(value))&&Number(value)>=lo&&Number(value)<=hi,name+' không hợp lệ.');return Number(value);}
function color(value,fallback){value=value??fallback;check(typeof value==='string'&&/^#[a-f\d]{6}$/i.test(value),'Màu Sky Star không hợp lệ.');return value;}
export function panelSkyOptions(options={}){
  check(options.instant instanceof Date||typeof options.instant==='string','Sky Star cần thời điểm UTC hợp lệ từ địa điểm/ngày đã chọn.');
  const date=new Date(options.instant);check(Number.isFinite(date.getTime())&&date.getUTCFullYear()>=1900&&date.getUTCFullYear()<=2100,'Sky Star cần thời điểm UTC hợp lệ từ địa điểm/ngày đã chọn.');
  const size=number(options.size??2400,256,4096,'Kích thước');check(Number.isInteger(size),'Kích thước cần là số nguyên.');
  for(const key of ['constellations','grid','labels','transparent'])check(options[key]===undefined||typeof options[key]==='boolean','Tùy chọn Sky Star phải là true/false.');
  return{instant:date.toISOString(),lat:number(options.lat,-90,90,'Latitude'),lon:number(options.lon,-180,180,'Longitude'),size,
    background:color(options.background??options.bg,'#000000'),starColor:color(options.starColor??options.star,'#ffffff'),
    lineColor:color(options.lineColor??options.line,'#ffffff'),constellations:options.constellations!==false,
    grid:options.grid===true,labels:options.labels===true,transparent:options.transparent===true};
}
export function meanSiderealDegreesUtc(instant,longitude=0){
  const date=new Date(instant),jd=date.getTime()/86400000+2440587.5,t=(jd-2451545)/36525;
  return((280.46061837+360.98564736629*(jd-2451545)+.000387933*t*t-t*t*t/38710000+longitude)%360+360)%360;
}
function circularDifference(a,b){return Math.abs(((Number(a)-Number(b))%360+540)%360-180);}
export function verifyPanelObserver(celestial,instant,lat,lon){
  check(celestial&&typeof celestial.position==='function'&&typeof celestial.zenith==='function','Thiếu API kiểm chứng vị trí D3-Celestial.');
  const p=celestial.position(),z=celestial.zenith(),expected=meanSiderealDegreesUtc(instant,lon);
  check(p&&p.length>=2&&p.slice(0,2).every(Number.isFinite)&&Math.abs(p[0]-lat)<=.0001&&circularDifference(p[1],lon)<=.0001,'D3-Celestial chưa áp dụng đúng địa điểm.');
  check(z&&z.length>=2&&z.slice(0,2).every(Number.isFinite)&&circularDifference(z[0],expected)<=.05&&Math.abs(z[1]-lat)<=.01,'D3-Celestial chưa áp dụng đúng thời điểm UTC.');
  return{position:[p[0],p[1]],zenith:[z[0],z[1]],expectedRightAscension:expected,verified:true};
}

/** Render at the panel's native2400px, then copy to requested output size.
 * Each call owns one fresh same-origin frame: no stale singleton observer. */
export function renderPanelSky(canvas,options={}){
  check(canvas?.ownerDocument&&typeof canvas.getContext==='function','Thiếu canvas Sky Star.');
  const settings=panelSkyOptions(options),doc=canvas.ownerDocument,win=doc.defaultView;
  check(win&&FRAME.origin===win.location.origin,'Renderer Sky Star phải cùng nguồn với web.');
  const token=Array.from(crypto.getRandomValues(new Uint8Array(16)),n=>n.toString(16).padStart(2,'0')).join('');
  const frame=doc.createElement('iframe');frame.title='MITOM exact panel Sky renderer';frame.setAttribute('aria-hidden','true');frame.tabIndex=-1;
  frame.style.cssText='position:fixed;left:-20000px;top:0;width:2400px;height:2400px;opacity:0;pointer-events:none;border:0;';
  frame.src=FRAME.href+'#'+token;
  return new Promise((resolve,reject)=>{
    let ended=false,requested=false;
    const cleanup=()=>{clearTimeout(timer);win.removeEventListener('message',message);options.signal?.removeEventListener('abort',abort);frame.remove();};
    const finish=(error,value)=>{if(ended)return;ended=true;cleanup();error?reject(error):resolve(value);};
    const abort=()=>finish(Object.assign(Error('Đã hủy tạo Sky Star.'),{code:'CANCELLED'}));
    const timer=setTimeout(()=>finish(Error('D3-Celestial chưa hoàn tất trong60giây; không xuất bản đồ thay thế.')),60000);
    function message(event){
      if(event.origin!==FRAME.origin||event.source!==frame.contentWindow||event.data?.schema!==SCHEMA||event.data?.token!==token)return;
      const data=event.data;
      if(data.type==='ready'&&!requested){requested=true;frame.contentWindow.postMessage({schema:SCHEMA,token,type:'render',settings},FRAME.origin);}
      else if(data.type==='error')finish(Error(String(data.message||'D3-Celestial chưa render được.').slice(0,1200)));
      else if(data.type==='result'){
        try{
          const source=frame.contentDocument?.querySelector('canvas[data-sky-panel-output]');
          check(requested&&source&&Number.isInteger(source.width)&&Number.isInteger(source.height)&&source.width>=RENDER_SIZE&&source.width<=RENDER_SIZE+4&&source.height>=RENDER_SIZE&&source.height<=RENDER_SIZE+4,'Canvas native Sky Star chưa sẵn sàng.');
          check(data.metadata?.schema===SCHEMA&&data.metadata.observer?.verified===true&&data.metadata.instant===settings.instant,'Thiếu chứng thực địa điểm/thời điểm của bản đồ.');
          const context=canvas.getContext('2d');check(context,'Không có Canvas2D.');canvas.width=canvas.height=settings.size;
          context.clearRect(0,0,settings.size,settings.size);context.drawImage(source,0,0,settings.size,settings.size);
          finish(null,{...data.metadata,width:settings.size,height:settings.size,kind:'sky',utc:settings.instant,lat:settings.lat,lon:settings.lon,
            renderer:'installed-panel-d3-celestial',projection:'stereographic',starsRendered:data.metadata.starsRendered,
            pixelIdenticalAtNativeSize:'Same vendor/configuration; browser antialiasing/platform may differ.'});
        }catch(error){finish(error);}
      }
    }
    win.addEventListener('message',message);options.signal?.addEventListener('abort',abort,{once:true});
    if(options.signal?.aborted){abort();return;}
    doc.body.append(frame);
  });
}

async function verifiedResources(){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),30000);
  const digest=async bytes=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),n=>n.toString(16).padStart(2,'0')).join('');
  async function read(url,max=2*1024*1024){const response=await fetch(url,{cache:'force-cache',credentials:'omit',signal:controller.signal});check(response.ok,'Không nạp được tài nguyên Sky Star local.');const bytes=new Uint8Array(await response.arrayBuffer());check(bytes.length>0&&bytes.length<=max,'Tài nguyên Sky Star vượt giới hạn.');return bytes;}
  try{
    const manifest=JSON.parse(new TextDecoder().decode(await read(new URL('manifest.json',ASSETS))));
    check(manifest.schema===SCHEMA&&manifest.files&&Object.keys(manifest.files).length>=12,'Manifest Sky Star không đúng bộ panel.');
    const texts={},verified=[];
    await Promise.all([...LIBRARIES,...FILES.map(name=>'data/'+name)].map(async name=>{
      const entry=manifest.files[name];check(entry&&/^[a-f0-9]{64}$/.test(entry.sha256)&&Number.isSafeInteger(entry.bytes),'Thiếu SHA-256 Sky Star.');
      const bytes=await read(new URL(name,ASSETS));check(bytes.length===entry.bytes&&await digest(bytes)===entry.sha256,'Tài nguyên Sky Star sai SHA-256: '+name);
      if(name.startsWith('data/'))texts[name.slice(5)]=new TextDecoder().decode(bytes);
      verified.push({name,sha256:entry.sha256,bytes:entry.bytes});
    }));
    return{texts,verified};
  }finally{clearTimeout(timer);}
}
function muteCanvasText(canvas){
  const ctx=canvas?.getContext('2d');if(!ctx)return()=>{};
  const fill=ctx.fillText,stroke=ctx.strokeText,ownFill=Object.hasOwn(ctx,'fillText'),ownStroke=Object.hasOwn(ctx,'strokeText');
  ctx.fillText=()=>{};ctx.strokeText=()=>{};
  return()=>{if(ownFill)ctx.fillText=fill;else delete ctx.fillText;if(ownStroke)ctx.strokeText=stroke;else delete ctx.strokeText;};
}
export function createPanelSkyConfiguration(celestial,settings){
  const cfg=celestial.settings().set({width:RENDER_SIZE,container:'smCelestialMap',datapath:new URL('data/',ASSETS).href,
    projection:'stereographic',transform:'equatorial',geopos:[settings.lat,settings.lon],follow:'zenith',zoomlevel:null,zoomextend:10,
    interactive:false,form:false,advanced:false,controls:false,settimezone:false,disableAnimations:true});
  cfg.stars.show=true;cfg.stars.limit=6;cfg.stars.colors=false;cfg.stars.size=7;
  cfg.stars.names=false;cfg.stars.proper=false;cfg.stars.designation=false;cfg.stars.propername=false;cfg.stars.style.fill=settings.starColor;
  cfg.constellations.show=false;cfg.constellations.names=false;cfg.constellations.lines=settings.constellations;cfg.constellations.bounds=false;
  cfg.constellations.lineStyle.stroke=settings.lineColor;cfg.constellations.lineStyle.opacity=.6;cfg.constellations.lineStyle.width=1.5;
  cfg.dsos.show=false;cfg.dsos.names=false;cfg.planets.show=true;cfg.planets.names=false;cfg.planets.symbolType='disk';cfg.mw.show=false;
  for(const name of ['graticule','equatorial','ecliptic','galactic','supergalactic'])if(cfg.lines[name])cfg.lines[name].show=name==='graticule'&&settings.grid;
  cfg.background.fill=settings.background;cfg.background.stroke=settings.background;return cfg;
}
async function renderInsideFrame(settings){
  const resources=await verifiedResources(),celestial=window.Celestial,d3=window.d3;
  check(celestial&&d3&&d3.geo&&typeof d3.geo.projection==='function','Không nạp được đúng D3-Celestial của panel.');
  try{Object.defineProperty(window,'devicePixelRatio',{value:1,configurable:true});}catch(ignore){}
  check(window.devicePixelRatio===1,'Không khóa được canvas native2400px.');
  const host=document.querySelector('#smCelestialMap');host.replaceChildren();
  const cfg=createPanelSkyConfiguration(celestial,settings),seen=new Set(),expected=new Set(FILES),originalJson=d3.json;
  let active=0,sealed=false,restoreText=()=>{},settled=false;
  const loaded=new Promise((resolve,reject)=>{
    const complete=()=>{if(!settled&&sealed&&active===0){settled=true;seen.size===expected.size?resolve():reject(Error('D3-Celestial chưa yêu cầu đủ9catalog panel.'));}};
    d3.json=function(url,callback){
      const resolved=new URL(url,location.href),name=resolved.pathname.split('/').pop();
      if(resolved.origin!==ASSETS.origin||!expected.has(name)||typeof callback!=='function')throw Error('Celestial yêu cầu tài nguyên ngoài bộ panel.');
      seen.add(name);active++;const request={aborted:false,abort(){this.aborted=true;}};
      setTimeout(()=>{try{if(!request.aborted)callback.call(d3,null,JSON.parse(resources.texts[name]));}catch(error){if(!settled){settled=true;reject(error);}}finally{active--;complete();}},0);
      return request;
    };
    try{celestial.container=null;celestial.display(cfg);restoreText=muteCanvasText(host.querySelector('canvas'));sealed=true;complete();}catch(error){settled=true;reject(error);}
  });
  try{
    await loaded;d3.json=originalJson;
    celestial.location([settings.lat,settings.lon]);celestial.date(new Date(settings.instant));
    const observer=verifyPanelObserver(celestial,settings.instant,settings.lat,settings.lon);
    const started=performance.now();let source=null;
    do{
      celestial.redraw();source=host.querySelector('canvas');
      // Celestial adds its authored1.5px background stroke to requested width;
      // canvas integer coercion can therefore produce2401x2402, just as panel.
      if(source&&source.width>=RENDER_SIZE&&source.width<=RENDER_SIZE+4&&source.height>=RENDER_SIZE&&source.height<=RENDER_SIZE+4&&performance.now()-started>=250)break;
      check(performance.now()-started<8000,'Celestial chưa hoàn tất canvas native.');await new Promise(resolve=>setTimeout(resolve,50));
    }while(true);
    const output=document.createElement('canvas');output.width=source.width;output.height=source.height;output.dataset.skyPanelOutput='1';
    const ctx=output.getContext('2d');ctx.drawImage(source,0,0);
    const pixels=ctx.getImageData(0,0,output.width,output.height).data;
    const probe=document.createElement('canvas');probe.width=probe.height=128;const pctx=probe.getContext('2d');pctx.drawImage(output,0,0,128,128);
    const sampled=pctx.getImageData(0,0,128,128).data;let nonBackground=0;
    for(let i=0;i<sampled.length;i+=4)if(Math.abs(sampled[i]-sampled[0])+Math.abs(sampled[i+1]-sampled[1])+Math.abs(sampled[i+2]-sampled[2])+Math.abs(sampled[i+3]-sampled[3])>18&&++nonBackground>=12)break;
    probe.width=probe.height=1;
    check(nonBackground>=12,'Canvas chưa có sao/hành tinh/chòm sao; không xuất nền rỗng.');
    if(settings.transparent){for(let i=0;i<pixels.length;i+=4)pixels[i+3]=Math.round(.299*pixels[i]+.587*pixels[i+1]+.114*pixels[i+2]);ctx.putImageData(new ImageData(pixels,output.width,output.height),0,0);}
    if(settings.labels){ctx.fillStyle=settings.starColor;ctx.textAlign='center';ctx.textBaseline='middle';ctx.font='24px sans-serif';ctx.fillText('N',output.width/2,26);ctx.fillText('S',output.width/2,output.height-26);ctx.fillText('E',26,output.height/2);ctx.fillText('W',output.width-26,output.height/2);}
    document.body.append(output);
    return{schema:SCHEMA,instant:settings.instant,observer,nativeRequestedWidth:RENDER_SIZE,nativeWidth:output.width,nativeHeight:output.height,renderDevicePixelRatio:1,
      starsRendered:JSON.parse(resources.texts['stars.6.json']).features.filter(feature=>Number.isFinite(feature.properties.mag)&&feature.properties.mag<=6&&celestial.clip(feature.geometry.coordinates)).length,
      catalogsLoaded:[...seen].sort(),verifiedResources:resources.verified,
      nativeConfiguration:{projection:'stereographic',transform:'equatorial',follow:'zenith',starsLimit:6,starsSize:7,starsExponent:cfg.stars.exponent,
        planets:true,milkyWay:false,labels:settings.labels,grid:settings.grid,constellations:settings.constellations},
      fonts:'Panel disables celestial text labels; optionalNSEW overlay is browser-only.',artworkUploaded:false,photoshopRequired:false};
  }finally{d3.json=originalJson;restoreText();}
}
export function installPanelSkyFrame(){
  const token=location.hash.slice(1);if(window.parent===window||!/^[a-f0-9]{32}$/.test(token))return;
  let used=false;const send=(type,values={})=>parent.postMessage({schema:SCHEMA,token,type,...values},location.origin);
  window.addEventListener('message',async event=>{
    if(used||event.source!==parent||event.origin!==location.origin||event.data?.schema!==SCHEMA||event.data.token!==token||event.data.type!=='render')return;
    used=true;try{const metadata=await renderInsideFrame(panelSkyOptions(event.data.settings));send('result',{metadata});}catch(error){send('error',{message:String(error?.message||error)});}
  });
  send('ready');
}
if(typeof document!=='undefined'&&document.documentElement.dataset.skyPanelFrame==='1')installPanelSkyFrame();
