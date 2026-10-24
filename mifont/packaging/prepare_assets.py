"""Metadata-clean branding, complete source and upstream redistribution notices."""
from pathlib import Path
import argparse
import hashlib
import importlib.metadata
import json
import shutil
import struct
import sys
import zipfile
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app_version import VERSION

def prepare(source,output):
    output.mkdir(parents=True,exist_ok=True)
    icon=(source/'assets/icon.png').read_bytes();offset=8
    allowed={b'IHDR',b'PLTE',b'tRNS',b'IDAT',b'IEND',b'sRGB',b'gAMA',b'cHRM',b'pHYs',b'iCCP'}
    assert icon.startswith(b'\x89PNG\r\n\x1a\n')
    while offset<len(icon):
        size=struct.unpack_from('>I',icon,offset)[0]
        if icon[offset+4:offset+8] not in allowed:raise ValueError('Remove non-rendering branding metadata before packaging.')
        offset+=size+12
    if offset!=len(icon):raise ValueError('Invalid PNG chunk boundaries.')
    with Image.open(source/'assets/icon.png') as image:
        image.convert('RGBA').save(output/'MiFont.ico',sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
    notices=output/'notices';notices.mkdir(exist_ok=True)
    packages=[]
    for name in ('fonttools','Pillow'):
        dist=importlib.metadata.distribution(name);folder=notices/name;folder.mkdir(exist_ok=True)
        for item in dist.files or[]:
            if any(word in item.name.lower() for word in ('license','licence','copying','notice')):
                path=Path(dist.locate_file(item))
                if path.is_file() and path.stat().st_size<2_000_000:shutil.copyfile(path,folder/str(item).replace('/','__').replace('\\','__'))
        (folder/'PACKAGE-METADATA.txt').write_text(dist.read_text('METADATA') or str(dist.metadata),encoding='utf-8')
        packages.append({'name':name,'version':dist.version,'license':dist.metadata.get('License-Expression') or dist.metadata.get('License')})
    shutil.copyfile(Path(sys.base_prefix)/'LICENSE.txt',notices/'PYTHON-LICENSE.txt')
    dist=importlib.metadata.distribution('pyinstaller')
    for item in dist.files or[]:
        if item.name.lower() in {'copying.txt','license','license.txt'}:
            path=Path(dist.locate_file(item))
            if path.is_file():shutil.copyfile(path,notices/('PYINSTALLER-'+item.name))
    vendor=source/'vendor/potrace'
    pins={'potrace.exe':'f1bcde040ae6576dc9c5868be7a72064957c5aea08e1dece240428319fc672a4',
          'source/potrace-1.16.tar.gz':'be8248a17dedd6ccbaab2fcc45835bb0502d062e40fbded3bc56028ce5eb7acc'}
    for name,checksum in pins.items():
        if hashlib.sha256((vendor/name).read_bytes()).hexdigest()!=checksum:raise ValueError('Unrecognized Potrace upstream resource.')
    shutil.copytree(vendor,notices/'potrace',dirs_exist_ok=True)
    packages.append({'name':'Potrace CLI','version':'1.16','license':'GPL-2.0-or-later','complete_source':'potrace/source/potrace-1.16.tar.gz'})
    ui_font=json.loads((source/'assets/fonts/SOURCE.json').read_text(encoding='utf-8'))
    ui_license=(source/'assets/fonts/OFL.txt').read_bytes()
    if ui_font['license']!='OFL-1.1' or hashlib.sha256(ui_license).hexdigest()!=ui_font['license_sha256']:
        raise ValueError('MiFont UI font license does not match its verified source.')
    for name in ('body.ttf','heading.ttf'):
        if hashlib.sha256((source/'assets'/name).read_bytes()).hexdigest()!=ui_font['sha256']:
            raise ValueError('MiFont UI font bytes do not match their verified source.')
    shutil.copytree(source/'assets/fonts',notices/'Inter',dirs_exist_ok=True)
    packages.append({'name':'Inter UI font','license':'OFL-1.1','sha256':ui_font['sha256'],
                     'source_url':ui_font['download_url'],'license_file':'Inter/OFL.txt'})
    (notices/'packages.json').write_text(json.dumps(packages,indent=2)+'\n')
    source_zip=notices/f'MiFont-source-{VERSION}.zip'
    allowed_roots={'static','assets','vendor','helpers','tests','packaging'}
    with zipfile.ZipFile(source_zip,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for path in sorted(source.rglob('*')):
            if not path.is_file():continue
            relative=path.relative_to(source)
            if any(part in {'__pycache__','.work','.git','Projects','ReferenceCache','build','dist'} for part in relative.parts):continue
            if len(relative.parts)>1 and relative.parts[0] not in allowed_roots:continue
            if len(relative.parts)==1 and path.suffix.lower() not in {'.py','.txt','.md','.ps1'}:continue
            archive.write(path,'mifont/'+relative.as_posix())
    (notices/'README.txt').write_text(
        f'MiFont {VERSION}\nFontTools, Pillow, Python, the Inter UI font and the separate Potrace CLI are bundled.\n'
        'See packages.json and each package license. Potrace complete unmodified source is included.\n'
        'PyInstaller bootloader redistribution exception: https://pyinstaller.org/en/stable/license.html\n'
        f'Complete app source: MiFont-source-{VERSION}.zip\n'
        'Rebuild with Python3.12 x64: create .work/mifont-env, install both pinned requirements files, then run mifont/build.ps1.\n'
        'No customer project, specimen or downloaded reference font is bundled. User-chosen OFL derivative notices travel with their exported projects/fonts.\n',encoding='utf-8')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();prepare(args.source.resolve(),args.output.resolve())
