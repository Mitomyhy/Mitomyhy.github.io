"""Authenticated loopback desktop workspace; all projects stay on this machine."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import multiprocessing
import os
from pathlib import Path
import queue
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import unquote,urlsplit
import uuid
import webbrowser
from io import BytesIO
import hashlib
import zipfile

try:
    from .app_version import VERSION
    from . import projects
    from .worker import run_operation
except ImportError:
    from app_version import VERSION
    import projects
    from worker import run_operation

RESOURCES=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))

class Conflict(ValueError):pass

def data_root():
    base=os.environ.get('LOCALAPPDATA')
    if not base:raise RuntimeError('Windows LocalAppData is unavailable. Use --data-root for a local workspace.')
    return Path(base)/'MITOM'/'MiFont'

def safe_filename(value):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',str(value)).strip(' .')[:100] or'MiFont'

class Session:
    def __init__(self,root=None):
        self.token=secrets.token_hex(32)
        self.store=projects.ProjectStore((Path(root) if root else data_root())/'Projects')
        self.temp=tempfile.TemporaryDirectory(prefix='mifont-session-')
        self.root=Path(self.temp.name)
        self.lock=threading.RLock();self.jobs={}
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='mifont')
        self.closed=False;self.last_request=time.monotonic()

    def project(self,key):return self.store.view(self.store.load(projects.identity(key)))

    def mutable(self,key,revision=None):
        projects.identity(key)
        if any(job['project_id']==key and job['status'] in {'queued','running'} for job in self.jobs.values()):
            raise Conflict('This project is processing. Wait or cancel its job before editing.')
        value=self.store.load(key)
        if revision is not None and revision!=value['revision']:raise Conflict('Project changed. Reload it before saving.')
        return value

    def update(self,request):
        with self.lock,self.store.lock:
            project=self.mutable(request.get('project_id'),request.get('revision'))
            for key in ('name','family','style'):
                if key in request:project[key]=projects.text(request[key],key)
            if 'charset' in request:project['charset']=projects.charset(request['charset'])
            return self.store.view(self.store.save(project))

    def upload(self,key,name,data):
        with self.lock,self.store.lock:
            self.mutable(key)
            project,image=self.store.upload(key,name,data)
            value=self.store.view(project)
            return {'project':value,'image':next(row for row in value['images'] if row['id']==image['id'])}

    def remove_image(self,request):
        with self.lock,self.store.lock:
            project=self.mutable(request.get('project_id'),request.get('revision'))
            self.store.remove_image(project,request.get('image_id'))
            return self.store.view(self.store.save(project))

    def glyph_update(self,request,remove=False):
        with self.lock,self.store.lock:
            project=self.mutable(request.get('project_id'),request.get('revision'))
            glyph_id=projects.identity(request.get('glyph_id'))
            entry=next((item for item in project['glyphs'] if item['id']==glyph_id),None)
            if entry is None:raise ValueError('Glyph not found.')
            if remove:project['glyphs']=[item for item in project['glyphs'] if item['id']!=glyph_id]
            else:
                record=self.store.record(project['id'],glyph_id)
                try:from .font_export import normalize_glyph
                except ImportError:from font_export import normalize_glyph
                record=normalize_glyph(record)
                if 'lsb' in request:
                    desired=projects.validate_record({**record,'lsb':request['lsb']})['lsb']
                    delta=desired-record['lsb']
                    record['recording']=[[operation,[[point[0]+delta,point[1]] if point is not None else None for point in points]]
                                         for operation,points in record['recording']]
                for key in ('char','advance_width'):
                    if key in request:record[key]=request[key]
                record=normalize_glyph(record)
                self.store.put_glyph(project,record,glyph_id=glyph_id,image_id=entry.get('image_id'),box=entry.get('box'))
            return self.store.view(self.store.save(project))

    def submit(self,kind,request):
        with self.lock,self.store.lock:
            project=self.mutable(request.get('project_id'),request.get('revision'))
            if len(self.jobs)>=200:raise ValueError('Restart MiFont to begin a fresh job session.')
            if sum(job['status'] in {'queued','running'} for job in self.jobs.values())>=10:raise ValueError('The job queue is full.')
            options=request.get('options',{})
            if not isinstance(options,dict):raise ValueError('Options must be an object.')
            payload={'options':deepcopy(options)}
            def image(key):
                selected=next((item for item in project['images'] if item['id']==key),None)
                if selected is None:raise ValueError('Project image not found.')
                return {'id':selected['id'],'path':str(self.store.image_path(project,selected)),
                        'width':selected['width'],'height':selected['height']}
            if kind=='generate':
                if not project['images']:raise ValueError('Upload at least one image before generating the font.')
                if len(project['images'])>12:raise ValueError('Generate from at most12 images at once. Remove extra images or start another font.')
                if len(project['builds'])>=100:raise ValueError('This font has100 saved builds. Start a new font to keep its history available.')
                try:from .auto_pipeline import normalize_options
                except ImportError:from auto_pipeline import normalize_options
                payload['options']=normalize_options(options)
                metadata=request.get('metadata',{})
                if not isinstance(metadata,dict):raise ValueError('Font metadata must be an object.')
                payload['metadata']={'family':projects.text(metadata.get('family'),'Font family',project['family'],80),
                                     'style':projects.text(metadata.get('style'),'Font style',project['style'],40)}
                payload['images']=[image(item['id']) for item in project['images']]
                payload['reference_cache_dir']=str(self.store.root.parent/'ReferenceCache')
            elif kind=='analyze':
                keys=request.get('image_ids') or [item['id'] for item in project['images']]
                if not isinstance(keys,list) or not 1<=len(keys)<=50:raise ValueError('Upload and select at least one image.')
                payload['images']=[image(key) for key in keys]
            elif kind=='extract':
                payload['image']=image(request.get('image_id'))
                regions=request.get('regions')
                if not isinstance(regions,list) or not 1<=len(regions)<=512:raise ValueError('Select between 1 and 512 glyph regions.')
                normalized=[]
                text=options.get('text','')
                if not isinstance(text,str):raise ValueError('Guided text must be a string.')
                for index,region in enumerate(regions):
                    if not isinstance(region,dict):raise ValueError('Invalid glyph region.')
                    box=region.get('box')
                    if not isinstance(box,list) or len(box)!=4 or any(type(value) is not int for value in box):raise ValueError('Crop box uses integer x,y,width,height.')
                    x,y,width,height=box
                    if x<0 or y<0 or width<1 or height<1 or x+width>payload['image']['width'] or y+height>payload['image']['height']:raise ValueError('Crop box lies outside its image.')
                    value=deepcopy(region);value['char']=value.get('char',text[index] if index<len(text) else'')
                    if not isinstance(value['char'],str) or len(value['char'])>1:raise ValueError('Map one character to each region.')
                    normalized.append(value)
                payload['regions']=normalized
            elif kind in {'complete','build','research'}:
                assigned=[item for item in project['glyphs'] if item.get('char')]
                if not assigned and kind!='research':raise ValueError('Extract and assign at least one observed character first.')
                payload['records']=[self.store.record(project['id'],item['id']) for item in assigned]
                if kind=='research':
                    query=request.get('query','')
                    if not isinstance(query,str) or len(query)>200:raise ValueError('Research query is too long.')
                    payload['query']=query
                    payload['cache_dir']=str(self.store.root.parent/'ReferenceCache')
                elif kind=='complete':
                    payload['options'].setdefault('target_chars',project['charset'])
                    if options.get('mode')=='reference':
                        reference_id=options.get('reference_id')
                        if not isinstance(reference_id,str) or not any(card.get('id')==reference_id for card in project.get('references',[])):
                            raise ValueError('Research and select a verified open-font reference first.')
                        payload['references']=deepcopy(project['references'])
                        payload['cache_dir']=str(self.store.root.parent/'ReferenceCache')
                else:
                    metadata=request.get('metadata',{})
                    if not isinstance(metadata,dict):raise ValueError('Font metadata must be an object.')
                    payload['metadata']={'family':projects.text(metadata.get('family'),'Font family',project['family'],80),
                        'style':projects.text(metadata.get('style'),'Font style',project['style'],40)}
                    active_references={details['reference_id'] for record in payload['records']
                                       for details in (record.get('provenance',{}),record.get('generation',{}))
                                       if isinstance(details,dict) and details.get('reference_id')}
                    obligations=[value for value in project.get('license_obligations',[]) if value.get('id') in active_references]
                    if active_references:
                        if {value.get('id') for value in obligations}!=active_references:raise ValueError('Reference license information is missing. Research and complete those glyphs again.')
                        payload['metadata'].update(license_obligations=obligations,requires_renamed_family=True)
            else:raise ValueError('Unknown MiFont operation.')
            key=uuid.uuid4().hex;folder=self.root/key;folder.mkdir()
            job={'id':key,'project_id':project['id'],'revision':project['revision'],'kind':kind,'status':'queued',
                 'progress':0,'message':'Waiting in queue','message_key':'generate_queued' if kind=='generate' else '',
                 'result':None,'project':None,'folder':folder,'cancel':threading.Event()}
            self.jobs[key]=job
            self.executor.submit(self._run,job,payload)
            return self.snapshot(key)

    def _run(self,job,payload):
        process=messages=None
        try:
            with self.lock:
                if job['cancel'].is_set():return
                job.update(status='running',message='Preparing local job',message_key='generate_analyze' if job['kind']=='generate' else '')
            context=multiprocessing.get_context('spawn');messages=context.Queue()
            process=context.Process(target=run_operation,args=(job['kind'],payload,str(job['folder']),messages),daemon=True)
            process.start();deadline=time.monotonic()+900
            while True:
                if job['cancel'].is_set():
                    process.terminate()
                    with self.lock:job.update(status='cancelled',message='Cancelled',message_key='generate_cancelled' if job['kind']=='generate' else '')
                    return
                if time.monotonic()>deadline:raise TimeoutError('This operation exceeded 15 minutes. Use a smaller image or fewer glyphs.')
                try:event=messages.get(timeout=.2)
                except queue.Empty:
                    if process.is_alive():continue
                    try:event=messages.get(timeout=.5)
                    except queue.Empty:raise RuntimeError('The local font worker stopped before producing a result.')
                if event.get('kind')=='progress':
                    with self.lock:job.update(progress=max(0,min(99,int(event.get('progress',0)))),message=str(event.get('message','Processing'))[:300],
                                              message_key=str(event.get('message_key',''))[:100])
                elif event.get('kind')=='done':
                    path=job['folder']/'result.json'
                    if path.stat().st_size>32*1024*1024:raise ValueError('Job outline result exceeds 32MB.')
                    result=json.loads(path.read_text(encoding='utf-8'))
                    with self.lock,self.store.lock:
                        project=self.store.load(job['project_id'])
                        if project['revision']!=job['revision']:raise Conflict('Project changed in another window; results were kept separate.')
                        if job['cancel'].is_set():job.update(status='cancelled',message='Cancelled');return
                        if job['kind']=='generate':
                            job.update(message='Saving complete font results',message_key='generate_save')
                            result=self._stage_generated(project,job,payload,result)
                        elif job['kind']=='analyze':
                            for analyzed in result['images']:
                                image=next(item for item in project['images'] if item['id']==analyzed['image_id'])
                                image['candidates']=analyzed.get('regions',[])
                                image['analysis']={key:value for key,value in analyzed.items() if key not in {'regions','image_id'}}
                        elif job['kind']=='extract':
                            if len(project['glyphs'])+len(result['glyphs'])>512:raise ValueError('Project exceeds 512 glyph records.')
                            for glyph in result['glyphs']:self.store.put_glyph(project,glyph['record'],image_id=glyph['image_id'],box=glyph['box'])
                        elif job['kind']=='complete':
                            observed={item['char'] for item in project['glyphs'] if item.get('char') and item.get('origin')=='observed'}
                            project['glyphs']=[item for item in project['glyphs'] if item.get('char') or item.get('origin')!='generated']
                            for record in result['glyphs']:
                                if record.get('origin')=='generated' and record.get('char') not in observed:
                                    existing=next((item for item in project['glyphs'] if item.get('char')==record.get('char') and item.get('origin')=='generated'),None)
                                    if existing and self.store.record(project['id'],existing['id'])==record:continue
                                    if existing is None and len(project['glyphs'])>=512:raise ValueError('Project exceeds512 glyph records.')
                                    self.store.put_glyph(project,record,glyph_id=existing['id'] if existing else None)
                            project['completion']={key:value for key,value in result.items() if key!='glyphs'}
                            obligation=result.get('provenance',{}).get('license_obligations')
                            if obligation:
                                retained=[item for item in project.get('license_obligations',[]) if item.get('id')!=obligation.get('id')]
                                project['license_obligations']=retained+[obligation]
                        elif job['kind']=='research':
                            project['references']=result.get('references',[])
                            project['research']={key:value for key,value in result.items() if key!='references'}
                        else:
                            build_id=uuid.uuid4().hex
                            for format in ('ttf','otf'):
                                source=job['folder']/('font.'+format)
                                if not 100<=source.stat().st_size<=64*1024*1024:raise ValueError('Font output is empty or oversized.')
                                shutil.copyfile(source,self.store.folder(project['id'])/'builds'/(build_id+'.'+format))
                            build={'id':build_id,**payload['metadata'],'metrics':result['metrics'],'created_at':projects.now(),
                                   'coverage':self.store.view(project)['coverage']}
                            project['builds'].append(build);result={**result,'build_id':build_id,'coverage':build['coverage']}
                        if job['cancel'].is_set():
                            job.update(status='cancelled',message='Cancelled',message_key='generate_cancelled' if job['kind']=='generate' else '');return
                        if self.store.load(project['id'])['revision']!=job['revision']:
                            raise Conflict('Project changed in another window; results were kept separate.')
                        saved=self.store.save(project)
                        job.update(status='done',progress=100,message='Ready',message_key='generate_ready' if job['kind']=='generate' else '',
                                   result={key:value for key,value in result.items() if key not in ({'glyphs','images'} if job['kind']=='generate' else {'glyphs'})},project=self.store.view(saved))
                    return
                else:raise RuntimeError(event.get('message','The local font operation failed.'))
        except Exception as exc:
            with self.lock:
                if job['cancel'].is_set():job.update(status='cancelled',message='Cancelled',message_key='generate_cancelled' if job['kind']=='generate' else '')
                else:job.update(status='error',message=str(exc)[:1200],message_key='generate_error' if job['kind']=='generate' else '')
        finally:
            if process is not None and process.pid is not None:
                process.join(timeout=2)
                if process.is_alive():process.terminate();process.join(timeout=2)
                process.close()
            if messages is not None:messages.close()

    def _stage_generated(self,project,job,payload,result):
        """Validate a complete worker result before the one project.json commit.

        Glyph blobs and new build files are immutable staged assets. Exceptions
        or cancellation before save leave the old project/builds referenced.
        """
        if not isinstance(result,dict) or not isinstance(result.get('glyphs'),list) or not 95<=len(result['glyphs'])<=512:
            raise ValueError('Generation did not produce the complete95-character font. Try a clearer alphabet image.')
        try:from .font_export import normalize_glyph,_metadata,_notices
        except ImportError:from font_export import normalize_glyph,_metadata,_notices
        from fontTools.ttLib import TTFont
        metadata=result.get('font_metadata',payload['metadata'])
        if not isinstance(metadata,dict):raise ValueError('Generated font metadata is invalid.')
        metadata={**payload['metadata'],**deepcopy(metadata)}
        obligations=deepcopy(result.get('license_obligations',metadata.get('license_obligations',[])))
        if isinstance(obligations,dict):obligations=[obligations]
        if not isinstance(obligations,list) or len(obligations)>16:raise ValueError('Generated font license metadata is invalid.')
        metadata['license_obligations']=obligations
        if obligations:
            metadata['requires_renamed_family']=True
        family,style,*_=_metadata(metadata)
        metadata.update(family=family,style=style)
        notices=_notices(metadata)
        sources={image['id']:image for image in project['images']}
        normalized=[];characters=set();active_references=set()
        for item in result['glyphs']:
            if not isinstance(item,dict) or not isinstance(item.get('record'),dict):raise ValueError('Generation returned an invalid glyph.')
            record=normalize_glyph(item['record']);char=record['char']
            if not char or char in characters or (char!=' ' and not record['recording']):raise ValueError('Generated glyph mappings are incomplete or duplicated.')
            characters.add(char)
            image_id=item.get('image_id');box=item.get('box')
            if image_id is not None:
                if image_id not in sources:raise ValueError('Generated glyph references an unknown source image.')
                if not isinstance(box,list) or len(box)!=4 or any(type(value)is not int for value in box):raise ValueError('Generated source crop is invalid.')
                x,y,width,height=box;source=sources[image_id]
                if x<0 or y<0 or width<1 or height<1 or x+width>source['width'] or y+height>source['height']:raise ValueError('Generated source crop lies outside its image.')
            elif box is not None:raise ValueError('A generated source crop needs its project image.')
            for details in (record.get('provenance',{}),record.get('generation',{})):
                if isinstance(details,dict) and details.get('reference_id'):active_references.add(details['reference_id'])
            normalized.append((record,image_id,box))
        if not set(projects.ASCII)<=characters:raise ValueError('The generated font is missing required letters, numbers or punctuation.')
        if not active_references<={item.get('id') for item in obligations}:
            raise ValueError('Generated reference glyphs are missing their original license notices.')
        expected_cmap={ord(char) for char in characters}
        files={}
        for format,table in (('ttf','glyf'),('otf','CFF ')):
            path=job['folder']/('font.'+format)
            if not path.is_file() or not 100<=path.stat().st_size<=64*1024*1024:raise ValueError('The generated font file is empty or oversized.')
            data=path.read_bytes()
            with TTFont(BytesIO(data),lazy=False) as font:
                font.ensureDecompiled()
                if table not in font or font['head'].unitsPerEm!=1000 or set(font.getBestCmap() or {})!=expected_cmap:
                    raise ValueError('The generated font file failed outline or full-character verification.')
                if font['name'].getDebugName(1)!=family or font['name'].getDebugName(2)!=style:
                    raise ValueError('The generated font family metadata does not match the result.')
                if notices.get('license_text') and notices['license_text'] not in (font['name'].getDebugName(13) or ''):
                    raise ValueError('The generated font is missing its embedded source license.')
            files[format]=data
        analyses=result.get('images',[])
        if not isinstance(analyses,list):raise ValueError('Generated image analysis is invalid.')
        for analyzed in analyses:
            if not isinstance(analyzed,dict) or analyzed.get('image_id') not in sources:raise ValueError('Generation returned an unknown image analysis.')
            source=sources[analyzed['image_id']]
            source['candidates']=deepcopy(analyzed.get('regions',[]))
            source['analysis']={key:deepcopy(value) for key,value in analyzed.items() if key not in {'image_id','regions'}}
        project['glyphs']=[]
        for record,image_id,box in normalized:
            if job['cancel'].is_set():raise RuntimeError('Cancelled before saving the completed font.')
            self.store.put_glyph(project,record,image_id=image_id,box=box)
        project.update(family=family,style=style,charset=projects.ASCII)
        old_obligations={item.get('id'):item for item in project.get('license_obligations',[])}
        old_obligations.update({item.get('id'):item for item in obligations})
        if old_obligations:project['license_obligations']=list(old_obligations.values())
        automation=deepcopy(result.get('automation',{}));warnings=deepcopy(result.get('warnings',[]))
        project['automation']={'details':automation,'warnings':warnings}
        project['completion']={'warnings':warnings,'provenance':deepcopy(result.get('provenance',{}))}
        build_id=uuid.uuid4().hex
        for format,data in files.items():
            (self.store.folder(project['id'])/'builds'/(build_id+'.'+format)).write_bytes(data)
        coverage=self.store.view(project)['coverage']
        build={'id':build_id,**metadata,'metrics':deepcopy(result.get('metrics',{})),
               'coverage':coverage,'created_at':projects.now(),'automation':automation,'warnings':warnings,
               'generation_options':deepcopy(payload['options']),
               'source_image_ids':[item['id'] for item in payload['images']]}
        project['builds'].append(build)
        return {**result,'build_id':build_id,'coverage':coverage,'font_metadata':metadata}

    def snapshot(self,key):
        with self.lock:
            job=self.jobs.get(projects.identity(key))
            if not job:raise ValueError('Job not found.')
            return {name:deepcopy(job[name]) for name in ('id','project_id','kind','status','progress','message','message_key','result','project')}

    def cancel(self,key):
        with self.lock:
            job=self.jobs.get(projects.identity(key))
            if not job:raise ValueError('Job not found.')
            if job['status'] in {'queued','running'}:
                job['cancel'].set();job.update(status='cancelled',message='Cancelled',message_key='generate_cancelled' if job['kind']=='generate' else '')
            return self.snapshot(key)

    def export(self,key,format,build_id=None):
        with self.lock,self.store.lock:
            project=self.store.load(projects.identity(key))
            if format=='project':return self.store.archive(key),safe_filename(project['name'])+'.mifont.zip','application/zip'
            if format not in {'ttf','otf','fonts'}:raise ValueError('Export TTF, OTF, font bundle or project ZIP.')
            builds=project['builds']
            build=next((item for item in builds if item['id']==build_id),None) if build_id else(builds[-1] if builds else None)
            if not build:raise ValueError('Build the font before exporting.')
            if format=='fonts':return self._font_bundle(project,build)
            path=self.store.folder(key)/'builds'/(projects.identity(build['id'])+'.'+format)
            return path.read_bytes(),safe_filename(build['family']+'-'+build['style'])+'.'+format,'font/'+format

    def _font_bundle(self,project,build):
        """One local download with both fonts and the exact build's notices."""
        name=safe_filename(build['family']+'-'+build['style'])
        coverage=deepcopy(build.get('coverage',{}))
        obligations=deepcopy(build.get('license_obligations',[]))
        if isinstance(obligations,dict):obligations=[obligations]
        if not isinstance(obligations,list):raise ValueError('Saved font license notices are invalid.')
        output=BytesIO();files={}
        with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as package:
            for format in ('ttf','otf'):
                path=self.store.folder(project['id'])/'builds'/(projects.identity(build['id'])+'.'+format)
                data=path.read_bytes();filename=name+'.'+format
                package.writestr(filename,data)
                files[filename]={'sha256':hashlib.sha256(data).hexdigest(),'size':len(data)}
            for obligation in obligations:
                if not isinstance(obligation,dict) or not isinstance(obligation.get('license_text'),str):raise ValueError('A saved source license notice is missing.')
                digest=hashlib.sha256(obligation['license_text'].encode()).hexdigest()
                if digest!=obligation.get('license_sha256'):raise ValueError('A saved source license notice changed.')
                folder='licenses/'+hashlib.sha256(str(obligation.get('id','')).encode()).hexdigest()[:20]
                package.writestr(folder+'/OFL.txt',obligation['license_text'])
                package.writestr(folder+'/SOURCE.json',json.dumps({key:obligation.get(key) for key in
                                  ('id','family','sha256','license','license_sha256','source_url','license_url')},ensure_ascii=False,indent=2))
            manifest={'schema':1,'app':'MiFont','version':VERSION,'family':build['family'],'style':build['style'],
                      'coverage':coverage,'files':files,'source_families':[item.get('family') for item in obligations],
                      'generated_characters_are_proposals':bool(coverage.get('generated_chars'))}
            package.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
            lines=['MiFont: '+build['family']+' '+build['style'],'','Includes editable TrueType (.ttf) and OpenType CFF (.otf) fonts.',
                   'Observed characters were traced from your images. Missing characters are style proposals, not recovered originals.',
                   'Review shapes, joins and spacing before production use.','',
                   'Observed characters: '+coverage.get('observed_chars',''),
                   'Generated proposals: '+coverage.get('generated_chars',''),
                   'Missing characters: '+coverage.get('missing_chars','')]
            if obligations:lines.extend(['','This derivative font uses OFL reference outlines. The full source notices and OFL terms are in licenses/.',
                                         'Keep those notices with redistribution and respect source Reserved Font Names.'])
            lines.extend(['']+[str(warning) for warning in build.get('warnings',[])])
            package.writestr('README.txt','\n'.join(lines)+'\n')
        return output.getvalue(),name+'-TTF-OTF.zip','application/zip'

    def close(self):
        self.closed=True
        for job in self.jobs.values():job['cancel'].set()
        self.executor.shutdown(wait=True,cancel_futures=True);self.temp.cleanup()

