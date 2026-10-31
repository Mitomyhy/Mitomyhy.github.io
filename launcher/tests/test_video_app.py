"""Exercise signed-package boundaries without launching any executable."""
from pathlib import Path
import base64
import copy
import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app_catalogue
import backend
import self_update
import video_app
from mitom_ui import catalogue_selection, MITOMLauncherUI


def sha(data):
    return hashlib.sha256(data).hexdigest()


class VideoFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='product-video-launcher-', dir=Path(__file__).parents[2] / 'build')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bundle = self.root / 'bundle'
        self.bundle.mkdir()
        self.machine = self.root / 'machine'
        environment = mock.patch.dict(os.environ, {'APPDATA': str(self.machine / 'Roaming')})
        environment.start()
        self.addCleanup(environment.stop)
        self.files = {name: ('fixture:' + name).encode() for name in video_app.REQUIRED_RUNTIME_FILES}
        self.files['cep/com.mitom.productvideo/CSXS/manifest.xml'] = (
            b'<ExtensionManifest ExtensionBundleId="com.mitom.productvideo"><ExtensionList>'
            b'<Extension Id="com.mitom.productvideo.panel"/></ExtensionList></ExtensionManifest>')
        self.files['python/python312.dll'] = b'fixture dependency'
        self.archive = self.bundle / 'video.zip'
        self.release = {'schema': 1, 'app_id': 'product-video', 'version': '1.0.0',
                        'entry': 'python/python.exe', 'manifest_sha256': '0' * 64,
                        'archive': {'bundled': 'video.zip', 'size': 1, 'sha256': '0' * 64}}
        self.write_archive()

    def write_archive(self, *, extra=None, pin_manifest=True):
        manifest = {key: self.release[key] for key in ('schema', 'app_id', 'version', 'entry')}
        manifest['files'] = [{'path': name, 'size': len(content), 'sha256': sha(content)}
                             for name, content in sorted(self.files.items())]
        raw = json.dumps(manifest).encode()
        if pin_manifest:
            self.release['manifest_sha256'] = sha(raw)
        with zipfile.ZipFile(self.archive, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('product-video-app.json', raw)
            for name, content in self.files.items():
                archive.writestr(name, content)
            if extra:
                archive.writestr(*extra)
        self.release['archive'].update(size=self.archive.stat().st_size, sha256=sha(self.archive.read_bytes()))

    def install(self):
        return video_app.install_app(self.machine, self.release, bundle_root=self.bundle)

    def multipart(self):
        release = copy.deepcopy(self.release)
        data = self.archive.read_bytes()
        half = len(data) // 2
        chunks = [data[:half], data[half:]]
        url = 'https://mitomyhy.github.io/releases/product-video/1.0.0/video.zip'
        release['archive'].pop('bundled')
        release['archive']['url'] = url
        release['archive']['parts'] = [{'url': url + f'.part{index:03d}', 'size': len(chunk), 'sha256': sha(chunk)}
                                        for index, chunk in enumerate(chunks, 1)]
        return release, {row['url']: chunk for row, chunk in zip(release['archive']['parts'], chunks)}

    def test_multipart_download_verifies_parts_and_combined_zip_then_reuses_cache(self):
        release, content = self.multipart()
        opener = mock.Mock()
        opener.open.side_effect = lambda request, timeout: io.BytesIO(content[request.full_url])
        progress = []
        with mock.patch.object(video_app.urllib.request, 'build_opener', return_value=opener):
            installed = video_app.install_app(self.machine, release, progress=lambda *values: progress.append(values))
        self.assertEqual(opener.open.call_count, 2)
        self.assertEqual(progress[-1], (release['archive']['size'], release['archive']['size']))
        self.assertEqual((Path(installed['path']) / 'worker/product_server.py').read_bytes(), self.files['worker/product_server.py'])
        with mock.patch.object(video_app.urllib.request, 'build_opener', side_effect=AssertionError('verified cache should be reused')):
            video_app.install_app(self.machine, release)

    def test_bad_part_preserves_installed_app_and_good_part_for_next_retry(self):
        old = Path(self.install()['path'])
        release, content = self.multipart()
        parts = release['archive']['parts']
        opener = mock.Mock()
        opener.open.side_effect = lambda request, timeout: io.BytesIO(content[request.full_url] if request.full_url == parts[0]['url'] else b'X' * parts[1]['size'])
        with mock.patch.object(video_app.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaisesRegex(video_app.VideoAppError, 'checksum'):
                video_app.install_app(self.machine, release)
        self.assertEqual(opener.open.call_count, 2)
        self.assertEqual((old / 'worker/product_server.py').read_bytes(), self.files['worker/product_server.py'])
        self.assertTrue((self.machine / 'Downloads/ProductVideo-1.0.0.part001').is_file())
        opener.reset_mock()
        def remaining_only(request, timeout):
            self.assertEqual(request.full_url, parts[1]['url'])
            return io.BytesIO(content[request.full_url])
        opener.open.side_effect = remaining_only
        with mock.patch.object(video_app.urllib.request, 'build_opener', return_value=opener):
            video_app.install_app(self.machine, release)
        self.assertEqual(opener.open.call_count, 1)

    def test_individually_valid_parts_cannot_bypass_combined_zip_pin(self):
        release, content = self.multipart()
        release['archive']['sha256'] = 'e' * 64
        opener = mock.Mock()
        opener.open.side_effect = lambda request, timeout: io.BytesIO(content[request.full_url])
        with mock.patch.object(video_app.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaisesRegex(video_app.VideoAppError, 'combined archive checksum'):
                video_app.install_app(self.machine, release)
        self.assertFalse((self.machine / 'Apps/ProductVideo/1.0.0').exists())
        self.assertFalse((self.machine / 'Downloads/ProductVideo-1.0.0.zip').exists())

    def test_invalid_parts_are_rejected_before_any_download(self):
        valid, _ = self.multipart()
        for change in ('count', 'total', 'duplicate', 'size', 'foreign', 'encoded-traversal'):
            with self.subTest(change=change):
                release = copy.deepcopy(valid)
                parts = release['archive']['parts']
                if change == 'count': parts.pop()
                elif change == 'total': parts[0]['size'] += 1
                elif change == 'duplicate': parts[1]['url'] = parts[0]['url']
                elif change == 'size': parts[0]['size'] = video_app.MAX_PART_BYTES + 1
                elif change == 'foreign': parts[0]['url'] = 'https://evil.invalid/a'
                else: parts[0]['url'] = 'https://mitomyhy.github.io/releases/product-video/%2e%2e/a'
                with mock.patch.object(video_app.urllib.request, 'build_opener') as network:
                    with self.assertRaises(video_app.VideoAppError):
                        video_app.install_app(self.machine, release)
                    network.assert_not_called()

    def test_short_part_transport_retries_are_bounded_and_leave_no_partial_install(self):
        release, _ = self.multipart()
        opener = mock.Mock()
        opener.open.side_effect = lambda *args, **kwargs: io.BytesIO(b'short')
        with mock.patch.object(video_app.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaises(ConnectionError):
                video_app.install_app(self.machine, release)
        self.assertEqual(opener.open.call_count, 3)
        self.assertEqual(list((self.machine / 'Downloads').iterdir()), [])

    def test_photoshop_addon_changes_only_its_own_folder_and_preserves_previous_extra_files(self):
        cep = self.machine / 'Roaming/Adobe/CEP/extensions'
        main_panel = cep / 'com.uoy.orderpanel/index.html'
        main_panel.parent.mkdir(parents=True)
        main_panel.write_bytes(b'keep R80 and Save Task')
        first = self.install()
        own = cep / 'com.mitom.productvideo'
        self.assertEqual(first['photoshop_extension']['path'], str(own))
        (own / 'user-extra.txt').write_bytes(b'preserve this too')
        repaired = self.install()
        self.assertEqual(main_panel.read_bytes(), b'keep R80 and Save Task')
        backup = Path(repaired['photoshop_extension']['backup'])
        self.assertEqual((backup / 'user-extra.txt').read_bytes(), b'preserve this too')
        self.assertNotIn('extensions', backup.relative_to(self.machine / 'Roaming/Adobe/CEP').parts)
        self.assertFalse((own / 'user-extra.txt').exists())

    def test_photoshop_addon_activation_failure_rolls_back_both_components(self):
        old = Path(self.install()['path'])
        own = self.machine / 'Roaming/Adobe/CEP/extensions/com.mitom.productvideo'
        (own / 'user-extra.txt').write_bytes(b'keep previous addon')
        original = (old / 'worker/product_server.py').read_bytes()
        self.files['worker/product_server.py'] = b'new verified worker'
        self.write_archive()
        real_rename = Path.rename
        def fail_addon(path, destination):
            if path.name.startswith('.product-video-stage-') and Path(destination).name == 'com.mitom.productvideo':
                raise OSError('simulated Photoshop extension activation failure')
            return real_rename(path, destination)
        with mock.patch.object(Path, 'rename', fail_addon):
            with self.assertRaisesRegex(OSError, 'extension activation'):
                self.install()
        self.assertEqual((old / 'worker/product_server.py').read_bytes(), original)
        self.assertEqual((own / 'user-extra.txt').read_bytes(), b'keep previous addon')

    def test_conflicting_photoshop_extension_identity_is_rejected(self):
        self.files['cep/com.mitom.productvideo/CSXS/manifest.xml'] = b'<ExtensionManifest ExtensionBundleId="com.uoy.orderpanel"/>'
        self.write_archive()
        with self.assertRaisesRegex(video_app.VideoAppError, 'identity differs'):
            self.install()
        self.assertFalse((self.machine / 'Apps/ProductVideo/1.0.0').exists())
        self.assertFalse((self.machine / 'Roaming/Adobe/CEP/extensions/com.uoy.orderpanel').exists())

    def test_offline_install_hashes_all_files_and_preserves_panel_and_user_data(self):
        private = self.machine / 'PhotoshopPanel' / 'personal-settings.json'
        private.parent.mkdir(parents=True)
        private.write_bytes(b'private settings')
        with mock.patch('urllib.request.build_opener', side_effect=AssertionError('Network used for bundled package')):
            installed = self.install()
        self.assertEqual(private.read_bytes(), b'private settings')
        state = video_app.installed_state(self.machine, self.release)
        self.assertTrue(state['video_current_verified'])
        self.assertEqual(state['video_path'], installed['path'])

    def test_open_has_fixed_bundled_entry_without_a_shell_or_environment_injection(self):
        target = Path(self.install()['path'])
        with mock.patch.dict(os.environ, {'NODE_OPTIONS': '--require unsafe.js', 'NODE_PATH': 'unsafe',
                                          'PYTHONHOME': 'unsafe', 'PYTHONPATH': 'unsafe'}), \
                mock.patch.object(video_app.subprocess, 'Popen') as launch:
            video_app.open_app(self.machine, self.release)
        command, options = launch.call_args
        self.assertEqual(command[0], [str(target / 'python/python.exe'), '-I', '-B',
            str(target / 'worker/product_server.py'), '--frontend', str(target / 'web'), '--open-browser'])
        self.assertEqual(options['cwd'], str(target))
        self.assertFalse(options.get('shell', False))
        for name in ('NODE_OPTIONS', 'NODE_PATH', 'PYTHONHOME', 'PYTHONPATH'):
            self.assertNotIn(name, options['env'])

    def test_modified_dependency_or_extra_file_disables_open(self):
        target = Path(self.install()['path'])
        for change in ('dependency', 'extra'):
            with self.subTest(change=change):
                dependency = target / 'python/python312.dll'
                dependency.write_bytes(self.files['python/python312.dll'] if change == 'extra' else b'changed')
                if change == 'extra':
                    (target / 'unowned.txt').write_text('keep me', encoding='utf-8')
                with mock.patch.object(video_app.subprocess, 'Popen') as launch:
                    with self.assertRaises(video_app.VideoAppError):
                        video_app.open_app(self.machine, self.release)
                    launch.assert_not_called()
                self.assertFalse(video_app.installed_state(self.machine, self.release)['video_current_verified'])

    def test_catalogue_state_does_not_rehash_runtime_dependencies(self):
        target = Path(self.install()['path'])
        digest = video_app._digest
        hashed = []
        def observed(path):
            hashed.append(Path(path))
            return digest(path)
        with mock.patch.object(video_app, '_digest', side_effect=observed):
            self.assertTrue(video_app.installed_state(self.machine, self.release)['video_installed'])
        self.assertIn(target / 'product-video-app.json', hashed)
        self.assertNotIn(target / 'python/python312.dll', hashed)
        self.assertNotIn(target / 'node/node.exe', hashed)

    def test_each_open_rehashes_dependencies_even_with_restored_metadata_then_repair_clears_failure(self):
        target = Path(self.install()['path'])
        dependency = target / 'python/python312.dll'
        stamp = dependency.stat()
        with mock.patch.object(video_app.subprocess, 'Popen') as launch:
            video_app.open_app(self.machine, self.release)
            dependency.write_bytes(b'X' * stamp.st_size)
            os.utime(dependency, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            with self.assertRaises(video_app.VideoAppError):
                video_app.open_app(self.machine, self.release)
            self.assertEqual(launch.call_count, 1)
        state = video_app.installed_state(self.machine, self.release)
        self.assertTrue(state['video_repair_required'])
        self.assertFalse(state['video_current_verified'])
        self.install()
        self.assertTrue(video_app.installed_state(self.machine, self.release)['video_current_verified'])

    def test_full_inventory_rejects_an_empty_reparse_directory(self):
        target = Path(self.install()['path'])
        linked = target / 'empty-reparse'
        linked.mkdir()
        real_scandir = os.scandir
        @contextlib.contextmanager
        def marked_scandir(path):
            with real_scandir(path) as entries:
                wrapped = []
                for entry in entries:
                    if Path(entry.path) == linked:
                        info = entry.stat(follow_symlinks=False)
                        entry = types.SimpleNamespace(path=entry.path, stat=lambda **kwargs: types.SimpleNamespace(
                            st_mode=info.st_mode, st_file_attributes=0x400))
                    wrapped.append(entry)
                yield iter(wrapped)
        with mock.patch.object(video_app.os, 'scandir', side_effect=marked_scandir):
            with self.assertRaisesRegex(video_app.VideoAppError, 'Linked'):
                video_app.verify_install(target, self.release)

    def test_repair_preserves_modified_old_runtime_as_backup(self):
        target = Path(self.install()['path'])
        (target / 'unowned.txt').write_text('private extra', encoding='utf-8')
        (target / 'worker/product_server.py').write_bytes(b'modified code')
        repaired = self.install()
        old = Path(repaired['backup'])
        self.assertEqual((old / 'unowned.txt').read_text('utf-8'), 'private extra')
        self.assertEqual((old / 'worker/product_server.py').read_bytes(), b'modified code')
        self.assertTrue(video_app.installed_state(self.machine, self.release)['video_current_verified'])

    def test_failed_activation_of_new_folder_restores_old_runtime(self):
        target = Path(self.install()['path'])
        original = (target / 'worker/product_server.py').read_bytes()
        real_rename = Path.rename
        def fail_new_stage(path, destination):
            if path.name.startswith('.product-video-stage-'):
                raise OSError('simulated atomic activation failure')
            return real_rename(path, destination)
        with mock.patch.object(Path, 'rename', fail_new_stage):
            with self.assertRaises(OSError):
                self.install()
        self.assertEqual((target / 'worker/product_server.py').read_bytes(), original)
        self.assertTrue(video_app.installed_state(self.machine, self.release)['video_current_verified'])

    def test_archive_hash_and_manifest_mismatch_preserve_old_install(self):
        target = Path(self.install()['path'])
        original = (target / 'worker/product_server.py').read_bytes()
        self.archive.write_bytes(b'not the pinned archive')
        with self.assertRaises(video_app.VideoAppError):
            self.install()
        self.assertEqual((target / 'worker/product_server.py').read_bytes(), original)
        self.files['worker/product_server.py'] = b'new bytes but old manifest pin'
        self.write_archive(pin_manifest=False)
        with self.assertRaises(video_app.VideoAppError):
            self.install()

    def test_missing_fixed_component_or_changed_entry_is_rejected(self):
        for entry in ('cmd.exe', 'launch.cmd', 'python/other.exe', '../python.exe'):
            with self.subTest(entry=entry):
                release = copy.deepcopy(self.release)
                release['entry'] = entry
                with self.assertRaises(video_app.VideoAppError):
                    video_app.validate_release(release)
        del self.files['node/node.exe']
        self.write_archive()
        with self.assertRaises(video_app.VideoAppError):
            self.install()

    def test_pinned_archives_cannot_add_traversal_or_windows_aliases(self):
        for name in ('../escaped.txt', 'node/NUL.txt', 'node/alias. ', 'node/a:stream', 'NODE/NODE.EXE'):
            with self.subTest(name=name):
                self.write_archive(extra=(name, b'untrusted'))
                with self.assertRaises(video_app.VideoAppError):
                    self.install()
        self.assertFalse((self.root / 'escaped.txt').exists())

    def test_symlink_member_and_foreign_release_urls_are_rejected(self):
        link = zipfile.ZipInfo('node/link')
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        self.write_archive(extra=(link, b'../../elsewhere'))
        with self.assertRaises(video_app.VideoAppError):
            self.install()
        for url in ('http://mitomyhy.github.io/release.zip', 'https://evil.example/app.zip',
                    'https://github.com/Mitomyhy/other-repo/releases/download/v1/app.zip',
                    'https://mitomyhy.github.io/releases/product-video/1.0.0/app.zip?redirect=1'):
            with self.subTest(url=url):
                release = copy.deepcopy(self.release)
                release['archive']['url'] = url
                with self.assertRaises(video_app.VideoAppError):
                    video_app.validate_release(release)

    def test_catalogue_shows_real_video_state_and_preserves_legacy_counts(self):
        self.assertEqual(catalogue_selection({})[0]['all'], 3)
        counts, entries = catalogue_selection({'video_available': True}, query='video')
        self.assertEqual(counts['all'], 4)
        self.assertEqual([item['id'] for item in entries], ['video'])
        ui = object.__new__(MITOMLauncherUI)
        ui.state = {'video_current_verified': False}
        self.assertEqual(ui._video_key(), 'install_video')
        ui.state['video_current_verified'] = True
        self.assertEqual(ui._video_key(), 'open_video')


class CatalogueVideoTests(unittest.TestCase):
    def setUp(self):
        self.original_envelope = json.loads((Path(__file__).parent / 'fixtures/public-apps-catalogue.json').read_text('utf-8-sig'))
        self.payload = json.loads(base64.b64decode(self.original_envelope['payload']))
        self.video = {'schema': 1, 'app_id': 'product-video', 'version': '1.0.0',
            'entry': 'python/python.exe', 'manifest_sha256': 'a' * 64,
            'archive': {'url': 'https://mitomyhy.github.io/releases/product-video/1.0.0/app.zip',
                        'size': 100, 'sha256': 'b' * 64}}
        self.payload['apps']['product-video'] = self.video
        self.payload['history']['product-video'] = []

    def envelope(self):
        return {**self.original_envelope, 'payload': base64.b64encode(json.dumps(self.payload).encode()).decode()}

    def test_injecting_video_into_real_signed_envelope_fails_signature(self):
        with self.assertRaises(backend.LauncherError):
            app_catalogue.verify_catalogue(backend, self.envelope())

    def test_authenticated_shape_accepts_optional_video_but_not_unknown_or_misidentified_apps(self):
        # Isolate schema/application identity after the RSA gate. The previous
        # test and existing public-signature tests exercise the real RSA gate.
        with mock.patch.object(app_catalogue.hmac, 'compare_digest', return_value=True):
            result = app_catalogue.verify_catalogue(backend, self.envelope())
            self.assertEqual(result['apps']['product-video']['entry'], 'python/python.exe')
            self.payload['apps']['product-video']['entry'] = 'cmd.exe'
            with self.assertRaises(backend.LauncherError):
                app_catalogue.verify_catalogue(backend, self.envelope())
            self.payload['apps']['product-video']['entry'] = 'python/python.exe'
            self.payload['apps']['unknown'] = self.video
            self.payload['history']['unknown'] = []
            with self.assertRaises(backend.LauncherError):
                app_catalogue.verify_catalogue(backend, self.envelope())

    def test_manual_video_install_and_typed_handover_route_to_video_only(self):
        import main
        app = object.__new__(main.MITOMAdapter)
        app._pending_install_action = 'video'
        app._video_release = self.video
        app._install_video_current = mock.Mock()
        app._install_vector_current = mock.Mock()
        app._install_mifont_current = mock.Mock()
        self.assertTrue(app._continue_install({}))
        app._install_video_current.assert_called_once()
        app._install_vector_current.assert_not_called()
        app._install_mifont_current.assert_not_called()
        self.assertEqual(self_update._resume_action('video'), 'video')
        for invalid in ('powershell', 'http://example.invalid', ['video']):
            with self.assertRaises(self_update.SelfUpdateError):
                self_update._resume_action(invalid)

    def test_fixed_video_uri_has_no_install_or_arbitrary_command_surface(self):
        for uri in ('mitom://video', 'mitom://video/'):
            self.assertEqual(backend.open_uri_action(uri), 'video')
        self.assertEqual(backend.open_uri_action('uoy://launch'), 'catalogue')
        for uri in ('mitom://install', 'mitom://video?install=1', 'mitom://video#run',
                    'mitom://video/anything', 'mitom://user@video', 'mitom://video:80',
                    'mitom://video" --install'):
            with self.assertRaises(backend.LauncherError):
                backend.open_uri_action(uri)

    def test_video_uri_opens_verified_app_or_selects_card_without_installing(self):
        import main
        for installed in (True, False):
            with self.subTest(installed=installed):
                app = object.__new__(main.MITOMAdapter)
                app._open_video_requested = True
                app._video_release = self.video
                app.busy = False
                app.ui = types.SimpleNamespace(search=mock.Mock(), _render_catalogue=mock.Mock())
                app.status_var = types.SimpleNamespace(set=mock.Mock())
                app._refresh_state = mock.Mock(return_value={'video_current_verified': installed})
                app._open_video = mock.Mock()
                app._request_install = mock.Mock()
                app._open_requested_video()
                self.assertEqual(app._open_video.call_count, int(installed))
                self.assertFalse(app._open_video_requested)
                app.ui.search.set.assert_called_with('product video')
                app._request_install.assert_not_called()

    def test_authenticated_catalogue_cannot_remove_video_from_existing_cache(self):
        # Focus on the application-preservation boundary after authentication;
        # unsigned additions are rejected by the real-signature test above.
        class Response(io.BytesIO):
            def geturl(self):
                return app_catalogue.CATALOGUE_URL
        with tempfile.TemporaryDirectory(dir=Path(__file__).parents[2] / 'build') as temporary:
            root = Path(temporary)
            cache = root / 'apps-catalogue-signed.json'
            original = json.dumps(self.envelope()).encode()
            cache.write_bytes(original)
            self.payload['version'] = '2026.10.31.1'
            del self.payload['apps']['product-video']
            del self.payload['history']['product-video']
            opener = mock.Mock()
            opener.open.return_value = Response(json.dumps(self.envelope()).encode())
            with mock.patch.object(app_catalogue.hmac, 'compare_digest', return_value=True), \
                    mock.patch.object(backend, 'launcher_data_root', return_value=root), \
                    mock.patch.object(app_catalogue.urllib.request, 'build_opener', return_value=opener):
                with self.assertRaisesRegex(backend.LauncherError, 'disappeared'):
                    app_catalogue.probe_app_catalogue(backend)
            self.assertEqual(cache.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
