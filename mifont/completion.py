"""Explicit approximate ASCII completion from an original geometric grammar.

This is not neural inference, font identification, or a font clone. No system,
paid or third-party seed font is read. Observed outlines are never replaced.
The caller must opt in to proposals and review them before production use.
"""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import hashlib
import math
from pathlib import Path
from statistics import median

ASCII_CHARACTERS = ''.join(chr(value) for value in range(32, 127))
METHOD = 'component-grammar-v1'


class CompletionError(ValueError):
    pass


class CompletionUnavailable(CompletionError):
    pass


def _finite(value, name, low=-4000, high=4000):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not low <= value <= high:
        raise CompletionError(f'{name} must be a finite number between {low} and {high}.')
    return float(value)


def _records(glyphs):
    try:
        from .font_export import normalize_glyph, FontError
    except ImportError:
        from font_export import normalize_glyph, FontError
    if isinstance(glyphs, dict):
        values = []
        for char, record in glyphs.items():
            if not isinstance(record, dict) or record.get('char', char) != char:
                raise CompletionError('Glyph mapping keys must match the character labels.')
            values.append(dict(record, char=char))
    elif isinstance(glyphs, list):
        values = glyphs
    else:
        raise CompletionError('Glyphs must be a list or a character-to-record mapping.')
    if len(values) > 512:
        raise CompletionError('Complete at most 512 reference glyphs per project.')
    result = {}
    for record in values:
        if not isinstance(record, dict) or not isinstance(record.get('char'), str) or len(record['char']) != 1:
            raise CompletionError('Each reference glyph needs one explicit character label.')
        char = record['char']
        if char in result:
            raise CompletionError(f'Duplicate character label: {char!r}.')
        recording = record.get('recording')
        if not isinstance(recording, (list, tuple)) or len(recording) > 20000:
            raise CompletionError('Each glyph needs a bounded FontTools Pen recording.')
        for item in recording:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise CompletionError('Invalid outline recording.')
            operation, points = item
            if operation not in {'moveTo', 'lineTo', 'curveTo', 'qCurveTo', 'closePath', 'endPath'}:
                raise CompletionError('Unsupported outline operation.')
            if not isinstance(points, (list, tuple)):
                raise CompletionError('Invalid outline coordinates.')
            for point in points:
                if point is None and operation == 'qCurveTo':
                    continue
                if not isinstance(point, (list, tuple)) or len(point) != 2:
                    raise CompletionError('Outline points must contain X and Y coordinates.')
                _finite(point[0], 'Outline X', -16000, 16000)
                _finite(point[1], 'Outline Y', -16000, 16000)
        _finite(record.get('advance_width', 600), 'Advance width', 0, 4000)
        try:
            # Validate contour order/arity and JSON metadata without replacing
            # the caller's observed representation or its provenance.
            normalize_glyph(record, preview=False)
        except FontError as exc:
            raise CompletionError('The reference outline is not a valid closed glyph recording.') from exc
        result[char] = deepcopy(record)
    return result


def _bbox(record):
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.recordingPen import replayRecording
    pen = BoundsPen(None)
    try:
        replayRecording(record['recording'], pen)
    except (ValueError, TypeError, IndexError, AssertionError) as exc:
        raise CompletionError('The reference outline is not a valid Pen recording.') from exc
    return pen.bounds


