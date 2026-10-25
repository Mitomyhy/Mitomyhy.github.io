"""Build editable, installable fonts from independently reconstructed outlines."""
from __future__ import annotations
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import tempfile
import unicodedata
import xml.etree.ElementTree as ET

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.recordingPen import RecordingPen,replayRecording
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

UNITS_PER_EM=1000
ASCII_CHARSET=''.join(chr(code) for code in range(32,127))
SVG_NS='http://www.w3.org/2000/svg'


class FontError(ValueError):
    pass


def _number(value,name,low=-16000,high=16000):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:
        raise FontError(f'{name} must be a finite number between {low} and {high}.')
    return float(value)


def _recording(value):
    if not isinstance(value,list) or len(value)>20000:
        raise FontError('Glyph recording must contain at most 20,000 outline commands.')
    result=[];open_contour=False
    for item in value:
        if not isinstance(item,(list,tuple)) or len(item)!=2:
            raise FontError('Invalid glyph outline command.')
        operation,points=item
        if operation not in {'moveTo','lineTo','curveTo','qCurveTo','closePath','endPath'} or not isinstance(points,(list,tuple)):
            raise FontError('Unsupported glyph outline command.')
        if operation in {'moveTo','lineTo'} and len(points)!=1:
            raise FontError('A move or line command needs one point.')
        if operation=='curveTo' and len(points)!=3:
            raise FontError('A cubic command needs three points.')
        if operation=='qCurveTo' and not 2<=len(points)<=32:
            raise FontError('A quadratic command needs two through 32 points.')
        if operation in {'closePath','endPath'} and points:
            raise FontError('A closing command cannot contain points.')
        if operation=='moveTo':
            if open_contour:raise FontError('Close the current contour before moving to another.')
            open_contour=True
        elif not open_contour:
            raise FontError('Start each contour with moveTo.')
        normalized=[]
        for index,point in enumerate(points):
            if point is None and operation=='qCurveTo' and index==len(points)-1:
                normalized.append(None);continue
            if not isinstance(point,(list,tuple)) or len(point)!=2:
                raise FontError('An outline point needs x and y coordinates.')
            normalized.append((_number(point[0],'Glyph x'),_number(point[1],'Glyph y')))
        operation='closePath' if operation=='endPath' else operation
        if operation=='closePath':open_contour=False
        result.append((operation,tuple(normalized)))
    if open_contour:raise FontError('Font contours must be closed.')
    return result


def _preview(normalized):
    pen=SVGPathPen(None)
    replayRecording(normalized['recording'],TransformPen(pen,(1,0,0,-1,0,0)))
    x0,y0,x1,y1=normalized['bbox'] or [0,0,normalized['advance_width'],700]
    left=min(0,x0)-30;right=max(normalized['advance_width'],x1)+30
    top=min(-800,-y1)-30;bottom=max(200,-y0)+30
    root=ET.Element('svg',{'xmlns':SVG_NS,'viewBox':f'{left:g} {top:g} {max(1,right-left):g} {max(1,bottom-top):g}',
                           'width':'400','height':'400'})
    if pen.getCommands():ET.SubElement(root,'path',{'d':pen.getCommands(),'fill':'#111111','fill-rule':'nonzero'})
    return ET.tostring(root,encoding='unicode')


def normalize_glyph(record,preview=True):
    """Validate one JSON-safe Y-up 1000 UPM outline, preserving provenance."""
    if not isinstance(record,dict):raise FontError('A glyph record must be an object.')
    try:
        serialized=json.dumps(record,allow_nan=False)
        if len(serialized)>3_000_000:raise FontError('Glyph record is too large.')
    except (TypeError,ValueError) as exc:
        raise FontError('Glyph records must contain finite JSON-safe values.') from exc
    char=record.get('char','')
    if not isinstance(char,str) or len(char)>1 or (char and not char.isprintable()):
        raise FontError('Assign one printable character to each glyph.')
    drawing=_recording(record.get('recording',[]))
    bounds=BoundsPen(None);replayRecording(drawing,bounds)
    bbox=list(bounds.bounds) if bounds.bounds else None
    advance=round(_number(record.get('advance_width',max(150,(bbox[2] if bbox else 260)+40)),'Advance width',1,16000))
    result=deepcopy(record)
    result.update(char=char,recording=[[op,[list(point) if point is not None else None for point in args]] for op,args in drawing],
                  advance_width=advance,lsb=round(bbox[0]) if bbox else 0,bbox=bbox,
                  units_per_em=1000,origin=record.get('origin','observed'))
    if result['origin'] not in {'observed','generated'}:raise FontError('Glyph origin must be observed or generated.')
    if preview:result['preview_svg']=_preview(result)
    return result


