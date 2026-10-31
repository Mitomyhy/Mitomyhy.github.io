"""Local product matting and scene seeds. No network calls or generative claims."""
from pathlib import Path
import hashlib
import math
import random
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, ImageStat

U2NETP_MD5 = '8e83ca70e441ab06c318d82300c84806'


class AssetError(RuntimeError):
    pass


def cutout_product(source, output, model=None):
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original).convert('RGBA')
        image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    alpha = image.getchannel('A')
    method = 'original_alpha'
    if alpha.getextrema()[0] >= 250:
        if model and Path(model).is_file():
            import numpy as np
            import onnxruntime as ort
            model = Path(model)
            if hashlib.md5(model.read_bytes()).hexdigest() != U2NETP_MD5:
                raise AssetError('Local product mask model failed its pinned checksum.')
            options = ort.SessionOptions()
            options.intra_op_num_threads = 4
            options.inter_op_num_threads = 1
            session = ort.InferenceSession(str(model), sess_options=options, providers=['CPUExecutionProvider'])
            pixels = np.asarray(image.convert('RGB').resize((320, 320)), dtype=np.float32) / 255
            pixels = (pixels - np.array([.485, .456, .406], dtype=np.float32)) / np.array([.229, .224, .225], dtype=np.float32)
            prediction = session.run(None, {session.get_inputs()[0].name: pixels.transpose(2, 0, 1)[None]})[0][0, 0]
            lo, hi = float(prediction.min()), float(prediction.max())
            if hi - lo < .001:
                raise AssetError('The mask model could not separate this product. Use a transparent PNG.')
            alpha = Image.fromarray(((prediction - lo) / (hi - lo) * 255).clip(0, 255).astype('uint8')).resize(image.size, Image.Resampling.LANCZOS)
            method = 'u2netp_local_cpu'
        else:
            # A connected background key is limited to truly flat borders.
            # It never guesses a silhouette for a photo with a complex scene.
            rgb = image.convert('RGB')
            corners = [rgb.getpixel(point) for point in [(0, 0), (rgb.width - 1, 0), (0, rgb.height - 1), (rgb.width - 1, rgb.height - 1)]]
            average = tuple(round(sum(c[n] for c in corners) / 4) for n in range(3))
            if any(max(abs(c[n] - average[n]) for n in range(3)) > 25 for c in corners):
                raise AssetError('Use a transparent product PNG or install the local product mask model for photographic backgrounds.')
            diff = ImageChops.difference(rgb, Image.new('RGB', rgb.size, average))
            r, g, b = diff.split()
            distance = ImageChops.lighter(ImageChops.lighter(r, g), b)
            mask = distance.point(lambda value: 0 if value < 32 else 1)
            for corner in [(0, 0), (rgb.width - 1, 0), (0, rgb.height - 1), (rgb.width - 1, rgb.height - 1)]:
                if mask.getpixel(corner) == 0:
                    ImageDraw.floodfill(mask, corner, 255)
            alpha = mask.point(lambda value: 0 if value == 255 else 255)
            method = 'connected_flat_background'
    bounds = alpha.point(lambda x: 255 if x > 20 else 0).getbbox()
    if not bounds or (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]) < image.width * image.height * .005:
        raise AssetError('No usable product silhouette was found.')
    coverage = ImageStat.Stat(alpha).mean[0] / 255
    if coverage < .005:
        raise AssetError('The product mask is empty.')
    image.putalpha(alpha)
    image = image.crop(bounds)
    margin = max(2, round(max(image.size) * .018))
    result = Image.new('RGBA', (image.width + 2 * margin, image.height + 2 * margin))
    result.alpha_composite(image, (margin, margin))
    result.save(output, 'PNG')
    return {'method': method, 'width': result.width, 'height': result.height, 'coverage': round(coverage, 3)}


def scene_seed(output, theme, palette, *, portrait=False, seed=42):
    """A locally drawn image seed; optional LTX turns it into a moving AI backdrop."""
    width, height = ((448, 704) if portrait else (704, 448))
    colors = [tuple(bytes.fromhex(color.lstrip('#'))) for color in palette]
    top, bottom, accent = colors
    if theme == 'christmas':
        # Keep a holiday backdrop subdued behind the artwork. AI-selected
        # colors tint the room rather than flooding it with saturated stripes.
        top = tuple(round(a * .18 + b * .82) for a, b in zip(top, (18, 43, 32)))
        bottom = tuple(round(a * .2 + b * .8) for a, b in zip(bottom, (86, 64, 38)))
    canvas = Image.new('RGB', (width, height))
    draw = ImageDraw.Draw(canvas)
    for y in range(height):
        ratio = y / max(1, height - 1)
        draw.line((0, y, width, y), fill=tuple(round(top[n] * (1 - ratio) + bottom[n] * ratio) for n in range(3)))
    rng = random.Random(seed)
    decor = Image.new('RGBA', canvas.size)
    d = ImageDraw.Draw(decor)
    if theme == 'christmas':
        # Out of focus trees frame an empty product area.
        for x, scale in [(width * .12, 1), (width * .88, .8)]:
            for level in range(6):
                cy = height * (.1 + level * .13)
                half = width * (.05 + level * .022) * scale
                d.polygon([(x, cy - height * .14), (x - half, cy + height * .17), (x + half, cy + height * .17)], fill=(17, 52, 35, 200))
            for _ in range(13):
                cx, cy = x + rng.uniform(-width * .1, width * .1), rng.uniform(height * .15, height * .85)
                radius = rng.uniform(3, 8)
                d.ellipse((cx-radius, cy-radius, cx+radius, cy+radius), fill=rng.choice([(205, 25, 29, 210), (234, 197, 100, 230)]))
    elif theme in ('floral', 'wedding', 'nursery', 'memorial'):
        for _ in range(28):
            x = rng.choice([rng.uniform(-30, width * .16), rng.uniform(width * .84, width + 30)])
            y, radius = rng.uniform(0, height), rng.uniform(10, 32)
            d.ellipse((x-radius, y-radius, x+radius, y+radius), fill=(*accent, 120))
    elif theme == 'coastal':
        for n in range(5):
            y = height * (.55 + n * .07)
            d.rectangle((0, y, width, y + 4), fill=(235, 246, 244, 70))
    elif theme in ('rustic', 'sports'):
        d.rectangle((0, height * .78, width, height), fill=(*bottom, 150))
    canvas = Image.alpha_composite(canvas.convert('RGBA'), decor.filter(ImageFilter.GaussianBlur(12)))
    lights = Image.new('RGBA', canvas.size)
    d = ImageDraw.Draw(lights)
    for _ in range(65):
        x, y = rng.uniform(0, width), rng.uniform(0, height)
        radius = rng.uniform(2, 12)
        d.ellipse((x-radius, y-radius, x+radius, y+radius), fill=(255, 235, 182, rng.randrange(35, 110)))
    canvas = Image.alpha_composite(canvas, lights.filter(ImageFilter.GaussianBlur(2)))
    canvas.convert('RGB').save(output, 'PNG')
    return {'width': width, 'height': height, 'kind': 'procedural_seed'}
