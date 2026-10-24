"""Synthetic-only regression fixtures; customer and official samples stay local."""
import ctypes
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import xml.etree.ElementTree as ET
from PIL import Image,ImageDraw
from vector_migic import engine


class FidelityTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.root=Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_flat_interiors_keep_distinct_inks_not_jpeg_blends(self):
        image=Image.new('RGBA',(240,200),'white')
        draw=ImageDraw.Draw(image)
        draw.rectangle((10,10,110,150),fill=(44,123,192))
        draw.rectangle((130,10,230,150),fill=(45,62,81))
        draw.rectangle((0,170,239,199),fill='black')
        source=self.root/'logo.jpg';image.convert('RGB').save(source,quality=87)
        with Image.open(source) as decoded:
            palette=engine._logo_palette(decoded.convert('RGBA'),16)
        self.assertEqual(len(palette),4)
        for ink in ((255,255,255),(44,123,192),(45,62,81),(0,0,0)):
            self.assertLess(min(sum((a-b)**2 for a,b in zip(ink,color)) for color in palette),36)

    def test_thin_distinct_accent_survives_palette_detection(self):
        image=Image.new('RGBA',(512,512),'white')
        draw=ImageDraw.Draw(image)
        draw.rectangle((20,20,490,490),fill=(40,120,190))
        draw.line((100,150,300,150),fill=(235,15,35),width=1)
        palette=engine._logo_palette(image,8)
        self.assertIn((235,15,35),palette)

    def test_photographic_noise_does_not_become_flat_logo_palette(self):
        import random
        generator=random.Random(31)
        image=Image.frombytes('RGBA',(64,64),bytes(channel for _ in range(4096) for channel in (*[generator.randrange(256) for _ in range(3)],255)))
        self.assertIsNone(engine._logo_palette(image,8))

    def test_high_color_antialiased_logo_auto_is_bounded_and_keeps_letter_hole(self):
        import random
        generator=random.Random(133)
        width,height=512,384
        pixels=bytes(channel for y in range(height) for x in range(width)
                     for channel in (x//2,30+y//2,20+(x+y)//32+generator.randrange(8),255))
        image=Image.frombytes('RGBA',(width,height),pixels)
        lettering=Image.new('L',(width*4,height*4),0)
        draw=ImageDraw.Draw(lettering)
        draw.rectangle((240,200,420,1250),fill=255)
        draw.ellipse((420,200,1400,1250),fill=255)
        draw.ellipse((650,410,1180,1040),fill=0)
        lettering=lettering.resize(image.size,Image.Resampling.LANCZOS)
        image=Image.composite(Image.new('RGBA',image.size,'white'),image,lettering)
        self.assertIsNone(image.getcolors(65536),'Fixture must exercise the high-color PNG failure.')
        source=self.root/'high-color-logo.png';image.save(source)
        output=self.root/'high-color-logo.svg'
        result=engine.vectorize(source,output,{'colors':0,'max_dimension':512})
        self.assertEqual(result['trace_backend'],'potrace-1.16')
        self.assertEqual(result['curve_backend'],'potrace')
        self.assertLessEqual(result['color_count'],32)
        self.assertLessEqual(result['path_count'],64)
        self.assertEqual(result['options']['colors'],0)
        prepared=engine._prepare(image.copy(),engine.normalize_options({'colors':0,'max_dimension':512}),[])
        self.assertTrue(all(channel>230 for channel in prepared.getpixel((80,150))[:3]))
        self.assertFalse(all(channel>230 for channel in prepared.getpixel((220,180))[:3]))
        expected=lettering.point(lambda value:255 if value>=128 else 0)
        white_pixels=bytes(255 if all(channel>230 for channel in pixel[:3]) else 0
                           for pixel in prepared.get_flattened_data())
        actual=Image.frombytes('L',prepared.size,white_pixels)
        from PIL import ImageChops
        intersection=ImageChops.darker(expected,actual).histogram()[255]
        union=ImageChops.lighter(expected,actual).histogram()[255]
        self.assertGreater(intersection/union,.985)
        root=ET.fromstring(output.read_bytes())
        white=[p for p in root.iter('{'+engine.SVG_NS+'}path')
               if p.get('fill') and all(int(p.get('fill')[n:n+2],16)>230 for n in (1,3,5))]
        self.assertTrue(white)
        self.assertTrue(any(re.search('[Cc]',p.get('d','')) for p in white))

    def test_auto_fallback_ignores_hidden_rgb_and_manual_palette_is_not_replaced(self):
        import random
        generator=random.Random(35)
        image=Image.frombytes('RGBA',(160,120),bytes(channel for _ in range(19200)
                         for channel in (*[generator.randrange(256) for _ in range(3)],0)))
        draw=ImageDraw.Draw(image)
        draw.rectangle((20,20,70,90),fill=(240,20,40,255))
        draw.rectangle((90,20,140,90),fill=(255,255,255,255))
        palette=engine._automatic_palette(image)
        self.assertEqual(set(palette),{(240,20,40),(255,255,255)})
        noisy=Image.effect_noise((96,96),65).convert('RGBA')
        prepared=engine._prepare(noisy,engine.normalize_options({'colors':0}),[])
        self.assertLessEqual(len(prepared.getcolors(256)),32)
        image=Image.new('RGBA',(180,140),'white')
        draw=ImageDraw.Draw(image)
        for index in range(12):
            draw.rectangle((index*15,10,index*15+14,130),fill=(index*20,240-index*15,30+index*17))
        options=engine.normalize_options({'colors':4})
        prepared=engine._prepare(image,options,[])
        self.assertEqual(options['colors'],4)
        self.assertLessEqual(len(prepared.getcolors(256)),4)

    def test_precision_backend_strict_palette_and_compound_holes(self):
        image=Image.new('RGBA',(180,140),(0,0,0,0))
        draw=ImageDraw.Draw(image)
        draw.rectangle((15,15,160,120),fill=(44,123,192,255))
        draw.ellipse((45,35,115,105),fill=(0,0,0,0))
        draw.line((20,125,160,125),fill=(220,20,50),width=2)
        source=self.root/'transparent.png';image.save(source)
        output=self.root/'vector.svg'
        result=engine.vectorize(source,output,{'colors':8})
        root=ET.fromstring(output.read_bytes())
        paths=list(root.iter('{'+engine.SVG_NS+'}path'))
        self.assertEqual(result['trace_backend'],'potrace-1.16')
        self.assertEqual(result['curve_backend'],'potrace')
        self.assertLessEqual(result['color_count'],8)
        self.assertTrue(any(len(re.findall('[Mm]',path.get('d','')))>1 for path in paths))
        self.assertNotIn('<metadata',output.read_text())
        self.assertNotIn('<!DOCTYPE',output.read_text())

    def test_opaque_adjacent_colors_have_opaque_underpainting(self):
        image=Image.new('RGB',(160,120),(200,20,40))
        ImageDraw.Draw(image).rectangle((80,0,159,119),fill=(25,80,210))
        source=self.root/'adjacent.png';image.save(source)
        output=self.root/'adjacent.svg'
        result=engine.vectorize(source,output,{'colors':2,'transparent':False})
        root=ET.fromstring(output.read_bytes())
        self.assertEqual(result['color_count'],2)
        self.assertEqual(root[0].tag,'{'+engine.SVG_NS+'}path')
        self.assertEqual(root[0].get('d'),'M0 0 H160 V120 H0 Z')

    def test_native_grid_protects_thin_inks_and_counter_holes(self):
        image=Image.new('RGBA',(180,140),'white')
        draw=ImageDraw.Draw(image)
        draw.ellipse((10,10,165,125),fill='black')
        palette=engine._logo_palette(image,16)
        self.assertTrue(engine._safe_aliased_grid(image,palette))
        draw.line((20,132,160,132),fill=(210,25,45),width=1)
        self.assertFalse(engine._safe_aliased_grid(image,engine._logo_palette(image,16)))
        prepared=engine._prepare(image.copy(),engine.normalize_options(),[])
        self.assertEqual(prepared.size,(360,280))
        draw.line((20,132,160,132),fill='white',width=1)
        draw.rectangle((70,60,71,61),fill='white')
        self.assertFalse(engine._safe_aliased_grid(image,engine._logo_palette(image,16)))
        draw.rectangle((70,60,71,85),fill='white')
        self.assertFalse(engine._safe_aliased_grid(image,engine._logo_palette(image,16)))

    def test_thin_gray_ink_is_not_erased_as_an_edge_blend(self):
        image=Image.new('RGBA',(240,180),'white')
        draw=ImageDraw.Draw(image)
        draw.rectangle((20,20,100,130),fill='black')
        draw.line((105,35,105,95),fill=(63,63,63),width=1)
        draw.line((110,110,180,110),fill=(63,63,63),width=2)
        self.assertIn((63,63,63),engine._logo_palette(image,16))
        prepared=engine._prepare(image.copy(),engine.normalize_options(),[])
        self.assertEqual(prepared.getpixel((210,120)),(63,63,63,255))
        self.assertEqual(prepared.getpixel((260,220)),(63,63,63,255))

    def test_unrelated_gray_fill_does_not_color_a_black_white_edge(self):
        image=Image.new('RGB',(120,80),'white')
        draw=ImageDraw.Draw(image)
        draw.rectangle((0,0,49,79),fill='black')
        draw.rectangle((5,55,20,70),fill=(63,63,63))
        draw.line((49,0,49,45),fill=(64,64,64))
        draw.line((50,0,50,45),fill=(192,192,192))
        assigned=engine._local_ink_assignment(image,Image.new('L',image.size,255),
                                             [(0,0,0),(255,255,255),(63,63,63)],1)
        for y in range(5,40):
            self.assertIn(assigned.getpixel((49,y)),((0,0,0),(255,255,255)))
            self.assertIn(assigned.getpixel((50,y)),((0,0,0),(255,255,255)))
        self.assertEqual(assigned.getpixel((10,60)),(63,63,63))

    def test_border_depth_does_not_place_background_ink_under_inner_edges(self):
        image=Image.new('RGBA',(200,180),(255,153,0,255))
        draw=ImageDraw.Draw(image)
        draw.rectangle((25,15,175,160),fill='black')
        draw.ellipse((60,35,140,115),fill='white')
        draw.rectangle((40,125,110,145),fill=(63,63,63))
        colors=sorted(((count,color[:3]) for count,color in image.getcolors(256)),reverse=True)
        inks,labels,ranks,depths,ordered,background=engine._opaque_color_layers(image,colors)
        self.assertEqual(inks[background],(255,153,0))
        self.assertLess(depths[inks.index((0,0,0))],depths[inks.index((255,255,255))])
        self.assertEqual(depths[inks.index((255,255,255))],depths[inks.index((63,63,63))])

    def test_photo_and_pixel_choices_keep_their_requested_backend(self):
        image=Image.new('RGB',(120,90),'white')
        ImageDraw.Draw(image).ellipse((10,10,100,75),fill=(30,90,220))
        source=self.root/'source.png';image.save(source)
        photo=engine.vectorize(source,self.root/'photo.svg',{'preset':'photo'})
        pixel=engine.vectorize(source,self.root/'pixel.svg',{'mode':'none'})
        self.assertEqual(photo['trace_backend'],'vtracer-0.6.15')
        self.assertEqual(pixel['trace_backend'],'vtracer-0.6.15')
        self.assertEqual((pixel['trace_width'],pixel['trace_height']),image.size)

    def test_timeout_terminates_actual_cli_child(self):
        bitmap=self.root/'mask.pbm';Image.new('1',(128,128),0).save(bitmap,format='PPM')
        created=[]
        original=subprocess.Popen
        def owned_process(*args,**kwargs):
            process=original(*args,**kwargs);created.append(process);return process
        with mock.patch.object(engine,'POTRACE_TIMEOUT_SECONDS',0),mock.patch.object(engine.subprocess,'Popen',side_effect=owned_process):
            with self.assertRaisesRegex(engine.VectorizationError,'timed out'):
                engine._run_precision_mask(engine._precision_executable(),bitmap,self.root/'mask.svg',engine.normalize_options(),1)
        self.assertEqual(len(created),1)
        self.assertIsNotNone(created[0].poll())

    @unittest.skipUnless(os.name=='nt','Windows job lifecycle')
    def test_killing_worker_terminates_its_owned_descendant(self):
        script="""from vector_migic.engine import _ensure_cli_job
import subprocess,sys,time
_ensure_cli_job()
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
print(child.pid,flush=True)
time.sleep(60)
"""
        worker=subprocess.Popen([sys.executable,'-c',script],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
                                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        process_handle=None
        try:
            line=worker.stdout.readline().strip()
            self.assertTrue(line.isdigit(),worker.stderr.read() if worker.poll() is not None else line)
            child_pid=int(line)
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.OpenProcess.argtypes=(ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong)
            kernel.OpenProcess.restype=ctypes.c_void_p
            kernel.GetExitCodeProcess.argtypes=(ctypes.c_void_p,ctypes.POINTER(ctypes.c_ulong))
            kernel.CloseHandle.argtypes=(ctypes.c_void_p,)
            process_handle=kernel.OpenProcess(0x100000|0x1000|1,False,child_pid)
            self.assertTrue(process_handle)
            worker.terminate();worker.wait(timeout=5)
            deadline=time.monotonic()+5
            while True:
                code=ctypes.c_ulong()
                self.assertTrue(kernel.GetExitCodeProcess(process_handle,ctypes.byref(code)))
                if code.value!=259:break
                if time.monotonic()>deadline:
                    kernel.TerminateProcess.argtypes=(ctypes.c_void_p,ctypes.c_uint)
                    kernel.TerminateProcess(process_handle,1)
                    self.fail('Owned descendant survived worker cancellation.')
                time.sleep(.02)
        finally:
            if worker.poll() is None:worker.kill();worker.wait(timeout=3)
            if process_handle:kernel.CloseHandle(process_handle)
            worker.stdout.close();worker.stderr.close()


if __name__=='__main__':unittest.main()
