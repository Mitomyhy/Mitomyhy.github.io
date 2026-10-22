/* View-only zoom. Never changes the raster or downloaded PNG. */
export function zoomAt(view,point,delta) {
  if(![view.scale,view.x,view.y,point.x,point.y,delta].every(Number.isFinite)||view.scale<=0)throw Error('Mức zoom không hợp lệ.');
  const scale=Math.max(.5,Math.min(8,view.scale*Math.exp(-Math.max(-300,Math.min(300,delta))*.002)));
  const ratio=scale/view.scale;
  return {scale,x:point.x-(point.x-view.x)*ratio,y:point.y-(point.y-view.y)*ratio};
}
export function createWorkspaceZoom(stage,canvas,{isEnabled=()=>true,onChange=()=>{}}={}) {
  if(!stage?.addEventListener||!canvas?.style)throw Error('Thiếu vùng zoom.');
  let view={scale:1,x:0,y:0},drag=null,disposed=false;
  function paint(){canvas.style.transformOrigin='center';canvas.style.transform=`translate(${view.x}px, ${view.y}px) scale(${view.scale})`;stage.classList.toggle('workspace-can-pan',isEnabled());onChange({...view});}
  function wheel(event){if(disposed||!isEnabled())return;event.preventDefault();const rect=stage.getBoundingClientRect(),delta=event.deltaY*(event.deltaMode===1?16:event.deltaMode===2?rect.height:1);view=zoomAt(view,{x:event.clientX-rect.left-rect.width/2,y:event.clientY-rect.top-rect.height/2},delta);paint();}
  function down(event){if(disposed||!isEnabled()||event.button!==0)return;event.preventDefault();drag={x:event.clientX,y:event.clientY,prior:{...view},id:event.pointerId};stage.setPointerCapture?.(event.pointerId);}
  function move(event){if(!drag||!isEnabled()||event.pointerId!==drag.id)return;view={...view,x:drag.prior.x+event.clientX-drag.x,y:drag.prior.y+event.clientY-drag.y};paint();}
  function up(){drag=null;}
  stage.addEventListener('wheel',wheel,{passive:false});stage.addEventListener('pointerdown',down);stage.addEventListener('pointermove',move);
  for(const event of ['pointerup','pointercancel','lostpointercapture'])stage.addEventListener(event,up);
  return {reset(){view={scale:1,x:0,y:0};drag=null;paint();},refresh:paint,state(){return {...view};},dispose(){disposed=true;stage.removeEventListener('wheel',wheel);stage.removeEventListener('pointerdown',down);stage.removeEventListener('pointermove',move);for(const event of ['pointerup','pointercancel','lostpointercapture'])stage.removeEventListener(event,up);canvas.style.transform='';stage.classList.remove('workspace-can-pan');}};
}
