"""New versions from the public GitHub releases.

The PC checks the latest stable release with no account or token. It reports a new desktop version of CopyOver
and keeps the verified APK so Android can download it from the PC. When this CopyOver came from the installer, it
can also download the new installer, check it and run it: the installer closes CopyOver, replaces the files
(settings and history stay next to the exe) and opens it again.
"""
import functools
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

import private_files
from i18n import t
from version import APP_NAME, REPOSITORY as REPOSITORY, VERSION

MAX_APK = 200 * 1024 * 1024
MAX_INSTALLER = 300 * 1024 * 1024
MAX_JSON = 1024 * 1024
LAST_API = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
RELEASES_PAGE = f'https://github.com/{REPOSITORY}/releases/latest'
LINUX_UPDATE_GUIDE = f'https://github.com/{REPOSITORY}/blob/main/docs/linux.md#update'
DOWNLOADS_PREFIX = f'https://github.com/{REPOSITORY}/releases/download/'
HEADERS = {'User-Agent': f'{APP_NAME}/{VERSION}', 'Accept': 'application/vnd.github+json'}
# The uninstall entry Inno Setup writes for the AppId in installer.iss (per user, no admin).
UNINSTALL_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{5CCE71F4-BA1A-4FFB-BB41-4A337AFFC666}_is1'
# Quiet install: progress window only, no questions, and installer.iss opens CopyOver again at the end.
INSTALLER_ARGUMENTS = ['/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART']


def validate_manifest(value):
    code, name, size, digest = (value[k] for k in ('versionCode', 'versionName', 'size', 'sha256'))
    if type(code) is not int or not 0 < code <= 2100000000:
        raise ValueError('Invalid version')
    if not isinstance(name, str) or not re.fullmatch(r'[0-9A-Za-z.+_-]{1,80}', name):
        raise ValueError('Invalid version name')
    if type(size) is not int or not 0 < size <= MAX_APK:
        raise ValueError('Invalid size')
    if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise ValueError('Invalid hash')
    return dict(versionCode=code, versionName=name, size=size, sha256=digest)


def _numbers(version):
    found = re.fullmatch(r'v?(\d+)\.(\d+)(?:\.(\d+))?', str(version or '').strip())
    if not found:
        return None
    return tuple(int(n or 0) for n in found.groups())


def has_new_version(label, current=VERSION) -> bool:
    """True if the release tag («v1.2.0») is newer than the installed version."""
    new_value, installed = _numbers(label), _numbers(current)
    return bool(new_value and installed and new_value > installed)


def installed_folder():
    """Folder where the installer put CopyOver, from its uninstall entry; None without one (zip, source, Linux)."""
    if sys.platform != 'win32':
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            return Path(winreg.QueryValueEx(key, 'InstallLocation')[0])
    except OSError:
        return None


def update_page() -> str:
    """Where a new version is explained. Releases have no Linux download: Linux updates from the repository."""
    return RELEASES_PAGE if sys.platform == 'win32' else LINUX_UPDATE_GUIDE


@functools.lru_cache(maxsize=1)
def self_updates() -> bool:
    """True if this CopyOver.exe is the installed one: then it can install the new version by itself. A portable
    zip copy (or the source) only links to the releases page, so the installer never touches another folder."""
    folder = installed_folder()
    if not folder or not getattr(sys, 'frozen', False):
        return False
    own = Path(sys.executable).resolve().parent
    return os.path.normcase(str(own)) == os.path.normcase(str(folder.resolve()))


def installer_of(release):
    """The Windows installer of a release (name, url, size, sha256 or None), or None if it has none.

    The hash comes from the «digest» GitHub computes for each file; releases without it bring it in latest.json.
    """
    files = {a.get('name'): a for a in release.get('assets') or [] if isinstance(a, dict)}
    for name, file in files.items():
        if not isinstance(name, str) or not re.fullmatch(r'CopyOver-Setup-[0-9A-Za-z.+_-]{1,80}\.exe', name):
            continue
        size, digest = file.get('size'), str(file.get('digest') or '')
        if type(size) is not int or not 0 < size <= MAX_INSTALLER:
            return None
        return {'name': name, 'url': file.get('browser_download_url'), 'size': size,
                'sha256': digest[7:] if re.fullmatch(r'sha256:[a-f0-9]{64}', digest) else None,
                'manifiesto': (files.get('latest.json') or {}).get('browser_download_url')}
    return None


