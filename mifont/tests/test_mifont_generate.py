"""Automatic font generation commits only complete, verified local results."""
from copy import deepcopy
from io import BytesIO
import hashlib
import json
from pathlib import Path
import queue
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
import uuid
import zipfile

from PIL import Image
from fontTools.ttLib import TTFont

from mifont import main, completion
from mifont.font_export import build_font
from test_mifont_completion import rectangle


class MemoryQueue(queue.Queue):
    def close(self):pass


class InlineProcess:
    def __init__(self, writer, args):
        self.writer=writer;self.args=args;self.pid=None;self.terminated=False
    def start(self):
        self.pid=1;self.writer(Path(self.args[2]),self.args[3])
    def is_alive(self):return False
    def join(self,timeout=None):pass
    def terminate(self):self.terminated=True
    def close(self):pass


class GenerateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        owner=Path(__file__).resolve().parents[1]
        if owner.name=='mifont':owner=owner.parent
        cls.temp_root=owner/'.work/mifont-generate-tests'
        cls.temp_root.mkdir(parents=True,exist_ok=True)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=self.temp_root)
        self.folder=Path(self.temp.name)
        self.old_tempdir=tempfile.tempdir;tempfile.tempdir=str(self.folder)
        self.session=main.Session(self.folder/'data')
        project=self.session.store.create({'family':'MiFont Original','name':'Existing project'})
        output=BytesIO();Image.new('RGB',(200,240),'white').save(output,'PNG')
        upload=self.session.upload(project['id'],'sample.png',output.getvalue())
        self.project=upload['project'];self.image=self.project['images'][0]
        self.observed=rectangle()
        project=self.session.store.load(self.project['id'])
        self.session.store.put_glyph(project,self.observed,image_id=self.image['id'],box=[10,10,80,180])
        self.old_build=uuid.uuid4().hex
        build_font([self.observed],{'family':'MiFont Original'},
                   self.session.store.folder(project['id'])/'builds'/(self.old_build+'.ttf'),
                   self.session.store.folder(project['id'])/'builds'/(self.old_build+'.otf'))
        project['builds'].append({'id':self.old_build,'family':'MiFont Original','style':'Regular','coverage':{}})
        self.project=self.session.store.view(self.session.store.save(project))
        self.completed=completion.complete_glyphs([self.observed],{'mode':'proposal','allow_approximate':True})['glyphs']

    def tearDown(self):
        self.session.close();tempfile.tempdir=self.old_tempdir;self.temp.cleanup()

    def original_bytes(self):
        folder=self.session.store.folder(self.project['id'])
        return ((folder/'project.json').read_bytes(),
                (folder/'builds'/(self.old_build+'.ttf')).read_bytes(),
                (folder/'builds'/(self.old_build+'.otf')).read_bytes())

    def job(self):
        key=uuid.uuid4().hex;folder=self.session.root/key;folder.mkdir()
        job={'id':key,'project_id':self.project['id'],'revision':self.session.store.load(self.project['id'])['revision'],
             'kind':'generate','status':'queued','progress':0,'message':'Waiting','message_key':'generate_queued',
             'result':None,'project':None,'folder':folder,'cancel':threading.Event()}
        self.session.jobs[key]=job
        payload={'images':[{'id':self.image['id'],'path':str(self.session.store.image_path(self.project,self.image))}],
                 'metadata':{'family':'MiFont Automatic','style':'Regular'},'options':{'threshold':'auto'}}
        return job,payload

    def result(self,folder,licensed=False):
        records=deepcopy(self.completed)
        metadata={'family':'MiFont Automatic','style':'Regular'};obligations=[]
        if licensed:
            license_text='Synthetic QA notice\nSIL OPEN FONT LICENSE Version 1.1\n'
            obligation={'id':'unit-source','family':'Synthetic source','license':'OFL-1.1',
                        'license_text':license_text,'license_sha256':hashlib.sha256(license_text.encode()).hexdigest(),
                        'source_url':'https://github.com/google/fonts/tree/main/ofl/test',
                        'license_url':'https://raw.githubusercontent.com/google/fonts/main/ofl/test/OFL.txt'}
            obligations=[obligation];metadata['license_obligations']=obligations
            target=next(row for row in records if row['char']=='g')
            target['provenance']['reference_id']='unit-source'
        metrics=build_font(records,metadata,folder/'font.ttf',folder/'font.otf')
        return {'glyphs':[{'record':row,'image_id':self.image['id'] if row['char']=='I' else None,
                           'box':[10,10,80,180] if row['char']=='I' else None} for row in records],
                'images':[{'image_id':self.image['id'],'regions':[],'warnings':[]}],
                'font_metadata':metadata,'metrics':metrics,'license_obligations':obligations,
                'warnings':['Missing characters are approximate proposals.'],
                'automation':{'artwork_uploaded':False,'ascii95':True},'files':{'ttf':'font.ttf','otf':'font.otf'}}

    def run_writer(self,job,payload,writer):
        context=types.SimpleNamespace(Queue=MemoryQueue,
            Process=lambda target,args,daemon:InlineProcess(writer,args))
        with patch.object(main.multiprocessing,'get_context',return_value=context):
            self.session._run(job,payload)
        return self.session.snapshot(job['id'])

    def good_writer(self,licensed=False,after=None):
        def writer(folder,messages):
            result=self.result(folder,licensed=licensed)
            if after:after(folder,result)
            (folder/'result.json').write_text(json.dumps(result),encoding='utf-8')
            messages.put({'kind':'progress','progress':80,'message':'Building fonts','message_key':'generate_build'})
            messages.put({'kind':'done'})
        return writer

    def test_success_validates_full_fonts_commits_once_and_keeps_old_build(self):
        before=self.original_bytes();job,payload=self.job()
        snapshot=self.run_writer(job,payload,self.good_writer())
        self.assertEqual(snapshot['status'],'done');self.assertEqual(snapshot['message_key'],'generate_ready')
        project=snapshot['project'];self.assertEqual(project['revision'],job['revision']+1)
        self.assertEqual(len(project['glyphs']),95);self.assertEqual(project['coverage']['missing_chars'],'')
        self.assertEqual(len(project['builds']),2);self.assertEqual(project['builds'][0]['id'],self.old_build)
        self.assertEqual(project['family'],'MiFont Automatic')
        self.assertEqual(self.original_bytes()[1:],before[1:])
        for format in ('ttf','otf'):
            data,_,_=self.session.export(project['id'],format)
            with TTFont(BytesIO(data)) as font:self.assertEqual(set(font.getBestCmap()),set(range(32,127)))
        second,payload=self.job();again=self.run_writer(second,payload,self.good_writer())
        self.assertEqual(again['status'],'done');self.assertEqual(len(again['project']['glyphs']),95)
        self.assertEqual(len(again['project']['builds']),3)

    def test_corrupt_font_or_partial_asset_failure_never_replaces_previous_result(self):
        for scenario in ('bad_font','asset_error','save_error'):
            before=self.original_bytes();job,payload=self.job()
            def damage(folder,result):
                if scenario=='bad_font':(folder/'font.otf').write_bytes(b'invalid')
            writer=self.good_writer(after=damage)
            if scenario=='asset_error':
                original=self.session.store.put_glyph;count=[0]
                def fail(project,record,**kwargs):
                    count[0]+=1
                    if count[0]==3:raise OSError('Simulated staged write failure')
                    return original(project,record,**kwargs)
                with patch.object(self.session.store,'put_glyph',side_effect=fail):snapshot=self.run_writer(job,payload,writer)
            elif scenario=='save_error':
                with patch.object(self.session.store,'save',side_effect=OSError('Simulated atomic save failure')):
                    snapshot=self.run_writer(job,payload,writer)
            else:snapshot=self.run_writer(job,payload,writer)
            self.assertEqual(snapshot['status'],'error');self.assertEqual(snapshot['message_key'],'generate_error')
            self.assertEqual(self.original_bytes(),before)

    def test_cancel_during_staging_keeps_current_project_and_prior_fonts(self):
        before=self.original_bytes();job,payload=self.job();original=self.session.store.put_glyph;count=[0]
        def cancel(project,record,**kwargs):
            count[0]+=1
            if count[0]==3:job['cancel'].set()
            return original(project,record,**kwargs)
        with patch.object(self.session.store,'put_glyph',side_effect=cancel):
            snapshot=self.run_writer(job,payload,self.good_writer())
        self.assertEqual(snapshot['status'],'cancelled')
        self.assertEqual(snapshot['message_key'],'generate_cancelled');self.assertEqual(self.original_bytes(),before)

    def test_worker_failure_and_revision_conflict_do_not_commit(self):
        before=self.original_bytes();job,payload=self.job()
        snapshot=self.run_writer(job,payload,lambda folder,messages:messages.put({'kind':'error','message':'No supported labels found. Use a clearer image.'}))
        self.assertEqual(snapshot['status'],'error');self.assertIn('clearer image',snapshot['message'])
        self.assertEqual(self.original_bytes(),before)
        job,payload=self.job()
        def changed(folder,result):
            project=self.session.store.load(self.project['id']);project['name']='Changed elsewhere'
            self.session.store.save(project)
        snapshot=self.run_writer(job,payload,self.good_writer(after=changed))
        self.assertEqual(snapshot['status'],'error');self.assertIn('another window',snapshot['message'])
        saved=self.session.store.load(self.project['id'])
        self.assertEqual(saved['name'],'Changed elsewhere');self.assertEqual(len(saved['builds']),1)

    def test_bundle_contains_both_fonts_exact_build_notices_and_no_source_images(self):
        job,payload=self.job();snapshot=self.run_writer(job,payload,self.good_writer(licensed=True))
        self.assertEqual(snapshot['status'],'done',snapshot['message'])
        data,name,mime=self.session.export(self.project['id'],'fonts')
        self.assertEqual(mime,'application/zip');self.assertTrue(name.endswith('-TTF-OTF.zip'))
        with zipfile.ZipFile(BytesIO(data)) as package:
            names=package.namelist()
            self.assertEqual(sum(name.endswith('.ttf') for name in names),1)
            self.assertEqual(sum(name.endswith('.otf') for name in names),1)
            self.assertTrue(any(name.endswith('/OFL.txt') for name in names))
            self.assertTrue(any(name.endswith('/SOURCE.json') for name in names))
            self.assertFalse(any(name.endswith('.png') for name in names))
            manifest=json.loads(package.read('manifest.json'))
            self.assertEqual(manifest['source_families'],['Synthetic source'])
            for format in ('ttf','otf'):
                font_name=next(name for name in names if name.endswith('.'+format))
                font_data=package.read(font_name)
                self.assertEqual(hashlib.sha256(font_data).hexdigest(),manifest['files'][font_name]['sha256'])
                with TTFont(BytesIO(font_data)) as font:self.assertIn('SIL OPEN FONT LICENSE',font['name'].getDebugName(13))

    def test_image_remove_detaches_related_glyphs_and_keeps_prior_build_files(self):
        before=self.original_bytes()
        project=self.session.remove_image({'project_id':self.project['id'],'image_id':self.image['id'],
                                           'revision':self.project['revision']})
        self.assertEqual(project['images'],[]);self.assertEqual(project['glyphs'],[])
        self.assertEqual(len(project['builds']),1);self.assertEqual(self.original_bytes()[1:],before[1:])
        with self.assertRaises(main.Conflict):
            self.session.remove_image({'project_id':self.project['id'],'image_id':self.image['id'],'revision':self.project['revision']})

    def test_submit_snapshots_all_images_with_zero_glyphs_and_rejects_stale_revision(self):
        output=BytesIO();Image.new('RGB',(80,90),'white').save(output,'PNG')
        self.session.upload(self.project['id'],'second.png',output.getvalue())
        project=self.session.store.load(self.project['id']);project['glyphs']=[]
        project=self.session.store.save(project)
        module=types.ModuleType('mifont.auto_pipeline')
        module.normalize_options=lambda options:dict(options)
        request={'project_id':project['id'],'revision':project['revision'],
                 'metadata':{'family':'One click'},'options':{'threshold':'auto'}}
        with patch.dict(sys.modules,{'mifont.auto_pipeline':module}), patch.object(self.session.executor,'submit') as queued:
            job=self.session.submit('generate',request)
        payload=queued.call_args.args[2]
        self.assertEqual(len(payload['images']),2);self.assertNotIn('records',payload)
        self.assertEqual(payload['metadata']['family'],'One click')
        self.assertEqual(payload['options']['threshold'],'auto')
        request['options']['threshold']=99
        self.assertEqual(payload['options']['threshold'],'auto')
        self.assertEqual(job['message_key'],'generate_queued')
        with self.assertRaises(main.Conflict):self.session.update({'project_id':project['id'],'family':'Busy'})
        self.session.cancel(job['id'])
        with patch.dict(sys.modules,{'mifont.auto_pipeline':module}):
            with self.assertRaises(main.Conflict):self.session.submit('generate',{**request,'revision':project['revision']-1})

    def test_automatic_threshold_settings_roundtrip_after_session_restart(self):
        job,payload=self.job()
        requested={'threshold':151,'foreground':'auto','ocr':True,'offline':True}
        payload['options']=deepcopy(requested)
        snapshot=self.run_writer(job,payload,self.good_writer())
        self.assertEqual(snapshot['status'],'done',snapshot['message'])
        build=snapshot['project']['builds'][-1]
        self.assertEqual(build['generation_options'],requested)
        payload['options']['threshold']=99
        self.assertEqual(build['generation_options']['threshold'],151)
        project_id=self.project['id'];self.session.close()
        self.session=main.Session(self.folder/'data')
        restored=self.session.project(project_id)
        self.assertEqual(restored['builds'][-1]['generation_options'],requested)
        self.assertEqual(restored['builds'][-1]['source_image_ids'],[self.image['id']])
        data,_,mime=self.session.export(project_id,'fonts')
        self.assertEqual(mime,'application/zip')
        with zipfile.ZipFile(BytesIO(data)) as package:
            self.assertEqual(json.loads(package.read('manifest.json'))['family'],'MiFont Automatic')


if __name__=='__main__':unittest.main()
