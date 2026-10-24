"""Bounded public open-font research; all artwork comparison stays local.

Only Google Fonts catalogue metadata and license-verified Google Fonts source
files are requested. Neither screenshots nor outline data are sent remotely.
Candidate downloads are references, never implicit replacement glyphs.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from io import BytesIO
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from PIL import Image, ImageChops, ImageDraw, ImageFont

CATALOG_URL = 'https://fonts.google.com/metadata/fonts'
SOURCE_ROOT = 'https://raw.githubusercontent.com/google/fonts/main/ofl/'
MAX_CATALOG = 8 * 1024 * 1024
MAX_FONT = 3 * 1024 * 1024
DEFAULT_FAMILIES = ('Lora', 'Roboto', 'Roboto Mono', 'Oswald', 'Kalam', 'Dancing Script')


class ReferenceError(ValueError):
    pass


def _allowed_url(url):
    try:
        value = urlsplit(url)
        port = value.port
    except ValueError as exc:
        raise ReferenceError('Unsupported online font source URL.') from exc
    if value.scheme != 'https' or value.username or value.password or port not in (None, 443) or value.fragment:
        raise ReferenceError('Research only reads official HTTPS open-font sources.')
    if value.hostname == 'fonts.google.com' and value.path == '/metadata/fonts' and not value.query:
        return url
    if (value.hostname == 'raw.githubusercontent.com' and value.path.startswith('/google/fonts/main/ofl/')
            and not value.query and all(part not in {'.', '..'} for part in value.path.split('/'))):
        return url
    raise ReferenceError('Unsupported online font source.')


class _OfficialRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        _allowed_url(newurl)
        return super().redirect_request(request, response, code, message, headers, newurl)


def _fetch(url, maximum, timeout=10):
    _allowed_url(url)
    request = Request(url, headers={'User-Agent': 'MiFont/1.0 public open-font research'})
    try:
        with build_opener(_OfficialRedirects()).open(request, timeout=timeout) as response:
            length = response.headers.get('Content-Length')
            if length and int(length) > maximum:
                raise ReferenceError('The public reference exceeds the research download limit.')
            data = response.read(maximum + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ReferenceError('The official font source could not be reached. Retry when online.') from exc
    if len(data) > maximum:
        raise ReferenceError('The public reference exceeds the research download limit.')
    return data


def _default_cache():
    base = Path(os.environ.get('LOCALAPPDATA', Path.home()))
    return base / 'MITOM/MiFont/ReferenceCache'


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _catalog(cache):
    path = cache / 'google-fonts-catalog.json'
    online = True
    try:
        data = _fetch(CATALOG_URL, MAX_CATALOG)
        if data.startswith(b")]}'"):
            if b'\n' not in data:
                raise ReferenceError('The official font catalogue returned an unsupported response.')
            data = data.split(b'\n', 1)[1]
        result = json.loads(data)
        if not isinstance(result, dict) or not isinstance(result.get('familyMetadataList'), list):
            raise ReferenceError('The official font catalogue returned an unsupported response.')
        _write(path, data)
    except (ReferenceError, ValueError) as exc:
        if not path.is_file() or path.stat().st_size > MAX_CATALOG:
            raise ReferenceError('Online font research is unavailable; no cached catalogue is present.') from exc
        try:
            result = json.loads(path.read_bytes())
            if not isinstance(result, dict) or not isinstance(result.get('familyMetadataList'), list):
                raise ValueError('Invalid cached catalogue')
        except (ValueError, OSError) as cached_error:
            raise ReferenceError('The cached public catalogue is invalid. Retry online research.') from cached_error
        online = False
    return result['familyMetadataList'], online


def _cached_family_source(name, slug, cache):
    """Offline research reuses only a fully reverified owned-cache reference."""
    path = cache / slug / 'reference.json'
    if not path.is_file() or path.stat().st_size > 256 * 1024:
        raise ReferenceError('No verified cached reference is available.')
    try:
        card = json.loads(path.read_bytes())
        if not isinstance(card, dict) or card.get('family') != name:
            raise ReferenceError('The cached source family does not match.')
        trusted = resolve_reference(card.get('id'), [card], cache_dir=cache)
        card.update(cached_source=True)
        return card, Path(trusted['font_path']).read_bytes()
    except (ValueError, TypeError, OSError) as exc:
        raise ReferenceError('The cached reference could not be verified.') from exc


def _family_source(family, cache):
    name = family['family']
    slug = re.sub('[^a-z0-9]', '', name.lower())
    if not slug or len(slug) > 100:
        raise ReferenceError('Unsupported reference family name.')
    base = SOURCE_ROOT + slug + '/'
    metadata_url, license_url = base + 'METADATA.pb', base + 'OFL.txt'
    try:
        metadata_data = _fetch(metadata_url, 128 * 1024)
        license_data = _fetch(license_url, 128 * 1024)
    except ReferenceError:
        return _cached_family_source(name, slug, cache)
    metadata = metadata_data.decode('utf-8')
    declared_name = re.search(r'^name:\s*"([^"\n]+)"', metadata, re.M)
    if not declared_name or declared_name.group(1) != name or not re.search(r'^license:\s*"OFL"', metadata, re.M):
        raise ReferenceError('The selected family does not have verified OFL source metadata.')
    if b'SIL OPEN FONT LICENSE Version 1.1' not in license_data:
        raise ReferenceError('The reference font license could not be verified.')
    blocks = re.findall(r'fonts\s*\{(.*?)\n\}', metadata, re.S)
    block = next((item for item in blocks if 'style: "normal"' in item and 'weight: 400' in item), blocks[0] if blocks else '')
    found = re.search(r'filename:\s*"([^"\n]+)"', block)
    if not found:
        raise ReferenceError('The official font source has no supported font file.')
    filename = found.group(1)
    if Path(filename).name != filename or '\\' in filename or not filename.lower().endswith(('.ttf', '.otf')):
        raise ReferenceError('Unsupported source font filename.')
    url = base + quote(filename, safe='')
    folder = cache / slug
    index_file = folder / 'reference.json'
    font_file = folder / 'reference.font'
    font_data = None
    if index_file.is_file() and font_file.is_file() and font_file.stat().st_size <= MAX_FONT:
        try:
            saved = json.loads(index_file.read_bytes())
            cached = font_file.read_bytes()
            if saved.get('download_url') == url and saved.get('sha256') == hashlib.sha256(cached).hexdigest():
                font_data = cached
        except (ValueError, OSError):
            pass
    if font_data is None:
        try:
            font_data = _fetch(url, MAX_FONT)
        except ReferenceError:
            return _cached_family_source(name, slug, cache)
    from fontTools.ttLib import TTFont
    with TTFont(BytesIO(font_data), lazy=True) as font:
        if 'head' not in font or 'cmap' not in font:
            raise ReferenceError('The official font file is invalid.')
        cmap = font.getBestCmap() or {}
        coverage = sum(ord(char) in cmap for char in ''.join(chr(c) for c in range(32, 127)))
    digest = hashlib.sha256(font_data).hexdigest()
    reference = {'id': slug + '-' + digest[:16], 'family': name,
                 'category': family.get('category', ''), 'license': 'OFL-1.1',
                 'license_url': license_url, 'license_text': license_data.decode('utf-8'),
                 'license_sha256': hashlib.sha256(license_data).hexdigest(),
                 'source_url': 'https://github.com/google/fonts/tree/main/ofl/' + slug,
                 'metadata_url': metadata_url, 'download_url': url,
                 'sha256': digest, 'size': len(font_data), 'ascii_coverage': coverage,
                 'use': 'reference-only', 'generated_source_family': None,
                 'completion_notice': 'Reference matching does not identify or clone the original font. Using its actual outlines for completion requires explicit selection, source-family attribution and the OFL license.'}
    _write(font_file, font_data)
    _write(folder / 'OFL.txt', license_data)
    _write(index_file, json.dumps(reference, ensure_ascii=False, indent=2).encode('utf-8'))
    return reference, font_data


def _observed_font(records):
    """Build an in-memory test font only from the user's observed recordings."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.pens.cu2quPen import Cu2QuPen
    from fontTools.pens.recordingPen import replayRecording
    builder = FontBuilder(1000, isTTF=True)
    order, outlines, advances, cmap = ['.notdef'], {}, {}, {}
    empty = TTGlyphPen(None)
    outlines['.notdef'], advances['.notdef'] = empty.glyph(), (500, 0)
    for char, record in records.items():
        name = f'u{ord(char):06X}'
        pen = TTGlyphPen(None)
        replayRecording(record['recording'], Cu2QuPen(pen, max_err=1))
        order.append(name)
        outlines[name] = pen.glyph()
        advances[name] = (max(1, round(record.get('advance_width', 600))), 0)
        cmap[ord(char)] = name
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap(cmap)
    builder.setupGlyf(outlines)
    builder.setupHorizontalMetrics(advances)
    builder.setupHorizontalHeader(ascent=1000, descent=-400)
    builder.setupNameTable({'familyName': 'MiFont local comparison', 'styleName': 'Regular',
                            'uniqueFontIdentifier': 'MiFontLocalComparison', 'fullName': 'MiFont local comparison',
                            'psName': 'MiFontLocalComparison'})
    builder.setupOS2(sTypoAscender=1000, sTypoDescender=-400, usWinAscent=1600, usWinDescent=700)
    builder.setupPost()
    builder.setupMaxp()
    output = BytesIO()
    builder.save(output)
    return output.getvalue()