def _style(records, metrics):
    from fontTools.pens.areaPen import AreaPen
    from fontTools.pens.perimeterPen import PerimeterPen
    from fontTools.pens.recordingPen import replayRecording
    visible = {char: record for char, record in records.items()
               if record.get('origin', 'observed') == 'observed' and record['recording']}
    if not visible:
        raise CompletionError('Add at least one labeled, observed glyph before proposing missing characters.')
    bounds = {char: _bbox(record) for char, record in visible.items()}
    cap = [box[3] for char, box in bounds.items() if char.isupper() and box and box[3] > 200]
    xheights = [box[3] for char, box in bounds.items() if char in 'acemnorsuvwxz' and box and box[3] > 150]
    cap_height = _finite(metrics.get('cap_height', median(cap) if cap else 700), 'Cap height', 250, 1200)
    x_height = _finite(metrics.get('x_height', median(xheights) if xheights else cap_height * .714), 'X height', 150, cap_height)
    ascender = _finite(metrics.get('ascender', max(800, cap_height)), 'Ascender', cap_height, 1400)
    descender = _finite(metrics.get('descender', -200), 'Descender', -500, -20)
    advances = [record.get('advance_width', 600) for char, record in visible.items() if char.isalpha()]
    width = max(280, min(900, median(advances) - 90 if advances else cap_height * .72))
    strokes, slants = [], []
    for char, record in visible.items():
        area, perimeter = AreaPen(None), PerimeterPen(None)
        try:
            replayRecording(record['recording'], area)
            replayRecording(record['recording'], perimeter)
            if perimeter.value > 0:
                strokes.append(2 * abs(area.value) / perimeter.value)
        except (ValueError, TypeError, IndexError, AssertionError):
            pass
        if char in 'Ilit':
            points = [point for operation, values in record['recording'] for point in values if point is not None]
            box = bounds[char]
            if box and box[3] - box[1] > 200:
                height = box[3] - box[1]
                top = [point[0] for point in points if point[1] > box[1] + height * .8]
                bottom = [point[0] for point in points if point[1] < box[1] + height * .2]
                if top and bottom:
                    slants.append((median(top) - median(bottom)) / (height * .8))
    stroke = max(18, min(150, median(strokes) if strokes else cap_height * .085))
    slant = max(-.35, min(.45, median(slants) if slants else 0))
    return dict(cap_height=cap_height, x_height=x_height, ascender=ascender,
                descender=descender, width=width, stroke=stroke, slant=slant,
                reference_count=len(visible))


def _arc(cx, cy, rx, ry, start=0, end=360, steps=24):
    return [(cx + rx * math.cos(math.radians(start + (end - start) * index / steps)),
             cy + ry * math.sin(math.radians(start + (end - start) * index / steps)))
            for index in range(steps + 1)]


