"""Create a reviewable portable app from pinned local inputs; never publish it.

No prior app directory or archive is overwritten. Only explicit runtime/source
allowlists are copied. AI model weights, private QA assets and user data are
excluded. pnpm's frozen production lock installs physical copies without scripts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PYTHON_CORE = ROOT / 'build/video-ai-distribution/MITOM-Video-CPU-2026.10.23.1/python'
SITE_PACKAGES = ROOT / '.work/product-video-env/Lib/site-packages'
NODE_ROOT = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
RENDERER = ROOT / 'video-local-engine/remotion-studio'
AI_STUB = ROOT / '.work/full-transfer-20260927/UOY-FULL-PS-PANEL-3D-20260927-R10/local-ai-runtime'
MASK_SHA = '309c8469258dda742793dce0ebea8e6dd393174f89934733ecc8b14c76f4ddd8'
BINARY_ROWS_SHA = '4bdf42d213db6a894e53052a7061171b978436d337f91a001085b79f4791356d'
# A 40 MiB payload remains below the connector's 64 MiB IPC frame even after
# base64 expansion and JSON framing. This changes transport only, not ZIP bytes.
PUBLISH_PART_BYTES = 40 * 1024**2


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', 'utf-8')


def plain(path):
    if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
        raise ValueError('Linked runtime input is not allowed: ' + str(path))
    return path


def copy_file(source, destination):
    plain(source)
    if not source.is_file():
        raise ValueError('Required runtime input missing: ' + str(source))
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def copy_tree(source, destination, *, ignore=()):
    plain(source)
    for item in sorted(source.iterdir()):
        plain(item)
        if item.name in ignore or item.suffix in ('.pyc', '.pyo', '.whl'):
            continue
        if item.is_dir():
            copy_tree(item, destination / item.name, ignore=ignore)
        else:
            copy_file(item, destination / item.name)


def add_ai_stub(stage):
    template = json.loads((AI_STUB / 'runtime-template.json').read_text('utf-8-sig'))
    rows = sorted(template['binaryFiles'], key=lambda row: row['path'])
    if hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest() != BINARY_ROWS_SHA:
        raise ValueError('Unexpected local AI binary manifest')
    for row in rows:
        source = AI_STUB / row['path']
        if source.stat().st_size != row['size'] or sha(source) != row['sha256']:
            raise ValueError('Local AI runtime binary differs: ' + row['path'])
        copy_file(source, stage / 'ai-runtime' / row['path'])
    copy_tree(AI_STUB / 'licenses', stage / 'ai-runtime/licenses')
    template.update(model='models/Qwen3.5-0.8B-Q4_K_M.gguf', modelFile='models/Qwen3.5-0.8B-Q4_K_M.gguf',
                    modelSha256='bd258782e35f7f458f8aced1adc053e6e92e89bc735ba3be89d38a06121dc517',
                    mmproj='models/mmproj-F16.gguf', mmprojFile='models/mmproj-F16.gguf',
                    mmprojSha256='56e4c6cfe73b0c82e3e82bc518d7591997e61d81f723fc41a586f4fa69ea2453',
                    modelSize=532517120, mmprojSize=204987232,
                    modelRevision='6ab461498e2023f6e3c1baea90a8f0fe38ab64d0')
    write_json(stage / 'ai-runtime/runtime-template.json', template)


def build(output, version, *, offline=True):
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Product Video version must use N.N.N')
    output = output.resolve()
    if output.exists():
        raise ValueError('Choose a new staging directory; existing builds are preserved.')
    output.mkdir(parents=True)
    stage = output / 'app'
    stage.mkdir()
    copy_file(ROOT / 'video-local-engine/PRODUCT-VIDEO.md', stage / 'README.md')
    # The old distribution supplies only the official embeddable Python core.
    # Its old Lib/site-packages is never copied.
    for source in sorted(PYTHON_CORE.iterdir()):
        if source.is_file() and source.name != 'python312._pth':
            copy_file(source, stage / 'python' / source.name)
    copy_tree(SITE_PACKAGES, stage / 'python/Lib/site-packages', ignore={
        '__pycache__', 'tests', 'test', 'pip', 'pip-23.2.1.dist-info', '.pytest_cache'})
    (stage / 'python/python312._pth').write_text('python312.zip\n.\nLib/site-packages\nimport site\n', 'ascii')
    copy_file(NODE_ROOT / 'bin/node.exe', stage / 'node/node.exe')
    copy_file(ROOT / '.work/reverse-engineering-tools/portable/node-v24.22.1-win-x64/LICENSE', stage / 'node/LICENSE')
    worker_files = sorted(source for source in (ROOT / 'video-local-engine').glob('*.py')
                          if not source.name.startswith('test_'))
    for source in worker_files:
        copy_file(source, stage / 'worker' / source.name)
    for name in ('index.html', 'app.js', 'client.mjs', 'style.css'):
        copy_file(HERE / 'web-ui' / name, stage / 'web' / name)
    copy_tree(HERE / 'panel-integration/com.mitom.productvideo', stage / 'cep/com.mitom.productvideo')
    for name in ('render.mjs', 'package.json', 'pnpm-lock.yaml', 'REMOTION-LICENSE.md'):
        copy_file(RENDERER / name, stage / 'renderer' / name)
    copy_tree(RENDERER / 'src', stage / 'renderer/src', ignore={'__pycache__'})
    environment = dict(os.environ, CI='true', PNPM_CONFIG_PM_ON_FAIL='ignore')
    environment.pop('NODE_OPTIONS', None)
    environment.pop('NODE_PATH', None)
    command = [str(NODE_ROOT / 'bin/node.exe'), str(NODE_ROOT / 'node_modules/pnpm/bin/pnpm.mjs'),
               'install', '--prod', '--frozen-lockfile', '--ignore-scripts',
               '--store-dir', str(ROOT / '.pnpm-store'), '--node-linker=hoisted',
               '--config.enableGlobalVirtualStore=false', '--config.package-import-method=copy']
    if offline:
        command.append('--offline')
    result = subprocess.run(command, cwd=stage / 'renderer', env=environment, text=True, capture_output=True)
    (output / 'pnpm-install.log').write_text(result.stdout + result.stderr, 'utf-8')
    if result.returncode:
        raise ValueError('Production dependency installation failed; see pnpm-install.log')
    # Native helpers and all dependency licenses remain. Only package-manager
    # command shims are removed; the app invokes the bundled node directly.
    bin_directory = stage / 'renderer/node_modules/.bin'
    if bin_directory.exists():
        if bin_directory.resolve().is_relative_to(stage.resolve()):
            shutil.rmtree(bin_directory)
    add_ai_stub(stage)
    mask = ROOT / 'video-local-engine/models/u2netp.onnx'
    if sha(mask) != MASK_SHA:
        raise ValueError('Product mask weights differ from the reviewed pin')
    for name in ('u2netp.onnx', 'u2netp.provenance.json'):
        copy_file(mask.with_name(name), stage / 'models' / name)
    copy_tree(HERE / 'vendor-licenses', stage / 'licenses')
    (stage / 'licenses/FFmpeg-source.txt').write_text(
        'Remotion 4.0.539 bundles FFmpeg under GPLv2+.\n'
        'Source and build scripts: https://github.com/remotion-dev/remotion/tree/v4.0.539/packages/compositor/ffmpeg/build\n'
        'Upstream acknowledgements: https://www.remotion.dev/docs/acknowledgements\n', 'utf-8')
    notices = (
        'MITOM Product Video 1.0.0\n\n'
        'Python: python/LICENSE.txt\nNode.js: node/LICENSE\n'
        'Python dependency licenses remain in python/Lib/site-packages.\n'
        'Remotion: renderer/REMOTION-LICENSE.md. Its Free License eligibility applies to each user.\n'
        'Renderer dependency licenses remain with each node_modules package.\n'
        'Remotion FFmpeg GPLv2+ license and source location: licenses/FFmpeg-GPL-2.0.txt and licenses/FFmpeg-source.txt.\n'
        'llama.cpp and its dependencies: ai-runtime/licenses.\n'
        'Qwen model weights download separately; none are included in this app ZIP.\n'
        'u2netp Apache 2.0 license: licenses/U2NET-Apache-2.0.txt; source/pins: models/u2netp.provenance.json.\n'
        'Chrome or Edge is required and remains separately installed.\n'
        'No user images, fonts, projects, login or session data are included.\n')
    (stage / 'THIRD_PARTY_NOTICES.txt').write_text(notices, 'utf-8')
    print('Runtime staged; verifying its complete file inventory.', flush=True)
    rows = []
    for path in sorted(stage.rglob('*')):
        plain(path)
        if path.is_file():
            rows.append({'path': path.relative_to(stage).as_posix(), 'size': path.stat().st_size, 'sha256': sha(path)})
    return seal(output, stage, version, rows, worker_files)


def seal(output, stage, version, rows, worker_files):
    if any(row['path'].endswith(('.ttf', '.otf')) for row in rows):
        raise ValueError('A font asset unexpectedly entered the portable app')
    manifest = {'schema': 1, 'app_id': 'product-video', 'version': version,
                'entry': 'python/python.exe', 'files': rows}
    write_json(stage / 'product-video-app.json', manifest)
    print('Runtime files pinned; compressing portable archive.', flush=True)
    archive = output / ('MITOM-Product-Video-' + version + '.zip')
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED, compresslevel=6, strict_timestamps=False) as package:
        for path in sorted(stage.rglob('*')):
            if path.is_file():
                info = zipfile.ZipInfo(path.relative_to(stage).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                with path.open('rb') as source, package.open(info, 'w') as destination:
                    shutil.copyfileobj(source, destination, 1024 * 1024)
    descriptor = {'schema': 1, 'app_id': 'product-video', 'version': version,
                  'min_launcher_version': '2026.10.31.1', 'entry': 'python/python.exe',
                  'manifest_sha256': sha(stage / 'product-video-app.json'),
                  'archive': {'url': 'https://mitomyhy.github.io/releases/product-video/' + version + '/' + archive.name,
                              'size': archive.stat().st_size, 'sha256': sha(archive)}}
    if archive.stat().st_size > 95 * 1024**2:
        parts = []
        with archive.open('rb') as stream:
            index = 1
            while content := stream.read(PUBLISH_PART_BYTES):
                part = archive.with_name(archive.name + f'.part{index:03d}')
                with part.open('xb') as destination:
                    destination.write(content)
                parts.append({'url': descriptor['archive']['url'] + f'.part{index:03d}',
                              'size': len(content), 'sha256': sha(part)})
                index += 1
        descriptor['archive']['parts'] = parts
    write_json(output / 'product-video-release.json', descriptor)
    sys.path.insert(0, str(ROOT / 'launcher'))
    import video_app
    video_app.validate_release(descriptor)
    video_app.verify_install(stage, descriptor)
    report = {'ok': True, 'files': len(rows), 'runtime_bytes': sum(row['size'] for row in rows),
              'archive_bytes': archive.stat().st_size, 'under_95_MiB': archive.stat().st_size < 95 * 1024**2,
              'archive_sha256': descriptor['archive']['sha256'], 'worker_files': [p.name for p in worker_files],
              'public_archive_parts': descriptor['archive'].get('parts', []),
              'symlinks': False, 'models_included': ['u2netp.onnx'], 'ai_weights_included': False,
              'private_assets_included': False, 'publish': False}
    write_json(output / 'packaging-report.json', report)
    return report


def refresh_reviewed_fixes(output, version, previous):
    """Repack the two reviewed cache/bytecode fixes; preserve the prior build."""
    sys.path.insert(0, str(ROOT / 'launcher'))
    import video_app
    previous, output = previous.resolve(strict=True), output.resolve()
    if output.exists():
        raise ValueError('Choose a new staging directory; existing builds are preserved.')
    release = video_app.validate_release(json.loads((previous / 'product-video-release.json').read_text('utf-8')))
    if release['version'] != version:
        raise ValueError('The reviewed refresh must retain the unpublished app version.')
    source = video_app.verify_install(previous / 'app', release)
    changes = {}
    for relative, current, old, new in (
        ('worker/product_job.py', ROOT / 'video-local-engine/product_job.py',
         b"[ltx['python'], '-u',", b"[ltx['python'], '-B', '-u',"),
        ('renderer/render.mjs', ROOT / 'video-local-engine/remotion-studio/render.mjs',
         b'webpackOverride: config => ({...config, cache: false}),', b'enableCaching: false,'),
    ):
        before, after = (source / relative).read_bytes(), current.read_bytes()
        if before.count(old) != 1 or before.replace(old, new) != after:
            raise ValueError('Only the reviewed cache/bytecode line changes are allowed: ' + relative)
        changes[relative] = after
    output.mkdir(parents=True)
    stage = output / 'app'
    # The complete source tree was just verified, including absence of all
    # symlinks/reparse points. Copy only this immutable, allowlisted tree.
    shutil.copytree(source, stage, copy_function=shutil.copyfile)
    for relative, after in changes.items():
        (stage / relative).write_bytes(after)
    manifest = json.loads((stage / 'product-video-app.json').read_text('utf-8'))
    for row in manifest['files']:
        if row['path'] in changes:
            row.update(size=len(changes[row['path']]), sha256=sha(stage / row['path']))
    print('Reviewed cache/bytecode fixes staged; rebuilding pins and archive.', flush=True)
    report = seal(output, stage, version, manifest['files'], sorted((stage / 'worker').glob('*.py')))
    report.update(source_refresh_from=str(previous), source_changed=sorted(changes))
    write_json(output / 'packaging-report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--version', default='1.0.0')
    parser.add_argument('--allow-network', action='store_true', help='Permit fetching lock-pinned package dependencies if cache is incomplete.')
    parser.add_argument('--refresh-reviewed-fixes', type=Path, help='Reuse a fully verified unpublished prior app for the two reviewed cache/bytecode line fixes; never overwrite that build.')
    options = parser.parse_args()
    result = (refresh_reviewed_fixes(options.output, options.version, options.refresh_reviewed_fixes)
              if options.refresh_reviewed_fixes else
              build(options.output, options.version, offline=not options.allow_network))
    print(json.dumps(result, indent=2))