def request_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=20) as response:
        body = response.read(MAX_JSON + 1)
    if len(body) > MAX_JSON:
        raise ValueError('Response too large')
    return json.loads(body.decode('utf-8'))


def download(url, destination, limit):
    """Downloads a file from a release of this repository, up to `limite` bytes."""
    if not str(url).startswith(DOWNLOADS_PREFIX):
        raise ValueError('Download outside the repository')
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': HEADERS['User-Agent']}),
                                timeout=60) as response, open(destination, 'wb') as file:
        total = 0
        while block := response.read(64 * 1024):
            total += len(block)
            if total > limit:
                raise ValueError('File too large')
            file.write(block)


def query_release():
    release = request_json(LAST_API)
    if not isinstance(release, dict) or release.get('draft') or release.get('prerelease'):
        raise ValueError('Solo versiones estables')
    tag = release.get('tag_name')
    if not isinstance(tag, str) or not re.fullmatch(r'[A-Za-z0-9._+-]{1,100}', tag) or tag.startswith('-'):
        raise ValueError('Invalid tag')
    return release


class UpdateCache:
    def __init__(self, directory, on_view_version=None):
        self.directory = Path(directory)
        self.lock = threading.Lock()
        self.manifest = None
        self.source = None
        self.new_version = None
        self.installer = None  # installer of new_version (see installer_of)
        self.installing = threading.Lock()
        self.on_view_version = on_view_version
        self.last_cause = None  # last logged reason the check failed
        self.message = t('The PC is checking for updates.')
        try:
            manifest = validate_manifest(json.loads((self.directory / 'latest.json').read_text('utf-8')))
            self._verify(self.directory / (manifest['sha256'] + '.apk'), manifest)
            self.manifest = manifest
            try:
                self.source = json.loads((self.directory / 'source.json').read_text('utf-8'))
            except (OSError, ValueError):
                pass
            self.message = t('Update available from the PC.')
        except (OSError, ValueError, KeyError, TypeError):
            pass

    @staticmethod
    def _verify(path, manifest):
        if path.stat().st_size != manifest['size']:
            raise ValueError('Wrong size')
        with path.open('rb') as source:
            digest = hashlib.sha256()
            while block := source.read(64 * 1024):
                digest.update(block)
            if digest.hexdigest() != manifest['sha256']:
                raise ValueError('Wrong hash')

    def load_prepared(self):
        """Picks up a verified local build without restarting the running server."""
        try:
            prepared = validate_manifest(json.loads((self.directory / 'latest.json').read_text('utf-8')))
            with self.lock:
                if prepared == self.manifest or (self.manifest and prepared['versionCode'] <= self.manifest['versionCode']):
                    return
                self._verify(self.directory / (prepared['sha256'] + '.apk'), prepared)
                self.manifest = prepared
                self.message = t('Update available from the PC.')
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def status(self):
        self.load_prepared()
        with self.lock:
            return {'update': self.manifest, 'message': self.message}

    def current_apk(self):
        """The verified APK of the latest version, or None while the PC has none yet."""
        with self.lock:
            manifest = self.manifest
        return self.apk(manifest['sha256']) if manifest else None

    def version_apk(self):
        with self.lock:
            return self.manifest['versionName'] if self.manifest else None

    def apk(self, digest):
        if not re.fullmatch(r'[a-f0-9]{64}', digest):
            return None
        path = self.directory / (digest + '.apk')
        return path if path.is_file() else None

    def refresh(self):
        try:
            self._refresh()
            self.last_cause = None
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            cause, message = self._explain(error)
            with self.lock:
                self.message = message
            # Logged the first time it happens (and when the reason changes), not every hour.
            if cause != self.last_cause:
                self.last_cause = cause
                logging.info('Updates: %s', cause)

    def _explain(self, error):
        """(what to log, what to show). The visible message leaves out the error details."""
        if isinstance(error, urllib.error.HTTPError):
            if error.code == 404:
                return ('GitHub answered 404: no published version, or the repository is private',
                        t('Update available from the PC.') if self.manifest else
                        t('No published version of CopyOver was found on GitHub. If the repository is private, '
                          'download the new version directly from its page.'))
            return (f'GitHub answered HTTP {error.code}',
                    t('The PC couldn’t check for versions right now; it will try again later.'))
        if isinstance(error, OSError):  # includes URLError and timeouts: no internet or no DNS
            reason = getattr(error, 'reason', None) or error
            return (f'no connection to GitHub ({type(error).__name__}: {reason})',
                    t('The PC couldn’t check for new versions. Check its internet connection.') if self.manifest else
                    t('There’s no APK available on the PC. Check its internet connection.'))
        return (f'the published version is not valid ({type(error).__name__}: {error})',
                t('The PC couldn’t check for versions right now; it will try again later.'))

    def _notify_desktop(self, tag, installer=None):
        if not has_new_version(tag):
            return
        with self.lock:
            self.installer = installer
        if self.new_version == tag:
            return
        self.new_version = tag
        if self.on_view_version:
            try:
                self.on_view_version(tag)
            except Exception:
                logging.exception("Couldn't announce version %s", tag)

    def _refresh(self):
        self.load_prepared()
        release = query_release()
        tag = release['tag_name']
        self._notify_desktop(tag, installer_of(release))
        assets = {a['name']: a for a in release['assets']}
        if not 0 < assets['Puente.apk']['size'] <= MAX_APK or not 0 < assets['puente-update.json']['size'] <= 4096:
            raise ValueError('Invalid files')
        source_identity = {'tag': tag, 'assets': [
            {k: assets[name].get(k) for k in ('id', 'size', 'updated_at', 'digest')}
            for name in ('Puente.apk', 'puente-update.json')]}
        if self.manifest and self.source == source_identity:
            with self.lock:
                self.message = t('Update available from the PC.')
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='download-', dir=self.directory) as folder:
            source = Path(folder)
            download(assets['puente-update.json']['browser_download_url'], source / 'puente-update.json', 4096)
            manifest = validate_manifest(json.loads((source / 'puente-update.json').read_text('utf-8')))
            with self.lock:
                if self.manifest and manifest['versionCode'] < self.manifest['versionCode']:
                    return
            if manifest['size'] != assets['Puente.apk']['size']:
                raise ValueError('Wrong announced size')
            download(assets['Puente.apk']['browser_download_url'], source / 'Puente.apk', MAX_APK)
            self._verify(source / 'Puente.apk', manifest)
            # With the hash in the name, a half-finished download on the phone keeps pointing at the same file
            # after an update.
            shutil.move(str(source / 'Puente.apk'), str(self.directory / (manifest['sha256'] + '.apk')))
            temporary = source / 'latest.json'
            temporary.write_text(json.dumps(manifest), 'utf-8')
            temporary.replace(self.directory / 'latest.json')
            source_file = source / 'source.json'
            source_file.write_text(json.dumps(source_identity), 'utf-8')
            source_file.replace(self.directory / 'source.json')
            with self.lock:
                self.manifest = manifest
                self.source = source_identity
                self.message = t('Update available from the PC.')

    def _manifest_hash(self, installer):
        """sha256 of the installer from the release's latest.json (written by build.ps1)."""
        if not installer.get('manifiesto'):
            raise ValueError('The published version has no installer hash')
        with tempfile.TemporaryDirectory(prefix='download-') as folder:
            destination = Path(folder) / 'latest.json'
            download(installer['manifiesto'], destination, 64 * 1024)
            manifest = json.loads(destination.read_text('utf-8-sig'))
        digest = manifest.get('sha256_instalador') if manifest.get('instalador') == installer['name'] else None
        if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
            raise ValueError('Invalid installer hash')
        return digest

    def install_new_version(self) -> bool:
        """Downloads the new version's installer, checks its size and hash, and runs it in quiet mode.

        False if there's nothing to install here (no new version, or a portable copy). Raises if the download or
        the check fails; nothing is run then.
        """
        with self.lock:
            installer = self.installer
        if not installer or not self_updates():
            return False
        if not self.installing.acquire(blocking=False):
            return True  # a click while it's already downloading
        try:
            digest = installer['sha256'] or self._manifest_hash(installer)
            folder = private_files.temporary_subfolder(f'{APP_NAME}-actualizacion')
            destination = folder / installer['name']
            download(installer['url'], destination, MAX_INSTALLER)
            self._verify(destination, {'size': installer['size'], 'sha256': digest})
            logging.info('Installing %s', installer['name'])
            # Its own process group: the installer closes CopyOver.exe and must not close with it.
            subprocess.Popen([str(destination), *INSTALLER_ARGUMENTS], close_fds=True,
                             creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0)
                             | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
            return True
        finally:
            self.installing.release()

    def start(self):
        def worker():
            while True:
                self.refresh()
                threading.Event().wait(3600)
        threading.Thread(target=worker, name='actualizaciones', daemon=True).start()
