"""AI geometry/transaction tests and the authenticated export/reset HTTP contract."""
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest
import zipfile

from vector_migic import engine, main


ART = b'''<svg xmlns="http://www.w3.org/2000/svg" width="320" height="240" viewBox="0 0 640 480">
<g transform="translate(30 20)">
<path fill="#1234ff" fill-rule="evenodd" d="M10 10H310V310H10Z M80 80H240V240H80Z"/>
<path fill="#e02030" d="M330 10 C500 30 380 220 520 250 L330 250 Z"/>
</g></svg>'''


def artwork_records(data):
    return data.decode('ascii').split('%AI5_BeginLayer\n', 1)[1].split('\nLB\n', 1)[0]


class AIExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'art.svg'
        self.source.write_bytes(ART)

    def tearDown(self):
        self.temp.cleanup()

    def test_genuine_illustrator_records_preserve_canvas_hole_and_bezier(self):
        output = self.root / 'editable.ai'
        result = engine.export_vector(self.source, output)
        data = output.read_bytes()
        self.assertTrue(data.startswith(b'%!PS-Adobe-3.0\n'))
        self.assertNotIn(b'EPSF', data[:80])
        self.assertNotIn(b'%PDF-', data)
        self.assertIn(b'%AI5_FileFormat 4.0\n', data)
        self.assertIn(b'%AI5_ArtSize: 240 180\n', data)
        self.assertIn(b'%%BoundingBox: 0 0 240 180\n', data)
        self.assertIn(b'0 180 translate\n%%EndSetup', data)
        body = artwork_records(data)
        self.assertIn('1 XR\n*u\n', body)
        self.assertEqual(body.splitlines().count('f'), 3)
        self.assertIn('\n*U\n', body)
        # Full SVG viewBox + group transforms are applied; AI's artboard is
        # downward-positive in display space [0, 0, width, -height].
        self.assertIn('15 -11.25 m\n127.5 -11.25 L\n127.5 -123.75 L\n15 -123.75 L\n15 -11.25 L\nf', body)
        self.assertIn('41.25 -37.5 m\n101.25 -37.5 L', body)
        self.assertIn('198.75 -18.75 153.75 -90 206.25 -101.25 C', body)
        for line in body.splitlines():
            if line.endswith(' Xa'):
                values = [float(value) for value in line.split()[:-1]]
                self.assertEqual(len(values), 7)
                self.assertTrue(all(0 <= value <= 1 for value in values))
        self.assertNotRegex(body, r'(?i)(image|raster|font|https?:|data:)')
        self.assertEqual(self.source.read_bytes(), ART)
        self.assertEqual(result['output_format'], 'ai')
        self.assertEqual(result['path_count'], 2)

    def test_rgb_fills_remain_rgb_and_alpha_background_is_absent(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">' + b''.join(
            f'<path fill="{color}" d="M{x} 10h10v10h-10Z"/>'.encode()
            for color, x in [('#ff0000', 10), ('#00ff00', 30), ('#0000ff', 50)]) + b'</svg>'
        data = engine._export_data(svg, 'ai')
        fills = [list(map(float, line.split()[:-1]))[-3:] for line in artwork_records(data).splitlines() if line.endswith(' Xa')]
        self.assertEqual(fills, [[1, 0, 0], [0, 1, 0], [0, 0, 1]])
        self.assertEqual(len(re.findall(r' m\n', artwork_records(data))), 3)
        self.assertIn(b'%AI5_ArtSize: 75 75', data)

    def test_quadratic_arcs_relative_commands_and_implicit_fill_closure(self):
        svg = b'''<svg xmlns="http://www.w3.org/2000/svg" width="200" height="120">
        <g transform="translate(10 5) scale(1.5)"><path fill="#00ff00"
        d="m10 10 q20 -10 40 0 a20 15 0 0 1 40 20 l-20 20"/></g></svg>'''
        body = artwork_records(engine._export_data(svg, 'ai'))
        self.assertIn('18.75 -15 m', body)
        self.assertGreaterEqual(body.count(' C\n'), 2)
        # SVG fills implicitly close an open contour; svglib preserves that
        # visible fill as an explicit closing edge in the editable AI path.
        self.assertTrue(body.endswith('\nf'))
        self.assertIn('18.75 -15 L\nf', body)
        self.assertNotRegex(body, r'(?i)\b(nan|inf)\b')

    def test_unsupported_features_fail_without_replacing_existing_output(self):
        output = self.root / 'keep.ai'
        for attr in ['fill-opacity=".5"', 'opacity=".5"', 'style="opacity: .5"', 'stroke="red" stroke-width="2"']:
            with self.subTest(attr=attr):
                self.source.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><path fill="blue" ' + attr + ' d="M0 0H50V50H0Z"/></svg>')
                output.write_bytes(b'previous reviewed output')
                with self.assertRaises(engine.VectorizationError):
                    engine.export_vector(self.source, output)
                self.assertEqual(output.read_bytes(), b'previous reviewed output')

    def test_extension_must_match_and_no_embedded_content(self):
        with self.assertRaises(engine.VectorizationError):
            engine.export_vector(self.source, self.root / 'wrong.pdf', 'ai')
        malicious = ART.replace(b'<g ', b'<image href="file:///private.png"/><g ', 1)
        with self.assertRaises(engine.VectorizationError):
            engine._export_data(malicious, 'ai')