def _grammar(char):
    """Original centerline constructions; not outlines copied from a font."""
    circle = lambda: _arc(.5, .5, .36, .48)
    upper = {
        'A': [[(.1, 0), (.5, 1), (.9, 0)], [(.27, .42), (.73, .42)]],
        'B': [[(.15, 0), (.15, 1), (.58, 1), (.84, .87), (.84, .66), (.58, .5), (.15, .5)], [(.58, .5), (.86, .35), (.86, .15), (.58, 0), (.15, 0)]],
        'C': [_arc(.53, .5, .39, .48, 45, 315)],
        'D': [[(.15, 0), (.15, 1), (.5, 1), (.78, .85), (.88, .5), (.78, .15), (.5, 0), (.15, 0)]],
        'E': [[(.86, 1), (.15, 1), (.15, 0), (.86, 0)], [(.15, .5), (.72, .5)]],
        'F': [[(.15, 0), (.15, 1), (.86, 1)], [(.15, .53), (.72, .53)]],
        'G': [_arc(.5, .5, .38, .48, 45, 360), [(.88, .5), (.58, .5)]],
        'H': [[(.15, 0), (.15, 1)], [(.85, 0), (.85, 1)], [(.15, .5), (.85, .5)]],
        'I': [[(.2, 1), (.8, 1)], [(.5, 1), (.5, 0)], [(.2, 0), (.8, 0)]],
        'J': [[(.25, 1), (.82, 1), (.82, .22), (.68, 0), (.38, 0), (.18, .18)]],
        'K': [[(.15, 0), (.15, 1)], [(.85, 1), (.15, .45), (.85, 0)]],
        'L': [[(.15, 1), (.15, 0), (.85, 0)]],
        'M': [[(.1, 0), (.1, 1), (.5, .35), (.9, 1), (.9, 0)]],
        'N': [[(.15, 0), (.15, 1), (.85, 0), (.85, 1)]],
        'O': [circle()],
        'P': [[(.15, 0), (.15, 1), (.6, 1), (.85, .85), (.85, .65), (.6, .5), (.15, .5)]],
        'Q': [circle(), [(.58, .2), (.95, -.1)]],
        'R': [[(.15, 0), (.15, 1), (.6, 1), (.85, .85), (.85, .65), (.6, .5), (.15, .5)], [(.55, .5), (.9, 0)]],
        'S': [[(.85, .87), (.65, 1), (.32, 1), (.14, .8), (.2, .62), (.75, .37), (.86, .18), (.67, 0), (.32, 0), (.14, .14)]],
        'T': [[(.08, 1), (.92, 1)], [(.5, 1), (.5, 0)]],
        'U': [[(.15, 1), (.15, .23), (.28, .04), (.5, 0), (.72, .04), (.85, .23), (.85, 1)]],
        'V': [[(.1, 1), (.5, 0), (.9, 1)]],
        'W': [[(.06, 1), (.25, 0), (.5, .65), (.75, 0), (.94, 1)]],
        'X': [[(.12, 1), (.88, 0)], [(.12, 0), (.88, 1)]],
        'Y': [[(.1, 1), (.5, .5), (.9, 1)], [(.5, .5), (.5, 0)]],
        'Z': [[(.12, 1), (.88, 1), (.12, 0), (.88, 0)]],
    }
    if char in upper:
        return upper[char], [], 1.25 if char in 'MW' else 1
    lower = {
        'a': [_arc(.46, .5, .32, .47), [(.8, .97), (.8, 0)]],
        'b': [[(.16, 0), (.16, 1.42)], _arc(.5, .47, .34, .46)],
        'c': [_arc(.5, .5, .36, .47, 45, 315)],
        'd': [_arc(.47, .47, .33, .46), [(.8, 0), (.8, 1.42)]],
        'e': [[(.16, .52), (.84, .52), (.8, .8), (.62, .97), (.36, .97), (.16, .78), (.14, .3), (.34, .03), (.65, .02), (.84, .16)]],
        'f': [[(.3, 0), (.3, 1.12), (.44, 1.42), (.7, 1.42)], [(.08, .95), (.65, .95)]],
        'g': [_arc(.47, .5, .33, .46), [(.8, .97), (.8, -.25), (.65, -.4), (.3, -.4)]],
        'h': [[(.16, 0), (.16, 1.42)], [(.16, .75), (.35, .97), (.62, .97), (.8, .76), (.8, 0)]],
        'i': [[(.5, 0), (.5, .95)]],
        'j': [[(.6, .95), (.6, -.24), (.45, -.4), (.2, -.4)]],
        'k': [[(.16, 0), (.16, 1.42)], [(.82, .95), (.16, .4), (.82, 0)]],
        'l': [[(.4, 1.42), (.4, .12), (.55, 0)]],
        'm': [[(.1, 0), (.1, .95)], [(.1, .75), (.25, .95), (.4, .95), (.5, .75), (.5, 0)], [(.5, .75), (.65, .95), (.8, .95), (.9, .75), (.9, 0)]],
        'n': [[(.16, 0), (.16, .95)], [(.16, .72), (.35, .95), (.63, .95), (.8, .75), (.8, 0)]],
        'o': [_arc(.5, .5, .35, .47)],
        'p': [[(.16, -.4), (.16, .95)], _arc(.5, .5, .34, .46)],
        'q': [_arc(.47, .5, .33, .46), [(.8, .95), (.8, -.4)]],
        'r': [[(.2, 0), (.2, .95)], [(.2, .72), (.42, .95), (.7, .95)]],
        's': [[(.8, .85), (.62, .97), (.35, .97), (.16, .78), (.25, .6), (.7, .4), (.82, .2), (.64, .03), (.35, .03), (.16, .16)]],
        't': [[(.4, 1.3), (.4, .18), (.53, .02), (.75, .02)], [(.1, .95), (.75, .95)]],
        'u': [[(.16, .95), (.16, .22), (.34, .02), (.6, .02), (.8, .25)], [(.8, .95), (.8, 0)]],
        'v': [[(.12, .95), (.5, 0), (.88, .95)]],
        'w': [[(.07, .95), (.25, 0), (.5, .65), (.75, 0), (.93, .95)]],
        'x': [[(.15, .95), (.85, 0)], [(.15, 0), (.85, .95)]],
        'y': [[(.12, .95), (.48, .08), (.85, .95)], [(.48, .08), (.32, -.32), (.12, -.4)]],
        'z': [[(.16, .95), (.84, .95), (.16, .03), (.84, .03)]],
    }
    if char in lower:
        return lower[char], [(.5, 1.25)] if char == 'i' else [(.6, 1.25)] if char == 'j' else [], 1.25 if char in 'mw' else .6 if char in 'ilj' else 1
    digits = {
        '0': [circle()], '1': [[(.24, .8), (.5, 1), (.5, 0)], [(.2, 0), (.8, 0)]],
        '2': [[(.15, .8), (.3, 1), (.65, 1), (.85, .8), (.8, .6), (.15, 0), (.85, 0)]],
        '3': [[(.16, .92), (.4, 1), (.7, .95), (.84, .78), (.64, .52), (.4, .5)], [(.64, .52), (.85, .25), (.68, .03), (.35, 0), (.15, .12)]],
        '4': [[(.72, 0), (.72, 1), (.12, .3), (.9, .3)]],
        '5': [[(.85, 1), (.2, 1), (.15, .55), (.65, .55), (.85, .35), (.8, .14), (.6, 0), (.3, 0), (.12, .12)]],
        '6': [_arc(.5, .34, .35, .33), [(.15, .35), (.2, .8), (.42, 1), (.72, 1)]],
        '7': [[(.12, 1), (.88, 1), (.35, 0)]],
        '8': [_arc(.5, .75, .31, .24), _arc(.5, .25, .36, .25)],
        '9': [_arc(.5, .67, .35, .32), [(.85, .65), (.8, .2), (.58, 0), (.28, 0)]],
    }
    if char in digits:
        return digits[char], [], 1
    punctuation = {
        ' ': ([], [], .55), '.': ([], [(.5, .02)], .4), ',': ([[ (.5, .02), (.35, -.16)]], [], .4),
        ':': ([], [(.5, .15), (.5, .65)], .4), ';': ([[(.5, .15), (.35, -.05)]], [(.5, .65)], .4),
        '!': ([[(.5, 1), (.5, .3)]], [(.5, .02)], .4),
        '?': ([[(.2, .8), (.3, .97), (.65, .97), (.8, .8), (.7, .62), (.5, .48), (.5, .3)]], [(.5, .02)], .9),
        '"': ([[(.3, .8), (.3, 1)], [(.7, .8), (.7, 1)]], [], .6),
        "'": ([[(.5, .8), (.5, 1)]], [], .35),
        '`': ([[(.35, 1), (.65, .8)]], [], .5),
        '-': ([[(.2, .45), (.8, .45)]], [], .65), '_': ([[(.05, -.15), (.95, -.15)]], [], 1),
        '+': ([[(.15, .45), (.85, .45)], [(.5, .1), (.5, .8)]], [], 1),
        '=': ([[(.15, .3), (.85, .3)], [(.15, .65), (.85, .65)]], [], 1),
        '/': ([[(.12, -.1), (.88, 1.1)]], [], .8), '\\': ([[(.12, 1.1), (.88, -.1)]], [], .8),
        '|': ([[(.5, -.2), (.5, 1.1)]], [], .35),
        '<': ([[(.8, .1), (.2, .5), (.8, .9)]], [], 1), '>': ([[(.2, .1), (.8, .5), (.2, .9)]], [], 1),
        '(': ([_arc(.8, .5, .55, .65, 100, 260)], [], .6), ')': ([_arc(.2, .5, .55, .65, -80, 80)], [], .6),
        '[': ([[(.8, 1.1), (.3, 1.1), (.3, -.15), (.8, -.15)]], [], .6),
        ']': ([[(.2, 1.1), (.7, 1.1), (.7, -.15), (.2, -.15)]], [], .6),
        '{': ([[(.85, 1.1), (.55, 1), (.55, .7), (.2, .5), (.55, .3), (.55, 0), (.85, -.1)]], [], .65),
        '}': ([[(.15, 1.1), (.45, 1), (.45, .7), (.8, .5), (.45, .3), (.45, 0), (.15, -.1)]], [], .65),
        '^': ([[(.15, .7), (.5, 1), (.85, .7)]], [], .9),
        '~': ([[(.1 + .8 * i / 16, .6 + .1 * math.sin(i / 16 * math.tau)) for i in range(17)]], [], 1),
        '#': ([[(.3, 0), (.45, 1)], [(.6, 0), (.75, 1)], [(.1, .35), (.9, .35)], [(.1, .65), (.9, .65)]], [], 1),
        '$': (upper['S'] + [[(.5, -.12), (.5, 1.12)]], [], 1),
        '%': ([[(.12, 0), (.88, 1)], _arc(.25, .8, .13, .17), _arc(.75, .2, .13, .17)], [], 1.2),
        '&': ([[(.9, .05), (.3, .72), (.28, .9), (.43, 1), (.65, .93), (.68, .75), (.2, .35), (.12, .18), (.28, 0), (.55, 0), (.9, .55)]], [], 1.1),
        '*': ([[(.15, .85), (.85, .45)], [(.15, .45), (.85, .85)], [(.5, .2), (.5, 1.05)]], [], 1),
        '@': ([_arc(.5, .5, .47, .49, 30, 350), _arc(.5, .48, .22, .27), [(.72, .75), (.72, .26), (.88, .27), (.98, .48)]], [], 1.35),
    }
    return punctuation.get(char)


