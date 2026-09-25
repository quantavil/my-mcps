"""Package-bound Ditto MCP backend. CLIs are invoked only inside the MCP server."""
from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import tomllib
import uuid
import zipfile
import xml.etree.ElementTree as ET

from portable import command as tool_command, sdk_tool, DeviceLease

CAPABILITIES = ('jadx', 'apktool', 'r2flutter')
SESSION_ID = uuid.uuid4().hex
BRIDGE_VERSION = tomllib.loads(Path(__file__).with_name('pyproject.toml').read_text(encoding='utf-8'))['project']['version']
MAX_EXPORT_BYTES = 512 * 1024 * 1024
TIMEOUT = 600
COMMANDS = {
    'jadx': os.environ.get('DITTO_JADX_BIN', 'jadx'),
    'apktool': os.environ.get('DITTO_APKTOOL_BIN', 'apktool'),
    'r2flutter': os.environ.get('DITTO_R2FLUTTER_BIN', 'r2flutter'),
}


class BridgeError(RuntimeError):
    """A backend did not produce defensible Ditto evidence."""


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def digest_json(value):
    payload = json.dumps(value, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False).encode()
    return hashlib.sha256(payload).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n',
                          encoding='utf-8')


def run(command, timeout=TIMEOUT, env=None):
    try:
        result = subprocess.run(tool_command(command[0], command[1:]), capture_output=True, timeout=timeout,
                                env=env, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BridgeError(f'backend command failed: {command[0]}: {error}') from None
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).decode('utf-8', 'replace')[-1500:]
        raise BridgeError(f'{command[0]} exited {result.returncode}: {detail}')
    return result.stdout.decode('utf-8', 'replace')


def apk_package_name(apk):
    aapt = sdk_tool('aapt')
    output = run([aapt, 'dump', 'badging', str(apk)], timeout=30)
    match = re.search(r"^package: name='([^']+)'", output, re.MULTILINE)
    if not match:
        raise BridgeError('AAPT could not identify the APK package')
    return match.group(1)


def version(capability):
    binary = COMMANDS[capability]
    flag = '--version' if capability != 'r2flutter' else '-V'
    output = run([binary, flag], timeout=15).strip()
    if not output:
        raise BridgeError(f'{capability} did not report a version')
    return output.splitlines()[0][:200]


def _aot_binary(apk, stage):
    with zipfile.ZipFile(apk) as archive:
        names = archive.namelist()
        selected = [name for name in names if re.fullmatch(
            r'lib/arm64-v8a/libapp\.so', name)]
        if len(selected) != 1:
            raise BridgeError('APK must contain one arm64-v8a/libapp.so')
        info = archive.getinfo(selected[0])
        if info.file_size > MAX_EXPORT_BYTES:
            raise BridgeError('AOT binary exceeds export limit')
        target = stage / 'libapp.so'
        with archive.open(info) as source, target.open('xb') as output:
            shutil.copyfileobj(source, output)
    return target


def _profile(value):
    if isinstance(value, dict):
        for key in ('dart_profile', 'dart_version', 'version', 'profile'):
            found = value.get(key)
            if isinstance(found, str) and found and found not in ('unverified', 'unavailable'):
                return found
        for child in value.values():
            found = _profile(child)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = _profile(child)
            if found:
                return found
    return None


