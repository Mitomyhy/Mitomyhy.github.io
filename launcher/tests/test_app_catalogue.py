import base64
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app_catalogue as catalogue
import backend
import mi_font_app
import vector_app


class Response(io.BytesIO):
    def geturl(self):
        return catalogue.CATALOGUE_URL


class AppCatalogueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = (Path(__file__).parent / 'fixtures/public-apps-catalogue.json').read_bytes()
        cls.envelope = json.loads(cls.raw)

    def test_real_signature_projects_both_apps_and_rejects_tampering(self):
        result = catalogue.verify_catalogue(backend, self.envelope)
        self.assertEqual(set(result['apps']), {'mifont', 'vector-migic'})
        self.assertEqual(result['apps']['mifont']['version'], '1.0.2')
        changed = copy.deepcopy(self.envelope)
        payload = json.loads(base64.b64decode(changed['payload']))
        payload['apps']['mifont']['archive']['sha256'] = 'a' * 64
        changed['payload'] = base64.b64encode(json.dumps(payload).encode()).decode()
        with self.assertRaises(backend.LauncherError):
            catalogue.verify_catalogue(backend, changed)
        changed = dict(self.envelope, key_id='server-supplied-key')
        with self.assertRaises(backend.LauncherError):
            catalogue.verify_catalogue(backend, changed)

    def test_network_result_and_cache_both_require_signature(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parents[2] / 'build') as folder:
            root = Path(folder)
            opener = mock.Mock()
            opener.open.return_value = Response(self.raw)
            with mock.patch.object(backend, 'launcher_data_root', return_value=root), \
                    mock.patch.object(catalogue.urllib.request, 'build_opener', return_value=opener):
                result = catalogue.probe_app_catalogue(backend)
                self.assertEqual(catalogue.load_cached_catalogue(backend), result)
                self.assertEqual(opener.open.call_args.args[0].full_url, catalogue.CATALOGUE_URL)
                path = root / 'apps-catalogue-signed.json'
                value = json.loads(path.read_bytes())
                value['signature'] = base64.b64encode(b'\x00' * 512).decode()
                path.write_text(json.dumps(value))
                with self.assertRaises(backend.LauncherError):
                    catalogue.load_cached_catalogue(backend)

    def test_new_app_version_does_not_require_new_launcher_descriptor(self):
        for module in (mi_font_app, vector_app):
            with self.subTest(app=module.APP_ID), \
                    mock.patch.object(module, '_online_release', None), \
                    mock.patch.object(module, '_online_history', ()):
                bundled = module.load_release()
                updated = copy.deepcopy(bundled)
                updated['version'] = '9.0.0'
                updated['manifest_sha256'] = 'a' * 64
                module.use_catalogue(updated, [bundled])
                self.assertEqual(module.load_release()['version'], '9.0.0')
                self.assertEqual(module._online_history[0]['version'], bundled['version'])
                updated['archive']['size'] = 1
                self.assertNotEqual(module.load_release()['archive']['size'], 1)
                with self.assertRaises(RuntimeError):
                    module.use_catalogue(bundled)

    def test_unsigned_and_oversized_network_responses_preserve_cache(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parents[2] / 'build') as folder:
            root = Path(folder)
            cache = root / 'apps-catalogue-signed.json'
            cache.write_bytes(self.raw)
            for raw in (b'{}', b'x' * (catalogue.LIMIT + 1)):
                opener = mock.Mock()
                opener.open.return_value = Response(raw)
                with mock.patch.object(backend, 'launcher_data_root', return_value=root), \
                        mock.patch.object(catalogue.urllib.request, 'build_opener', return_value=opener):
                    with self.assertRaises(backend.LauncherError):
                        catalogue.probe_app_catalogue(backend)
                self.assertEqual(cache.read_bytes(), self.raw)


if __name__ == '__main__':
    unittest.main()
