"""One contained local AI + Remotion job. User prompts are data, never code."""
from pathlib import Path
import argparse, json, os, subprocess, sys, time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from product_assets import cutout_product, scene_seed


def emit(**event):
    print(json.dumps(event, ensure_ascii=True), flush=True)


def stream_child(command, folder, cancel, *, phase=None, deadline=1800):
    with (folder / 'child.log').open('a', encoding='utf-8') as log:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log, stdin=subprocess.DEVNULL,
                                   text=True, encoding='utf-8', errors='replace',
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        result = None
        try:
            for line in process.stdout:
                if len(line) > 65536:
                    continue
                if cancel.exists():
                    raise InterruptedError('Generation cancelled')
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if phase:
                    emit(phase=phase, step=event.get('step'), total_steps=event.get('total_steps'))
                else:
                    emit(**event)
                if event.get('phase') == 'complete' and event.get('ok') is True:
                    result = event
                if event.get('phase') == 'failed':
                    raise RuntimeError(event.get('error', 'Local AI failed'))
            code = process.wait(timeout=deadline)
            if code:
                raise RuntimeError(f'Local generation process exited with code {code}; inspect child.log')
            return result
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            process.stdout.close()


def run(request_file, start_marker):
    until = time.monotonic() + 20
    while not start_marker.is_file():
        if time.monotonic() > until:
            raise TimeoutError('Parent did not establish job containment')
        time.sleep(.05)
    request = json.loads(request_file.read_text('utf-8'))
    folder = request_file.parent.resolve()
    cancel = folder / 'cancel'
    options = request['options']
    if request.get('needs_setup'):
        from product_setup import prepare_runtime
        emit(phase='model_setup', progress=0)
        prepare_runtime(Path(request['ai_runtime']), Path(request['ai_stub']), cancel, emit)
    emit(phase='extracting')
    mask = cutout_product(request['image'], folder / 'product.png', request.get('mask_model'))
    if cancel.exists():
        raise InterruptedError('Generation cancelled')
    emit(phase='planning')
    from prompt_planner import plan_scene
    plan = plan_scene(Path(request['image']), request['prompt'],
                      runtime=Path(request['ai_runtime']) if request.get('ai_runtime') else None,
                      workdir=folder, emit=lambda event: emit(**event))
    if cancel.exists():
        raise InterruptedError('Generation cancelled')
    background = {'theme': plan['theme'], 'palette': plan['palette']}
    background_mode = 'rendered_scene'
    if request.get('background_image'):
        from PIL import Image, ImageOps
        with Image.open(request['background_image']) as original:
            image = ImageOps.exif_transpose(original).convert('RGB')
            image.thumbnail((2400, 2400))
            image.save(folder / 'background.png')
        background['src'] = 'background.png'
        background_mode = 'uploaded_photo'
    else:
        scene_seed(folder / 'background.png', plan['theme'], plan['palette'],
                   portrait=options['aspect_ratio'] == '9:16', seed=options['seed'])
        background['src'] = 'background.png'
        ltx = request.get('ltx')
        if ltx:
            emit(phase='background_ai')
            portrait = options['aspect_ratio'] == '9:16'
            bg_request = {'model': ltx['model'], 'ffmpeg': ltx['ffmpeg'],
                          'image': str(folder / 'background.png'),
                          'prompt': plan['background_prompt'] + ' Empty central foreground for product placement. No lettering, no logos, no product. Soft shallow depth of field, realistic lighting, gentle background motion.',
                          'options': {'width': 320 if portrait else 512, 'height': 512 if portrait else 320,
                                      'frames': 49, 'fps': 8, 'seed': options['seed']},
                          'threads': 4, 'output': str(folder / 'background.mp4'),
                          'cancel_marker': str(cancel)}
            bg_file = folder / 'background-request.json'
            bg_file.write_text(json.dumps(bg_request), encoding='utf-8')
            try:
                stream_child([ltx['python'], '-B', '-u', str(Path(__file__).with_name('inference.py')),
                              '--request', str(bg_file), '--start-marker', str(start_marker)],
                             folder, cancel, phase='background_ai')
            finally:
                bg_file.unlink(missing_ok=True)
            background['videoSrc'] = 'background.mp4'
            background_mode = 'ltx_local_cpu'
    scene = {'schemaVersion': 1, 'aspectRatio': options['aspect_ratio'],
             'durationSeconds': options['duration_seconds'], 'fps': 30,
             'resolution': options['resolution'], 'seed': options['seed'],
             'product': {'src': 'product.png', 'scale': .8, 'anchor': plan['anchor'], 'material': plan['material']},
             'background': background, 'motion': plan['motion'],
             'lighting': {'warmth': plan.get('warmth', .2), 'shadow': .3, 'glint': .25}}
    scene_file = folder / 'scene.json'
    scene_file.write_text(json.dumps(scene), encoding='utf-8')
    emit(phase='rendering', progress=0)
    result = stream_child([request['node'], request['renderer'], '--request', str(scene_file),
                           '--output', str(folder / 'video.mp4'), '--browser', request['browser'],
                           '--concurrency', '1'], folder, cancel)
    if not result or not (folder / 'video.mp4').is_file():
        raise RuntimeError('Remotion did not confirm a completed video')
    summary = {key: result[key] for key in ('width', 'height', 'fps', 'durationSeconds', 'frames', 'audio', 'fileSize', 'rendererVersion', 'motionMode')}
    summary.update(background_mode=background_mode, planner=plan.get('planner', 'local_vision_model'), mask=mask)
    (folder / 'result.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    emit(phase='completed', ok=True, **summary)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--start-marker', type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args.request, args.start_marker)
    except BaseException as exc:
        emit(phase='failed', error=str(exc)[:1200], error_type=type(exc).__name__)
        raise SystemExit(1)
