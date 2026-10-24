"""Public research uses bounded GETs; observed outlines never leave the machine."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mifont import references
from test_mifont_completion import reference_fixture


class ReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_root = Path(__file__).resolve().parents[2] / '.work/mifont-tests'
        cls.temp_root.mkdir(parents=True, exist_ok=True)

    def fixture(self, folder):
        records, _ = reference_fixture(folder)
        font_data = (folder / 'seed.ttf').read_bytes()
        license_data = b'Synthetic test fixture only. SIL OPEN FONT LICENSE Version 1.1\n'
        metadata = b'name: "Synthetic QA"\nlicense: "OFL"\nfonts {\n  style: "normal"\n  weight: 400\n  filename: "SyntheticQA.ttf"\n}\n'
        family = {'family': 'Synthetic QA', 'category': 'Handwriting', 'isOpenSource': True,
                  'subsets': ['latin'], 'popularity': 1}
        responses = {references.CATALOG_URL: json.dumps({'familyMetadataList': [family]}).encode(),
                     references.SOURCE_ROOT + 'syntheticqa/METADATA.pb': metadata,
                     references.SOURCE_ROOT + 'syntheticqa/OFL.txt': license_data,
                     references.SOURCE_ROOT + 'syntheticqa/SyntheticQA.ttf': font_data}
        calls = []

        def fetch(url, maximum, timeout=10):
            references._allowed_url(url)
            calls.append(url)
            data = responses[url]
            if len(data) > maximum:
                raise references.ReferenceError('Too large')
            return data
        return records, family, responses, calls, fetch

    def test_allowlist_refuses_nonofficial_or_insecure_endpoints(self):
        for url in ('http://fonts.google.com/metadata/fonts', 'https://example.com/a',
                    'https://user@raw.githubusercontent.com/google/fonts/main/ofl/a/a.ttf',
                    'https://fonts.google.com:444/metadata/fonts', 'https://fonts.google.com/metadata/fonts?art=1',
                    'https://raw.githubusercontent.com/other/fonts/main/ofl/a.ttf'):
            with self.assertRaises(references.ReferenceError):
                references._allowed_url(url)
        self.assertEqual(references._allowed_url(references.CATALOG_URL), references.CATALOG_URL)

    def test_blank_query_spans_broad_categories_and_style_words_are_only_guidance(self):
        self.assertEqual(set(references.DEFAULT_FAMILIES), {'Lora', 'Roboto', 'Roboto Mono', 'Oswald', 'Kalam', 'Dancing Script'})
        with tempfile.TemporaryDirectory(dir=self.temp_root) as tmp:
            folder = Path(tmp); records, family, responses, _, fetch = self.fixture(folder)
            other = {**family, 'family': 'Other Serif', 'category': 'Serif', 'popularity': 2}
            responses[references.CATALOG_URL] = json.dumps({'familyMetadataList': [family, other]}).encode()
            with patch.object(references, '_fetch', fetch), patch.object(references, 'DEFAULT_FAMILIES', ('Synthetic QA',)):
                result = references.research_sources(records, query='brush script', cache_dir=folder / 'cache')
            self.assertEqual([card['family'] for card in result['references']], ['Synthetic QA'])
            self.assertEqual(result['provenance']['style_keyword_category'], 'Handwriting')
            self.assertEqual(result['provenance']['reference_font_limit'], 8)
            self.assertTrue(any('do not identify' in message for message in result['warnings']))

    def test_actual_local_raster_match_and_project_bound_reference_resolution(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root) as tmp:
            folder = Path(tmp); cache = folder / 'cache'
            records, _, _, calls, fetch = self.fixture(folder)
            observed = [record for record in records if record['char'] in 'Axil']
            before = deepcopy(observed)
            with patch.object(references, '_fetch', fetch), patch.object(references, 'DEFAULT_FAMILIES', ('Synthetic QA',)):
                report = references.research_sources(observed, options={'candidate_limit': 1}, cache_dir=cache)
            self.assertEqual(observed, before)
            self.assertEqual(len(report['references']), 1)
            card = report['references'][0]
            self.assertGreater(card['match_score'], .97)
            self.assertEqual(set(card['matched_chars']), set('Axil'))
            self.assertEqual(card['license'], 'OFL-1.1')
            self.assertFalse(report['provenance']['artwork_uploaded'])
            self.assertEqual(len(calls), 4)
            self.assertTrue(all(url.startswith(('https://fonts.google.com/', references.SOURCE_ROOT)) for url in calls))
            trusted = references.resolve_reference(card['id'], report['references'], cache_dir=cache)
            self.assertEqual(hashlib.sha256(Path(trusted['font_path']).read_bytes()).hexdigest(), card['sha256'])
            with self.assertRaises(references.ReferenceError):
                references.resolve_reference(card['id'], [], cache_dir=cache)
            modified = deepcopy(card); modified['family'] = 'Spoofed'
            with self.assertRaises(references.ReferenceError):
                references.resolve_reference(card['id'], [modified], cache_dir=cache)
            Path(trusted['font_path']).write_bytes(b'changed cached data')
            with self.assertRaises(references.ReferenceError):
                references.resolve_reference(card['id'], [card], cache_dir=cache)

    def test_unverified_or_nonofl_sources_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root) as tmp:
            folder = Path(tmp)
            _, family, responses, _, fetch = self.fixture(folder)
            for mutation in ('license', 'metadata', 'filename'):
                changed = dict(responses)
                if mutation == 'license':
                    changed[references.SOURCE_ROOT + 'syntheticqa/OFL.txt'] = b'Personal use only'
                elif mutation == 'metadata':
                    changed[references.SOURCE_ROOT + 'syntheticqa/METADATA.pb'] = b'name: "Synthetic QA"\nlicense: "APACHE2"\n'
                else:
                    changed[references.SOURCE_ROOT + 'syntheticqa/METADATA.pb'] = responses[references.SOURCE_ROOT + 'syntheticqa/METADATA.pb'].replace(b'SyntheticQA.ttf', b'../outside.ttf')
                with patch.object(references, '_fetch', lambda url, maximum, timeout=10: changed[url]):
                    with self.assertRaises(references.ReferenceError):
                        references._family_source(family, folder / mutation)

    def test_cache_tampering_traversal_and_limits_are_rejected(self):
        for value in ('../../font', 'a-' + 'f' * 15, 'a-' + 'x' * 16):
            with self.assertRaises(references.ReferenceError):
                references.resolve_reference(value, [], cache_dir=self.temp_root)
        for options in ({'candidate_limit': 9}, {'candidate_limit': True}, {'font_path': 'private'},
                        {'category': 'Paid fonts'}):
            with self.assertRaises(references.ReferenceError):
                references.research_sources([], options=options, cache_dir=self.temp_root)

    def test_cached_catalogue_is_explicit_and_no_paid_lookup_occurs(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root) as tmp:
            folder = Path(tmp)
            (folder / 'google-fonts-catalog.json').write_text('{"familyMetadataList":[]}', encoding='utf-8')
            with patch.object(references, '_fetch', side_effect=references.ReferenceError('offline')):
                report = references.research_sources([], query='private source title', cache_dir=folder)
            self.assertFalse(report['provenance']['online_catalogue'])
            self.assertEqual(report['references'], [])
            self.assertIn('cached public metadata', report['warnings'][0])

    def test_offline_research_rechecks_cached_source_and_keeps_local_scores(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root) as tmp:
            folder = Path(tmp); cache = folder / 'cache'
            records, _, _, _, fetch = self.fixture(folder)
            with patch.object(references, '_fetch', fetch), patch.object(references, 'DEFAULT_FAMILIES', ('Synthetic QA',)):
                online = references.research_sources(records, cache_dir=cache)
            with patch.object(references, '_fetch', side_effect=references.ReferenceError('offline')), patch.object(references, 'DEFAULT_FAMILIES', ('Synthetic QA',)):
                offline = references.research_sources(records, cache_dir=cache)
            self.assertEqual(offline['references'][0]['id'], online['references'][0]['id'])
            self.assertEqual(offline['references'][0]['match_score'], online['references'][0]['match_score'])
            self.assertTrue(offline['references'][0]['cached_source'])
            self.assertFalse(offline['provenance']['online_catalogue'])
            self.assertTrue(any('verified cached font bytes' in text for text in offline['warnings']))
            font_path = next(cache.glob('*/reference.font')); font_path.write_bytes(b'tampered')
            with patch.object(references, '_fetch', side_effect=references.ReferenceError('offline')), patch.object(references, 'DEFAULT_FAMILIES', ('Synthetic QA',)):
                rejected = references.research_sources(records, cache_dir=cache)
            self.assertEqual(rejected['references'], [])


if __name__ == '__main__':
    unittest.main()
