// Fixed reviewed orchestration only. No Python source comes from artwork/options.
export const BRIDGE_PYTHON = String.raw`
import json
from pathlib import Path
import browser_postprocess as _bp

_work = Path('/mitom_work')
_work.mkdir(exist_ok=True)

def _read(name):
    return (_work/name).read_bytes()

def _write_packed(prefix, result):
    for name, value in result.items():
        if name == 'metadata_json':
            (_work/(prefix+'-metadata.json')).write_text(value)
        else:
            (_work/(prefix+'-'+name)).write_bytes(value)
    return result['metadata_json']

def mitom_prepare(config_json):
    return _write_packed('prep', _bp.prepare_rgba_buffers(config_json, _read('source.rgba')))

def mitom_recover(config_json):
    return _write_packed('camera', _bp.recover_camera_buffers(config_json,
        _read('raw-points.f32'), _read('raw-normal.f32'), _read('raw-mask.f32'), _read('raw-metric_scale.f32')))

_original_arrays = _bp.process_arrays
def _arrays_with_progress(*args, **kwargs):
    kwargs['progress'] = lambda name: _mitom_stage(str(name))
    return _original_arrays(*args, **kwargs)
_bp.process_arrays = _arrays_with_progress

def mitom_compose(config_json):
    config = json.loads(config_json)
    prep = json.loads((_work/'prep-metadata.json').read_text())
    restored = _bp.restore_da2_buffers(json.dumps({'graph_side':1036,'restore':prep['da2_restore']}),_read('raw-disparity.f32'))
    da_metadata = json.loads(restored['metadata_json'])
    options = dict(config['options'])
    options['product_type'] = config['product_type']
    options['geometry_frame'] = prep['geometry_frame']
    options['da2_frame'] = None
    options['repair_geometry'] = True
    options['model_budget'] = {'moge_tokens':1225,'moge_side':490,'da2_side':1036,
        'label':'Browser CPU Standard FP32 1225; full native composition; NOT 14400-token UHD inference'}
    process_config = {'width':prep['width'],'height':prep['height'],
        'geometry_width':490,'geometry_height':490,
        'da2_width':da_metadata['width'],'da2_height':da_metadata['height'],'options':options}
    return _write_packed('final', _bp.process_packed_buffers(json.dumps(process_config),
        _read('prep-rgba_u8'),_read('camera-depth_f32'),_read('camera-normal_f32'),
        _read('camera-valid_u8'),_read('camera-intrinsics_f32'),restored['disparity_f32']))
`;