def _analyze(capability, apk, stage):
    binary = COMMANDS[capability]
    analysis = stage / 'analysis'
    analysis.mkdir()
    if capability == 'jadx':
        try:
            result = subprocess.run(
                tool_command(binary, ['--threads-count', '1', '-d', str(analysis), str(apk)]),
                capture_output=True, timeout=TIMEOUT, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise BridgeError(f'JADX backend failed: {error}') from None
        log = (result.stdout + result.stderr).decode('utf-8', 'replace')
        partial = re.search(r'ERROR\s*-\s*finished with errors, count:\s*(\d+)', log)
        if result.returncode != 0 and not (result.returncode == 3 and partial):
            raise BridgeError(f'JADX exited {result.returncode}: {log[-1000:]}')
        if not (analysis / 'sources').is_dir():
            raise BridgeError('JADX emitted no decompiled source files')
        errors = int(partial.group(1)) if partial else 0
        if errors:
            (analysis / 'jadx.log').write_text(log[-128000:], encoding='utf-8')
        count = sum(path.is_file() for path in analysis.rglob('*'))
        return {'command': 'jadx', 'files': count, 'decompilation_errors': errors,
                'limitations': ([f'{errors} classes failed to decompile; inspect JADX logs']
                                if errors else [])}
    if capability == 'apktool':
        run([binary, 'd', '-f', str(apk), '-o', str(analysis)])
        if not (analysis / 'AndroidManifest.xml').is_file():
            raise BridgeError('Apktool did not decode AndroidManifest.xml')
        return {'command': 'apktool d', 'manifest': 'analysis/AndroidManifest.xml'}
    binary_path = _aot_binary(apk, stage)
    try:
        header = json.loads(run([binary, '-jH', str(binary_path)]))
    except json.JSONDecodeError:
        raise BridgeError('r2Flutter header was not JSON') from None
    profile = _profile(header)
    if not profile:
        raise BridgeError('r2Flutter could not identify a Dart profile')
    write_json(analysis / 'header.json', header)
    for action, name in (('-jc', 'classes.json'), ('-jf', 'functions.json'),
                         ('-jz', 'strings.json')):
        raw = run([binary, action, str(binary_path)])
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            raise BridgeError(f'r2Flutter {name} was not JSON') from None
        if not isinstance(decoded, (dict, list)):
            raise BridgeError(f'r2Flutter {name} has no structured output')
        (analysis / name).write_text(raw, encoding='utf-8')
    binary_path.unlink()
    return {'command': 'r2flutter', 'abi': 'arm64-v8a', 'dart_profile': profile}


def validate_export(directory, capability=None, verify_files=True):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise BridgeError('analysis export must be a real directory')
    try:
        receipt = json.loads((directory / 'receipt.json').read_text(encoding='utf-8'))
        result = json.loads((directory / 'result.json').read_text(encoding='utf-8'))
        if (receipt.get('status') != 'healthy' or receipt.get('provenance') != 'mcp'
                or (capability is not None and receipt.get('capability') != capability)
                or receipt.get('response_sha256') != digest_json(result)):
            raise BridgeError('analysis receipt or result changed')
        expected = result.get('files')
        if not isinstance(expected, dict) or not expected:
            raise BridgeError('analysis has no file index; create a fresh export')
        if not verify_files:
            return receipt, result
        actual = {}
        for path in directory.rglob('*'):
            if path.is_symlink():
                raise BridgeError('analysis export contains a link')
            if path.is_file() and path.relative_to(directory).as_posix() not in ('receipt.json', 'result.json'):
                actual[path.relative_to(directory).as_posix()] = digest(path)
        if actual != expected:
            raise BridgeError('analysis file hashes changed')
        return receipt, result
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise BridgeError(f'invalid analysis export: {error}') from None


def query_analysis(output_dir, query='', path=None, offset=0, limit=40, capability=None, scan_offset=0):
    """Bounded text retrieval without rerunning a decompiler."""
    root = Path(output_dir).resolve()
    receipt, result = validate_export(root, capability, verify_files=False)
    if not isinstance(query, str) or len(query) > 500:
        raise BridgeError('query must be a string of at most 500 characters')
    if not 1 <= limit <= 100 or not 0 <= offset <= 10000000 or not 0 <= scan_offset <= MAX_EXPORT_BYTES:
        raise BridgeError('limit must be 1..100 and offset 0..10000000')
    files = result['files']
    if path is not None:
        if path not in files:
            raise BridgeError('path is not in the analysis index')
        target = (root / path).resolve()
        if not target.is_relative_to(root):
            raise BridgeError('path escapes analysis export')
        if target.is_symlink() or digest(target) != files[path]:
            raise BridgeError('analysis file hash changed')
        with target.open('rb') as handle:
            handle.seek(offset)
            data = handle.read(min(limit * 1024, 65536))
            more = bool(handle.read(1))
        return {'path': path, 'text': data.decode('utf-8', 'replace'),
                'next_offset': offset + len(data) if more else None,
                'package_sha256': result['package_sha256']}
    names = sorted(files)
    matches, scanned = [], 0
    needle = query.encode('utf-8').lower()
    for file_index in range(offset, len(names)):
        name = names[file_index]
        target = (root / name).resolve()
        if not target.is_relative_to(root) or (root / name).is_symlink():
            raise BridgeError('analysis path escapes export or is linked')
        if not needle:
            matches.append({'path': name})
        else:
            with target.open('rb') as handle:
                position = scan_offset if file_index == offset else 0
                handle.seek(position)
                while True:
                    block = handle.read(65536)
                    if not block:
                        break
                    scanned += len(block)
                    index = block.lower().find(needle)
                    if index >= 0:
                        if digest(target) != files[name]:
                            raise BridgeError('analysis file hash changed')
                        matches.append({'path': name, 'byte_offset': position + index,
                                        'excerpt': block[max(0, index - 120):index + 380]
                                        .decode('utf-8', 'replace')})
                        break
                    if len(block) < 65536:
                        break
                    overlap = min(len(needle) - 1, len(block) - 1)
                    position += len(block) - overlap
                    handle.seek(position)
                    if scanned >= 32 * 1024 * 1024:
                        return {'matches': matches, 'next_offset': file_index,
                                'next_scan_offset': position,
                                'scan_limit_reached': True,
                                'package_sha256': result['package_sha256']}
        if len(matches) >= limit:
            return {'matches': matches,
                    'next_offset': file_index + 1 if file_index + 1 < len(names) else None,
                    'next_scan_offset': 0, 'package_sha256': result['package_sha256']}
    return {'matches': matches, 'next_offset': None, 'next_scan_offset': 0,
            'package_sha256': result['package_sha256']}


def analyze_package(capability, apk_path, output_dir):
    if capability not in CAPABILITIES:
        raise BridgeError(f'unsupported capability: {capability}')
    apk = Path(apk_path).expanduser().resolve(strict=True)
    if not apk.is_file() or apk.suffix.lower() != '.apk':
        raise BridgeError('supply one existing APK file')
    output = Path(output_dir).expanduser().absolute()
    package_sha = digest(apk)
    tool_version = version(capability)
    if output.exists():
        receipt, result = validate_export(output, capability)
        if (receipt['target']['package_sha256'] != package_sha
                or receipt['tool_version'] != tool_version):
            raise BridgeError('cached export belongs to another APK or tool version; use a new output directory')
        return {'receipt': receipt, 'export_dir': str(output), 'result': {key: value for key, value in result.items() if key != 'files'}, 'cached': True}
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.ditto-export-', dir=output.parent))
    try:
        finding = _analyze(capability, apk, stage)
        total = sum(path.stat().st_size for path in stage.rglob('*') if path.is_file())
        if total > MAX_EXPORT_BYTES:
            raise BridgeError('analysis export exceeds 512 MiB')
        request = {'capability': capability, 'package_sha256': package_sha,
                   'output_dir': str(output)}
        result = {'package_sha256': package_sha, 'finding': finding,
                  'tool_version': tool_version, 'export_bytes': total,
                  'files': {path.relative_to(stage).as_posix(): digest(path)
                            for path in sorted(stage.rglob('*')) if path.is_file()}}
        write_json(stage / 'result.json', result)
        receipt = {
            'schema_version': 1, 'capability': capability,
            'server': f'ditto-{capability}-mcp', 'tool': 'analyze_package',
            'tool_version': tool_version, 'session_id': SESSION_ID,
            'observed_at': datetime.now(timezone.utc).isoformat(),
            'request_sha256': digest_json(request),
            'response_sha256': digest_json(result), 'status': 'healthy',
            'limitations': finding.get('limitations', []), 'provenance': 'mcp',
            'target': {'package_sha256': package_sha},
        }
        if capability == 'r2flutter':
            receipt.update({'supported': True, 'abi': finding['abi'],
                            'dart_profile': finding['dart_profile']})
        write_json(stage / 'receipt.json', receipt)
        os.replace(stage, output)
        return {'receipt': receipt, 'export_dir': str(output), 'result': {key: value for key, value in result.items() if key != 'files'}}
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


class MobileController:
    """One emulator owner and one active phase export per MCP process."""

    def __init__(self, adb=None):
        self.adb = adb or sdk_tool('adb')
        self.lease = None
        self.serial = None
        self.target = None
        self.environment = None
        self.receipt = None
        self.stage = None
        self.preview_mode = False
        self.output = None
        self.package_sha = None
        self.package_name = None
        self.apk_path = None
        self.records = []
        self.actions = []
        self.replay_plan = []
        self._reset_timings()

    def _reset_timings(self):
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.started_clock = time.monotonic()
        self.command_timings = {}

    @contextmanager
    def _measure_command(self, args):
        kind = 'install' if args[0] == 'install' else {
            ('exec-out', 'screencap'): 'screenshot', ('shell', 'uiautomator'): 'hierarchy',
            ('shell', 'input'): 'input'}.get(args[:2], 'device_checks')
        if args == ('exec-out', 'cat', '/sdcard/ditto-hierarchy.xml'):
            kind = 'hierarchy'
        start = time.monotonic()
        try:
            yield
        finally:
            metric = self.command_timings.setdefault(kind, {'calls': 0, 'duration_ms': 0})
            metric['calls'] += 1
            metric['duration_ms'] += (time.monotonic() - start) * 1000

    def _adb(self, *args, timeout=60):
        if not self.serial:
            raise BridgeError('probe an emulator before using mobile control')
        with self._measure_command(args):
            return run([self.adb, '-s', self.serial, *map(str, args)], timeout=timeout)

    def _bytes(self, *args, timeout=60):
        try:
            with self._measure_command(args):
                result = subprocess.run([self.adb, '-s', self.serial, *map(str, args)],
                                        capture_output=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise BridgeError(f'ADB backend failed: {error}') from None
        if result.returncode != 0 or not result.stdout:
            raise BridgeError('ADB backend returned no capture: '
                              + result.stderr.decode('utf-8', 'replace')[-500:])
        return result.stdout

    def _identity(self, serial, target_id):
        if not serial.startswith('emulator-'):
            raise BridgeError('Ditto mobile-control currently requires an emulator')
        self.serial = serial
        devices = self._adb('get-state').strip()
        if devices != 'device':
            raise BridgeError(f'emulator {serial} is {devices or "unavailable"}')
        avd = self._adb('emu', 'avd', 'name').splitlines()[0].strip()
        if avd != target_id:
            raise BridgeError(f'emulator identity differs: expected {target_id}, found {avd}')
        self.target = {'kind': 'emulator', 'id': target_id}

    def _screen(self, timeout=60):
        shot = self._bytes('exec-out', 'screencap', '-p', timeout=timeout)
        if not shot.startswith(b'\x89PNG\r\n\x1a\n'):
            raise BridgeError('emulator screenshot is not PNG')
        return shot

    def _hierarchy(self, timeout=60):
        deadline = time.monotonic() + timeout
        dump = self._adb('shell', 'uiautomator', 'dump', '/sdcard/ditto-hierarchy.xml', timeout=timeout)
        if 'dumped to: /sdcard/ditto-hierarchy.xml' not in dump:
            raise BridgeError(f'emulator hierarchy dump failed: {dump.strip()[:200]}')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BridgeError('UI hierarchy timed out')
        xml = self._bytes('exec-out', 'cat', '/sdcard/ditto-hierarchy.xml', timeout=remaining)
        if b'<hierarchy' not in xml:
            raise BridgeError('emulator hierarchy export is invalid')
        return xml

    def inspect_ui(self, query='', limit=60, timeout=60):
        """Return bounded UI labels and bounds without sending another screenshot."""
        if self.stage is None and not self.preview_mode:
            raise BridgeError('begin capture or preview before inspecting UI')
        if not isinstance(query, str) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise BridgeError('UI query or limit is invalid')
        try:
            root = ET.fromstring(self._hierarchy(timeout=timeout))
        except ET.ParseError as error:
            raise BridgeError(f'emulator hierarchy is malformed: {error}') from None
        nodes = []
        for element in root.iter('node'):
            attrs = element.attrib
            label = attrs.get('text', '') or attrs.get('content-desc', '')
            resource_id = attrs.get('resource-id', '')
            if not label and not resource_id:
                continue
            if query and not any(query.casefold() in value.casefold()
                                 for value in (label, resource_id)):
                continue
            bounds = [int(value) for value in re.findall(r'\d+', attrs.get('bounds', ''))]
            if len(bounds) != 4:
                continue
            nodes.append({'text': attrs.get('text', '')[:240],
                          'description': attrs.get('content-desc', '')[:240],
                          'resource_id': resource_id[:240],
                          'bounds': bounds, 'clickable': attrs.get('clickable') == 'true',
                          'package': attrs.get('package', '')})
            if len(nodes) >= limit:
                break
        return {'nodes': nodes, 'truncated': len(nodes) >= limit}

    def _target(self, selector, match='exact', timeout=60):
        if not isinstance(selector, str) or not selector.strip() or match not in ('exact', 'contains'):
            raise BridgeError('target selector or match mode is invalid')
        nodes = self.inspect_ui(selector, limit=100, timeout=timeout)['nodes']
        def selected(node):
            values = (node['text'], node['description'], node['resource_id'])
            return any((selector.casefold() == value.casefold() if match == 'exact'
                        else selector.casefold() in value.casefold()) for value in values if value)
        matches = [node for node in nodes if selected(node)]
        clickable = [node for node in matches if node['clickable']]
        matches = clickable or matches
        unique = {tuple(node['bounds']): node for node in matches}
        if not unique:
            raise BridgeError(f'UI target not found: {selector}')
        if len(unique) != 1:
            raise BridgeError(f'UI target is ambiguous: {selector} ({len(unique)} matches)')
        return next(iter(unique.values()))

    def _wait_target(self, selector, match, duration_ms):
        if not isinstance(duration_ms, int) or not 0 < duration_ms <= 30000:
            raise BridgeError('target wait must be 1..30000 ms')
        deadline = time.monotonic() + duration_ms / 1000
        while True:
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BridgeError(f'UI target wait timed out: {selector}')
                return self._target(selector, match, timeout=remaining)
            except BridgeError as error:
                if 'not found' not in str(error) or time.monotonic() >= deadline:
                    raise
                time.sleep(min(0.5, max(0, deadline - time.monotonic())))

    def _environment(self):
        viewport = self._adb('shell', 'wm', 'size').strip().split(':')[-1].strip()
        density = self._adb('shell', 'wm', 'density').strip().split(':')[-1].strip()
        api = self._adb('shell', 'getprop', 'ro.build.version.sdk').strip()
        locale = self._adb('shell', 'getprop', 'persist.sys.locale').strip()
        if not locale:
            locale = self._adb('shell', 'getprop', 'ro.product.locale').strip()
        if not viewport or not density or not api or not locale:
            raise BridgeError('emulator did not report environment facts')
        font_scale = self._adb('shell', 'settings', 'get', 'system', 'font_scale').strip()
        if font_scale == 'null':
            font_scale = '1.0'
        theme = self._adb('shell', 'cmd', 'uimode', 'night').strip()
        display = self._adb('shell', 'dumpsys', 'input')
        rotation = re.search(r'SurfaceOrientation: (\d+)', display)
        if not rotation:
            display = self._adb('shell', 'dumpsys', 'window', 'displays')
            rotation = re.search(r'\bmRotation=(\d+)\b', display)
        if not font_scale or not theme or not rotation:
            raise BridgeError('emulator did not report font scale, theme, or orientation')
        renderer = self._adb('shell', 'getprop', 'ro.hardware.egl').strip()
        return {'viewport_px': viewport, 'density_dpi': int(density),
                'api_level': api, 'locale': locale, 'font_scale': font_scale,
                'theme': theme, 'orientation': int(rotation.group(1)),
                'renderer': renderer or 'unknown'}

    def _assert_app_focus(self, package_name, timeout=30):
        window = self._adb('shell', 'dumpsys', 'window', timeout=timeout)
        match = re.search(r'mCurrentFocus=([^\n]+)', window)
        focus = match.group(1) if match else ''
        if 'Application Not Responding' in focus or 'isn\'t responding' in focus:
            raise BridgeError(f'emulator has an ANR dialog: {focus[:200]}')
        if package_name not in focus:
            raise BridgeError(f'expected {package_name} in focused window; got {focus[:200]}')

    def _wait_for_app_focus(self, package_name, seconds=20):
        deadline = time.monotonic() + seconds
        while True:
            try:
                self._assert_app_focus(package_name, timeout=max(0.001, min(30, deadline - time.monotonic())))
                return
            except BridgeError as error:
                remaining = deadline - time.monotonic()
                if 'ANR dialog' in str(error) or remaining <= 0:
                    raise
                time.sleep(min(1, remaining))

    def _assert_installed_apk(self, package_name, expected_sha):
        paths = self._adb('shell', 'pm', 'path', package_name).splitlines()
        if len(paths) != 1 or not paths[0].startswith('package:/'):
            raise BridgeError('installed package does not have one base APK')
        installed = paths[0].removeprefix('package:').strip()
        observed = self._adb('shell', 'sha256sum', installed).split()[0]
        if observed != expected_sha:
            raise BridgeError('installed APK hash differs from supplied APK')

    def probe(self, serial, target_id, apk_path, package_name, output_dir):
        apk = Path(apk_path).expanduser().resolve(strict=True)
        if apk_package_name(apk) != package_name:
            raise BridgeError('requested package name differs from supplied APK')
        package_sha = digest(apk)
        self._identity(serial, target_id)
        self._ensure_installed(apk, package_name, package_sha)
        launched = self._adb('shell', 'monkey', '-p', package_name, '1')
        if 'Events injected: 1' not in launched:
            raise BridgeError('emulator could not launch the installed package')
        self._wait_for_app_focus(package_name)
        self._adb('shell', 'input', 'tap', '1', '1')
        self._adb('shell', 'input', 'text', 'D')
        self._adb('shell', 'input', 'swipe', '10', '10', '20', '20', '100')
        self._assert_app_focus(package_name)
        shot = self._screen()
        self._hierarchy()
        self._adb('shell', 'input', 'keyevent', '4')
        self.environment = self._environment()
        request = {'serial': serial, 'target_id': target_id,
                   'package_sha256': package_sha, 'package_name': package_name}
        response = {'environment': self.environment, 'screenshot_sha256':
                    hashlib.sha256(shot).hexdigest()}
        receipt = {
            'schema_version': 1, 'capability': 'mobile-control',
            'server': 'ditto-mobile-control-mcp', 'tool': 'mobile_control',
            'tool_version': BRIDGE_VERSION, 'session_id': SESSION_ID,
            'observed_at': datetime.now(timezone.utc).isoformat(),
            'request_sha256': digest_json(request),
            'response_sha256': digest_json(response), 'status': 'healthy',
            'limitations': ['Android emulator only; network and semantics capture unavailable',
                           'Input probes verify command execution, not semantic UI outcomes'],
            'provenance': 'mcp', 'target': self.target,
            'environment': self.environment,
            'probes': {name: True for name in (
                'launch', 'tap', 'type', 'swipe', 'back', 'screenshot', 'hierarchy')},
            'screenshot_sha256': response['screenshot_sha256'],
        }
        output = Path(output_dir).expanduser().absolute()
        if output.exists():
            raise BridgeError(f'probe output already exists: {output}')
        output.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='.ditto-probe-', dir=output.parent))
        try:
            write_json(stage / 'receipt.json', receipt)
            (stage / 'probe.png').write_bytes(shot)
            os.replace(stage, output)
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise
        self.receipt = receipt
        return {'receipt': receipt, 'export_dir': str(output)}

    def begin(self, serial, apk_path, package_name, output_dir):
        if not self.receipt or serial != self.serial:
            raise BridgeError('active mobile-control probe does not match this emulator')
        apk = Path(apk_path).expanduser().resolve(strict=True)
        if apk_package_name(apk) != package_name:
            raise BridgeError('requested package name differs from supplied APK')
        output = Path(output_dir).expanduser().absolute()
        if output.exists() or self.stage is not None or self.preview_mode:
            raise BridgeError('capture output exists or another capture is active')
        self._reset_timings()
        self._identity(serial, self.target['id'])
        self.environment = self._environment()
        package_sha = digest(apk)
        installed = self._ensure_installed(apk, package_name, package_sha)
        self._adb('shell', 'am', 'force-stop', package_name)
        launched = self._adb('shell', 'monkey', '-p', package_name, '1')
        if 'Events injected: 1' not in launched:
            raise BridgeError('installed package did not launch')
        self._wait_for_app_focus(package_name)
        output.parent.mkdir(parents=True, exist_ok=True)
        self.stage = Path(tempfile.mkdtemp(prefix='.ditto-capture-', dir=output.parent))
        self.output = output
        self.package_sha = package_sha
        self.package_name = package_name
        self.apk_path = apk
        self.records = []
        self.actions = []
        self.replay_plan = []
        return {'installed_package_sha256': self.package_sha,
                'target': self.target, 'session_id': SESSION_ID, 'installed': installed}

    def _ensure_installed(self, apk, package_name, package_sha):
        try:
            self._assert_installed_apk(package_name, package_sha)
            return False
        except BridgeError:
            self._adb('install', '-r', str(apk), timeout=180)
            self._assert_installed_apk(package_name, package_sha)
            return True

    def preview_begin(self, serial, target_id, package_name):
        """Control a running debug app without installation or evidence export."""
        if self.stage is not None or self.preview_mode:
            raise BridgeError('another mobile session is active')
        if not isinstance(package_name, str) or not re.fullmatch(
                r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+', package_name):
            raise BridgeError('preview needs a valid Android package name')
        self._identity(serial, target_id)
        installed = self._adb('shell', 'pm', 'path', package_name).strip()
        if not installed.startswith('package:/'):
            raise BridgeError('preview package is not installed on this emulator')
        self._assert_app_focus(package_name)
        self.package_name = package_name
        self.preview_mode = True
        self.actions = []
        return {'mode': 'preview', 'target': self.target,
                'evidence_eligible': False}

    def perform(self, action, x=None, y=None, end_x=None, end_y=None,
                text=None, duration_ms=300, step=None, selector=None, match='exact',
                target_timeout_ms=5000):
        if self.stage is None and not self.preview_mode:
            raise BridgeError('begin capture or preview before UI actions')
        if step is not None and (not isinstance(step, str) or not step.strip()):
            raise BridgeError('protocol step must be a nonempty string')
        start = time.monotonic()
        started_at = datetime.now(timezone.utc).isoformat()
        arguments = {}
        if action == 'tap' and x is not None and y is not None:
            arguments = {'x': int(x), 'y': int(y)}
            self._adb('shell', 'input', 'tap', int(x), int(y))
        elif action == 'tap_target':
            target = self._wait_target(selector, match, target_timeout_ms)
            left, top, right, bottom = target['bounds']
            self._adb('shell', 'input', 'tap', (left + right) // 2, (top + bottom) // 2)
            arguments = {'selector': selector, 'match': match, 'bounds': target['bounds'],
                         'target_timeout_ms': target_timeout_ms}
        elif action == 'wait_target':
            target = self._wait_target(selector, match, duration_ms)
            arguments = {'selector': selector, 'match': match, 'bounds': target['bounds'],
                         'duration_ms': duration_ms}
        elif action == 'type' and isinstance(text, str) and text:
            if not re.fullmatch(r'[A-Za-z0-9 @.,_+:/=-]+', text):
                raise BridgeError('ADB text input supports simple ASCII fixtures; this text needs a Unicode input backend')
            arguments = {'text': text}
            self._adb('shell', 'input', 'text', text.replace(' ', '%s'))
        elif action == 'swipe' and None not in (x, y, end_x, end_y):
            if not 0 < duration_ms <= 30000:
                raise BridgeError('swipe duration must be 1..30000 ms')
            arguments = {'x': int(x), 'y': int(y), 'end_x': int(end_x),
                         'end_y': int(end_y), 'duration_ms': int(duration_ms)}
            self._adb('shell', 'input', 'swipe', int(x), int(y), int(end_x),
                      int(end_y), int(duration_ms))
        elif action == 'back':
            self._adb('shell', 'input', 'keyevent', '4')
        elif action in ('stop', 'restart', 'reset'):
            self._adb('shell', 'am', 'force-stop', self.package_name)
            if action == 'reset':
                result = self._adb('shell', 'pm', 'clear', self.package_name)
                if result.strip() != 'Success':
                    raise BridgeError('fixture reset failed')
            if action != 'stop':
                self._adb('shell', 'monkey', '-p', self.package_name, '1')
                self._wait_for_app_focus(self.package_name)
        elif action == 'launch':
            launched = self._adb('shell', 'monkey', '-p', self.package_name, '1')
            if 'Events injected: 1' not in launched:
                raise BridgeError('installed package did not launch')
        elif action == 'wait' and 0 < duration_ms <= 30000:
            arguments = {'duration_ms': int(duration_ms)}
            time.sleep(duration_ms / 1000)
            self._assert_app_focus(self.package_name)
        else:
            raise BridgeError('unsupported or incomplete UI action')
        event = {'action': action, 'arguments': arguments, 'step': step,
                 'result': 'command_executed',
                 'started_at': started_at, 'duration_ms': round((time.monotonic() - start) * 1000, 2),
                 'at': datetime.now(timezone.utc).isoformat()}
        if self.stage is not None:
            self.actions.append(event)
        return event

    def replay(self, steps):
        if not isinstance(steps, list) or not 1 <= len(steps) <= 100:
            raise BridgeError('replay requires 1..100 action objects')
        allowed = {'action', 'x', 'y', 'end_x', 'end_y', 'text', 'duration_ms',
                   'step', 'selector', 'match', 'target_timeout_ms'}
        if any(not isinstance(item, dict) or 'action' not in item or set(item) - allowed
               for item in steps):
            raise BridgeError('replay contains an invalid action object')
        # Stop on the first execution failure. Inspect before retrying partial replay.
        for index, item in enumerate(steps):
            try:
                self.perform(**item)
            except Exception as error:
                raise BridgeError(f'replay stopped at step {index + 1}: {error}') from error
        return {'executed': len(steps), 'observation_required': True}

    def run_checkpoints(self, plan=None, plan_path=None):
        """Replay verified steps and retain completed checkpoints if a later step fails."""
        if self.stage is None:
            raise BridgeError('begin capture before running checkpoints')
        if plan_path:
            if plan is not None:
                raise BridgeError('supply plan or plan_path, not both')
            saved = json.loads(Path(plan_path).expanduser().read_text(encoding='utf-8'))
            replay = saved.get('replay', saved) if isinstance(saved, dict) else None
            if not isinstance(replay, dict):
                raise BridgeError('saved replay must be an object containing plan')
            plan = replay.get('plan')
            if not isinstance(plan, list) or any(not isinstance(item, dict)
                    or not isinstance(item.get('expect'), str) or not item['expect'].strip() for item in plan):
                raise BridgeError('saved replay needs a reviewed unique expect marker for every checkpoint')
        if not isinstance(plan, list) or not 1 <= len(plan) <= 100:
            raise BridgeError('checkpoint plan requires 1..100 entries')
        completed = []
        for item in plan:
            if not isinstance(item, dict) or not isinstance(item.get('checkpoint'), dict):
                raise BridgeError('checkpoint plan entry is invalid')
            checkpoint = item['checkpoint']
            identifier = checkpoint.get('checkpoint_id')
            try:
                if item.get('steps'):
                    self.replay(item['steps'])
                if 'expect' in item:
                    self._wait_target(item['expect'], item.get('match', 'exact'),
                                      item.get('expect_timeout_ms', 5000))
                self.capture(**checkpoint)
                if 'expect' in item:
                    self.replay_plan[-1].update({key: item[key] for key in
                        ('expect', 'match', 'expect_timeout_ms') if key in item})
            except (BridgeError, TypeError) as error:
                return {'completed': completed, 'stopped_at': identifier,
                        'error': str(error), 'capture_active': self.stage is not None}
            completed.append(identifier)
        return {'completed': completed, 'stopped_at': None, 'capture_active': True}

    def recover(self):
        """Resume after device replacement within the same MCP process."""
        if self.stage is None or self.apk_path is None:
            raise BridgeError('no active capture to recover')
        self._identity(self.serial, self.target['id'])
        if self._environment() != self.environment:
            raise BridgeError('replacement emulator environment differs from capture')
        self._ensure_installed(self.apk_path, self.package_name, self.package_sha)
        self._adb('shell', 'am', 'force-stop', self.package_name)
        self._adb('shell', 'monkey', '-p', self.package_name, '1')
        self.actions = []
        return {'completed': list(dict.fromkeys(record['checkpoint_id'] for record in self.records)),
                'replay_required': True, 'capture_active': True}

    def capture(self, number, checkpoint_id, fixture, setup, actions, kinds,
                observed_state=None):
        if self.stage is None:
            raise BridgeError('begin capture before checkpoint capture')
        if (not isinstance(number, int) or not 1 <= number <= 999
                or not isinstance(checkpoint_id, str)
                or not re.fullmatch(r'[a-z0-9][a-z0-9_]*', checkpoint_id)):
            raise BridgeError('checkpoint number or ID is invalid')
        if (not isinstance(fixture, str) or not fixture
                or not isinstance(setup, str)
                or not isinstance(actions, list)
                or any(not isinstance(item, str) for item in actions)):
            raise BridgeError('fixture, setup, or action protocol is invalid')
        if not isinstance(kinds, list) or not kinds or 'png' not in kinds:
            raise BridgeError('checkpoint needs declared PNG artifact')
        if len(kinds) != len(set(kinds)):
            raise BridgeError('evidence kinds must be unique')
        if any(kind not in ('png', 'xml', 'trace', 'state') for kind in kinds):
            raise BridgeError('requested evidence kind has no controller backend')
        executed = [event['step'] for event in self.actions if event.get('step')]
        if actions != executed:
            raise BridgeError('declared action protocol differs from executed steps')
        if not isinstance(observed_state, str) or not observed_state.strip():
            raise BridgeError('capture needs an observed_state description to review against the image')
        if self._environment() != self.environment:
            raise BridgeError('capture environment changed; begin a new session and restore matching settings')
        if 'trace' in kinds and not self.actions:
            raise BridgeError('behavior trace requires successful UI actions')
        self._assert_app_focus(self.package_name)
        stem = f'{number:03d}_{checkpoint_id}'
        if any(record['checkpoint_id'] == checkpoint_id for record in self.records):
            raise BridgeError('checkpoint was already captured in this export')
        data = {'png': self._screen()}
        if 'xml' in kinds:
            data['xml'] = self._hierarchy()
        if 'trace' in kinds:
            data['trace'] = (json.dumps({'actions': self.actions, 'observed_state': observed_state,
                                        'observation_source': 'agent',
                                        'verify_against': 'PNG/XML'}, indent=2)
                             + '\n').encode()
        if 'state' in kinds:
            state = {'limitation': 'foreground activity only; persistence needs before/after restart evidence',
                     'foreground': self._adb('shell', 'dumpsys', 'activity',
                                             'activities')[-10000:]}
            data['state'] = (json.dumps(state, indent=2) + '\n').encode()
        records = []
        for kind in kinds:
            name = f'{stem}.{kind}'
            target = self.stage / name
            target.write_bytes(data[kind])
            records.append({
                'checkpoint_id': checkpoint_id, 'kind': kind, 'path': name,
                'sha256': digest(target), 'server': self.receipt['server'],
                'tool': self.receipt['tool'], 'session_id': SESSION_ID,
                'target': self.target, 'action_result': 'success',
                'provenance': 'mcp', 'installed_package_sha256': self.package_sha,
                'fixture': fixture, 'setup_sha256': digest_json(setup),
                'actions_sha256': digest_json(executed),
                'executed_actions': list(self.actions), 'observed_state': observed_state,
                'observation_source': 'agent',
            })
        self.records.extend(records)
        steps = []
        for event in self.actions:
            step = {'action': event['action'], **{key: value for key, value in
                    event['arguments'].items() if key != 'bounds'}}
            if event.get('step'):
                step['step'] = event['step']
            steps.append(step)
        self.replay_plan.append({'steps': steps, 'checkpoint': {
            'number': number, 'checkpoint_id': checkpoint_id, 'fixture': fixture,
            'setup': setup, 'actions': actions, 'kinds': kinds, 'observed_state': observed_state}})
        self.actions = []
        return {'checkpoint_id': checkpoint_id,
                'artifacts': [{key: record[key] for key in ('kind', 'path', 'sha256')}
                              for record in records]}

    def acquire_device(self, serial):
        if self.lease is not None:
            raise BridgeError('controller already owns a capture; finalize or abort it first')
        lease = DeviceLease(serial)
        try:
            lease.acquire()
        except RuntimeError as error:
            raise BridgeError(str(error)) from None
        self.lease = lease

    def release_device(self):
        if self.lease is not None:
            self.lease.release()
            self.lease = None

    def abort(self):
        if self.stage is not None:
            shutil.rmtree(self.stage, ignore_errors=True)
        self.stage = self.output = None
        self.preview_mode = False
        self.records, self.actions = [], []
        self.replay_plan = []
        self.release_device()
        return {'aborted': True}

    def finalize(self):
        if self.stage is None or not self.records:
            raise BridgeError('no active captured checkpoints to finalize')
        wall_ms = (time.monotonic() - self.started_clock) * 1000
        adb_ms = sum(item['duration_ms'] for item in self.command_timings.values())
        capture = {
            'schema_version': 1, 'provenance': 'mcp',
            'server': self.receipt['server'], 'tool': self.receipt['tool'],
            'session_id': SESSION_ID, 'target': self.target,
            'environment': self.environment,
            'limitations': self.receipt['limitations'],
            'installed_package_sha256': self.package_sha,
            'captured_at': datetime.now(timezone.utc).isoformat(),
            'artifacts': self.records,
            'replay': {'status': 'candidate', 'plan': self.replay_plan},
            'timings': {'started_at': self.started_at, 'wall_ms': round(wall_ms, 2),
                        'adb_ms': round(adb_ms, 2), 'other_ms': round(max(0, wall_ms - adb_ms), 2),
                        'commands': self.command_timings,
                        'checkpoint_ids': list(dict.fromkeys(item['checkpoint_id'] for item in self.records))},
        }
        write_json(self.stage / 'capture.json', capture)
        os.replace(self.stage, self.output)
        output = str(self.output)
        self.stage = self.output = None
        self.records = []
        self.actions = []
        self.replay_plan = []
        return {'export_dir': output, 'checkpoint_count': len({
            record['checkpoint_id'] for record in capture['artifacts']}),
            'capture_sha256': digest(Path(output) / 'capture.json')}


MOBILE = MobileController()
