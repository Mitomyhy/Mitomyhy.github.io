"""Offline image-to-font outlines with explicit, editable character mapping."""
from __future__ import annotations
from collections import Counter
import ctypes
import hashlib
from io import BytesIO
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import warnings
import xml.etree.ElementTree as ET

from PIL import Image,ImageChops,ImageMath,ImageOps,UnidentifiedImageError
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.transformPen import TransformPen
from fontTools.misc.transform import Transform,Identity
from fontTools.svgLib.path import parse_path

try:
    from .font_export import FontError,ASCII_CHARSET,normalize_glyph,glyph_preview,build_font
except ImportError:
    from font_export import FontError,ASCII_CHARSET,normalize_glyph,glyph_preview,build_font

MAX_FILE_BYTES=32*1024*1024
MAX_INPUT_PIXELS=16_000_000
FORMATS={'PNG','JPEG','WEBP','BMP','TIFF'}
POTRACE_SHA256='f1bcde040ae6576dc9c5868be7a72064957c5aea08e1dece240428319fc672a4'
DEFAULT_OPTIONS={'foreground':'auto','ink_color':'#000000','threshold':'auto','color_tolerance':64,
                 'min_area':8,'ignore_border':True,'smoothing':.5,'speckle':0,'side_bearing':40,'cap_height':700,
                 'x_height':500,'baseline_y':None,'reference_height':None,'char':'','text':'','ocr_words':[]}
_POTRACE_JOB=None


def _num(value,name,low,high,integer=False):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:
        raise FontError(f'{name} must be a finite number between {low} and {high}.')
    if integer and value!=int(value):raise FontError(f'{name} must be a whole number.')
    return int(value) if integer else float(value)


def normalize_options(options=None):
    options={} if options is None else options
    if not isinstance(options,dict):raise FontError('Image options must be an object.')
    if set(options)-set(DEFAULT_OPTIONS):raise FontError('Unknown image option: '+', '.join(sorted(set(options)-set(DEFAULT_OPTIONS))))
    result={**DEFAULT_OPTIONS,**options}
    if result['foreground'] not in {'auto','dark','light','color'}:raise FontError('Foreground must be auto, dark, light or color.')
    if not isinstance(result['ignore_border'],bool):raise FontError('ignore_border must be true or false.')
    if not isinstance(result['ink_color'],str) or not re.fullmatch('#[0-9a-fA-F]{6}',result['ink_color']):raise FontError('Ink color must be #RRGGBB.')
    if result['threshold']!='auto':result['threshold']=_num(result['threshold'],'Threshold',1,254,True)
    for name,low,high in [('color_tolerance',8,255),('min_area',1,256),('speckle',0,128),('side_bearing',0,300),('cap_height',100,1500),('x_height',50,1500)]:
        result[name]=_num(result[name],name,low,high,True)
    result['smoothing']=_num(result['smoothing'],'Smoothing',0,1)
    for name in ('baseline_y','reference_height'):
        if result[name] is not None:result[name]=_num(result[name],name,0 if name=='baseline_y' else 1,100000)
    if not isinstance(result['char'],str) or len(result['char'])>1 or (result['char'] and not result['char'].isprintable()):raise FontError('Assign one printable character per crop.')
    if not isinstance(result['text'],str) or len(result['text'])>2000:raise FontError('Reference text may contain at most 2,000 characters.')
    if not isinstance(result['ocr_words'],list) or len(result['ocr_words'])>1000:raise FontError('OCR suggestions must be a bounded word list.')
    return result