def _propose(char, style):
    from fontTools.pens.recordingPen import RecordingPen
    from fontTools.pens.transformPen import TransformPen
    grammar = _grammar(char)
    if grammar is None:
        return None
    strokes, dots, width_factor = grammar
    width = style['width'] * width_factor
    height = style['x_height'] if char.islower() else style['cap_height']
    radius = min(style['stroke'] / 2, width * .12, height * .12)
    recording = RecordingPen()
    pen = TransformPen(recording, (1, 0, style['slant'], 1, radius + 25, 0))
    kappa = .5522847498307936

    def stroke(a, b):
        ax, ay, bx, by = a[0] * width, a[1] * height, b[0] * width, b[1] * height
        length = math.hypot(bx - ax, by - ay)
        if length < .001:
            return
        dx, dy = (bx - ax) / length, (by - ay) / length
        nx, ny, r, k = -dy, dx, radius, kappa * radius
        point = lambda x, y, normal=0, along=0: (x + normal * nx + along * dx, y + normal * ny + along * dy)
        pen.moveTo(point(ax, ay, r))
        pen.lineTo(point(bx, by, r))
        pen.curveTo(point(bx, by, r, k), point(bx, by, k, r), point(bx, by, 0, r))
        pen.curveTo(point(bx, by, -k, r), point(bx, by, -r, k), point(bx, by, -r))
        pen.lineTo(point(ax, ay, -r))
        pen.curveTo(point(ax, ay, -r, -k), point(ax, ay, -k, -r), point(ax, ay, 0, -r))
        pen.curveTo(point(ax, ay, k, -r), point(ax, ay, r, -k), point(ax, ay, r))
        pen.closePath()

    for polyline in strokes:
        for a, b in zip(polyline, polyline[1:]):
            stroke(a, b)
    for x, y in dots:
        # Four clockwise cubic arcs, consistent with the stroke winding.
        points = _arc(x * width, y * height, radius, radius, 0, -360, 4)
        pen.moveTo(points[0])
        for index in range(4):
            a = -math.pi / 2 * index
            b = -math.pi / 2 * (index + 1)
            p, q = points[index], points[index + 1]
            pen.curveTo((p[0] + math.sin(a) * kappa * radius, p[1] - math.cos(a) * kappa * radius),
                        (q[0] - math.sin(b) * kappa * radius, q[1] + math.cos(b) * kappa * radius), q)
        pen.closePath()
    result = dict(char=char, origin='generated', approximate=True, recording=deepcopy(recording.value),
                  advance_width=round(width + style['stroke'] + 75, 3), lsb=25,
                  generation=dict(method=METHOD, approximate=True, neural=False,
                                  reference_count=style['reference_count'], needs_review=True))
    result['provenance'] = deepcopy(result['generation'])
    box = _bbox(result)
    result['bbox'] = list(box) if box else [0, 0, 0, 0]
    if box:
        result['lsb'] = round(box[0], 3)
        result['advance_width'] = round(max(result['advance_width'], box[2] + 40), 3)
    try:
        from .font_export import glyph_preview
    except ImportError:
        from font_export import glyph_preview
    result['preview_svg'] = glyph_preview(result)
    return result