def _mask(font, char):
    box = font.getbbox(char)
    if not box or box[2] <= box[0] or box[3] <= box[1]:
        return None
    image = Image.new('L', (box[2] - box[0], box[3] - box[1]))
    ImageDraw.Draw(image).text((-box[0], -box[1]), char, font=font, fill=255)
    bbox = image.getbbox()
    if bbox is None:
        return None
    image = image.crop(bbox)
    ratio = image.width / image.height
    image.thumbnail((88, 88), Image.Resampling.LANCZOS)
    result = Image.new('L', (96, 96))
    result.paste(image, ((96 - image.width) // 2, (96 - image.height) // 2))
    return result.point(lambda value: 255 if value >= 128 else 0), ratio


def _match(records, font_data):
    from fontTools.ttLib import TTFont
    observed = ImageFont.truetype(BytesIO(_observed_font(records)), 120)
    reference = ImageFont.truetype(BytesIO(font_data), 120)
    with TTFont(BytesIO(font_data), lazy=True) as font:
        cmap = font.getBestCmap() or {}
    scores, chars = [], []
    for char in records:
        if ord(char) not in cmap:
            continue
        a, b = _mask(observed, char), _mask(reference, char)
        if a is None or b is None:
            continue
        overlap = ImageChops.multiply(a[0], b[0]).histogram()[255]
        union = ImageChops.lighter(a[0], b[0]).histogram()[255]
        if union:
            score = overlap / union * math.exp(-.15 * abs(math.log(a[1] / b[1])))
            scores.append(score)
            chars.append(char)
    return round(sum(scores) / len(scores), 4) if scores else None, ''.join(chars)


def research_sources(glyphs, query='', options=None, progress=None, *, cache_dir=None):
    """Fetch public candidate provenance and rank glyph similarity locally.

    options: candidate_limit (1..8, default8),
    category ('Handwriting'/'Sans Serif'/... optional). No image upload occurs.
    cache_dir is a trusted server-side setting, not a client project option.
    """
    try:
        from .completion import _records
    except ImportError:
        from completion import _records
    options = {} if options is None else options
    if not isinstance(options, dict) or set(options) - {'candidate_limit', 'category'}:
        raise ReferenceError('Unsupported research options.')
    limit = options.get('candidate_limit', 8)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 8:
        raise ReferenceError('Research at most eight candidate families per request.')
    if not isinstance(query, str) or len(query) > 120:
        raise ReferenceError('Use a font-family query of at most 120 characters.')
    category = options.get('category')
    if category is not None and category not in {'Handwriting', 'Sans Serif', 'Serif', 'Display', 'Monospace'}:
        raise ReferenceError('Unsupported font category.')
    records = _records(glyphs)
    observed = {char: record for char, record in sorted(records.items())
                if record.get('origin', 'observed') == 'observed' and char.isalnum() and record['recording']}
    observed = dict(list(observed.items())[:12])
    cache = Path(cache_dir or _default_cache())
    if progress:
        progress({'progress': 5, 'message': 'Reading the official open-font catalogue'})
    catalogue, online = _catalog(cache)
    available = [family for family in catalogue if isinstance(family, dict)
                 and isinstance(family.get('family'), str) and len(family['family']) <= 120
                 and family.get('isOpenSource') is True and isinstance(family.get('subsets'), list)
                 and 'latin' in family['subsets']]
    by_name = {family['family']: family for family in available}
    matching = [family for family in available if query.casefold() in family['family'].casefold()
                and (category is None or family.get('category') == category)] if query.strip() else []
    matching.sort(key=lambda family: (family['family'].casefold() != query.casefold(), family.get('popularity', 99999)))
    inferred_category = None
    if not matching and category is None and query.strip():
        # Style words guide a public catalogue category; they are not a model
        # prediction or a claim that a particular font has been identified.
        words = set(re.findall(r'[a-z]+', query.casefold()))
        for tokens, candidate in (({'sans'}, 'Sans Serif'), ({'mono', 'monospace', 'typewriter'}, 'Monospace'),
                                  ({'script', 'brush', 'cursive', 'handwriting', 'handwritten'}, 'Handwriting'),
                                  ({'serif'}, 'Serif'), ({'display', 'decorative', 'blackletter'}, 'Display')):
            if words & tokens:
                inferred_category = category = candidate
                break
    if not matching:
        seeds = [by_name[name] for name in DEFAULT_FAMILIES if name in by_name and (category is None or by_name[name].get('category') == category)]
        others = sorted((family for family in available if category is not None and family.get('category') == category), key=lambda family: family.get('popularity', 99999))
        matching = list({family['family']: family for family in seeds + others}.values())
    chosen = matching[:limit]
    warnings = []
    if inferred_category:
        warnings.append('No matching public family name was found. The style words guide ' + inferred_category + ' alternatives; they do not identify the original font.')
    if not online:
        warnings.append('The online catalogue was unavailable; this request used its cached public metadata.')
    if query.strip() and not any(query.casefold() in family['family'].casefold() for family in chosen):
        warnings.append('That font name was not found in the open catalogue. Showing open-font alternatives; these do not establish the original font identity.')
    if not observed:
        warnings.append('Add labeled observed letters for local shape comparison. Candidate cards currently show verified public sources only.')
    references = []
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix='mifont-reference') as executor:
        jobs = {executor.submit(_family_source, family, cache): family for family in chosen}
        for index, future in enumerate(as_completed(jobs)):
            family = jobs[future]
            try:
                card, font_data = future.result()
                score, chars = _match(observed, font_data) if observed else (None, '')
                card.update(match_score=score, matched_chars=chars,
                            match_method='local normalized glyph-mask IoU; not identification or reconstruction')
                references.append(card)
            except (ReferenceError, ValueError, KeyError, OSError, AssertionError) as exc:
                warnings.append(f"{family['family']}: reference verification or matching was unavailable.")
            if progress:
                progress({'progress': 20 + round(75 * (index + 1) / max(1, len(chosen))), 'message': 'Comparing licensed reference glyphs locally'})
    references.sort(key=lambda card: (-(card['match_score'] if card['match_score'] is not None else -1), card['family']))
    if any(card.get('cached_source') for card in references):
        warnings.append('Some official sources were unreachable. Those cards use verified cached font bytes and license notices; their local similarity scores remain available.')
    return {'references': references, 'warnings': warnings,
            'provenance': {'catalogue_url': CATALOG_URL, 'catalogue_families': len(catalogue),
                           'online_catalogue': online, 'artwork_uploaded': False,
                           'fonts_installed': False, 'observed_outlines_changed': False,
                           'candidate_category': category, 'style_keyword_category': inferred_category,
                           'reference_font_limit': limit, 'maximum_reference_bytes': MAX_FONT}}


def resolve_reference(reference_id, saved_references, *, cache_dir=None):
    """Resolve a saved research card to verified local font bytes, never a path input.

    The backend calls this with the project's persisted cards and its own cache
    directory. Client supplied paths, URLs or a free-standing card are rejected.
    """
    if not isinstance(reference_id, str) or not re.fullmatch(r'[a-z0-9]{1,100}-[0-9a-f]{16}', reference_id):
        raise ReferenceError('Choose a verified reference from this project.')
    if not isinstance(saved_references, list) or len(saved_references) > 64:
        raise ReferenceError('Invalid saved project references.')
    found = [card for card in saved_references if isinstance(card, dict) and card.get('id') == reference_id]
    if len(found) != 1:
        raise ReferenceError('Research and select that reference in this project first.')
    card = found[0]
    slug, prefix = reference_id.rsplit('-', 1)
    cache = Path(cache_dir or _default_cache()).resolve()
    folder = (cache / slug).resolve()
    if folder.parent != cache:
        raise ReferenceError('The reference cache path is outside its owned directory.')
    index_file, font_file, license_file = folder / 'reference.json', folder / 'reference.font', folder / 'OFL.txt'
    for path, maximum in ((index_file, 256 * 1024), (font_file, MAX_FONT), (license_file, 128 * 1024)):
        if path.resolve().parent != folder or not path.is_file() or path.stat().st_size > maximum:
            raise ReferenceError('The cached reference is missing or invalid; research it again.')
    try:
        cached = json.loads(index_file.read_bytes())
        fields = ('id', 'family', 'sha256', 'size', 'license', 'license_text', 'license_sha256',
                  'source_url', 'license_url', 'download_url')
        if any(card.get(key) != cached.get(key) for key in fields):
            raise ReferenceError('The saved reference no longer matches its verified cache.')
        digest = card['sha256']
        if (not re.fullmatch('[0-9a-f]{64}', digest) or not digest.startswith(prefix)
                or card['license'] != 'OFL-1.1'
                or card['license_url'] != SOURCE_ROOT + slug + '/OFL.txt'
                or card['source_url'] != 'https://github.com/google/fonts/tree/main/ofl/' + slug
                or not card['download_url'].startswith(SOURCE_ROOT + slug + '/')):
            raise ReferenceError('The saved reference has unsupported provenance.')
        _allowed_url(card['download_url'])
        data, license_data = font_file.read_bytes(), license_file.read_bytes()
        if (len(data) != card['size'] or hashlib.sha256(data).hexdigest() != digest
                or hashlib.sha256(license_data).hexdigest() != card['license_sha256']
                or license_data.decode('utf-8') != card['license_text']
                or b'SIL OPEN FONT LICENSE Version 1.1' not in license_data):
            raise ReferenceError('The cached reference changed; research and select it again.')
    except (KeyError, TypeError, ValueError, OSError) as exc:
        if isinstance(exc, ReferenceError):
            raise
        raise ReferenceError('The cached reference is invalid; research and select it again.') from exc
    return {**{key: card[key] for key in fields}, 'font_path': str(font_file)}