def handler_for(session):
    class Handler(BaseHTTPRequestHandler):
        server_version='MiFont/'+VERSION
        def log_message(self,*args):pass
        def send_data(self,status,data,mime,filename=None):
            self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)))
            for key,value in {'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
                'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; font-src 'self' blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"}.items():self.send_header(key,value)
            if filename:self.send_header('Content-Disposition','attachment; filename="'+filename.encode('ascii','replace').decode()+'"')
            self.end_headers();self.wfile.write(data)
        def json(self,status,value):self.send_data(status,json.dumps(value,ensure_ascii=False).encode(),'application/json; charset=utf-8')
        def authorize(self):
            origin='http://127.0.0.1:'+str(self.server.server_port)
            if self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port) or self.headers.get('Origin') not in (None,origin):raise PermissionError('This desktop session only accepts its own local window.')
            if not secrets.compare_digest(self.headers.get('X-MiFont-Token',''),session.token):raise PermissionError('Reopen MiFont from the launcher to reconnect this window.')
            session.last_request=time.monotonic()
        def read_body(self,maximum):
            if self.headers.get('Transfer-Encoding'):raise ValueError('Chunked uploads are not supported.')
            length=int(self.headers.get('Content-Length','0'))
            if not 1<=length<=maximum:raise ValueError('Request is empty or exceeds the local upload limit.')
            data=self.rfile.read(length)
            if len(data)!=length:raise ValueError('The upload was interrupted.')
            return data
        def do_GET(self):
            try:
                route=urlsplit(self.path).path
                static={'/':('static/index.html','text/html; charset=utf-8'),'/index.html':('static/index.html','text/html; charset=utf-8'),
                    '/static/app.js':('static/app.js','text/javascript; charset=utf-8'),'/static/style.css':('static/style.css','text/css; charset=utf-8'),
                    '/assets/icon.png':('assets/icon.png','image/png'),'/assets/body.ttf':('assets/body.ttf','font/ttf'),'/assets/heading.ttf':('assets/heading.ttf','font/ttf')}
                if route in static:
                    path,mime=static[route];return self.send_data(200,(RESOURCES/path).read_bytes(),mime)
                self.authorize()
                if route=='/api/ping':return self.json(200,{'ok':True})
                if route=='/api/config':
                    try:
                        from . import engine
                    except ImportError:import engine
                    return self.json(200,{'app_id':'mifont','version':VERSION,'charset':projects.ASCII,'default_options':{**engine.DEFAULT_OPTIONS,'ocr':True},
                        'generate':{'automatic':True,'options':{'threshold':'auto','foreground':'auto','ocr':True,'offline':False},'outputs':['ttf','otf','fonts']},
                        'completion':{'mode':'observed','modes':['observed','proposal','reference'],'requires_allow_approximate':True,'model_installed':False},
                        'research':{'online':True,'artwork_uploaded':False,'fonts_installed':False,'options':{'candidate_limit':3,'category':''}},
                        'max_upload':projects.MAX_UPLOAD})
                if route=='/api/projects':return self.json(200,{'projects':session.store.listing()})
                parts=route.strip('/').split('/')
                if len(parts)==3 and parts[:2]==['api','project']:return self.json(200,session.project(parts[2]))
                if len(parts)==3 and parts[:2]==['api','job']:return self.json(200,session.snapshot(parts[2]))
                if len(parts)==5 and parts[0]=='api':
                    kind,project_id,key,format=parts[1:]
                    project=session.store.load(projects.identity(project_id));projects.identity(key)
                    if kind=='image':
                        image=next((item for item in project['images'] if item['id']==key),None)
                        if image is None:raise ValueError('Image not found.')
                        if format=='preview':path=session.store.folder(project_id)/'images'/(key+'.preview.png')
                        elif format=='source':path=session.store.image_path(project,image)
                        else:raise ValueError('Unknown image view.')
                        return self.send_data(200,path.read_bytes(),'image/png')
                    if kind=='glyph':
                        glyph=next((item for item in project['glyphs'] if item['id']==key),None)
                        if glyph is None:raise ValueError('Glyph not found.')
                        if format not in {'svg','crop'}:raise ValueError('Unknown glyph view.')
                        path=session.store.folder(project_id)/'glyphs'/(glyph.get('blob_id',key)+('.svg' if format=='svg' else'.png'))
                        return self.send_data(200,path.read_bytes(),'image/svg+xml' if format=='svg' else'image/png')
                    if kind=='font-preview':
                        data,_,mime=session.export(project_id,format,key);return self.send_data(200,data,mime)
                self.json(404,{'error':'Not found.'})
            except PermissionError as exc:self.json(403,{'error':str(exc)})
            except (ValueError,KeyError,TypeError,FileNotFoundError) as exc:self.json(400,{'error':str(exc)})
            except (BrokenPipeError,ConnectionResetError):pass
            except Exception:self.json(500,{'error':'The local app could not complete this request.'})
        def do_POST(self):
            try:
                self.authorize();route=urlsplit(self.path).path
                if route=='/api/upload':return self.json(200,session.upload(self.headers.get('X-Project-Id'),unquote(self.headers.get('X-File-Name','image.png')),self.read_body(projects.MAX_UPLOAD)))
                if route=='/api/project/import':
                    with session.lock,session.store.lock:return self.json(200,session.store.view(session.store.import_archive(self.read_body(projects.MAX_PROJECT_ZIP))))
                request=json.loads(self.read_body(2*1024*1024))
                if not isinstance(request,dict):raise ValueError('JSON request must be an object.')
                if route=='/api/project/new':
                    with session.lock,session.store.lock:return self.json(200,session.store.view(session.store.create(request)))
                if route=='/api/project/update':return self.json(200,session.update(request))
                if route=='/api/image/remove':return self.json(200,session.remove_image(request))
                if route in {'/api/glyph/update','/api/glyph/remove'}:return self.json(200,session.glyph_update(request,route.endswith('/remove')))
                if route in {'/api/generate','/api/analyze','/api/extract','/api/complete','/api/build','/api/research'}:return self.json(202,session.submit(route.rsplit('/',1)[1],request))
                if route=='/api/crop':
                    region={key:request[key] for key in ('box','char','baseline_y','reference_height') if key in request}
                    return self.json(202,session.submit('extract',{**request,'regions':[region]}))
                if route=='/api/cancel':return self.json(200,session.cancel(request.get('job_id')))
                if route=='/api/export':
                    data,name,mime=session.export(request.get('project_id'),request.get('format'),request.get('build_id'))
                    return self.send_data(200,data,mime,name)
                if route=='/api/font-bundle':
                    data,name,mime=session.export(request.get('project_id'),'fonts',request.get('build_id'))
                    return self.send_data(200,data,mime,name)
                if route=='/api/shutdown':
                    self.json(200,{'ok':True});threading.Thread(target=self.server.shutdown,daemon=True).start();return
                self.json(404,{'error':'Not found.'})
            except PermissionError as exc:self.json(403,{'error':str(exc)})
            except Conflict as exc:self.json(409,{'error':str(exc)})
            except (ValueError,KeyError,TypeError,FileNotFoundError) as exc:self.json(400,{'error':str(exc)})
            except (BrokenPipeError,ConnectionResetError):pass
            except Exception as exc:self.json(500,{'error':str(exc)[:800]})
    return Handler

