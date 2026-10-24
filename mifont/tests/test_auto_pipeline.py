"""One-click generation tests using only programmatically drawn specimens."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image,ImageDraw,ImageFont
from fontTools.ttLib import TTFont

from mifont import auto_pipeline,completion,engine
from mifont.font_export import reference_name_conflict


class AutomaticTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='mifont-auto-test-')
        self.folder=Path(self.temporary.name)

    def tearDown(self):self.temporary.cleanup()

    def word(self):
        image=Image.new('RGB',(300,100),'white');draw=ImageDraw.Draw(image)
        draw.text((12,10),'HELLO',font=ImageFont.load_default(size=70),fill='black')
        path=self.folder/'word.png';image.save(path)
        recognized={'available':True,'text':'HELLO','words':[{'text':'HELLO','box':[12,10,235,70]}]}
        return path,recognized

    def assert_fonts(self,result,folder,family):
        self.assertEqual(result['font_metadata']['family'],family)
        self.assertEqual(len(result['glyphs']),95)
        self.assertEqual(result['coverage']['missing_chars'],'')
        for filename,table in [('font.ttf','glyf'),('font.otf','CFF ')]:
            with TTFont(folder/filename) as font:
                self.assertIn(table,font);self.assertEqual(set(font.getBestCmap()),set(range(32,127)))
                self.assertEqual(font['name'].getDebugName(1),family)
            self.assertGreater(sum(ImageFont.truetype(str(folder/filename),32).getmask('HELLO 123!?')),0)

    def test_upload_only_generates_actual_fonts_without_user_labels(self):
        path,recognized=self.word();progress=[];output=self.folder/'result'
        with patch.object(auto_pipeline.ocr,'suggest_labels',return_value=recognized),patch.object(auto_pipeline.references,'research_sources',side_effect=AssertionError('Offline must not call the network')):
            result=auto_pipeline.generate([{'id':'image-one','path':str(path)}],{'family':'Stringlabs'},{'offline':True},output,progress.append)
        self.assert_fonts(result,output,'Stringlabs')
        self.assertEqual(result['coverage']['observed_chars'],'EHLO')
        self.assertTrue(all(g['image_id']=='image-one' and g['box'] for g in result['glyphs'] if g['record']['origin']=='observed'))
        self.assertTrue(all(g['image_id'] is None for g in result['glyphs'] if g['record']['origin']=='generated'))
        self.assertEqual(progress[-1]['progress'],99)
        self.assertTrue(all('message_key' in event for event in progress))
        self.assertFalse(result['automation']['artwork_uploaded'])

    def test_twenty_six_boxes_without_sequence_evidence_are_not_called_alphabet(self):
        mask=Image.new('L',(800,130));draw=ImageDraw.Draw(mask)
        for index in range(26):draw.rectangle((5+index*30,10,20+index*30,110),fill=255)
        lines=auto_pipeline._line_geometry(mask)
        regions,used,evidence=auto_pipeline._infer_chart(mask,lines,[],'')
        self.assertEqual(regions,[]);self.assertEqual(used,set());self.assertEqual(evidence,[])

    def test_noisy_cream_sheet_uses_headers_and_ordered_ocr_anchors(self):
        image=Image.new('RGB',(1300,550),(190,70,50));draw=ImageDraw.Draw(image)
        font=ImageFont.load_default(size=49);header=ImageFont.load_default(size=18)
        ink=(245,229,197)
        draw.rectangle((0,0,1299,549),outline=ink,width=3)
        draw.text((100,15),'UPPERCASE & LOWERCASE',font=header,fill='#222222')
        texts=['ABCDEFGHIJKLMN','OPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz','1234567890']
        boxes=[]
        for index,text in enumerate(texts):
            y=65+index*105;draw.text((100,y),text,font=font,fill=ink)
            bbox=draw.textbbox((100,y),text,font=font);boxes.append([bbox[0],bbox[1],bbox[2]-bbox[0],bbox[3]-bbox[1]])
        draw.text((900,390),'NUMBER PUNCTUATION',font=header,fill='#222222')
        for x in range(10,1290,37):draw.point((x,50),fill=ink)
        path=self.folder/'cream.png';image.save(path)
        recognized={'available':True,'text':'LOWERCASE OPQRSTUVWX abcdefghijklmnop NUMBER PUNCTUATION 234567890',
                    'words':[{'text':'OPQRSTUVWX','box':boxes[1]},{'text':'abcdefghijklmnop','box':boxes[2]}, {'text':'234567890','box':boxes[3]}]}
        with patch.object(auto_pipeline.ocr,'suggest_labels',return_value=recognized):
            result=auto_pipeline.generate([{'id':'cream','path':path}],{'family':'MiFont Synthetic Sheet'},{'offline':True},self.folder/'sheet')
        self.assert_fonts(result,self.folder/'sheet','MiFont Synthetic Sheet')
        self.assertEqual(set(result['coverage']['observed_chars']),set('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'))
        self.assertTrue(result['automation']['layout_evidence'])
        self.assertTrue(all(g['record']['provenance']['label_evidence'] for g in result['glyphs'] if g['record']['origin']=='observed'))

    def test_blank_reference_has_actionable_error_and_preserves_previous_outputs(self):
        path=self.folder/'blank.png';Image.new('RGB',(100,100),'white').save(path)
        output=self.folder/'saved';output.mkdir();(output/'font.ttf').write_bytes(b'previous');(output/'font.otf').write_bytes(b'previous')
        with self.assertRaisesRegex(engine.FontError,'sharper, larger image'):
            auto_pipeline.generate([{'id':'blank','path':path}],{'family':'Test'},{'offline':True},output)
        self.assertEqual((output/'font.ttf').read_bytes(),b'previous')
        self.assertEqual((output/'font.otf').read_bytes(),b'previous')

    def test_automatic_ofl_fit_keeps_chosen_name_and_embeds_notices(self):
        path,recognized=self.word()
        recording=[['moveTo',[[40,0]]],['lineTo',[[500,0]]],['lineTo',[[500,700]]],['lineTo',[[40,700]]],['closePath',[]]]
        seed=engine.normalize_glyph({'char':'H','recording':recording,'advance_width':550,'origin':'observed'})
        completed=completion.complete_glyphs([seed],{'mode':'proposal','allow_approximate':True})
        engine.build_font(completed['glyphs'],{'family':'Synthetic Reference'},self.folder/'reference.ttf',self.folder/'reference.otf')
        license_text='Copyright 2026 Synthetic Authors.\nSIL OPEN FONT LICENSE Version 1.1\nPREAMBLE\nSynthetic test notice, not a distributed seed font.'
        reference={'id':'synthetic-0123456789abcdef','family':'Synthetic Reference','license':'OFL-1.1','font_path':str(self.folder/'reference.ttf'),
                   'sha256':hashlib.sha256((self.folder/'reference.ttf').read_bytes()).hexdigest(),'license_text':license_text,
                   'license_sha256':hashlib.sha256(license_text.encode()).hexdigest(),'source_url':'https://example.com/source','license_url':'https://example.com/OFL',
                   'match_score':.9,'matched_chars':'EHLO'}
        with patch.object(auto_pipeline.ocr,'suggest_labels',return_value=recognized),patch.object(auto_pipeline.references,'research_sources',return_value={'references':[reference],'warnings':[]}),patch.object(auto_pipeline.references,'resolve_reference',return_value=reference):
            result=auto_pipeline.generate([{'id':'letters','path':path}],{'family':'Stringlabs'},{},self.folder/'licensed')
        self.assert_fonts(result,self.folder/'licensed','Stringlabs')
        self.assertEqual(result['license_obligations'][0]['family'],'Synthetic Reference')
        with TTFont(self.folder/'licensed/font.otf') as font:self.assertEqual(font['name'].getDebugName(13),license_text)
        observed=[g for g in result['glyphs'] if g['record']['origin']=='observed']
        self.assertEqual({g['record']['char'] for g in observed},set('EHLO'))
        self.assertTrue(all(g['record']['provenance'].get('reference_id')==reference['id'] for g in result['glyphs'] if g['record']['origin']=='generated'))

    def test_reserved_names_are_exact_and_custom_family_is_allowed(self):
        obligations=[{'family':'Source Family','license_text':'Copyright Example, with Reserved Font Name "Special Name".\nPREAMBLE\nOriginal notice.'}]
        self.assertTrue(reference_name_conflict('source family',obligations))
        self.assertTrue(reference_name_conflict('Special Name',obligations))
        self.assertFalse(reference_name_conflict('Stringlabs',obligations))
        for options in ({'threshold':0},{'threshold':255},{'threshold':True},{'offline':'yes'},{'font_path':'private'}):
            with self.assertRaises(engine.FontError):auto_pipeline.normalize_options(options)


if __name__=='__main__':unittest.main()
