"""Optional Windows OCR suggestions. Files and recognition stay on this machine."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from PIL import Image, ImageOps


def suggest_labels(path):
    result = {'available':False,'engine':'Windows OCR','text':'','words':[],'warnings':[]}
    if sys.platform != 'win32':
        result['warnings']=['Windows OCR is unavailable. Enter the image text or label regions manually.']
        return result
    resource = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'helpers' / 'windows_ocr.ps1'
    if not resource.is_file():
        result['warnings']=['Local OCR helper is unavailable. Manual character mapping remains available.']
        return result
    powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    try:
        with Image.open(path) as opened:
            oriented = ImageOps.exif_transpose(opened).convert('RGBA')
            original = oriented.size
            matte = Image.new('RGBA', original, 'white'); matte.alpha_composite(oriented)
            bitmap = matte.convert('RGB'); bitmap.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
        with tempfile.TemporaryDirectory(prefix='mifont-ocr-') as temporary:
            image = Path(temporary) / 'reference.png'; bitmap.save(image)
            run = subprocess.run([str(powershell),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
                                  '-File',str(resource),'-ImagePath',str(image)],
                                 capture_output=True,timeout=35,creationflags=subprocess.CREATE_NO_WINDOW)
            if run.returncode or len(run.stdout) > 512000:
                raise ValueError('OCR helper unavailable')
            data=json.loads(run.stdout.decode('utf-8-sig'))
        if not data.get('available'):
            result['warnings']=[str(data.get('error') or 'No local OCR language is installed.')[:300]]
            return result
        scale_x,scale_y=original[0]/bitmap.width,original[1]/bitmap.height
        words=[]
        for item in data.get('words',[])[:1000]:
            box=item.get('box',[]);text=item.get('text','')
            if not isinstance(text,str) or not text or len(box)!=4:continue
            x,y,w,h=[float(v) for v in box]
            words.append({'text':text[:200],'box':[round(x*scale_x),round(y*scale_y),max(1,round(w*scale_x)),max(1,round(h*scale_y))],
                          'line_id':int(item.get('line_id',0))})
        return {**result,'available':True,'text':str(data.get('text',''))[:4000],'words':words,
                'warnings':['OCR labels are suggestions. Review connected, decorative and ambiguous letters.']}
    except (OSError,ValueError,TypeError,KeyError,subprocess.TimeoutExpired):
        result['warnings']=['Windows OCR could not recognize this image. Enter its text or label regions manually.']
        return result