def open_window(url):
    for base in (os.environ.get('ProgramFiles(x86)'),os.environ.get('ProgramFiles')):
        if base:
            edge=Path(base)/'Microsoft/Edge/Application/msedge.exe'
            if edge.is_file():
                subprocess.Popen([str(edge),'--app='+url,'--new-window'],close_fds=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));return
    webbrowser.open(url)

def self_test():
    from PIL import Image,ImageDraw
    from fontTools.ttLib import TTFont
    try:from . import engine,completion
    except ImportError:import engine,completion
    with tempfile.TemporaryDirectory(prefix='mifont-test-') as directory:
        folder=Path(directory)
        store=projects.ProjectStore(folder/'Projects');project=store.create({'name':'Smoke font'})
        image=Image.new('RGB',(160,180),'white');draw=ImageDraw.Draw(image)
        draw.polygon([(20,155),(67,20),(90,20),(140,155),(110,155),(99,120),(55,120),(44,155)],fill='black')
        draw.polygon([(64,95),(90,95),(77,52)],fill='white')
        image.save(folder/'specimen.png')
        analyzed=engine.analyze_image(folder/'specimen.png',{'text':'A'})
        assert len(analyzed['regions'])==1
        region=analyzed['regions'][0]
        glyph=engine.extract_glyph(folder/'specimen.png',region['box'],{'char':'A','baseline_y':region['baseline_y'],'reference_height':region['reference_height']})
        self_glyph=store.put_glyph(project,glyph);project=store.save(project)
        restored=store.import_archive(store.archive(project['id']))
        assert restored['id']!=project['id'] and restored['charset']==projects.ASCII
        assert store.record(restored['id'],self_glyph['id'])['recording']==glyph['recording']
        completed=completion.complete_glyphs([glyph],{'mode':'proposal','allow_approximate':True})
        metrics=engine.build_font(completed['glyphs'],{'family':'MiFont Smoke'},folder/'font.ttf',folder/'font.otf')
        for format,table in (('ttf','glyf'),('otf','CFF ')):
            with TTFont(folder/('font.'+format)) as font:assert table in font and set(font.getBestCmap())==set(range(32,127))
    return {'ok':True,'version':VERSION,'persistent_project_roundtrip':True,'real_glyph_trace':True,
            'ascii95_ttf_otf':True,'font_metrics':metrics,'offline':True,'automatic_font_installation':False}

