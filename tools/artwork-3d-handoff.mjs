// Explicit GPT/Codex button handoff. Loading/acknowledgement never implicitly
// changes precision, device, engine settings, or retries a completed AI run.
export async function runArtwork3DHandoff(item,file,actions,signal) {
  if(!item||item.kind!=='artwork'||item.autoGenerate!==true||!['acrylic','ceramic_circle','ceramic_heart'].includes(item.templateKey)||!['cpu','gpu'].includes(item.device)||!(file instanceof Blob))throw Error('Artwork 3D handoff không hợp lệ.');
  const check=()=>{if(signal?.aborted)throw Object.assign(Error('Đã hủy nhận artwork / Generate 3D.'),{code:'CANCELLED'});};
  check();await actions.openTemplate(item.templateKey);check();
  await actions.acceptFile(file);check();
  // Decode succeeds before native RAM can be released. Once acknowledged, an
  // inference failure/cancel keeps the web source for an explicit manual retry.
  await actions.ack(item.id);actions.accepted();check();
  await actions.generate(true,item.device);check();
}
