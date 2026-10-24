"""Owned, persistent projects with bounded self-contained ZIP interchange."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import threading
import uuid
import warnings
import zipfile
import xml.etree.ElementTree as ET

from PIL import Image, ImageOps

ASCII = ''.join(chr(value) for value in range(32,127))
ID_RE = re.compile(r'^[a-f0-9]{32}$')
MAX_UPLOAD = 32 * 1024 * 1024
MAX_PROJECT_ZIP = 64 * 1024 * 1024
MAX_PROJECT_BYTES = 256 * 1024 * 1024
IMAGE_SUFFIXES = {'.png','.jpg','.jpeg','.webp','.bmp','.tif','.tiff'}
IMAGE_FORMATS = {'PNG','JPEG','WEBP','BMP','TIFF'}

def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def identity(value):
    if not isinstance(value,str) or not ID_RE.fullmatch(value):
        raise ValueError('Invalid project, image or glyph identifier.')
    return value

def plain(path):
    path=Path(path)
    for part in (path,*path.parents):
        if part.is_symlink() or getattr(part,'is_junction',lambda:False)():
            raise ValueError('MiFont cannot write through a linked project folder.')
    return path

def atomic_json(path,value):
    path=plain(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name('.'+path.name+'.'+secrets.token_hex(8)+'.tmp')
    try:
        temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)

def charset(value):
    if not isinstance(value,str) or not 1<=len(value)<=512:
        raise ValueError('Choose between 1 and 512 target characters.')
    if any(ord(char)<32 or 0xD800<=ord(char)<=0xDFFF for char in value):
        raise ValueError('Target characters cannot include control characters.')
    return ''.join(dict.fromkeys(value))

def text(value,name,default='',maximum=120):
    value=str(value if value is not None else default).strip()
    if not value or len(value)>maximum or any(ord(char)<32 for char in value):
        raise ValueError(f'{name} must contain1–{maximum} printable characters.')
    return value

def inspect_image(data):
    if not data or len(data)>MAX_UPLOAD:raise ValueError('Choose an image smaller than 32MB.')
    with warnings.catch_warnings():
        warnings.simplefilter('error',Image.DecompressionBombWarning)
        with Image.open(BytesIO(data)) as image:
            if image.format not in IMAGE_FORMATS:raise ValueError('Choose PNG, JPG, WebP, BMP or TIFF.')
            if image.width*image.height>32_000_000:raise ValueError('Image exceeds 32 megapixels.')
            image.load()
            oriented=ImageOps.exif_transpose(image).convert('RGBA')
            size=oriented.size
            preview=oriented.copy();preview.thumbnail((1600,1600),Image.Resampling.LANCZOS)
            output=BytesIO();preview.save(output,'PNG')
            return {'width':size[0],'height':size[1],'format':image.format},output.getvalue()

def validate_record(record):
    if not isinstance(record,dict):raise ValueError('Glyph outline must be an object.')
    value=deepcopy(record)
    char=value.get('char','')
    if not isinstance(char,str) or len(char)>1 or char and (ord(char)<32 or 0xD800<=ord(char)<=0xDFFF):
        raise ValueError('Assign one printable character to each glyph.')
    recording=value.get('recording')
    if not isinstance(recording,list) or len(recording)>100_000:raise ValueError('Invalid or oversized glyph outline.')
    allowed={'moveTo':(1,1),'lineTo':(1,1),'curveTo':(3,3),'qCurveTo':(1,32),'closePath':(0,0),'endPath':(0,0)}
    for item in recording:
        if not isinstance(item,(list,tuple)) or len(item)!=2 or item[0] not in allowed:raise ValueError('Unsupported outline command.')
        points=item[1];low,high=allowed[item[0]]
        if not isinstance(points,(list,tuple)) or not low<=len(points)<=high:raise ValueError('Invalid outline points.')
        for point in points:
            if point is None and item[0]=='qCurveTo':continue
            if not isinstance(point,(list,tuple)) or len(point)!=2 or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) or abs(x)>100_000 for x in point):
                raise ValueError('Glyph coordinates are invalid.')
    for key in ('advance_width','lsb'):
        number=value.get(key,600 if key=='advance_width' else 0)
        if isinstance(number,bool) or not isinstance(number,(int,float)) or not math.isfinite(number) or abs(number)>10_000:
            raise ValueError('Glyph metrics are invalid.')
        value[key]=number
    svg=value.get('preview_svg','')
    if svg:
        if not isinstance(svg,str) or len(svg)>8_000_000 or '<!DOCTYPE' in svg.upper():raise ValueError('Invalid glyph preview.')
        root=ET.fromstring(svg)
        if root.tag.split('}')[-1]!='svg':raise ValueError('Invalid glyph preview root.')
        for node in root.iter():
            if node.tag.split('}')[-1] not in {'svg','g','path'}:raise ValueError('Unsupported glyph preview element.')
            for key in node.attrib:
                if key.split('}')[-1] not in {'d','fill','fill-rule','viewBox','width','height','transform','version'}:
                    raise ValueError('Unsupported glyph preview attribute.')
    value['char']=char
    value['origin']='generated' if value.get('origin')=='generated' else'observed'
    return value

class ProjectStore:
    def __init__(self,root):
        self.root=plain(Path(root).absolute())
        self.root.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()

    def folder(self,project_id):return plain(self.root/identity(project_id))

    def load(self,project_id):
        path=self.folder(project_id)/'project.json'
        if not path.is_file():raise ValueError('Project not found.')
        value=json.loads(path.read_text(encoding='utf-8'))
        if value.get('schema')!=1 or value.get('id')!=project_id:raise ValueError('Invalid project document.')
        if not isinstance(value.get('images'),list) or not isinstance(value.get('glyphs'),list) or not isinstance(value.get('builds'),list):
            raise ValueError('Invalid project asset lists.')
        if len(value['images'])>50 or len(value['glyphs'])>512 or len(value['builds'])>100:
            raise ValueError('Project asset count exceeds limits.')
        for image in value['images']:
            identity(image['id'])
            if image.get('suffix') not in IMAGE_SUFFIXES:raise ValueError('Invalid project image format.')
        for glyph in value['glyphs']:
            identity(glyph['id'])
            if glyph.get('blob_id'):identity(glyph['blob_id'])
        for build in value['builds']:identity(build['id'])
        return value

    def save(self,project):
        value=deepcopy(project);value['revision']=int(value.get('revision',0))+1;value['modified_at']=now()
        atomic_json(self.folder(value['id'])/'project.json',value)
        return value

    def create(self,metadata=None):
        metadata=metadata or{}
        key=uuid.uuid4().hex
        folder=self.folder(key);folder.mkdir()
        for name in ('images','glyphs','builds'): (folder/name).mkdir()
        project={'schema':1,'id':key,'name':text(metadata.get('name'),'Project name','Untitled font'),
                 'family':text(metadata.get('family'),'Font family','MiFont Untitled'),
                 'style':text(metadata.get('style'),'Font style','Regular'),
                 'charset':charset(metadata.get('charset',ASCII)),'units_per_em':1000,'revision':0,
                 'created_at':now(),'modified_at':now(),'images':[],'glyphs':[],'builds':[]}
        return self.save(project)

    def listing(self):
        result=[]
        for folder in self.root.iterdir():
            if not folder.is_dir() or not ID_RE.fullmatch(folder.name):continue
            try:
                project=self.load(folder.name)
                result.append({key:project[key] for key in ('id','name','family','style','modified_at','revision')})
                result[-1].update(image_count=len(project['images']),glyph_count=len(project['glyphs']))
            except (OSError,ValueError,KeyError):continue
        return sorted(result,key=lambda value:value['modified_at'],reverse=True)

    def image_path(self,project,image):
        return plain(self.folder(project['id'])/'images'/(identity(image['id'])+image['suffix']))

    def upload(self,project_id,name,data):
        project=self.load(project_id)
        if len(project['images'])>=50:raise ValueError('This project already contains 50 images.')
        suffix=Path(name.replace('\\','/')).suffix.lower()
        if suffix not in IMAGE_SUFFIXES:raise ValueError('Choose PNG, JPG, WebP, BMP or TIFF.')
        info,preview=inspect_image(data)
        image={'id':uuid.uuid4().hex,'name':Path(name.replace('\\','/')).name[:180],
               'suffix':suffix,**info,'source_sha256':hashlib.sha256(data).hexdigest(),'candidates':[]}
        folder=self.folder(project_id)/'images'
        # Re-encode oriented input without EXIF so crop coordinates match preview.
        with Image.open(BytesIO(data)) as original:
            oriented=ImageOps.exif_transpose(original).convert('RGBA')
            image['suffix']='.png';oriented.save(folder/(image['id']+'.png'),'PNG')
        image['sha256']=hashlib.sha256((folder/(image['id']+'.png')).read_bytes()).hexdigest()
        (folder/(image['id']+'.preview.png')).write_bytes(preview)
        project['images'].append(image)
        return self.save(project),image

    def record(self,project_id,glyph_id):
        identity(glyph_id)
        entry=next((row for row in self.load(project_id)['glyphs'] if row['id']==glyph_id),None)
        blob_id=entry.get('blob_id',glyph_id) if entry else glyph_id
        value=validate_record(json.loads((self.folder(project_id)/'glyphs'/(identity(blob_id)+'.json')).read_text(encoding='utf-8')))
        if entry is not None:value['char']=entry.get('char','')
        return value

    def put_glyph(self,project,record,*,glyph_id=None,image_id=None,box=None):
        record=validate_record(record);glyph_id=identity(glyph_id) if glyph_id else uuid.uuid4().hex
        folder=self.folder(project['id'])/'glyphs'
        # Immutable outline versions make project.json the atomic commit point.
        blob_id=uuid.uuid4().hex
        atomic_json(folder/(blob_id+'.json'),record)
        if record.get('preview_svg'):(folder/(blob_id+'.svg')).write_text(record['preview_svg'],encoding='utf-8')
        entry={key:deepcopy(record.get(key)) for key in ('char','origin','advance_width','lsb','bbox','approximate','provenance') if key in record}
        entry.update(id=glyph_id,blob_id=blob_id,image_id=image_id,box=box)
        project['glyphs']=[old for old in project['glyphs'] if old['id']!=glyph_id]
        # A newly observed glyph supersedes an earlier mapping of the same char.
        if record['char']:
            for old in project['glyphs']:
                if old.get('char')==record['char']:
                    old['char']=''
        project['glyphs'].append(entry)
        if image_id and box:
            image=next((image for image in project['images'] if image['id']==image_id),None)
            if image:
                x,y,width,height=box
                with Image.open(self.image_path(project,image)) as source:
                    source.crop((x,y,x+width,y+height)).save(folder/(blob_id+'.png'),'PNG')
        return entry

    def view(self,project):
        value=deepcopy(project);key=value['id']
        for image in value['images']:
            image['preview_url']=f'/api/image/{key}/{image["id"]}/preview'
            image['source_url']=f'/api/image/{key}/{image["id"]}/source'
        for glyph in value['glyphs']:
            glyph['svg_url']=f'/api/glyph/{key}/{glyph["id"]}/svg'
            glyph['crop_url']=f'/api/glyph/{key}/{glyph["id"]}/crop' if (self.folder(key)/'glyphs'/(glyph.get('blob_id',glyph['id'])+'.png')).is_file() else None
        observed=''.join(glyph['char'] for glyph in value['glyphs'] if glyph.get('char') and glyph.get('origin')=='observed')
        generated=''.join(glyph['char'] for glyph in value['glyphs'] if glyph.get('char') and glyph.get('origin')=='generated')
        value['coverage']={'observed_chars':observed,'generated_chars':generated,
            'missing_chars':''.join(char for char in value['charset'] if char not in observed+generated),
            'unassigned_count':sum(not glyph.get('char') for glyph in value['glyphs'])}
        return value

    def archive(self,project_id):
        project=self.load(project_id);folder=self.folder(project_id)
        paths={'project.json'}
        for image in project['images']:paths.update({'images/'+image['id']+image['suffix'],'images/'+image['id']+'.preview.png'})
        for glyph in project['glyphs']:
            blob_id=glyph.get('blob_id',glyph['id'])
            paths.update('glyphs/'+blob_id+suffix for suffix in ('.json','.svg','.png') if (folder/'glyphs'/(blob_id+suffix)).is_file())
        for build in project['builds']:
            paths.update('builds/'+build['id']+'.'+suffix for suffix in ('ttf','otf') if (folder/'builds'/(build['id']+'.'+suffix)).is_file())
        output=BytesIO()
        with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as package:
            for name in sorted(paths):package.writestr(name,(folder/name).read_bytes())
            for obligation in project.get('license_obligations',[]):
                key=hashlib.sha256(str(obligation.get('id','')).encode()).hexdigest()[:20]
                package.writestr('licenses/'+key+'/OFL.txt',str(obligation.get('license_text','')))
                package.writestr('licenses/'+key+'/SOURCE.json',json.dumps({k:v for k,v in obligation.items() if k!='license_text'},indent=2))
        return output.getvalue()

    def import_archive(self,data):
        if not data or len(data)>MAX_PROJECT_ZIP:raise ValueError('Choose a MiFont project ZIP smaller than 64MB.')
        created_folder=None
        try:
            with zipfile.ZipFile(BytesIO(data)) as package:
                members=package.infolist()
                if not 1<=len(members)<=2048 or sum(row.file_size for row in members)>MAX_PROJECT_BYTES:raise ValueError('Project archive exceeds size limits.')
                names=[]
                for row in members:
                    name=row.filename;path=PurePosixPath(name)
                    if row.is_dir() or '\\' in name or ':' in name or path.is_absolute() or any(part in {'','.','..'} for part in name.split('/')) or stat.S_ISLNK(row.external_attr>>16):
                        raise ValueError('Unsafe project archive path.')
                    names.append(name)
                if len(set(name.casefold() for name in names))!=len(names) or 'project.json' not in names:raise ValueError('Duplicate files or missing project.json.')
                if package.getinfo('project.json').file_size>4*1024*1024:raise ValueError('Project document is too large.')
                project=json.loads(package.read('project.json'))
                if project.get('schema')!=1 or not isinstance(project.get('images'),list) or not isinstance(project.get('glyphs'),list):raise ValueError('Unsupported MiFont project format.')
                if len(project['images'])>50 or len(project['glyphs'])>512:raise ValueError('Project contains too many images or glyphs.')
                # New ID prevents import from overwriting any existing project.
                restored=self.create(project)
                created_folder=self.folder(restored['id'])
                folder=self.folder(restored['id'])
                images=[];glyphs=[];expected={'project.json'}
                for image in project['images']:
                    image_id=identity(image['id']);suffix=image.get('suffix','.png')
                    if suffix not in IMAGE_SUFFIXES:raise ValueError('Unsupported project image type.')
                    source_name='images/'+image_id+suffix;expected.add(source_name)
                    image_data=package.read(source_name);info,preview=inspect_image(image_data)
                    (folder/'images'/(image_id+suffix)).write_bytes(image_data)
                    (folder/'images'/(image_id+'.preview.png')).write_bytes(preview)
                    expected.add('images/'+image_id+'.preview.png')
                    images.append({'id':image_id,'name':str(image.get('name','Image'))[:180],'suffix':suffix,**info,
                                   'sha256':hashlib.sha256(image_data).hexdigest(),'candidates':[]})
                restored['images']=images
                for key in ('references','research','completion','license_obligations'):
                    if key in project:restored[key]=deepcopy(project[key])
                if not isinstance(restored.get('license_obligations',[]),list):raise ValueError('Invalid source license metadata.')
                for obligation in restored.get('license_obligations',[]):
                    if not isinstance(obligation,dict) or obligation.get('license')!='OFL-1.1' or not isinstance(obligation.get('license_text'),str):raise ValueError('Invalid source license notice.')
                    license_sha=hashlib.sha256(obligation['license_text'].encode()).hexdigest()
                    if obligation.get('license_sha256')!=license_sha:raise ValueError('Source license checksum mismatch.')
                    key=hashlib.sha256(str(obligation.get('id','')).encode()).hexdigest()[:20]
                    expected.update({'licenses/'+key+'/OFL.txt','licenses/'+key+'/SOURCE.json'})
                for glyph in project['glyphs']:
                    glyph_id=identity(glyph['id']);blob_id=identity(glyph.get('blob_id',glyph_id))
                    record_name='glyphs/'+blob_id+'.json';expected.add(record_name)
                    if package.getinfo(record_name).file_size>8*1024*1024:raise ValueError('Glyph data is too large.')
                    record=validate_record(json.loads(package.read(record_name)))
                    record['char']=glyph.get('char',record['char'])
                    image_id=glyph.get('image_id')
                    box=glyph.get('box')
                    if image_id is not None:
                        identity(image_id)
                        if image_id not in {row['id'] for row in images}:raise ValueError('Glyph references an unknown image.')
                    if box is not None and (not isinstance(box,list) or len(box)!=4 or any(type(value) is not int for value in box)):
                        raise ValueError('Invalid project glyph crop box.')
                    if box is not None:
                        image=next((row for row in images if row['id']==image_id),None)
                        x,y,width,height=box
                        if image is None or x<0 or y<0 or width<1 or height<1 or x+width>image['width'] or y+height>image['height']:
                            raise ValueError('Project glyph crop lies outside its image.')
                    self.put_glyph(restored,record,glyph_id=glyph_id,image_id=image_id,box=box)
                    for suffix in ('.svg','.png'):
                        if 'glyphs/'+blob_id+suffix in names:expected.add('glyphs/'+blob_id+suffix)
                # Build binaries are regenerated from verified outlines on import.
                for build in project.get('builds',[]):
                    build_id=identity(build['id'])
                    expected.update('builds/'+build_id+'.'+suffix for suffix in ('ttf','otf'))
                if not set(names)<=expected:raise ValueError('Unexpected files in MiFont project archive.')
                return self.save(restored)
        except BaseException:
            if created_folder is not None:
                created_folder=plain(created_folder.absolute())
                if created_folder.parent!=self.root or not ID_RE.fullmatch(created_folder.name):
                    raise RuntimeError('Refusing unsafe failed-import cleanup.')
                shutil.rmtree(created_folder)
            raise