def main(argv=None):
    multiprocessing.freeze_support();parser=argparse.ArgumentParser()
    parser.add_argument('--self-test',action='store_true');parser.add_argument('--result-json');parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--info-json');parser.add_argument('--data-root',type=Path);parser.add_argument('--port',type=int,default=0);parser.add_argument('--idle-timeout',type=int,default=300)
    args=parser.parse_args(argv)
    if args.self_test:
        try:result=self_test()
        except Exception as exc:result={'ok':False,'version':VERSION,'error':str(exc)}
        if args.result_json:Path(args.result_json).write_text(json.dumps(result,indent=2),encoding='utf-8')
        if sys.stdout:print(json.dumps(result))
        return 0 if result['ok'] else 1
    session=Session(args.data_root);server=ThreadingHTTPServer(('127.0.0.1',args.port),handler_for(session));server.daemon_threads=True
    origin='http://127.0.0.1:'+str(server.server_port);url=origin+'/#token='+session.token
    if args.info_json:Path(args.info_json).write_text(json.dumps({'url':url,'origin':origin,'token':session.token,'pid':os.getpid()}),encoding='utf-8')
    def watchdog():
        while not session.closed:
            time.sleep(5)
            with session.lock:active=any(job['status'] in {'queued','running'} for job in session.jobs.values())
            if args.idle_timeout>0 and not active and time.monotonic()-session.last_request>args.idle_timeout:server.shutdown();return
    threading.Thread(target=watchdog,daemon=True).start()
    if not args.no_browser:open_window(url)
    try:server.serve_forever(poll_interval=.25)
    finally:session.close();server.server_close()
    return 0

if __name__=='__main__':raise SystemExit(main())