def _reference_proposals(records, missing, style, reference, progress):
    """Fit explicitly selected, license-verified OFL outlines locally.

    `reference` is a server-resolved capability, never a client path option.
    Hashes are checked again here because a worker may start after selection.
    """
    if not isinstance(reference, dict) or reference.get('license') != 'OFL-1.1':
        raise CompletionError('Select a verified open-font reference before reference completion.')
    license_text = reference.get('license_text', '')
    if (not isinstance(license_text, str) or 'SIL OPEN FONT LICENSE Version 1.1' not in license_text
            or hashlib.sha256(license_text.encode('utf-8')).hexdigest() != reference.get('license_sha256')):
        raise CompletionError('The selected reference license changed; research and select it again.')
    path = Path(reference.get('font_path', ''))
    if not path.is_file() or path.stat().st_size > 3 * 1024 * 1024:
        raise CompletionError('The selected reference font is unavailable or exceeds the size limit.')
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != reference.get('sha256'):
        raise CompletionError('The selected reference font changed; research and select it again.')
    from fontTools.ttLib import TTFont
    from fontTools.pens.recordingPen import DecomposingRecordingPen, RecordingPen, replayRecording
    from fontTools.pens.transformPen import TransformPen
    try:
        from .font_export import normalize_glyph
    except ImportError:
        from font_export import normalize_glyph
    generated = []
    with TTFont(BytesIO(data), lazy=False) as font:
        glyphset = font.getGlyphSet()
        cmap = font.getBestCmap() or {}
        upm = font['head'].unitsPerEm
        if not 16 <= upm <= 16384:
            raise CompletionError('Unsupported reference font units.')

        def read(char):
            name = cmap.get(ord(char))
            if name is None:
                return None
            raw = DecomposingRecordingPen(glyphset)
            glyphset[name].draw(raw)
            pen = RecordingPen()
            replayRecording(raw.value, TransformPen(pen, (1000 / upm, 0, 0, 1000 / upm, 0, 0)))
            return {'char': char, 'recording': pen.value,
                    'advance_width': glyphset[name].width * 1000 / upm, 'origin': 'observed'}

        fitting = {char: value for char in set(records) | set('HIOxnoilt') if (value := read(char)) is not None}
        reference_style = _style(fitting, {})
        width_ratios, height_ratios = [], []
        for char, record in records.items():
            if record.get('origin', 'observed') != 'observed' or char not in fitting or not char.isalnum():
                continue
            a, b = _bbox(record), _bbox(fitting[char])
            if a and b and a[2] - a[0] > 20 and b[2] - b[0] > 20:
                width_ratios.append((a[2] - a[0]) / (b[2] - b[0]))
            if a and b and a[3] - a[1] > 80 and b[3] - b[1] > 80:
                height_ratios.append((a[3] - a[1]) / (b[3] - b[1]))
        sx = max(.35, min(2.5, median(width_ratios) if width_ratios else style['width'] / reference_style['width']))
        sy = max(.35, min(2.5, median(height_ratios) if height_ratios else style['cap_height'] / reference_style['cap_height']))
        shear = max(-.5, min(.5, style['slant'] - reference_style['slant'] * sx / sy))
        fit = {'scale_x': round(sx, 6), 'scale_y': round(sy, 6), 'slant_delta': round(shear, 6),
               'matched_chars': ''.join(sorted(char for char in set(records) & set(fitting)
                                              if records[char].get('origin', 'observed') == 'observed')),
               'method': 'median observed outline width/height ratios and stem slant; affine proposal'}
        for index, char in enumerate(missing):
            source = read(char)
            if source is not None:
                pen = RecordingPen()
                replayRecording(source['recording'], TransformPen(pen, (sx, 0, shear * sy, sy, 0, 0)))
                result = {'char': char, 'recording': pen.value, 'origin': 'generated', 'approximate': True,
                          'advance_width': max(1, round(source['advance_width'] * sx)),
                          'generation': {'method': 'ofl-reference-affine-v1', 'approximate': True,
                                         'neural': False, 'needs_review': True,
                                         'reference_id': reference['id'], 'source_family': reference['family'],
                                         'source_sha256': reference['sha256'], 'license': 'OFL-1.1', 'fit': fit}}
                result['provenance'] = deepcopy(result['generation'])
                # FontTools validates closed contours and creates the same safe preview as extraction.
                records[char] = normalize_glyph(result)
                generated.append(char)
            if progress:
                progress({'progress': round(100 * (index + 1) / len(missing)),
                          'message': 'Fitting selected open-font reference outlines locally'})
    return generated, fit


