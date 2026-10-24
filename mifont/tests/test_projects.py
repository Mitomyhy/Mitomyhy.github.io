from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from PIL import Image,ImageDraw,PngImagePlugin

from mifont import projects
from mifont.main import Session,Conflict
from mifont.font_export import build_font,normalize_glyph
from fontTools.ttLib import TTFont

class ProjectTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[2]/'build'
        root.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='mifont-project-test-',dir=root)
        self.folder=Path(self.temp.name)
        self.store=projects.ProjectStore(self.folder/'Projects')
        self.project=self.store.create({'name':'My glyphs','family':'Test Family','charset':' A'})
    def tearDown(self):self.temp.cleanup()
    def image(self):
        image=Image.new('RGBA',(80,100),'white');ImageDraw.Draw(image).rectangle((15,10,55,85),fill='black')
        metadata=PngImagePlugin.PngInfo();metadata.add_text('private','authoring-document-secret')
        output=BytesIO();image.save(output,'PNG',pnginfo=metadata);return output.getvalue()
    def record(self,char='A'):
        return normalize_glyph({'char':char,'origin':'observed','advance_width':300,
            'recording':[['moveTo',[[40,0]]],['lineTo',[[240,0]]],['lineTo',[[240,700]]],['lineTo',[[40,700]]],['closePath',[]]]})
    def test_upload_and_project_roundtrip_preserves_outlines_without_document_metadata(self):
        project,image=self.store.upload(self.project['id'],'sheet.png',self.image())
        original=self.image()
        self.assertNotIn(b'authoring-document-secret',self.store.image_path(project,image).read_bytes())
        glyph=self.store.put_glyph(project,self.record(),image_id=image['id'],box=[15,10,41,76])
        project=self.store.save(project)
        archive=self.store.archive(project['id'])
        restored=self.store.import_archive(archive)
        self.assertNotEqual(restored['id'],project['id'])
        self.assertEqual(self.store.record(restored['id'],glyph['id']),self.store.record(project['id'],glyph['id']))
        self.assertEqual(restored['glyphs'][0]['image_id'],image['id'])
        self.assertEqual(projects.ProjectStore(self.store.root).load(project['id'])['revision'],project['revision'])
        self.assertIn(b'authoring-document-secret',original)
    def test_mapping_duplicate_keeps_old_outline_unassigned(self):
        project=self.project
        first=self.store.put_glyph(project,self.record());first_id=first['id']
        second=self.store.put_glyph(project,self.record())
        self.store.save(project)
        self.assertEqual(self.store.record(project['id'],first_id)['char'],'')
        self.assertEqual(self.store.record(project['id'],second['id'])['char'],'A')
    def test_import_rejects_traversal_without_creating_project(self):
        before=self.store.listing();output=BytesIO()
        with zipfile.ZipFile(output,'w') as archive:
            archive.writestr('project.json',json.dumps(self.project));archive.writestr('../escape.txt','bad')
        with self.assertRaises(ValueError):self.store.import_archive(output.getvalue())
        self.assertEqual(self.store.listing(),before)
        self.assertFalse((self.folder/'escape.txt').exists())
    def test_failed_import_rolls_back_only_new_project(self):
        before=self.store.listing();output=BytesIO()
        with zipfile.ZipFile(output,'w') as archive:
            archive.writestr('project.json',json.dumps(self.project));archive.writestr('unexpected.txt','bad')
        with self.assertRaises(ValueError):self.store.import_archive(output.getvalue())
        self.assertEqual(self.store.listing(),before)
    def test_import_rejects_executable_svg_record(self):
        record=self.record();record['preview_svg']='<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        with self.assertRaises(ValueError):projects.validate_record(record)
    def test_persisted_image_path_cannot_escape_project(self):
        project=dict(self.project);project['images']=[{'id':'a'*32,'suffix':'/../../secret.txt'}]
        projects.atomic_json(self.store.folder(project['id'])/'project.json',project)
        with self.assertRaises(ValueError):self.store.load(project['id'])
    def test_lsb_edit_moves_real_outline_and_font_metrics(self):
        session=Session(self.folder/'SessionData')
        try:
            project=session.store.create({'family':'Bearing Test','charset':'A'})
            glyph=session.store.put_glyph(project,self.record());session.store.save(project)
            view=session.glyph_update({'project_id':project['id'],'glyph_id':glyph['id'],'lsb':120,'advance_width':500})
            changed=session.store.record(project['id'],glyph['id'])
            self.assertEqual(changed['lsb'],120);self.assertEqual(changed['bbox'][0],120)
            self.assertEqual(changed['recording'][0][1][0][0],120)
            self.assertEqual(changed['advance_width'],500)
            ttf,otf=self.folder/'bearing.ttf',self.folder/'bearing.otf'
            build_font([changed],{'family':'Bearing Test'},ttf,otf)
            with TTFont(ttf) as font:
                name=font.getBestCmap()[ord('A')]
                self.assertEqual(font['hmtx'][name],(500,120));self.assertEqual(font['glyf'][name].xMin,120)
            self.assertEqual(view['glyphs'][0]['lsb'],120)
        finally:session.close()
    def test_stale_revision_rejected_without_data_loss(self):
        session=Session(self.folder/'SessionData')
        try:
            project=session.store.create()
            updated=session.update({'project_id':project['id'],'revision':project['revision'],'name':'Renamed'})
            with self.assertRaises(Conflict):session.update({'project_id':project['id'],'revision':project['revision'],'name':'Old overwrite'})
            self.assertEqual(session.project(project['id'])['name'],'Renamed')
            self.assertGreater(updated['revision'],project['revision'])
        finally:session.close()

if __name__=='__main__':unittest.main()