class ExportHTTPTests(unittest.TestCase):
    def setUp(self):
        self.session = main.Session()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), main.handler_for(self.session))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.ids = ['a' * 32, 'b' * 32]
        for identity in self.ids:
            folder = self.session.root / identity
            folder.mkdir()
            output = folder / 'result.svg'
            output.write_bytes(ART)
            self.session.jobs[identity] = {'id': identity, 'name': 'duplicate.png', 'status': 'done', 'output': output, 'cancel': threading.Event()}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.session.close()

    def request(self, path, value=None):
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        headers = {'X-Migic-Token': self.session.token}
        body = None if value is None else json.dumps(value)
        connection.request('GET' if value is None else 'POST', path, body, headers)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_ai_download_and_batch_have_correct_format_mime_and_unique_names(self):
        status, headers, data = self.request('/api/export', {'job_id': self.ids[0], 'format': 'ai'})
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'application/postscript')
        self.assertIn('duplicate.ai', headers['Content-Disposition'])
        self.assertIn(b'%AI5_FileFormat 4.0', data)
        status, headers, data = self.request('/api/export-batch', {'job_ids': self.ids, 'format': 'ai'})
        self.assertEqual(status, 200)
        self.assertIn('VectorMigic-ai.zip', headers['Content-Disposition'])
        with zipfile.ZipFile(BytesIO(data)) as archive:
            names = archive.namelist()
            self.assertEqual(len(names), 2)
            self.assertEqual(len(set(names)), 2)
            self.assertTrue(all(name.endswith('.ai') and '/' not in name for name in names))
            for name in names:
                self.assertIn(b'%AI5_FileFormat 4.0', archive.read(name))

    def test_config_returns_complete_presets_for_full_reset(self):
        status, _, body = self.request('/api/config')
        self.assertEqual(status, 200)
        config = json.loads(body)
        expected_logo = dict(preset='logo', color_mode='color', colors=0, smoothing=.5,
                             speckle=1, transparent=True, mode='spline', max_dimension=2048,
                             corner_threshold=60, color_precision=8, alpha_threshold=128, bw_threshold=128)
        self.assertEqual(config['presets']['logo'], expected_logo)
        for name in engine.PRESETS:
            self.assertEqual(config['presets'][name], engine.normalize_options({'preset': name}))
            self.assertEqual(set(config['presets'][name]), set(expected_logo))


if __name__ == '__main__':
    unittest.main()
