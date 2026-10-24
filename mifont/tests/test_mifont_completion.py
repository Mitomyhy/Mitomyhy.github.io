"""CPU completion never substitutes observed outlines or claims neural inference."""
from copy import deepcopy
from io import BytesIO
import hashlib
from pathlib import Path
import tempfile
import unittest

from PIL import Image, ImageDraw, ImageFont
from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import RecordingPen, replayRecording
from fontTools.pens.transformPen import TransformPen

from mifont import completion
from mifont.font_export import build_font, normalize_glyph


def rectangle(char='I', width=80, height=700):
    return normalize_glyph({'char': char, 'origin': 'observed', 'advance_width': width + 90,
                            'recording': [['moveTo', [[30, 0]]], ['lineTo', [[30, height]]],
                                          ['lineTo', [[30 + width, height]]], ['lineTo', [[30 + width, 0]]],
                                          ['closePath', []]], 'source': {'reviewed': True}})


def reference_fixture(folder):
    """Own synthetic outlines, test license marker; no third-party font fixture."""
    records = [rectangle('A', 440), rectangle('x', 270, 500), rectangle('i', 60, 500),
               rectangle('l', 60, 700), rectangle('g', 300, 650), rectangle('7', 350),
               normalize_glyph({'char': ' ', 'recording': [], 'advance_width': 240, 'origin': 'observed'})]
    build_font(records, {'family_name': 'MiFont Synthetic QA'}, folder / 'seed.ttf', folder / 'seed.otf')
    data = (folder / 'seed.ttf').read_bytes()
    license_text = 'Synthetic test fixture only. SIL OPEN FONT LICENSE Version 1.1\n'
    return records, {'id': 'synthetic-' + hashlib.sha256(data).hexdigest()[:16],
                     'font_path': str(folder / 'seed.ttf'), 'family': 'Synthetic QA',
                     'sha256': hashlib.sha256(data).hexdigest(), 'license': 'OFL-1.1',
                     'license_text': license_text,
                     'license_sha256': hashlib.sha256(license_text.encode()).hexdigest(),
                     'source_url': 'https://github.com/google/fonts/tree/main/ofl/synthetic',
                     'license_url': 'https://raw.githubusercontent.com/google/fonts/main/ofl/synthetic/OFL.txt'}


class CompletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_root = Path(__file__).resolve().parents[2] / '.work/mifont-tests'
        cls.temp_root.mkdir(parents=True, exist_ok=True)

    def test_default_leaves_missing_without_any_fallback(self):
        observed = rectangle()
        before = deepcopy(observed)
        result = completion.complete_glyphs([observed])
        self.assertEqual(result['glyphs'], [observed])
        self.assertEqual(observed, before)
        self.assertIn('A', result['missing_chars'])
        self.assertFalse(result['generated_chars'])
        self.assertFalse(result['provenance']['neural'])

    def test_proposals_require_consent_and_model_never_falls_back(self):
        with self.assertRaises(completion.CompletionError):
            completion.complete_glyphs([rectangle()], {'mode': 'proposal'})
        with self.assertRaises(completion.CompletionUnavailable):
            completion.complete_glyphs([rectangle()], {'mode': 'model', 'allow_approximate': True})
        with self.assertRaises(completion.CompletionError):
            completion.complete_glyphs([], {'mode': 'proposal', 'allow_approximate': True})

    def test_ascii95_is_deterministic_distinct_and_preserves_input(self):
        observed = rectangle()
        options = {'mode': 'proposal', 'allow_approximate': True}
        events = []
        result = completion.complete_glyphs([observed], options, events.append)
        other = completion.complete_glyphs([observed], options)
        self.assertEqual(result, other)
        self.assertEqual(len(result['glyphs']), 95)
        self.assertEqual(result['missing_chars'], '')
        self.assertEqual(next(r for r in result['glyphs'] if r['char'] == 'I'), observed)
        mapping = {r['char']: r for r in result['glyphs']}
        self.assertNotEqual(mapping['B']['recording'], mapping['8']['recording'])
        self.assertNotEqual(mapping['A']['recording'], mapping['V']['recording'])
        self.assertTrue(all(row['generation']['needs_review'] for row in result['glyphs'] if row['origin'] == 'generated'))
        self.assertEqual(events[-1]['progress'], 100)
        self.assertEqual(mapping[' ']['recording'], [])

    def test_style_affects_generated_outlines_without_changing_observation(self):
        options = {'mode': 'proposal', 'allow_approximate': True, 'target_chars': 'IO'}
        narrow = completion.complete_glyphs([rectangle(width=45)], options)
        wide = completion.complete_glyphs([rectangle(width=140, height=850)], options)
        self.assertNotEqual(narrow['glyphs'][1]['recording'], wide['glyphs'][1]['recording'])
        self.assertGreater(wide['provenance']['style_parameters']['stroke'], narrow['provenance']['style_parameters']['stroke'])

    def test_true_font_exports_keep_circle_counter_and_punctuation(self):
        result = completion.complete_glyphs([rectangle()], {'mode': 'proposal', 'allow_approximate': True})
        with tempfile.TemporaryDirectory(dir=self.temp_root) as tmp:
            folder = Path(tmp)
            stats = build_font(result['glyphs'], {'family_name': 'MiFont QA'}, folder / 'qa.ttf', folder / 'qa.otf')
            self.assertEqual(stats['glyph_count'], 95)
            for suffix in ('ttf', 'otf'):
                with TTFont(folder / ('qa.' + suffix)) as font:
                    self.assertEqual(len(font.getBestCmap()), 95)
                    self.assertIn('glyf' if suffix == 'ttf' else 'CFF ', font)
                font = ImageFont.truetype(str(folder / ('qa.' + suffix)), 180)
                box = font.getbbox('O')
                image = Image.new('L', (box[2] - box[0] + 2, box[3] - box[1] + 2))
                ImageDraw.Draw(image).text((1 - box[0], 1 - box[1]), 'O', font=font, fill=255)
                self.assertEqual(image.getpixel((image.width // 2, image.height // 2)), 0)
                self.assertGreater(sum(image.histogram()[1:]), 200)
                self.assertGreater(font.getmask('!').getbbox()[3], 0)

    def test_validation_and_unavailable_unicode_are_honest(self):
        for options in ({'mode': 'wrong'}, {'target_chars': 'AA'}, {'metrics': {}, 'path': 'outside'},
                        {'mode': 'proposal', 'allow_approximate': True, 'metrics': {'cap_height': float('nan')}}):
            with self.assertRaises(completion.CompletionError):
                completion.complete_glyphs([rectangle()], options)
        result = completion.complete_glyphs([rectangle()], {'mode': 'proposal', 'allow_approximate': True, 'target_chars': 'IΩ'})
        self.assertEqual(result['missing_chars'], 'Ω')
        invalid = rectangle(); invalid['recording'][0][1][0][0] = float('nan')
        with self.assertRaises(completion.CompletionError):
            completion.complete_glyphs([invalid])
        invalid = rectangle(); invalid['recording'].pop()
        with self.assertRaises(completion.CompletionError):
            completion.complete_glyphs([invalid])

    def test_selected_reference_fits_source_shapes_and_retains_full_provenance(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root) as tmp:
            source, reference = reference_fixture(Path(tmp))
            observed = deepcopy(source[0])
            pen = RecordingPen()
            replayRecording(observed['recording'], TransformPen(pen, (1.2, 0, 0, .9, 0, 0)))
            observed.update(recording=pen.value, advance_width=observed['advance_width'] * 1.2)
            observed = normalize_glyph(observed)
            options = {'mode': 'reference', 'allow_approximate': True, 'accept_reference_license': True,
                       'reference_id': reference['id'], 'target_chars': 'Ag7 Ω'}
            result = completion.complete_glyphs([observed], options, reference=reference)
            self.assertEqual(next(r for r in result['glyphs'] if r['char'] == 'A'), observed)
            g = next(r for r in result['glyphs'] if r['char'] == 'g')
            self.assertAlmostEqual(g['bbox'][2] - g['bbox'][0], 300 * 1.2)
            self.assertAlmostEqual(g['bbox'][3] - g['bbox'][1], 650 * .9)
            self.assertEqual(g['generation']['source_family'], 'Synthetic QA')
            self.assertEqual(g['provenance']['reference_id'], reference['id'])
            self.assertTrue(g['approximate'])
            self.assertEqual(result['missing_chars'], 'Ω')
            self.assertEqual(result['provenance']['license_obligations']['license_text'], reference['license_text'])
            self.assertTrue(result['provenance']['requires_renamed_family'])
            self.assertEqual(result['font_metadata']['family_name'], 'MiFont Derived')

    def test_reference_consent_resolver_and_hash_are_required(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root) as tmp:
            _, reference = reference_fixture(Path(tmp))
            options = {'mode': 'reference', 'allow_approximate': True, 'reference_id': reference['id']}
            with self.assertRaises(completion.CompletionError):
                completion.complete_glyphs([rectangle('A')], options, reference=reference)
            options['accept_reference_license'] = True
            with self.assertRaises(completion.CompletionError):
                completion.complete_glyphs([rectangle('A')], options)
            Path(reference['font_path']).write_bytes(b'changed')
            with self.assertRaises(completion.CompletionError):
                completion.complete_glyphs([rectangle('A')], options, reference=reference)

    def test_reference_can_explicitly_rebuild_prior_proposals_without_replacing_observed(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root) as tmp:
            source, reference = reference_fixture(Path(tmp))
            observed = source[0]
            grammar = completion.complete_glyphs([observed], {'mode': 'proposal', 'allow_approximate': True,
                                                              'target_chars': 'Ag7'})
            options = {'mode': 'reference', 'allow_approximate': True, 'accept_reference_license': True,
                       'reference_id': reference['id'], 'target_chars': 'Ag7'}
            unchanged = completion.complete_glyphs(grammar['glyphs'], options, reference=reference)
            self.assertEqual(unchanged['glyphs'], grammar['glyphs'])
            options['replace_generated'] = True
            rebuilt = completion.complete_glyphs(grammar['glyphs'], options, reference=reference)
            self.assertEqual(len(rebuilt['glyphs']), 3)
            mapping = {row['char']: row for row in rebuilt['glyphs']}
            self.assertEqual(mapping['A'], observed)
            self.assertNotEqual(mapping['g']['recording'], next(row for row in grammar['glyphs'] if row['char'] == 'g')['recording'])
            self.assertEqual(mapping['g']['provenance']['reference_id'], reference['id'])
            again = completion.complete_glyphs(rebuilt['glyphs'], options, reference=reference)
            self.assertEqual(rebuilt['glyphs'], again['glyphs'])


if __name__ == '__main__':
    unittest.main()