def complete_glyphs(glyphs, options=None, progress=None, *, reference=None):
    """Return all records and explicit coverage/provenance, never hidden fallback.

    options: mode='observed'|'proposal'|'reference'|'model', allow_approximate=False,
    target_chars=ASCII95, metrics={cap_height,x_height,ascender,descender}.
    Progress callbacks receive {progress:int, message:str}.
    """
    options = {} if options is None else options
    if not isinstance(options, dict):
        raise CompletionError('Completion options must be an object.')
    if set(options) - {'mode', 'allow_approximate', 'target_chars', 'metrics', 'reference_id', 'accept_reference_license', 'replace_generated'}:
        raise CompletionError('Unknown completion option.')
    records = _records(glyphs)
    target = options.get('target_chars', ASCII_CHARACTERS)
    if not isinstance(target, str) or not 1 <= len(target) <= 256 or len(set(target)) != len(target):
        raise CompletionError('Choose 1–256 distinct target characters.')
    if any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in target):
        raise CompletionError('Target characters must be printable Unicode characters.')
    mode = options.get('mode', 'observed')
    if mode not in {'observed', 'proposal', 'reference', 'model'}:
        raise CompletionError('Choose observed, proposal, selected reference or model completion.')
    if mode != 'reference' and options.get('reference_id'):
        raise CompletionError('A selected reference requires reference completion mode.')
    if mode == 'reference' and (not isinstance(reference, dict) or options.get('reference_id') != reference.get('id')):
        raise CompletionError('The selected reference must be resolved from this project by the local server.')
    if mode == 'reference' and options.get('accept_reference_license') is not True:
        raise CompletionError('Reference completion requires explicit acceptance of its OFL source and derivative font license.')
    replace_generated = options.get('replace_generated', False)
    if not isinstance(replace_generated, bool) or (replace_generated and mode != 'reference'):
        raise CompletionError('Replacing generated proposals requires explicit selected-reference mode.')
    missing = [char for char in target if char not in records or
               (replace_generated and records[char].get('origin', 'observed') == 'generated')]
    generated = []
    warnings = []
    style = None
    if missing and mode == 'model':
        raise CompletionUnavailable('No verified neural completion model is bundled. This build offers explicit CPU geometry proposals or locally fitted, selected OFL references; neither is neural inference or recovery of the original font.')
    fit = None
    if missing and mode in {'proposal', 'reference'}:
        if options.get('allow_approximate') is not True:
            raise CompletionError('Approximate completion requires explicit approval (allow_approximate=true).')
        metrics = options.get('metrics', {})
        if not isinstance(metrics, dict) or set(metrics) - {'cap_height', 'x_height', 'ascender', 'descender'}:
            raise CompletionError('Invalid font metrics.')
        style = _style(records, metrics)
        if mode == 'reference':
            generated, fit = _reference_proposals(records, missing, style, reference, progress)
            warnings.append('Missing characters use the selected OFL reference with a local affine style fit. They are proposals from that source family, not recovered original characters or AI inference. Review spacing, joins and decorative differences. The derivative font must retain OFL notices and respect source Reserved Font Names.')
            unavailable = ''.join(char for char in missing if char not in generated)
            if unavailable:
                warnings.append('The selected source has no outlines for these characters: ' + unavailable + '. Existing proposals, if present, were preserved; no fallback outlines were substituted.')
        else:
            warnings.append('Generated characters are geometric style proposals, not AI inference or recovered original glyphs. Review their shapes, spacing and joining; decorative/brush details are not reconstructed.')
            for index, char in enumerate(missing):
                record = _propose(char, style)
                if record is not None:
                    records[char] = record
                    generated.append(char)
                if progress:
                    progress({'progress': round(100 * (index + 1) / len(missing)), 'message': 'Proposing missing characters on CPU'})
    missing = [char for char in target if char not in records]
    observed = sorted((char for char, record in records.items() if record.get('origin', 'observed') == 'observed'), key=ord)
    method = ('ofl-reference-affine-v1' if mode == 'reference' else METHOD) if generated else 'observed-only'
    provenance = {'method': method, 'neural': False, 'approximate': bool(generated), 'seed_font': None,
                  'style_parameters': style, 'observed_outlines_preserved': True}
    if generated and mode == 'reference':
        provenance.update(seed_font=reference['family'], reference_fit=fit,
                          license_obligations={key: reference[key] for key in ('id', 'family', 'sha256', 'license', 'license_text', 'license_sha256', 'source_url', 'license_url')},
                          requires_renamed_family=True)
    result = {'glyphs': [records[char] for char in sorted(records, key=ord)],
            'observed_chars': ''.join(observed), 'generated_chars': ''.join(generated),
            'missing_chars': ''.join(missing), 'target_chars': target,
            'engine': method, 'warnings': warnings, 'provenance': provenance}
    if generated and mode == 'reference':
        result['font_metadata'] = {'family_name': 'MiFont Derived', 'style_name': 'Regular',
                                   'license': 'OFL-1.1', 'license_url': reference['license_url'],
                                   'source_family': reference['family'], 'requires_renamed_family': True}
    return result