def _read_image(path):
    path=Path(path)
    if not path.is_file() or path.stat().st_size>MAX_FILE_BYTES:raise FontError('Choose an existing image no larger than 32 MiB.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(path) as opened:
                if opened.format not in FORMATS:raise FontError('Use PNG, JPG, WebP, BMP or TIFF.')
                if opened.width*opened.height>MAX_INPUT_PIXELS:raise FontError('Image exceeds the 16 megapixel limit.')
                info={'width':opened.width,'height':opened.height,'format':opened.format,'has_alpha':'A' in opened.getbands() or 'transparency' in opened.info}
                info['warnings']=['Only the first frame/page is analyzed.'] if getattr(opened,'n_frames',1)>1 else []
                opened.load();oriented=ImageOps.exif_transpose(opened).convert('RGBA')
                image=Image.frombytes('RGBA',oriented.size,oriented.tobytes());info.update(width=image.width,height=image.height)
                return image,info
    except FontError:raise
    except (OSError,ValueError,UnidentifiedImageError,Image.DecompressionBombWarning,Image.DecompressionBombError) as exc:
        raise FontError('This image could not be decoded safely.') from exc


def inspect_image(path):
    image,info=_read_image(path);image.close();return info


def create_preview(path,out_png,max_dimension=1600):
    max_dimension=_num(max_dimension,'Preview size',64,4096,True)
    image,info=_read_image(path)
    try:
        image.thumbnail((max_dimension,max_dimension),Image.Resampling.LANCZOS)
        stream=BytesIO();image.save(stream,format='PNG');Path(out_png).write_bytes(stream.getvalue())
        return {**info,'preview_width':image.width,'preview_height':image.height}
    finally:image.close()


def _otsu(histogram):
    total=sum(histogram);weighted=sum(i*n for i,n in enumerate(histogram));weight=0;before=0;best=-1;threshold=128
    for value,count in enumerate(histogram):
        weight+=count;before+=value*count
        if not weight or weight==total:continue
        score=weight*(total-weight)*(before/weight-(weighted-before)/(total-weight))**2
        if score>best:best=score;threshold=value
    return max(64,min(192,threshold))


def _foreground(image,options):
    alpha=image.getchannel('A');visible=alpha.point(lambda a:255 if a>=128 else 0)
    if options['foreground']=='auto' and visible.histogram()[0]>image.width*image.height*.01:
        return visible,{'foreground':'alpha','threshold':128}
    matte=Image.new('RGBA',image.size,'white');matte.alpha_composite(image);rgb=matte.convert('RGB')
    mode=options['foreground'];selected=None;background=None
    if mode in {'auto','color'}:
        small=rgb.copy();small.thumbnail((256,256),Image.Resampling.LANCZOS)
        quantized=small.quantize(colors=12,method=Image.Quantize.MEDIANCUT,dither=Image.Dither.NONE)
        entries=sorted(quantized.getcolors(),reverse=True);pal=quantized.getpalette()
        colors=[(count,tuple(pal[index*3:index*3+3])) for count,index in entries]
        background=colors[0][1]
        if mode=='color':selected=tuple(int(options['ink_color'][n:n+2],16) for n in (1,3,5))
        else:selected=max(colors,key=lambda item:item[0]*sum((a-b)**2 for a,b in zip(item[1],background)))[1]
        direction=tuple(a-b for a,b in zip(selected,background));length=sum(v*v for v in direction)
        if length<16**2:raise FontError('No contrasting ink was found. Select a crop or sample an ink color.')
        r,g,b=[channel.convert('F') for channel in rgb.split()]
        strength=ImageMath.lambda_eval(lambda p:((p['r']-background[0])*direction[0]+(p['g']-background[1])*direction[1]+(p['b']-background[2])*direction[2])*255/length,r=r,g=g,b=b).convert('L')
        # Other-colored headings/graphics are not part of the sampled ink.
        projection=ImageMath.lambda_eval(lambda p:((p['r']-background[0])*direction[0]+(p['g']-background[1])*direction[1]+(p['b']-background[2])*direction[2])/length,r=r,g=g,b=b)
        residual=ImageMath.lambda_eval(lambda p:(p['r']-background[0]-p['t']*direction[0])**2+(p['g']-background[1]-p['t']*direction[1])**2+(p['b']-background[2]-p['t']*direction[2])**2,r=r,g=g,b=b,t=projection)
        allowed=ImageMath.lambda_eval(lambda p:(p['d']<=options['color_tolerance']**2)*255,d=residual).convert('L')
    else:
        strength=rgb.convert('L');allowed=Image.new('L',image.size,255)
        if mode=='dark':strength=ImageOps.invert(strength)
    threshold=_otsu(strength.histogram()) if options['threshold']=='auto' else options['threshold']
    mask=ImageChops.multiply(strength.point(lambda v:255 if v>threshold else 0),allowed)
    mask=ImageChops.multiply(mask,visible)
    return mask,{'foreground':mode,'threshold':threshold,'ink_color':selected,'background_color':background}


def _runs(values):
    runs=[];start=None
    for index,value in enumerate(list(values)+[False]):
        if value and start is None:start=index
        elif not value and start is not None:runs.append([start,index]);start=None
    return runs


def _analysis_mask(mask,options):
    """Suppress border frames and isolated dust for detection, never extraction."""
    from array import array
    width,height=mask.size;pixels=bytearray(mask.tobytes())
    if options['ignore_border']:
        seeds=set(range(width))|set(range((height-1)*width,height*width))|{y*width for y in range(height)}|{y*width+width-1 for y in range(height)}
        todo=array('I')
        for p in seeds:
            if pixels[p]:pixels[p]=0;todo.append(p)
        while todo:
            p=todo.pop();x,y=p%width,p//width
            for yy in range(max(0,y-1),min(height,y+2)):
                for xx in range(max(0,x-1),min(width,x+2)):
                    q=yy*width+xx
                    if pixels[q]:pixels[q]=0;todo.append(q)
    output=bytearray(width*height);minimum=options['min_area']
    for start,value in enumerate(pixels):
        if not value:continue
        pixels[start]=0;todo=array('I',[start]);prefix=array('I');area=0
        while todo:
            p=todo.pop();area+=1
            if area<minimum:prefix.append(p)
            else:
                if area==minimum:
                    for q in prefix:output[q]=255
                output[p]=255
            x,y=p%width,p//width
            for yy in range(max(0,y-1),min(height,y+2)):
                for xx in range(max(0,x-1),min(width,x+2)):
                    q=yy*width+xx
                    if pixels[q]:pixels[q]=0;todo.append(q)
    return Image.frombytes('L',mask.size,bytes(output))


def _guided_boxes(mask,box,text):
    x,y,w,h=box;count=len(text)
    if count<1 or count>w:raise FontError('The reference text does not fit this crop.')
    crop=mask.crop((x,y,x+w,y+h));weights=[sum(crop.crop((i,0,i+1,h)).histogram()[1:]) for i in range(w)]
    proportions=[.45 if c in 'ilI!.,:;|\'' else 1.35 if c in 'MWmw@' else 1 for c in text]
    total=sum(proportions);cuts=[0];acc=0
    for index,portion in enumerate(proportions[:-1]):
        acc+=portion;expected=w*acc/total;radius=max(2,round(w/count*.35))
        low=max(cuts[-1]+1,round(expected)-radius);high=min(w-(count-index-1),round(expected)+radius)
        cut=min(range(low,max(low+1,high+1)),key=lambda p:weights[p]+abs(p-expected)*max(1,h*.12))
        cuts.append(cut)
    cuts.append(w)
    return [[x+cuts[i],y,cuts[i+1]-cuts[i],h] for i in range(count)]


def analyze_image(inputpath,options=None,progress=None):
    options=normalize_options(options);image,info=_read_image(inputpath)
    try:
        mask,mask_info=_foreground(image,options)
        mask=_analysis_mask(mask,options)
        if not mask.getbbox():raise FontError('No foreground remains. Change foreground or threshold.')
        width,height=mask.size;data=mask.tobytes()
        active=[any(data[y*width:(y+1)*width]) for y in range(height)];bands=_runs(active)
        merged=[]
        for band in bands:
            if merged:
                previous=merged[-1];a=previous[1]-previous[0];b=band[1]-band[0];gap=band[0]-previous[1]
                if gap<=2 or (a<b*.4 and gap<b*.6):previous[1]=band[1];continue
            merged.append(band)
        regions=[];lines=[]
        for line,(top,bottom) in enumerate(merged):
            crop=mask.crop((0,top,width,bottom));column=[crop.crop((x,0,x+1,bottom-top)).getbbox() is not None for x in range(width)]
            boxes=[]
            for left,right in _runs(column):
                ink=crop.crop((left,0,right,bottom-top));bounds=ink.getbbox()
                if ink.histogram()[255]<options['min_area']:continue
                boxes.append([left+bounds[0],top+bounds[1],bounds[2]-bounds[0],bounds[3]-bounds[1]])
            if not boxes:continue
            bottoms=sorted(b[1]+b[3] for b in boxes if b[3]>=(bottom-top)*.55)
            baseline=bottoms[len(bottoms)//2] if bottoms else bottom
            reference=max(1,sorted([b[3] for b in boxes],reverse=True)[max(0,len(boxes)//4)])
            lines.append({'id':line,'box':[min(b[0] for b in boxes),top,max(b[0]+b[2] for b in boxes)-min(b[0] for b in boxes),bottom-top],
                          'baseline_y':baseline,'reference_height':reference})
            for box in boxes:regions.append({'line_id':line,'box':box,'baseline_y':baseline,'reference_height':reference,'char':'','needs_review':True})
        notes=list(info['warnings']);text=options['text'];chars=[c for c in text if not c.isspace()]
        if chars:
            if len(chars)==len(regions):
                for region,char in zip(regions,chars):region.update(char=char,label_source='reference_text')
            else:
                texts=[line for line in text.splitlines() if line.strip()]
                if len(lines)==1 and len(texts)==1:
                    line=lines[0];boxes=_guided_boxes(mask,line['box'],''.join(chars));regions=[]
                    for box,char in zip(boxes,chars):regions.append({'line_id':line['id'],'box':box,'char':char,'baseline_y':line['baseline_y'],'reference_height':line['reference_height'],'label_source':'guided_cut','needs_review':True})
                    notes.append('Text-guided cuts are approximate; adjust touching or overlapping letters before extraction.')
                else:notes.append('Region count differs from reference text. Edit crops or assign characters manually.')
        elif options['ocr_words']:
            for word in options['ocr_words']:
                if not isinstance(word,dict) or not isinstance(word.get('text'),str) or not isinstance(word.get('box'),list) or len(word['box'])!=4:continue
                if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in word['box']):continue
                x,y,w,h=word['box'];matches=[r for r in regions if x<=r['box'][0]+r['box'][2]/2<=x+w and y<=r['box'][1]+r['box'][3]/2<=y+h]
                letters=[c for c in word['text'] if c in ASCII_CHARSET and c!=' ']
                if len(matches)==len(letters):
                    for region,char in zip(sorted(matches,key=lambda r:r['box'][0]),letters):region.update(char=char,label_source='ocr_suggestion',needs_review=True)
        if not regions:raise FontError('No usable glyph regions were found. Adjust foreground or crop manually.')
        image_key=hashlib.sha256(image.tobytes()).hexdigest()
        for index,region in enumerate(regions):
            region['id']=hashlib.sha256((image_key+':'+','.join(map(str,region['box']))).encode()).hexdigest()[:24]
            region['index']=index
        notes.append('Connected script, swashes and decorative graphics may require editable cuts. OCR labels are suggestions.')
        bx0,by0,bx1,by1=mask.getbbox()
        return {**info,'regions':regions,'lines':lines,'mask':mask_info,'foreground_box':[bx0,by0,bx1-bx0,by1-by0],'warnings':notes,'region_count':len(regions)}
    finally:image.close()


def _precision_executable():
    executable=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))/'vendor/potrace/potrace.exe'
    if not executable.is_file() or hashlib.sha256(executable.read_bytes()).hexdigest()!=POTRACE_SHA256:
        raise FontError('The local outline tracing component is missing or changed. Repair MiFont.')
    return executable


def _ensure_cli_job():
    global _POTRACE_JOB
    if _POTRACE_JOB is not None:return
    if os.name!='nt':return
    from ctypes import wintypes
    class Basic(ctypes.Structure):
        _fields_=[('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),('flags',wintypes.DWORD),('minimum',ctypes.c_size_t),('maximum',ctypes.c_size_t),('active',wintypes.DWORD),('affinity',ctypes.c_size_t),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
    class IO(ctypes.Structure):_fields_=[(name,ctypes.c_uint64) for name in ('read','write','other','read_bytes','write_bytes','other_bytes')]
    class Extended(ctypes.Structure):_fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateJobObjectW.argtypes=(ctypes.c_void_p,wintypes.LPCWSTR);kernel.CreateJobObjectW.restype=ctypes.c_void_p
    kernel.SetInformationJobObject.argtypes=(ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD);kernel.SetInformationJobObject.restype=wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes=(ctypes.c_void_p,ctypes.c_void_p);kernel.AssignProcessToJobObject.restype=wintypes.BOOL
    kernel.GetCurrentProcess.restype=ctypes.c_void_p;kernel.CloseHandle.argtypes=(ctypes.c_void_p,)
    handle=kernel.CreateJobObjectW(None,None);limits=Extended();limits.basic.flags=0x2000
    if not handle or not kernel.SetInformationJobObject(handle,9,ctypes.byref(limits),ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle,kernel.GetCurrentProcess()):
        if handle:kernel.CloseHandle(handle)
        raise FontError('Could not protect the local tracing process.')
    _POTRACE_JOB=(kernel,handle)


def _svg_recording(data,normalization):
    """Apply inherited Potrace group transforms before font normalization."""
    root=ET.fromstring(data);recording=RecordingPen()
    def visit(element,transform):
        text=element.get('transform','');remaining=text
        for match in re.finditer(r'(matrix|translate|scale)\s*\(([^)]*)\)',text):
            args=[float(v) for v in re.findall(r'[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?',match.group(2))]
            kind=match.group(1)
            if kind=='matrix' and len(args)==6:local=Transform(*args)
            elif kind=='translate' and len(args) in (1,2):local=Identity.translate(args[0],args[1] if len(args)==2 else 0)
            elif kind=='scale' and len(args) in (1,2):local=Identity.scale(args[0],args[1] if len(args)==2 else args[0])
            else:raise FontError('Unsupported outline transform.')
            transform=transform.transform(local);remaining=remaining.replace(match.group(0),'',1)
        if remaining.strip():raise FontError('Unsupported outline transform.')
        if element.tag.rsplit('}',1)[-1]=='path':parse_path(element.get('d',''),TransformPen(recording,transform))
        for child in element:visit(child,transform)
    visit(root,Transform(*normalization))
    return recording.value


def extract_glyph(inputpath,box,options=None,progress=None):
    options=normalize_options(options);image,info=_read_image(inputpath)
    try:
        if not isinstance(box,(list,tuple)) or len(box)!=4:raise FontError('Crop must be [x,y,width,height].')
        x,y,w,h=[_num(v,'Crop coordinate',0 if i<2 else 1,100000,True) for i,v in enumerate(box)]
        if x+w>image.width or y+h>image.height or w*h>4_000_000:raise FontError('Crop exceeds the image or 4 megapixel tracing limit.')
        # Keep the image's ink/background decision when a tight crop contains
        # mostly ink; reclassifying that crop would invert bold letterforms.
        full_mask,mask_info=_foreground(image,options)
        mask=full_mask.crop((x,y,x+w,y+h));ink=mask.getbbox()
        if not ink:raise FontError('This crop contains no selected ink.')
        reference=options['reference_height'] or ink[3]-ink[1]
        scale=options['cap_height']/reference
        baseline=(options['baseline_y']-y) if options['baseline_y'] is not None else ink[3]
        shift_x=options['side_bearing']-ink[0]*scale
        _ensure_cli_job()
        with tempfile.TemporaryDirectory(prefix='mifont-trace-') as folder:
            bitmap=Path(folder)/'glyph.pbm';output=Path(folder)/'glyph.svg'
            ImageOps.invert(mask).convert('1',dither=Image.Dither.NONE).save(bitmap,format='PPM')
            args=[str(_precision_executable()),'--svg','--flat','--unit','100','--alphamax',str(.8+.4*options['smoothing']),
                  '--opttolerance',str(.05+.3*options['smoothing']),'--turdsize',str(options['speckle']),'--output',str(output),str(bitmap)]
            process=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:process.wait(timeout=30)
            except subprocess.TimeoutExpired as exc:
                process.kill();process.wait(timeout=5);raise FontError('Glyph tracing timed out. Use a smaller crop.') from exc
            if process.returncode or not output.is_file() or output.stat().st_size>8*1024*1024:raise FontError('Glyph outline tracing failed or exceeded its limit.')
            recording=_svg_recording(output.read_bytes(),(scale,0,0,-scale,shift_x,baseline*scale))
        result={'char':options['char'],'origin':'observed','recording':[[op,[list(p) if p is not None else None for p in points]] for op,points in recording],
                'advance_width':round((ink[2]-ink[0])*scale+2*options['side_bearing']),
                'source_box':[x,y,w,h],'baseline_y':y+baseline,'reference_height':reference,
                'mask':mask_info,'needs_review':True,'method':'local-potrace','units_per_em':1000}
        return normalize_glyph(result)
    finally:image.close()