def glyph_preview(record):
    """Return safe path-only SVG; never modifies the supplied record."""
    return _preview(normalize_glyph(record,preview=False))


def _fallback_notdef():
    return [('moveTo',((50,0),)),('lineTo',((550,0),)),('lineTo',((550,700),)),('lineTo',((50,700),)),('closePath',()),
            ('moveTo',((120,70),)),('lineTo',((120,630),)),('lineTo',((480,630),)),('lineTo',((480,70),)),('closePath',())]


def _metadata(metadata):
    metadata=metadata or {}
    if not isinstance(metadata,dict):raise FontError('Font metadata must be an object.')
    family=metadata.get('family_name',metadata.get('family','MiFont Custom'))
    style=metadata.get('style_name',metadata.get('style','Regular'))
    if not isinstance(family,str) or not family.strip() or len(family)>80 or any(ord(c)<32 for c in family):raise FontError('Choose a font family name of 1 through 80 characters.')
    if not isinstance(style,str) or not style.strip() or len(style)>40 or any(ord(c)<32 for c in style):raise FontError('Choose a font style name of 1 through 40 characters.')
    if metadata.get('units_per_em',1000)!=1000:raise FontError('MiFont projects use 1000 units per em.')
    if reference_name_conflict(family,metadata.get('license_obligations',[])):
        raise FontError('Choose a different font family name; this name belongs to the selected open-font reference.')
    ascii_name=unicodedata.normalize('NFKD',family+'-'+style).encode('ascii','ignore').decode()
    ps_name=re.sub('[^A-Za-z0-9-]','',ascii_name)[:63] or 'MiFont-Custom'
    ascender=round(_number(metadata.get('ascender',800),'Ascender',100,2000))
    descender=round(_number(metadata.get('descender',-200),'Descender',-2000,0))
    cap=round(_number(metadata.get('cap_height',700),'Cap height',100,1500))
    xheight=round(_number(metadata.get('x_height',500),'X height',50,1500))
    return family.strip(),style.strip(),ps_name,ascender,descender,cap,xheight


def reference_name_conflict(family,obligations):
    """Protect actual source/reserved names without imposing an app prefix."""
    if isinstance(obligations,dict):obligations=[obligations]
    if not isinstance(obligations,list):return False  # _notices validates its shape.
    def key(value):return ''.join(c for c in unicodedata.normalize('NFKC',value).casefold() if c.isalnum())
    reserved=[]
    for item in obligations:
        if not isinstance(item,dict):continue
        if isinstance(item.get('family'),str):reserved.append(item['family'])
        text=item.get('license_text','')
        if not isinstance(text,str):continue
        # Only the copyright/header can declare RFNs; the standard OFL body
        # contains definitions that must never be mistaken for a declaration.
        header=text.split('PREAMBLE',1)[0][:2500]
        for match in re.finditer(r'Reserved Font Names?(?:\(s\))?\s*[:=]?\s*[\"\'\u201c]?([A-Za-z][A-Za-z0-9 _-]{1,79})',header,re.I):
            name=match.group(1).strip()
            if name.lower() not in {'version','refer','refers to any names specified as such after the'}:reserved.append(name)
    return bool(key(family)) and key(family) in {key(value) for value in reserved if value}


