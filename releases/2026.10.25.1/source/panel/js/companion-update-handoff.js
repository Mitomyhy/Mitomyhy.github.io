(function(root,factory){
  'use strict';
  var api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  if(root&&root.document){var start=function(){api.start(root);};if(root.document.readyState==='loading')root.document.addEventListener('DOMContentLoaded',start,{once:true});else start();}
})(typeof window!=='undefined'?window:globalThis,function(){
  'use strict';
  var HELPER_SHA='6671767bace45abff3ee915f5c1b34ef499f5dc2178edb97756f54d68e34f549';
  var MANIFEST_SHA='487684c5bb7cfea04b73dae17e59e413121747e1869cf7750ea5bab7c69280c1';
  function failure(code){var e=new Error(code);e.code=code;return e;}
  function plain(fs,path,file,missing){
    if(!path.isAbsolute(file)||/^\\\\|^\/\//.test(file)||file.length>220||file===path.parse(file).root)throw failure('UNSAFE_RUNTIME_PATH');
    var current=path.parse(file).root;
    file.slice(current.length).split(path.sep).forEach(function(part){if(!part||part==='.'||part==='..')throw failure('UNSAFE_RUNTIME_PATH');current=path.join(current,part);var stat;try{stat=fs.lstatSync(current);}catch(e){if(missing&&e.code==='ENOENT')return;throw failure('RUNTIME_MISSING');}if(stat.isSymbolicLink())throw failure('REPARSE_RUNTIME_PATH');});
    return file;
  }
  function report(value){
    if(!value||value.schema!==1||value.target_version!=='1.0.27'||['updated','updated_pending','already_current','not_registered','pending'].indexOf(value.status)<0||!Number.isInteger(value.updated_roots)||value.updated_roots<0||value.updated_roots>16||typeof value.reload_required!=='boolean'||!Array.isArray(value.pending)||value.pending.length>96||value.pending.some(function(row){return !row||typeof row!=='object'||!/^[A-Z_]{1,64}$/.test(row.reason||'');}))throw failure('HANDOFF_RESULT');
    return value;
  }
  function invoke(deps){
    return new Promise(function(resolve,reject){
      var child,timer,done=false,output='',errors=0;
      function finish(error,value){if(done)return;done=true;if(timer)deps.clearTimeout(timer);error?reject(error):resolve(value);}
      try{
        var fs=deps.fs,path=deps.path,local=deps.localAppData;
        if(typeof local!=='string'||!local)throw failure('LOCAL_RUNTIME_UNAVAILABLE');
        plain(fs,path,local);
        var core=path.join(local,'UOY','Launcher','current'),python=path.join(core,'python','python.exe');
        var helper=path.join(core,'companion-handoff','companion_handoff.py'),manifest=path.join(core,'companion-handoff','companion-handoff-manifest.json');
        [python,helper,manifest].forEach(function(file){plain(fs,path,file);if(!fs.lstatSync(file).isFile())throw failure('UNSAFE_RUNTIME_FILE');});
        [[helper,HELPER_SHA],[manifest,MANIFEST_SHA]].forEach(function(pin){var data=fs.readFileSync(pin[0]);if(data.length>65536||deps.crypto.createHash('sha256').update(data).digest('hex')!==pin[1])throw failure('HANDOFF_INTEGRITY');});
        // Only the recipient's managed runtime and fixed helper are invoked.
        // No Photoshop evalScript, browser API, arbitrary executable or shell.
        child=deps.spawn(python,['-I','-S',helper],{cwd:core,windowsHide:true,shell:false,stdio:['ignore','pipe','pipe']});
        timer=deps.setTimeout(function(){finish(failure('HANDOFF_TIMEOUT'));},120000);
        child.stdout.on('data',function(data){if(done)return;output+=String(data);if(output.length>65536)finish(failure('HANDOFF_RESULT_LIMIT'));});
        child.stderr.on('data',function(data){errors+=data.length;});
        child.on('error',function(){finish(failure('HANDOFF_UNAVAILABLE'));});
        child.on('close',function(code){if(done)return;if(code!==0){finish(failure('HANDOFF_UNAVAILABLE'));return;}try{finish(null,report(JSON.parse(output)));}catch(_){finish(failure('HANDOFF_RESULT'));}});
      }catch(error){finish(error);}
    });
  }
  function display(win,value,error){
    if(!error&&(!value||['already_current','not_registered'].indexOf(value.status)>=0))return;
    var doc=win.document,node=doc.getElementById('uoy-companion-update-status');
    if(!node){node=doc.createElement('div');node.id='uoy-companion-update-status';node.setAttribute('role','status');node.style.cssText='margin:6px;padding:9px;border:1px solid #76949a;border-radius:4px;background:#183239;color:#e8f4f4;font:12px/1.5 sans-serif;white-space:pre-wrap';doc.body.insertBefore(node,doc.body.firstChild);}
    var lines=[];
    if(value&&value.updated_roots>0)lines.push('Đã cập nhật mã Base Companion 1.0.27 tại '+value.updated_roots+' thư mục Chrome đang dùng.');
    if(value&&value.reload_required)lines.push('Bấm Tải lại / Reload UOY Base Companion tại chrome://extensions, sau đó tải lại riêng tab Base một lần. Không Xóa/Quên hoặc ghép lại.');
    if(value&&typeof value.backup==='string'&&value.backup.length<=512)lines.push('Sao lưu riêng Companion: '+value.backup+' (Rollback Panel không tự hoàn tác mục này).');
    if(error||value&&value.pending.length){lines.push('Còn mục chưa xác minh; file tùy chỉnh được giữ nguyên, chưa báo hoàn tất.');var code=error&&error.code||value.pending[0].reason;if(/^[A-Z_]{1,64}$/.test(code||''))lines.push('Mã kiểm tra: '+code);}
    if(value)value.pending.slice(0,3).forEach(function(row){if(typeof row.root==='string'&&row.root.length<=220)lines.push('Giữ nguyên: '+row.root);});
    node.textContent=lines.join('\n');
  }
  function start(win){
    if(win.__uoyCompanionHandoffR65)return;win.__uoyCompanionHandoffR65=true;
    try{
      var req=win.cep_node&&win.cep_node.require||win.require;
      if(typeof req!=='function')throw failure('CEP_NODE_UNAVAILABLE');
      var process=req('process');
      invoke({fs:req('fs'),path:req('path'),crypto:req('crypto'),spawn:req('child_process').spawn,localAppData:process.env.LOCALAPPDATA,setTimeout:win.setTimeout.bind(win),clearTimeout:win.clearTimeout.bind(win)}).then(function(value){display(win,value);},function(error){display(win,null,error);});
    }catch(error){display(win,null,error);}
  }
  return {invoke:invoke,validateReport:report,start:start,HELPER_SHA256:HELPER_SHA,MANIFEST_SHA256:MANIFEST_SHA};
});
