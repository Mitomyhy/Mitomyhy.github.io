"""Exercise the actual desktop HTTP worker, font tables and durable projects."""
from __future__ import annotations
import argparse
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from PIL import Image,ImageDraw,ImageFont
from fontTools.ttLib import TTFont

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app_version import VERSION

def child_pids(parent):
    script=f"Get-CimInstance Win32_Process -Filter 'ParentProcessId = {int(parent)}' | Select-Object -ExpandProperty ProcessId | ConvertTo-Json -Compress"
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],capture_output=True,text=True,check=True,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    value=json.loads(result.stdout) if result.stdout.strip() else[]
    return [value] if isinstance(value,int) else value

def alive_pids(values):
    if not values:return[]
    expression=' OR '.join('ProcessId = '+str(int(value)) for value in values)
    script=f"Get-CimInstance Win32_Process -Filter '{expression}' | Select-Object -ExpandProperty ProcessId | ConvertTo-Json -Compress"
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],capture_output=True,text=True,check=True,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    value=json.loads(result.stdout) if result.stdout.strip() else[]
    return [value] if isinstance(value,int) else value

def run(executable=None,online_reference=False):
    with tempfile.TemporaryDirectory(prefix='mifont-runtime-smoke-') as directory:
        folder=Path(directory);data_root=folder/'data';process=None;session=None;request=None
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def start():
            nonlocal process,session
            info=folder/('session-'+str(time.time_ns())+'.json')
            command=[str(executable)] if executable else[sys.executable,'-m','mifont.main']
            process=subprocess.Popen(command+['--no-browser','--info-json',str(info),'--data-root',str(data_root),'--idle-timeout','120'],
                cwd=Path(__file__).resolve().parents[2] if not executable else folder,
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            deadline=time.monotonic()+30
            while not info.is_file():
                if process.poll() is not None:raise RuntimeError('MiFont exited before its local server was ready.')
                if time.monotonic()>deadline:raise TimeoutError('MiFont startup timed out.')
                time.sleep(.1)
            session=json.loads(info.read_text())
        def request(path,data=None,extra=None,authorized=True,origin=None):
            headers={'Origin':origin or session['origin']}
            if authorized:headers['X-MiFont-Token']=session['token']
            if isinstance(data,dict):data=json.dumps(data).encode();headers['Content-Type']='application/json'
            headers.update(extra or{})
            with opener.open(urllib.request.Request(session['origin']+path,data=data,headers=headers),timeout=60) as response:
                return response.read(),response.headers
        def json_request(path,data=None,extra=None):return json.loads(request(path,data,extra)[0])
        def job(path,payload):
            value=json_request(path,payload);deadline=time.monotonic()+300
            while value['status'] not in {'done','error','cancelled'}:
                if time.monotonic()>deadline:raise TimeoutError('MiFont worker timed out: '+path)
                time.sleep(.1);value=json_request('/api/job/'+value['id'])
            assert value['status']=='done',value['message']
            return value
        def font_check(data,format,license_text=None,license_url=None,observed_metrics=True):
            assert data[:4]==(b'\x00\x01\x00\x00' if format=='ttf' else b'OTTO')
            with TTFont(BytesIO(data)) as font:
                assert ('glyf' in font)==(format=='ttf') and ('CFF ' in font)==(format=='otf')
                assert set(range(32,127))<=set(font.getBestCmap())
                if observed_metrics:
                    glyph=font.getBestCmap()[ord('A')]
                    assert font['hmtx'][glyph]==(700,75)
                if license_text is not None:
                    assert font['name'].getDebugName(13)==license_text
                    assert font['name'].getDebugName(14)==license_url
                    assert font['name'].getDebugName(1).startswith('MiFont ')
        try:
            start()
            for path in ('/','/static/app.js','/static/style.css','/assets/icon.png','/assets/body.ttf','/assets/heading.ttf'):
                raw,headers=request(path,authorized=False);assert len(raw)>100,path
                assert "font-src 'self' blob:" in headers['Content-Security-Policy']
            for kwargs in ({'authorized':False},{'origin':'https://example.invalid'}):
                try:request('/api/config',**kwargs)
                except urllib.error.HTTPError as error:assert error.code==403
                else:raise AssertionError('Private API accepted a foreign or unauthenticated request.')
            config=json_request('/api/config')
            assert config['version']==VERSION and config['app_id']=='mifont' and len(config['charset'])==95
            assert config['default_options']['threshold']=='auto'
            project=json_request('/api/project/new',{'name':'Runtime specimen','family':'MiFont Integration'})
            project_id=project['id']
            image=Image.new('RGB',(160,180),'white');draw=ImageDraw.Draw(image)
            draw.polygon([(20,155),(67,20),(90,20),(140,155),(110,155),(99,120),(55,120),(44,155)],fill='black')
            draw.polygon([(64,95),(90,95),(77,52)],fill='white')
            raster=BytesIO();image.save(raster,'PNG')
            uploaded=[]
            for index in range(2):
                result=json_request('/api/upload',raster.getvalue(),{'X-Project-Id':project_id,'X-File-Name':f'specimen-{index}.png','Content-Type':'application/octet-stream'})
                uploaded.append(result['image'])
            assert len(result['project']['images'])==2
            analyzed=job('/api/analyze',{'project_id':project_id,'image_ids':[uploaded[0]['id']],
                                       'options':{'text':'A','foreground':'auto','ocr':False}})
            region=analyzed['project']['images'][0]['candidates'][0]
            extracted=job('/api/extract',{'project_id':project_id,'image_id':uploaded[0]['id'],
                'regions':[{key:region[key] for key in ('box','char','baseline_y','reference_height')}],
                'options':{'foreground':'auto'}})
            glyph=extracted['project']['glyphs'][0]
            assert glyph['char']=='A' and glyph['origin']=='observed'
            project=json_request('/api/glyph/update',{'project_id':project_id,'glyph_id':glyph['id'],'lsb':75,'advance_width':700})
            for kind in ('svg','crop'):
                raw,_=request('/api/glyph/'+project_id+'/'+glyph['id']+'/'+kind)
                assert len(raw)>50
            completed=job('/api/complete',{'project_id':project_id,'options':{'mode':'proposal','allow_approximate':True}})
            assert len(completed['project']['glyphs'])==95 and not completed['project']['coverage']['missing_chars']
            assert completed['project']['coverage']['observed_chars']=='A'
            repeated=job('/api/complete',{'project_id':project_id,'options':{'mode':'proposal','allow_approximate':True}})
            assert len(repeated['project']['glyphs'])==95,'Repeated completion accumulated stale generated mappings.'
            built=job('/api/build',{'project_id':project_id})
            build_id=built['result']['build_id'];font_sizes={}
            for format in ('ttf','otf'):
                raw,headers=request('/api/export',{'project_id':project_id,'format':format,'build_id':build_id})
                font_check(raw,format);font_sizes[format]=len(raw)
                assert headers.get_content_type()=='font/'+format
            online=False
            if online_reference:
                research=job('/api/research',{'project_id':project_id,'query':'Dancing Script','options':{'candidate_limit':1}})
                cards=research['project']['references'];assert cards and cards[0]['license']=='OFL-1.1'
                card=cards[0]
                reference=job('/api/complete',{'project_id':project_id,'options':{'mode':'reference','reference_id':card['id'],
                    'allow_approximate':True,'accept_reference_license':True,'replace_generated':True}})
                assert len(reference['project']['glyphs'])==95
                assert reference['project']['coverage']['observed_chars']=='A'
                obligation=reference['project']['license_obligations'][0]
                built=job('/api/build',{'project_id':project_id});build_id=built['result']['build_id']
                for format in ('ttf','otf'):
                    raw,_=request('/api/export',{'project_id':project_id,'format':format,'build_id':build_id})
                    font_check(raw,format,obligation['license_text'],obligation['license_url'])
                online=True
            auto_project=json_request('/api/project/new',{'name':'One-click specimen','family':'One Click QA'})
            auto_id=auto_project['id']
            alphabet=Image.new('RGB',(1100,320),'white');letters=ImageDraw.Draw(alphabet)
            specimen_font=ImageFont.truetype(str(Path(__file__).resolve().parents[1]/'assets/body.ttf'),50)
            for row,text in enumerate(('ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz','0123456789')):
                letters.text((24,20+row*95),text,font=specimen_font,fill='black')
            alphabet_bytes=BytesIO();alphabet.save(alphabet_bytes,'PNG')
            auto_upload=json_request('/api/upload',alphabet_bytes.getvalue(),{'X-Project-Id':auto_id,'X-File-Name':'one-click.png','Content-Type':'application/octet-stream'})
            assert not auto_upload['project']['glyphs'],'One-click test unexpectedly had preassigned glyphs.'
            auto=job('/api/generate',{'project_id':auto_id,'metadata':{'family':'One Click QA'},'options':{'threshold':'auto'}})
            assert len(auto['project']['glyphs'])==95 and not auto['project']['coverage']['missing_chars']
            assert auto['result']['automation'] and auto['result']['build_id']
            observed_auto=[item for item in auto['project']['glyphs'] if item['origin']=='observed']
            assert observed_auto and all(item.get('image_id')==auto_upload['image']['id'] for item in observed_auto)
            auto_archive,_=request('/api/export',{'project_id':auto_id,'format':'project'})
            with zipfile.ZipFile(BytesIO(auto_archive)) as source_project:
                observed_record=observed_auto[0]
                record=json.loads(source_project.read('glyphs/'+observed_record.get('blob_id',observed_record['id'])+'.json'))
                assert record['method']=='local-potrace' and record['recording']
            auto_build=auto['result']['build_id']
            fonts_zip,fonts_headers=request('/api/export',{'project_id':auto_id,'format':'fonts','build_id':auto_build})
            assert fonts_headers.get_content_type()=='application/zip'
            with zipfile.ZipFile(BytesIO(fonts_zip)) as fonts:
                assert fonts.testzip() is None
                font_members={kind:[name for name in fonts.namelist() if name.lower().endswith('.'+kind)] for kind in ('ttf','otf')}
                assert all(len(names)==1 for names in font_members.values())
                assert any('readme' in name.lower() for name in fonts.namelist())
                bundle_manifest=json.loads(fonts.read('manifest.json'))
                assert bundle_manifest['version']==VERSION and bundle_manifest['family']=='One Click QA'
                assert not bundle_manifest['coverage']['missing_chars']
                assert len(set(bundle_manifest['coverage']['observed_chars']+bundle_manifest['coverage']['generated_chars']))==95
                for kind,names in font_members.items():
                    value=fonts.read(names[0]);font_check(value,kind,observed_metrics=False)
                    assert bundle_manifest['files'][names[0]]['size']==len(value)
                    assert bundle_manifest['files'][names[0]]['sha256']==hashlib.sha256(value).hexdigest()
                    with TTFont(BytesIO(value)) as font:
                        assert font['name'].getDebugName(1)=='One Click QA'
                        license_text=font['name'].getDebugName(13)
                        if license_text and 'SIL OPEN FONT LICENSE' in license_text:
                            license_members=[name for name in fonts.namelist() if name.lower().endswith('/ofl.txt') or name.lower()=='ofl.txt']
                            assert license_members and any(fonts.read(name).decode('utf-8').replace('\r\n','\n')==license_text.replace('\r\n','\n') for name in license_members)
                            assert any('source' in name.lower() and name.lower().endswith('.json') for name in fonts.namelist())
            auto_more=json_request('/api/upload',alphabet_bytes.getvalue(),{'X-Project-Id':auto_id,'X-File-Name':'one-click-added.png','Content-Type':'application/octet-stream'})
            auto_repeat=job('/api/generate',{'project_id':auto_id,'metadata':{'family':'One Click QA'},'options':{'threshold':180}})
            assert len(auto_repeat['project']['glyphs'])==95 and not auto_repeat['project']['coverage']['missing_chars']
            latest_auto=auto_repeat['project']['builds'][-1]
            assert latest_auto['generator_version']==VERSION
            assert latest_auto['generation_options']['threshold']==180
            assert set(latest_auto['source_image_ids'])=={auto_upload['image']['id'],auto_more['image']['id']}
            archive,_=request('/api/export',{'project_id':project_id,'format':'project'})
            with zipfile.ZipFile(BytesIO(archive)) as package:
                doc=json.loads(package.read('project.json'))
                assert len(doc['glyphs'])==95 and len(doc['images'])==2
                first=next(item for item in doc['glyphs'] if item.get('char')=='A')
                record=json.loads(package.read('glyphs/'+first.get('blob_id',first['id'])+'.json'))
                assert record['method']=='local-potrace'
                if online:
                    assert any(name.startswith('licenses/') and name.endswith('/OFL.txt') for name in package.namelist())
                    assert any(name.startswith('licenses/') and name.endswith('/SOURCE.json') for name in package.namelist())
            imported=json_request('/api/project/import',archive,{'Content-Type':'application/zip'})
            assert imported['id']!=project_id and len(imported['glyphs'])==95
            if online:
                imported_build=job('/api/build',{'project_id':imported['id']})
                raw,_=request('/api/export',{'project_id':imported['id'],'format':'ttf','build_id':imported_build['result']['build_id']})
                font_check(raw,'ttf',obligation['license_text'],obligation['license_url'])
            before=json_request('/api/project/'+project_id)
            many=[{'box':region['box'],'char':'','baseline_y':region['baseline_y'],'reference_height':region['reference_height']} for _ in range(150)]
            cancelled=json_request('/api/extract',{'project_id':project_id,'image_id':uploaded[0]['id'],'regions':many})
            deadline=time.monotonic()+15
            while cancelled['status']=='queued' and time.monotonic()<deadline:
                time.sleep(.1);cancelled=json_request('/api/job/'+cancelled['id'])
            time.sleep(.3);workers=child_pids(session['pid']);assert workers,'No real spawned worker was exercised before cancellation.'
            json_request('/api/cancel',{'job_id':cancelled['id']})
            deadline=time.monotonic()+15
            while alive_pids(workers) and time.monotonic()<deadline:time.sleep(.2)
            assert not alive_pids(workers),'Cancelled owned worker remained alive.'
            after=json_request('/api/project/'+project_id)
            assert after['revision']==before['revision'] and len(after['glyphs'])==95
            request('/api/shutdown',{});process.wait(timeout=15);assert process.returncode==0
            start()
            restored=json_request('/api/project/'+project_id)
            assert len(restored['glyphs'])==95 and len(restored['images'])==2
            restored_auto=json_request('/api/project/'+auto_id)
            assert len(restored_auto['glyphs'])==95 and restored_auto['builds']
            assert restored_auto['builds'][-1]['generation_options']['threshold']==180
            assert restored_auto['builds'][-1]['generator_version']==VERSION
            assert set(restored_auto['builds'][-1]['source_image_ids'])=={auto_upload['image']['id'],auto_more['image']['id']}
            raw,_=request('/api/font-preview/'+project_id+'/'+build_id+'/ttf')
            font_check(raw,'ttf',obligation['license_text'] if online else None,obligation['license_url'] if online else None)
            request('/api/shutdown',{});process.wait(timeout=15);assert process.returncode==0
            return {'ok':True,'version':VERSION,'actual_frozen_executable':bool(executable),'authenticated_loopback_api':True,
                'real_spawned_worker':True,'real_potrace_glyph_trace':True,'multiple_images':True,'editable_lsb_outline_and_metrics':True,
                'ascii95_ttf_glyf_and_otf_cff':True,'font_bytes':font_sizes,'project_zip_roundtrip':True,
                'persistent_restart':True,'real_cancelled_worker_exited':True,'cancelled_job_preserves_project':True,
                'repeated_completion_stable95':True,'public_reference_research_and_local_fit':online,
                'reference_font_name_ids13_14':online,'project_license_notices':online,'licenses_survive_import_and_rebuild':online,
                'one_click_auto_generate':True,'single_ttf_otf_zip':True,'auto_generate_without_manual_labels':True,
                'auto_generate_observed_chars':auto['project']['coverage']['observed_chars'],
                'auto_generate_uses_real_source_crops_and_potrace':True,'auto_generate_requested_family_preserved':True,
                'auto_generate_added_images_included':True,'auto_generate_threshold_persists':True,
                'auto_generate_threshold_rerun':True,'auto_generate_repeat_stable95':True,'auto_generate_project_persists':True,
                'artwork_uploaded':False,'automatic_font_installation':False,'clean_shutdown':True}
        finally:
            if process is not None and process.poll() is None:
                try:request('/api/shutdown',{});process.wait(timeout=15)
                except Exception:
                    process.terminate();process.wait(timeout=10)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--executable',type=Path);parser.add_argument('--online-reference',action='store_true')
    parser.add_argument('--result-json',type=Path,required=True);args=parser.parse_args()
    result=run(args.executable.resolve() if args.executable else None,args.online_reference)
    args.result_json.parent.mkdir(parents=True,exist_ok=True);args.result_json.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))