def _notices(metadata):
    """Preserve supplied reference notices in both OpenType naming systems."""
    result={}
    for key,limit in [('copyright',24000),('license_text',120000),('license_url',4000),('source_url',4000),('description',16000)]:
        value=metadata.get(key,'')
        if not isinstance(value,str) or len(value)>limit or any(ord(c)<32 and c not in '\n\r\t' for c in value):
            raise FontError(f'Invalid or oversized font metadata: {key}.')
        if value:result[key]=value
    obligations=metadata.get('license_obligations',[])
    if isinstance(obligations,dict):obligations=[obligations]
    if not isinstance(obligations,list) or len(obligations)>16:
        raise FontError('Font reference obligations must be a bounded list.')
    texts=[];urls=[];sources=[]
    for item in obligations:
        if not isinstance(item,dict) or item.get('license')!='OFL-1.1':raise FontError('Unsupported font reference license obligations.')
        text=item.get('license_text','');url=item.get('license_url','');source=item.get('source_url','')
        if not isinstance(text,str) or not text or len(text)>24000:raise FontError('Include the complete open-font license notice before exporting.')
        if item.get('license_sha256') and hashlib.sha256(text.encode('utf-8')).hexdigest()!=item['license_sha256']:
            raise FontError('The reference license notice changed. Select the reference again.')
        for value in (url,source):
            if not isinstance(value,str) or len(value)>4000 or (value and not value.startswith('https://')):raise FontError('Use HTTPS reference source and license URLs.')
        if text not in texts:texts.append(text)
        if url and url not in urls:urls.append(url)
        if source and source not in sources:sources.append(source)
    if texts:result['license_text']='\n\n'.join(texts)
    if texts and not result.get('copyright'):
        copyright_lines=[]
        for text in texts:
            for line in text.splitlines():
                if ('copyright' in line.lower() or '\u00a9' in line) and line.strip() not in copyright_lines:
                    copyright_lines.append(line.strip())
        if copyright_lines:result['copyright']='\n'.join(copyright_lines)
    if urls:result['license_url']='\n'.join(urls)
    if sources:
        references='Reference sources: '+'; '.join(sources)
        result['description']=(result.get('description','')+'\n'+references).strip()
    elif result.get('source_url'):
        result['description']=(result.get('description','')+'\nReference source: '+result['source_url']).strip()
    if any(len(value.encode('utf-16-be'))>60000 for value in result.values()):
        raise FontError('Combined font notices exceed the OpenType name record limit. Use fewer reference families.')
    return result


def _font_bytes(records,metadata,is_ttf):
    family,style,ps_name,ascender,descender,cap,xheight=_metadata(metadata)
    notices=_notices(metadata or {})
    names={char:('space' if char==' ' else f'uni{ord(char):04X}') for char in records}
    order=['.notdef']+[names[c] for c in sorted(records,key=ord)]
    drawings={'.notdef':_fallback_notdef()};metrics={'.notdef':(600,50)}
    for char,record in records.items():
        name=names[char];drawings[name]=record['recording'];metrics[name]=(record['advance_width'],record['lsb'])
    builder=FontBuilder(1000,isTTF=is_ttf);builder.setupGlyphOrder(order)
    builder.setupCharacterMap({ord(char):name for char,name in names.items()})
    if is_ttf:
        glyphs={}
        for name,recording in drawings.items():
            pen=TTGlyphPen(None);replayRecording(recording,Cu2QuPen(pen,max_err=.6,all_quadratic=True))
            glyphs[name]=pen.glyph()
        builder.setupGlyf(glyphs)
        # TrueType xMin includes off-curve controls, which may extend beyond
        # the visible cubic bounds after conversion. Keep its side bearing
        # synchronized with glyf or renderers translate an otherwise correct
        # outline by lsb-xMin. Advances and the outline's source origin stay
        # unchanged; CFF retains its original exact-curve metrics below.
        for name,glyph in glyphs.items():
            metrics[name]=(metrics[name][0],glyph.xMin if glyph.numberOfContours else 0)
    else:
        strings={}
        for name,recording in drawings.items():
            pen=T2CharStringPen(metrics[name][0],None);replayRecording(recording,pen)
            strings[name]=pen.getCharString(private=None,globalSubrs=None)
        # CFF1's own strings have no general Unicode encoding. The OpenType
        # naming table below remains the authoritative, full Unicode label.
        cff_family=unicodedata.normalize('NFKD',family).encode('ascii','ignore').decode().strip() or ps_name
        cff_style=unicodedata.normalize('NFKD',style).encode('ascii','ignore').decode().strip() or 'Regular'
        info={'FullName':cff_family+' '+cff_style,'FamilyName':cff_family,'Weight':cff_style,'version':'1.0'}
        # CFF strings are byte strings, so non-ASCII notices are transliterated
        # here; the OpenType name table retains the complete Unicode text.
        for field,key in [('Copyright','copyright'),('Notice','license_text')]:
            if notices.get(key):info[field]=unicodedata.normalize('NFKD',notices[key]).encode('ascii','replace').decode()
        builder.setupCFF(ps_name,info,strings,{})
    builder.setupHorizontalMetrics(metrics)
    builder.setupHorizontalHeader(ascent=ascender,descent=descender,lineGap=0)
    name_values={'familyName':family,'styleName':style,'uniqueFontIdentifier':ps_name+'-1.0',
                 'fullName':family+' '+style,'psName':ps_name,'version':'Version 1.000'}
    for field,key in [('copyright','copyright'),('description','description'),('licenseDescription','license_text'),('licenseInfoURL','license_url')]:
        if notices.get(key):name_values[field]=notices[key]
    builder.setupNameTable(name_values)
    highest=max([ascender]+[math.ceil(r['bbox'][3]) for r in records.values() if r['bbox']])
    lowest=min([descender]+[math.floor(r['bbox'][1]) for r in records.values() if r['bbox']])
    builder.setupOS2(sTypoAscender=ascender,sTypoDescender=descender,sTypoLineGap=0,
                     usWinAscent=max(0,highest),usWinDescent=max(0,-lowest),sxHeight=xheight,sCapHeight=cap,
                     usWeightClass=400,usWidthClass=5,fsSelection=64,achVendID='MIFN')
    builder.setupPost();builder.setupMaxp()
    buffer=BytesIO();builder.save(buffer);data=buffer.getvalue()
    with TTFont(BytesIO(data),lazy=False) as font:
        font.ensureDecompiled();assert font['head'].unitsPerEm==1000
        assert font.getBestCmap()=={ord(char):name for char,name in names.items()}
        assert ('glyf' in font)==is_ttf and ('CFF ' in font)==(not is_ttf)
    return data


