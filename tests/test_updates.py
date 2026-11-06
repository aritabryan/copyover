import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BASE = 'https://github.com/aritabryan/copyover/releases/download/v0.4.0/'


def release(apk_size, tag='v0.4.0', **extra):
    return dict({'tag_name': tag, 'draft': False, 'prerelease': False, 'assets': [
        {'name': 'Puente.apk', 'size': apk_size, 'browser_download_url': BASE + 'Puente.apk'},
        {'name': 'puente-update.json', 'size': 200, 'browser_download_url': BASE + 'puente-update.json'}]}, **extra)


class UpdateTests(unittest.TestCase):
    def test_locally_prepared_apk_shows_up_without_restarting(self):
        updates = self.module()
        content = b'PK-local-apk'
        manifest = dict(versionCode=16, versionName='1.0.7', size=len(content), sha256=hashlib.sha256(content).hexdigest())
        with tempfile.TemporaryDirectory() as folder:
            cache = updates.UpdateCache(folder)
            Path(folder, manifest['sha256'] + '.apk').write_bytes(content)
            Path(folder, 'latest.json').write_text(json.dumps(manifest), encoding='utf-8')
            self.assertEqual(cache.status()['update'], manifest)
            Path(folder, 'latest.json').write_text('{broken', encoding='utf-8')
            self.assertEqual(cache.status()['update'], manifest)
            tampered = dict(manifest, versionCode=17, sha256='a' * 64)
            Path(folder, 'a' * 64 + '.apk').write_bytes(b'other content')
            Path(folder, 'latest.json').write_text(json.dumps(tampered), encoding='utf-8')
            self.assertEqual(cache.status()['update'], manifest)

    def test_older_release_does_not_replace_the_local_build(self):
        updates = self.module()
        content = b'PK-local-apk'
        current = dict(versionCode=16, versionName='1.0.7', size=len(content), sha256=hashlib.sha256(content).hexdigest())
        older = dict(current, versionCode=15, versionName='1.0.6')
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, current['sha256'] + '.apk').write_bytes(content)
            Path(folder, 'latest.json').write_text(json.dumps(current), encoding='utf-8')
            cache = updates.UpdateCache(folder)
            with patch.object(updates, 'query_release', return_value=release(len(content), tag='v1.0.6')), \
                    patch.object(updates, 'download', side_effect=lambda url, target, limit: Path(target).write_text(json.dumps(older))) as download:
                cache.refresh()
            self.assertEqual(download.call_count, 1)
            self.assertEqual(cache.status()['update'], current)

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('updates'), 'Falta el actualizador de PC')
        import updates
        return updates

    def test_manifest_rejects_invalid_hash_size_and_version(self):
        updates = self.module()
        valid = dict(versionCode=4, versionName='0.4.0', size=8, sha256='a' * 64)
        self.assertEqual(updates.validate_manifest(valid), valid)
        for field, value in [('sha256', '../apk'), ('size', 0), ('size', 999999999), ('versionCode', True), ('versionName', '')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                updates.validate_manifest(dict(valid, **{field: value}))

    def test_repo_public_api_query(self):
        updates = self.module()
        self.assertEqual(updates.LAST_API, 'https://api.github.com/repos/aritabryan/copyover/releases/latest')
        self.assertNotIn('run_gh', dir(updates), 'Ya no debe depender de GitHub CLI')

    def test_download_is_verified_before_publishing_and_survives_restart(self):
        updates = self.module()
        apk = b'PK-test-apk'
        manifest = dict(versionCode=4, versionName='0.4.0', size=len(apk), sha256=hashlib.sha256(apk).hexdigest())

        def download(url, destination, limit):
            Path(destination).write_bytes(apk if url.endswith('Puente.apk') else json.dumps(manifest).encode())

        with tempfile.TemporaryDirectory() as folder:
            cache = updates.UpdateCache(Path(folder))
            with patch.object(updates, 'request_json', return_value=release(len(apk))), \
                    patch.object(updates, 'download', side_effect=download):
                cache.refresh()
            self.assertEqual(cache.status()['update'], manifest)
            self.assertEqual(cache.apk(manifest['sha256']).read_bytes(), apk)
            self.assertEqual(updates.UpdateCache(Path(folder)).status()['update'], manifest)
            with patch.object(updates, 'request_json', return_value=release(len(apk))), \
                    patch.object(updates, 'download', side_effect=download) as fetch:
                cache.refresh()
                self.assertEqual(fetch.call_count, 0, 'No debe descargar otra vez el mismo APK cada hora')
            with patch.object(updates, 'request_json', side_effect=OSError('SECRET must not leak')):
                cache.refresh()
            self.assertEqual(cache.status()['update'], manifest)
            self.assertNotIn('SECRET', json.dumps(cache.status()))

    def test_distinguishes_without_published_version_without_internet_and_records_once(self):
        updates = self.module()
        import urllib.error

        not_found = urllib.error.HTTPError(updates.LAST_API, 404, 'Not Found', {}, None)
        without_network = urllib.error.URLError(OSError('getaddrinfo failed'))
        with tempfile.TemporaryDirectory() as folder:
            cache = updates.UpdateCache(Path(folder))
            with patch.object(updates, 'request_json', side_effect=not_found), self.assertLogs(level='INFO') as registry:
                cache.refresh()
                cache.refresh()
            self.assertEqual(len(registry.records), 1, 'se anota la primera vez, no en cada consulta')
            self.assertIn('404', registry.output[0])
            self.assertIn('versión publicada', cache.status()['message'])
            self.assertNotIn('internet', cache.status()['message'])
            with patch.object(updates, 'request_json', side_effect=without_network), self.assertLogs(level='INFO') as registry:
                cache.refresh()
            self.assertIn('getaddrinfo', registry.output[0])
            self.assertIn('internet', cache.status()['message'])
            self.assertNotIn('getaddrinfo', json.dumps(cache.status()))
            error = urllib.error.HTTPError(updates.LAST_API, 403, 'rate limit', {}, None)
            with patch.object(updates, 'request_json', side_effect=error), self.assertLogs(level='INFO') as registry:
                cache.refresh()
            self.assertIn('HTTP 403', registry.output[0])

    def test_invalid_download_never_becomes_available(self):
        updates = self.module()
        with tempfile.TemporaryDirectory() as folder:
            cache = updates.UpdateCache(Path(folder))
            with patch.object(updates, 'request_json', return_value={}):
                cache.refresh()
            self.assertIsNone(cache.status()['update'])
            self.assertIsNone(cache.apk('../config.json'))

    def test_wrong_hash_is_not_offered(self):
        updates = self.module()

        def download(url, destination, limit):
            if url.endswith('Puente.apk'):
                Path(destination).write_bytes(b'bad')
            else:
                Path(destination).write_text(json.dumps(dict(versionCode=4, versionName='0.4.0', size=3, sha256='a' * 64)))

        with tempfile.TemporaryDirectory() as folder:
            cache = updates.UpdateCache(Path(folder))
            with patch.object(updates, 'request_json', return_value=release(3)), \
                    patch.object(updates, 'download', side_effect=download):
                cache.refresh()
            self.assertIsNone(cache.status()['update'])
            self.assertFalse((Path(folder) / 'latest.json').exists())

    def test_download_only_from_repo_releases(self):
        updates = self.module()
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
            updates.download('https://evil.example/Puente.apk', Path(folder) / 'x', 10)

    def test_new_version_notifies_without_needing_the_tray(self):
        import bridge
        import updates
        for platform, installed, text, link in (
            ('linux', False, 'install.sh', ''),
            ('win32', True, 'panel', ''),
            ('win32', False, 'panel', updates.RELEASES_PAGE),
        ):
            with patch.object(bridge.sys, 'platform', platform), patch.object(updates, 'self_updates', return_value=installed), \
                    patch.dict(bridge.STATE, {'new_version': None}), patch.object(bridge.so, 'notify', create=True) as notify:
                bridge.announce_new_version('v9.0.0')
                self.assertEqual(bridge.STATE['new_version'], 'v9.0.0')
            self.assertIn('v9.0.0', notify.call_args.args[1])
            self.assertIn(text, notify.call_args.args[1])
            self.assertEqual(notify.call_args.kwargs.get('link', ''), link)

    def test_linux_points_to_its_guide_because_releases_have_no_linux_download(self):
        import updates
        with patch.object(updates.sys, 'platform', 'linux'):
            self.assertEqual(updates.update_page(), updates.LINUX_UPDATE_GUIDE)
        with patch.object(updates.sys, 'platform', 'win32'):
            self.assertEqual(updates.update_page(), updates.RELEASES_PAGE)

    def test_has_new_version(self):
        updates = self.module()
        self.assertTrue(updates.has_new_version('v1.0.1', '1.0.0'))
        self.assertTrue(updates.has_new_version('1.1', '1.0.9'))
        self.assertFalse(updates.has_new_version('v1.0.0', '1.0.0'))
        self.assertFalse(updates.has_new_version('v0.9.0', '1.0.0'))
        self.assertFalse(updates.has_new_version('nightly', '1.0.0'))

    def test_notifies_once_per_version_even_without_an_apk(self):
        updates = self.module()
        seen = []
        without_apk = {'tag_name': 'v9.0.0', 'draft': False, 'prerelease': False, 'assets': []}
        with tempfile.TemporaryDirectory() as folder:
            cache = updates.UpdateCache(Path(folder), on_view_version=seen.append)
            with patch.object(updates, 'request_json', return_value=without_apk):
                cache.refresh()
                cache.refresh()
        self.assertEqual(seen, ['v9.0.0'])
        self.assertEqual(cache.new_version, 'v9.0.0')

    def release_with_installer(self, data, digest=True, tag='v9.0.0'):
        name = 'CopyOver-Setup-9.0.0.exe'
        installer = {'name': name, 'size': len(data), 'browser_download_url': BASE + name}
        if digest:
            installer['digest'] = 'sha256:' + hashlib.sha256(data).hexdigest()
        return {'tag_name': tag, 'draft': False, 'prerelease': False, 'assets': [
            installer, {'name': 'latest.json', 'size': 300, 'browser_download_url': BASE + 'latest.json'}]}

    def test_installer_uses_the_hash_github_publishes(self):
        updates = self.module()
        data = b'MZ-instalador'
        found = updates.installer_of(self.release_with_installer(data))
        self.assertEqual(found['name'], 'CopyOver-Setup-9.0.0.exe')
        self.assertEqual(found['sha256'], hashlib.sha256(data).hexdigest())
        self.assertIsNone(updates.installer_of(self.release_with_installer(data, digest=False))['sha256'])
        self.assertIsNone(updates.installer_of(release(10)), 'sin instalador no hay nada que instalar')
        odd = {'assets': [{'name': '..\\CopyOver-Setup-1.exe', 'size': 5}]}
        self.assertIsNone(updates.installer_of(odd))

    def install(self, updates, published, content, installed=True, manifest=None):
        """Runs install_new_version with downloads and the process faked; returns (result, Popen mock)."""
        def download(url, destination, limit):
            Path(destination).write_bytes(manifest if url.endswith('latest.json') else content)

        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as temporary:
            cache = updates.UpdateCache(Path(folder))
            with patch.object(updates, 'request_json', return_value=published):
                cache.refresh()
            with patch.object(updates, 'self_updates', return_value=installed), \
                    patch.object(updates, 'download', side_effect=download), \
                    patch.object(updates.tempfile, 'gettempdir', return_value=temporary), \
                    patch.object(updates.subprocess, 'Popen') as launch:
                try:
                    return cache.install_new_version(), launch
                except ValueError:
                    return 'error', launch

    def test_low_installed_copy_verifies_and_runs_installer(self):
        updates = self.module()
        data = b'MZ-instalador'
        ok, launch = self.install(updates, self.release_with_installer(data), data)
        self.assertTrue(ok)
        arguments = launch.call_args[0][0]
        self.assertTrue(arguments[0].endswith('CopyOver-Setup-9.0.0.exe'))
        self.assertEqual(arguments[1:], ['/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART'])

    def test_tampered_installer_does_not_run(self):
        updates = self.module()
        data = b'MZ-instalador'
        ok, launch = self.install(updates, self.release_with_installer(data), b'MZ-otra-cosa')
        self.assertEqual(ok, 'error')
        launch.assert_not_called()

    def test_portable_copy_installs_nothing(self):
        updates = self.module()
        data = b'MZ-instalador'
        ok, launch = self.install(updates, self.release_with_installer(data), data, installed=False)
        self.assertFalse(ok)
        launch.assert_not_called()

    def test_without_digest_uses_json_latest_hash(self):
        updates = self.module()
        data = b'MZ-instalador'
        manifest = '﻿' + json.dumps({'instalador': 'CopyOver-Setup-9.0.0.exe',
                                            'sha256_instalador': hashlib.sha256(data).hexdigest()})
        ok, launch = self.install(updates, self.release_with_installer(data, digest=False), data,
                                   manifest=manifest.encode('utf-8'))
        self.assertTrue(ok)
        launch.assert_called_once()

    def test_installer_does_not_close_itself_and_reopens_after_updating(self):
        iss = (Path(__file__).resolve().parent.parent / 'installer.iss').read_text('utf-8')
        self.assertNotIn('/T ', iss, 'taskkill /T cerraría el instalador que lanzó CopyOver')
        self.assertIn('Check: WizardSilent', iss)
        updates = self.module()
        self.assertIn('{5CCE71F4-BA1A-4FFB-BB41-4A337AFFC666}', updates.UNINSTALL_KEY)
        self.assertIn('AppId={{5CCE71F4-BA1A-4FFB-BB41-4A337AFFC666}', iss)

    def test_does_not_notify_when_the_release_is_installed(self):
        updates = self.module()
        seen = []
        current = {'tag_name': 'v' + updates.VERSION, 'draft': False, 'prerelease': False, 'assets': []}
        with tempfile.TemporaryDirectory() as folder:
            cache = updates.UpdateCache(Path(folder), on_view_version=seen.append)
            with patch.object(updates, 'request_json', return_value=current):
                cache.refresh()
        self.assertEqual(seen, [])
        self.assertIsNone(cache.new_version)


if __name__ == '__main__':
    unittest.main()
