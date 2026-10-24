"""Synthetic MiFont regression fixtures; no customer artwork or seed fonts."""
from copy import deepcopy
import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from PIL import Image,ImageDraw,ImageFont
from fontTools.ttLib import TTFont

from mifont import engine
from mifont.font_export import FontError,build_font,glyph_preview,normalize_glyph


def rectangle(x0,y0,x1,y1,clockwise=False):
    points=[(x0,y0),(x1,y0),(x1,y1),(x0,y1)]
    if clockwise:points.reverse()
    return [['moveTo',[list(points[0])]]]+[['lineTo',[list(p)]] for p in points[1:]]+[['closePath',[]]]


def ring(char='O'):
    return {'char':char,'origin':'observed','recording':rectangle(40,0,640,700)+rectangle(160,120,520,580,True),
            'advance_width':680,'source_box':[10,20,60,70]}


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='mifont-test-')
        self.folder=Path(self.temp.name)

    def tearDown(self):self.temp.cleanup()

    def image(self,name='source.png',transparent=False):
        image=Image.new('RGBA' if transparent else 'RGB',(200,130),(0,0,0,0) if transparent else 'white')
        draw=ImageDraw.Draw(image)
        draw.rectangle((20,15,90,114),fill=(0,0,0,255) if transparent else 'black')
        draw.rectangle((37,35,73,94),fill=(0,0,0,0) if transparent else 'white')
        draw.rectangle((120,15,169,114),fill=(0,0,0,255) if transparent else 'black')
        path=self.folder/name;image.save(path);return path

    def test_observed_contours_are_not_modified_by_normalization_or_preview(self):
        record=ring();snapshot=deepcopy(record);normalized=normalize_glyph(record)
        self.assertEqual(record,snapshot)
        self.assertEqual(normalized['recording'],record['recording'])
        self.assertEqual(normalized['bbox'],[40,0,640,700])
        root=ET.fromstring(glyph_preview(record))
        self.assertEqual({element.tag.rsplit('}',1)[-1] for element in root.iter()},{'svg','path'})
        self.assertEqual(record,snapshot)

    def test_ttf_and_otf_are_genuine_fonts_with_compound_holes(self):
        ttf,otf=self.folder/'test.ttf',self.folder/'test.otf'
        metrics=build_font([ring()],{'family':'MiFont Test'},ttf,otf)
        self.assertEqual(metrics['observed_chars'],'O')
        self.assertIn('A',metrics['missing_chars'])
        self.assertEqual(ttf.read_bytes()[:4],b'\x00\x01\x00\x00')
        self.assertEqual(otf.read_bytes()[:4],b'OTTO')
        for path,table in [(ttf,'glyf'),(otf,'CFF ')]:
            with TTFont(path) as font:
                self.assertIn(table,font)
                self.assertEqual(font.getBestCmap(),{32:'space',79:'uni004F'})
                self.assertEqual(font['hmtx']['uni004F'],(680,40))
                self.assertEqual(font['head'].unitsPerEm,1000)
            face=ImageFont.truetype(str(path),100)
            rendered=face.getmask('O',mode='L')
            mask=Image.frombytes('L',rendered.size,bytes(rendered))
            self.assertEqual(mask.getpixel((mask.width//2,mask.height//2)),0,'Counter must remain empty')
            self.assertGreater(mask.getpixel((mask.getbbox()[0]+2,mask.height//2)),200,'Outer stroke must remain filled')

    def test_full_ascii_exports_from_explicit_approximate_completion(self):
        from mifont.completion import complete_glyphs
        observed=normalize_glyph(ring());snapshot=deepcopy(observed)
        result=complete_glyphs([observed],{'mode':'proposal','allow_approximate':True})
        self.assertEqual(next(r for r in result['glyphs'] if r['char']=='O'),snapshot)
        self.assertEqual(len(result['glyphs']),95)
        ttf,otf=self.folder/'full.ttf',self.folder/'full.otf'
        metrics=build_font(result['glyphs'],{'family':'MiFont Proposal QA'},ttf,otf)
        self.assertEqual(metrics['missing_chars'],'')
        self.assertEqual(metrics['glyph_count'],95)
        for path in (ttf,otf):
            with TTFont(path) as font:self.assertEqual(set(font.getBestCmap()),set(range(32,127)))
            face=ImageFont.truetype(str(path),36)
            self.assertGreater(sum(face.getmask('O Missing 123 !?',mode='L')),0)

    def test_reference_notices_embedded_and_reserved_family_rejected(self):
        text='Copyright 2026 Synthetic Test Authors\nSIL OPEN FONT LICENSE Version 1.1\nSynthetic notice fixture.'
        obligation={'license':'OFL-1.1','license_text':text,'license_sha256':hashlib.sha256(text.encode()).hexdigest(),
                    'license_url':'https://openfontlicense.org/','source_url':'https://example.com/font/source'}
        metadata={'family':'MiFont Derived QA','license_obligations':[obligation]}
        ttf,otf=self.folder/'licensed.ttf',self.folder/'licensed.otf'
        build_font([ring()],metadata,ttf,otf)
        for path in (ttf,otf):
            with TTFont(path) as font:
                names={name.nameID:name.toUnicode() for name in font['name'].names if name.platformID==3}
                self.assertEqual(names[13],text)
                self.assertIn('Synthetic Test Authors',names[0])
                self.assertIn(obligation['source_url'],names[10])
                self.assertEqual(names[14],obligation['license_url'])
                if path.suffix=='.otf':self.assertEqual(font['CFF '].cff.topDictIndex[0].Notice,text)
        with self.assertRaisesRegex(FontError,'MiFont'):
            build_font([ring()],{**metadata,'family':'Reserved Source Family'},ttf,otf)
        with self.assertRaisesRegex(FontError,'notice changed'):
            build_font([ring()],{**metadata,'license_obligations':[{**obligation,'license_text':text+'x'}]},ttf,otf)

    def test_vietnamese_and_nonlatin_names_export_with_full_unicode_names(self):
        for family,style in [('MiFont Chữ ký','Thông thường'),('MiFont Café','Régulier'),('書体','標準')]:
            ttf,otf=self.folder/'unicode.ttf',self.folder/'unicode.otf'
            build_font([ring()],{'family':family,'style':style},ttf,otf)
            for path in (ttf,otf):
                with TTFont(path) as font:
                    self.assertEqual(font['name'].getDebugName(1),family)
                    self.assertEqual(font['name'].getDebugName(2),style)
                    if path.suffix=='.otf':
                        top=font['CFF '].cff.topDictIndex[0]
                        self.assertTrue(top.FamilyName)
                        self.assertTrue(top.Weight)
                        top.FamilyName.encode('ascii');top.Weight.encode('ascii')
                self.assertGreater(sum(ImageFont.truetype(str(path),32).getmask('O')),0)

    def test_invalid_assignments_fail_before_existing_files_change(self):
        ttf,otf=self.folder/'safe.ttf',self.folder/'safe.otf';ttf.write_bytes(b'sentinel');otf.write_bytes(b'sentinel')
        for records in ([ring(),ring()], [{**ring(),'char':''}], [{**ring(),'recording':[['moveTo',[[0,0]]]]}]):
            with self.assertRaises(FontError):build_font(records,{},ttf,otf)
            self.assertEqual(ttf.read_bytes(),b'sentinel');self.assertEqual(otf.read_bytes(),b'sentinel')
        for char in ('\x7f','\n','\ud800'):
            with self.assertRaises(FontError):normalize_glyph({**ring(),'char':char})

    def test_foreground_polarity_uses_full_image_for_tight_bold_crop(self):
        source=self.image()
        record=engine.extract_glyph(source,[20,15,71,100],{'char':'O','baseline_y':115,'reference_height':100})
        self.assertEqual(record['origin'],'observed')
        self.assertEqual(record['method'],'local-potrace')
        self.assertGreaterEqual(record['bbox'][0],38)
        self.assertLess(record['bbox'][2],540)
        self.assertLess(abs(record['bbox'][1]),2)
        self.assertAlmostEqual(record['bbox'][3],700,delta=2)
        self.assertGreaterEqual(sum(op=='moveTo' for op,_ in record['recording']),2)
        build_font([record],{},self.folder/'observed.ttf',self.folder/'observed.otf')
        face=ImageFont.truetype(str(self.folder/'observed.ttf'),100)
        mask=face.getmask('O',mode='L');rendered=Image.frombytes('L',mask.size,bytes(mask))
        self.assertEqual(rendered.getpixel((rendered.width//2,rendered.height//2)),0)
        self.assertGreater(rendered.getpixel((rendered.getbbox()[0]+2,rendered.height//2)),200)

    def test_potrace_group_transforms_are_applied_before_font_coordinates(self):
        data=b'<svg xmlns="http://www.w3.org/2000/svg"><g transform="translate(0 100) scale(0.1 -0.1)"><path d="M0 0 L1000 0 L1000 1000 L0 1000 Z"/></g></svg>'
        drawing=engine._svg_recording(data,(7,0,0,-7,40,700))
        glyph=normalize_glyph({'char':'A','recording':drawing,'advance_width':780})
        for actual,expected in zip(glyph['bbox'],[40,0,740,700]):self.assertAlmostEqual(actual,expected,places=6)

    def test_analysis_labels_and_ids_are_repeatable(self):
        source=self.image()
        one=engine.analyze_image(source,{'text':'OB'});two=engine.analyze_image(source,{'text':'OB'})
        self.assertEqual([r['char'] for r in one['regions']],['O','B'])
        self.assertEqual([r['id'] for r in one['regions']],[r['id'] for r in two['regions']])
        self.assertEqual(one['region_count'],2)
        self.assertTrue(all(r['needs_review'] for r in one['regions']))

    def test_guided_cuts_of_touching_script_are_marked_approximate(self):
        source=self.image();image=Image.open(source);draw=ImageDraw.Draw(image);draw.line((85,85,125,85),fill='black',width=2);image.save(source)
        result=engine.analyze_image(source,{'text':'OB'})
        self.assertEqual([r['char'] for r in result['regions']],['O','B'])
        self.assertEqual({r['label_source'] for r in result['regions']},{'guided_cut'})
        self.assertTrue(any('approximate' in warning for warning in result['warnings']))

    def test_ocr_is_only_a_reviewable_label_suggestion(self):
        source=self.image()
        result=engine.analyze_image(source,{'ocr_words':[{'text':'OB','box':[0,0,200,130]},{'text':'X','box':['bad',0,1,1]}]})
        self.assertEqual([r['char'] for r in result['regions']],['O','B'])
        self.assertTrue(all(r['needs_review'] and r['label_source']=='ocr_suggestion' for r in result['regions']))
        mismatch=engine.analyze_image(source,{'ocr_words':[{'text':'Wrong','box':[0,0,200,130]}]})
        self.assertTrue(all(r['char']=='' for r in mismatch['regions']))

    def test_alpha_color_and_light_ink_masks_keep_counters(self):
        alpha=self.image('alpha.png',True);result=engine.analyze_image(alpha)
        self.assertEqual(result['mask']['foreground'],'alpha')
        self.assertEqual(result['region_count'],2)
        image=Image.new('RGBA',(150,100),(12,13,15,255));draw=ImageDraw.Draw(image)
        draw.rectangle((20,20,60,80),fill=(190,20,15,255));draw.rectangle((90,20,120,80),fill='white')
        mask,_=engine._foreground(image,engine.normalize_options({'foreground':'color','ink_color':'#be140f'}))
        self.assertEqual(mask.getpixel((40,40)),255);self.assertEqual(mask.getpixel((100,40)),0)
        light,_=engine._foreground(image,engine.normalize_options({'foreground':'light'}))
        self.assertEqual(light.getpixel((100,40)),255);self.assertEqual(light.getpixel((0,0)),0)

    def test_border_suppression_changes_detection_not_observed_source(self):
        source=self.image();image=Image.open(source);ImageDraw.Draw(image).rectangle((0,0,199,129),outline='black',width=2);image.save(source)
        result=engine.analyze_image(source,{'ignore_border':True})
        self.assertEqual(result['region_count'],2)
        record=engine.extract_glyph(source,[0,0,10,130],{'foreground':'dark','char':'I'})
        self.assertTrue(record['recording'],'Extraction must preserve the explicitly selected frame crop')

    def test_exif_orientation_and_preview_strip_source_metadata(self):
        image=Image.new('RGB',(40,20),'white');exif=Image.Exif();exif[274]=6;exif[270]='private fixture metadata'
        source=self.folder/'oriented.jpg';image.save(source,exif=exif)
        info=engine.inspect_image(source);self.assertEqual((info['width'],info['height']),(20,40))
        preview=self.folder/'preview.png';engine.create_preview(source,preview)
        with Image.open(preview) as decoded:
            self.assertEqual(decoded.size,(20,40));self.assertFalse(decoded.getexif())

    def test_bounded_input_and_options_validation(self):
        source=self.image()
        with patch.object(engine,'MAX_INPUT_PIXELS',10):
            with self.assertRaisesRegex(FontError,'megapixel'):engine.inspect_image(source)
        for options in ({'foreground':'cloud'},{'threshold':float('nan')},{'unknown':1},{'ignore_border':'yes'}):
            with self.assertRaises(FontError):engine.normalize_options(options)
        with self.assertRaises(FontError):engine.extract_glyph(source,[0,0,999,999])


if __name__=='__main__':unittest.main()