def _atomic(path,data):
    path=Path(path)
    if not path.parent.is_dir():raise FontError('Font output folder does not exist.')
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.mifont-',delete=False) as stream:
            temporary=Path(stream.name);stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        if temporary and temporary.exists():temporary.unlink()


def build_font(glyphrecords,metadata,ttfpath,otfpath):
    """Export genuine quadratic glyf TTF and cubic CFF OTF from one project."""
    if isinstance(glyphrecords,dict):
        entries=[dict(value,char=key) for key,value in glyphrecords.items()]
    elif isinstance(glyphrecords,list):entries=glyphrecords
    else:raise FontError('Supply a list or character mapping of glyph records.')
    if not entries or len(entries)>1024:raise FontError('A font needs 1 through 1024 assigned glyphs.')
    records={};command_count=0
    for entry in entries:
        if not isinstance(entry,dict):raise FontError('A glyph record must be an object.')
        drawing=entry.get('recording',[])
        if not isinstance(drawing,list):raise FontError('Glyph recording must be an outline command list.')
        command_count+=len(drawing)
        if command_count>400000:raise FontError('Font outlines exceed the 400,000 command export limit. Simplify large glyph crops.')
        record=normalize_glyph(entry,preview=False);char=record['char']
        if not char:raise FontError('Assign a character to each glyph before exporting.')
        if char in records:raise FontError(f'Duplicate character assignment: {char}')
        if not record['recording'] and char!=' ':raise FontError(f'Glyph {char} has no outline.')
        records[char]=record
    if ' ' not in records:records[' ']=normalize_glyph({'char':' ','recording':[],'advance_width':300,'origin':'generated'},preview=False)
    ttfpath,otfpath=Path(ttfpath),Path(otfpath)
    if ttfpath.suffix.lower()!='.ttf' or otfpath.suffix.lower()!='.otf' or ttfpath.resolve()==otfpath.resolve():
        raise FontError('Choose separate .ttf and .otf output files.')
    if not ttfpath.parent.is_dir() or not otfpath.parent.is_dir():raise FontError('Font output folder does not exist.')
    ttf=_font_bytes(records,metadata,True);otf=_font_bytes(records,metadata,False)
    _atomic(ttfpath,ttf);_atomic(otfpath,otf)
    generated=''.join(c for c in sorted(records,key=ord) if records[c]['origin']=='generated')
    observed=''.join(c for c in sorted(records,key=ord) if records[c]['origin']=='observed')
    missing=''.join(c for c in ASCII_CHARSET if c not in records)
    return {'units_per_em':1000,'glyph_count':len(records),'characters':''.join(sorted(records,key=ord)),
            'observed_chars':observed,'generated_chars':generated,'missing_chars':missing,
            'ttf_bytes':len(ttf),'otf_bytes':len(otf),'ttf_outline':'quadratic-glyf','otf_outline':'cubic-CFF',
            'warnings':['Missing characters use .notdef; they were not reconstructed.'] if missing else []}
