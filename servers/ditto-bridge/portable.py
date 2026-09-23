"""Portable executable discovery and exclusive local device ownership."""
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile

from filelock import FileLock, Timeout


def sdk_root(env=None, windows=None):
    env = os.environ if env is None else env
    windows = os.name == 'nt' if windows is None else windows
    if windows:
        default = Path(env.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'Android/Sdk'
    elif sys.platform == 'darwin':
        default = Path.home() / 'Library/Android/sdk'
    else:
        default = Path.home() / 'Android/Sdk'
    return Path(env.get('ANDROID_HOME') or env.get('ANDROID_SDK_ROOT') or default)


def sdk_tool(name, env=None, windows=None):
    env = os.environ if env is None else env
    windows = os.name == "nt" if windows is None else windows
    override = env.get(f'DITTO_{name.upper()}_BIN')
    if override:
        return override
    suffix = '.exe' if windows else ''
    if name == 'aapt':
        versions = list((sdk_root(env, windows) / 'build-tools').glob(f'*/aapt{suffix}'))
        def version(path):
            import re
            return tuple(int(n) for n in re.findall(r'\d+', path.parent.name))
        if versions:
            return str(max(versions, key=version))
    else:
        folder = 'platform-tools' if name == 'adb' else 'emulator'
        candidate = sdk_root(env, windows) / folder / f'{name}{suffix}'
        if candidate.is_file() or env.get("ANDROID_HOME") or env.get("ANDROID_SDK_ROOT"):
            return str(candidate)
    return shutil.which(name + suffix, path=env.get('PATH')) or (str(candidate) if name != 'aapt' else name)


def command(binary, args=()):
    """Keep paths/arguments separate; avoid cmd.exe quoting and expansion."""
    executable = shutil.which(str(binary)) or str(binary)
    path = Path(executable)
    if path.suffix.lower() == '.jar':
        return [os.environ.get('DITTO_JAVA_BIN', 'java'), '-jar', executable, *map(str, args)]
    if path.suffix.lower() in ('.bat', '.cmd'):
        if path.stem == 'jadx' and (path.parent.parent / 'lib').is_dir():
            return [os.environ.get('DITTO_JAVA_BIN', 'java'), '-cp',
                    str(path.parent.parent / 'lib/*'), 'jadx.cli.JadxCLI', *map(str, args)]
        if path.stem == 'apktool' and path.with_suffix('.jar').is_file():
            return command(str(path.with_suffix('.jar')), args)
        if path.stem == 'dart':
            native = path.parent / 'cache/dart-sdk/bin/dart.exe'
            if native.is_file():
                return [str(native), *map(str, args)]
        raise RuntimeError(f'Use a native executable or .jar for {binary}; '
                           'for JADX use its extracted distribution with lib/.')
    return [executable, *map(str, args)]


class DeviceLease:
    """OS lock released on process exit; one owner per ADB endpoint/serial."""
    def __init__(self, serial, directory=None):
        endpoint = os.environ.get('ADB_SERVER_SOCKET', 'local:5037')
        key = hashlib.sha256(f'{endpoint}/{serial}'.encode()).hexdigest()[:24]
        self.path = Path(directory or tempfile.gettempdir()) / f'ditto-device-{key}.lock'
        # MCP calls may run on different worker threads in the same process.
        self.lock = FileLock(self.path, timeout=0, thread_local=False)

    def acquire(self):
        if self.lock.is_locked:
            return
        try:
            self.lock.acquire()
        except Timeout:
            raise RuntimeError('device is owned by another Ditto controller; use a separate emulator') from None

    def release(self):
        self.lock.release()
