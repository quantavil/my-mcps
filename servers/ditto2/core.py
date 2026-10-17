"""Collect and connect APK evidence without discarding the underlying exports."""

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import zipfile
from contextlib import contextmanager, nullcontext, suppress
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from inputs import describe_input, verify_input_files, compare_inputs


class Ditto2Error(ValueError):
    pass


class Finding(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    claim: str = Field(min_length=1)
    analyzer: Literal['apktool', 'jadx', 'r2flutter']
    path: str = Field(min_length=1, description='File path relative to the analyzer export')
    screens: list[str] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)


def _sha256(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def _json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise Ditto2Error(f'invalid JSON in {path}: {error}') from error


def _apk(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != '.apk' or not zipfile.is_zipfile(path):
        raise Ditto2Error(f'APK is missing or is not a ZIP archive: {path}')
    return path


def _script(path):
    if path is None:
        return None
    if not isinstance(path, (str, Path)) or not str(path).strip():
        raise Ditto2Error('script path must be a valid file path')
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise Ditto2Error(f'script file is missing: {path}')
    data = _json(path)
    if not isinstance(data, dict):
        raise Ditto2Error(f'script must be a JSON object: {path}')
    for section in ('views', 'states', 'operations', 'main'):
        if not isinstance(data.get(section), dict):
            raise Ditto2Error(f'script requires a {section} object: {path}')
    return path


def _binary(name, overrides=None):
    chosen = (overrides or {}).get(name, name)
    found = shutil.which(str(chosen))
    if found is None and str(chosen) == name:
        found = shutil.which(str(Path(sys.executable).parent / name))
    if found is None and str(chosen) == name and name == 'adb':
        for sdk in (os.environ.get('ANDROID_HOME'), os.environ.get('ANDROID_SDK_ROOT'),
                    Path.home() / 'Android' / 'Sdk'):
            if sdk:
                found = shutil.which(str(Path(sdk) / 'platform-tools' / 'adb'))
                if found:
                    break
    if found is None:
        raise Ditto2Error(f'{name} executable is missing; install it on PATH')
    return found


def _run(command, log_path, timeout, stdout_path=None, accepted_codes=(0,), env=None):
    with Path(log_path).open('wb') as log:
        with (Path(stdout_path).open('wb') if stdout_path else nullcontext(log)) as output:
            with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output,
                                  stderr=log, start_new_session=True, env=env) as process:
                try:
                    code = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired as error:
                    raise Ditto2Error(f'{Path(command[0]).name} timed out after {timeout}s; see {log_path}') from error
                finally:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
    if code not in accepted_codes:
        raise Ditto2Error(f'{Path(command[0]).name} exited {code}; see {log_path}')
    return code


@contextmanager
def _export(destination):
    root = Path(destination).expanduser().absolute()
    root.mkdir(parents=True, exist_ok=False)
    marker = root / '.incomplete'
    marker.write_text('in progress\n', encoding='utf-8')
    try:
        yield root
    except Exception as error:
        marker.write_text(str(error) + '\n', encoding='utf-8')
        raise Ditto2Error(f'{error}; partial evidence retained in {root}') from error
    else:
        marker.unlink()


def _arm64_snapshot(apk, root):
    member = 'lib/arm64-v8a/libapp.so'
    with zipfile.ZipFile(apk) as archive:
        try:
            info = archive.getinfo(member)
        except KeyError as error:
            raise Ditto2Error(f'APK has no {member}; r2Flutter requires an ARM64 AOT snapshot') from error
        mode = (info.external_attr >> 16) & 0o170000
        if info.is_dir() or info.flag_bits & 1 or mode == 0o120000 or info.file_size > 512 * 1024 * 1024:
            raise Ditto2Error(f'unsafe or oversized {member}')
        target = root / 'libapp.so'
        with archive.open(info) as source, target.open('wb') as output:
            shutil.copyfileobj(source, output)
    return target


def analyze_apk(apk_path, output_dir, binaries=None, split_paths=None):
    """Finalize independently attempted analyzers; partial results remain reviewable."""
    apk = _apk(apk_path)
    with _export(output_dir) as root:
        descriptor = describe_input(apk, split_paths)
        artifacts = descriptor['artifacts']
        paths = [item['path'] for item in artifacts]
        tools = {}
        for name in ('apktool', 'jadx', 'r2flutter'):
            tool = {'status': 'failed', 'log': f'{name}.log'}
            tools[name] = tool
            if name == 'r2flutter':
                snapshots = []
                for path in paths:
                    with zipfile.ZipFile(path) as archive:
                        if 'lib/arm64-v8a/libapp.so' in archive.namelist():
                            snapshots.append(path)
                if not snapshots:
                    tool.update(status='unsupported_abi', error='No lib/arm64-v8a/libapp.so in selected input')
                    (root / tool['log']).write_text(tool['error'] + '\n')
                    continue
                if len(snapshots) != 1:
                    tool['error'] = 'Ambiguous ARM64 snapshots in selected input'
                    (root / tool['log']).write_text(tool['error'] + '\n')
                    continue
            try:
                program = _binary(name, binaries)
            except Ditto2Error as error:
                tool.update(status='missing_tool', error=str(error))
                (root / tool['log']).write_text(str(error) + '\n')
                continue
            try:
                if name == 'apktool':
                    (root / name).mkdir()
                    usable, failures = [], []
                    for artifact in artifacts:
                        relative = f'apktool/{artifact["id"]}'
                        log = root / ('apktool.log' if artifact['id'] == 'base' else f'apktool-{artifact["id"]}.log')
                        try:
                            _run([program, 'd', '-s', artifact['path'], '-o', str(root / relative)], log, 900)
                            if not (root / relative).is_dir():
                                raise Ditto2Error('Apktool exited without producing an export')
                            usable.append(relative)
                        except Ditto2Error as error:
                            failures.append(str(error))
                    tool['usable_paths'] = usable
                    if failures:
                        tool['error'] = '; '.join(failures)
                    if not usable:
                        continue
                    tool.update(status='partial' if failures else 'ok', path=name)
                elif name == 'jadx':
                    code = _run([program, '-d', str(root / name), *paths], root / tool['log'], 1800, accepted_codes=(0, 3))
                    if not (root / name).is_dir():
                        raise Ditto2Error('JADX exited without producing an export')
                    tool.update(status='partial' if code == 3 else 'ok', path=name)
                    if code == 3:
                        fallback = {'status': 'failed', 'mode': 'fallback', 'log': 'jadx-fallback.log'}
                        tool.update(exit_code=code, fallback=fallback)
                        try:
                            fallback_code = _run([program, '-m', 'fallback', '--no-res', '-d', str(root / name / 'fallback'), *paths], root / fallback['log'], 1800, accepted_codes=(0, 3))
                            if (root / name / 'fallback').is_dir():
                                tool['fallback'] = {'status': 'ok' if fallback_code == 0 else 'partial', 'path': 'jadx/fallback', 'mode': 'fallback'}
                        except Ditto2Error as error:
                            fallback['error'] = str(error)
                else:
                    snapshot = _arm64_snapshot(snapshots[0], root)
                    (root / name).mkdir()
                    for flag, export in (('-H', 'header'), ('-f', 'functions'), ('-z', 'strings')):
                        path = root / name / f'{export}.json'
                        _run([program, '-j', flag, str(snapshot)], path.with_suffix('.log'), 600, stdout_path=path)
                        if not isinstance(_json(path), (dict, list)):
                            raise Ditto2Error(f'invalid r2Flutter JSON in {path}')
                    tool.update(status='ok', path=name)
                    (root / tool['log']).write_text('Completed header, functions and strings; see r2flutter/*.log\n')
            except Ditto2Error as error:
                tool.update(status='failed', error=str(error))
                tool.pop('path', None)
                if not (root / tool['log']).exists():
                    (root / tool['log']).write_text(str(error) + '\n')
        verify_input_files(descriptor)
        statuses = [item['status'] for item in tools.values()]
        status = ('complete' if all(s == 'ok' for s in statuses) else
                  'partial' if any(s in ('ok', 'partial') for s in statuses) else 'failed')
        result = {'schema_version': 2, 'apk_sha256': descriptor['artifacts'][0]['sha256'],
                  'apk_path': str(apk), 'input': descriptor, 'tools': tools, 'collection_status': status}
        (root / 'analysis.json').write_text(json.dumps(result, indent=2) + '\n')
    return {**result, 'output_dir': str(root)}


def _adb_read(adb, serial, args, root, name):
    output = root / f'{name}.stdout'
    _run([adb, '-s', serial, *args], root / f'{name}.log', 120, stdout_path=output)
    return output.read_text().strip()


def _verify_installed(adb, serial, descriptor, root):
    paths = _adb_read(adb, serial, ['shell', 'pm', 'path', descriptor['package']], root, 'installed-paths').splitlines()
    if not paths or any(not line.startswith('package:') for line in paths):
        raise Ditto2Error('selected package is not installed or pm path returned invalid output')
    with tempfile.TemporaryDirectory(prefix='.verify-', dir=root) as directory:
        hashes = []
        for index, line in enumerate(paths):
            local = Path(directory) / f'{index}.apk'
            _run([adb, '-s', serial, 'pull', line.removeprefix('package:').strip(), str(local)], root / f'installed-pull-{index}.log', 120)
            hashes.append(_sha256(local))
    if sorted(hashes) != sorted(item['sha256'] for item in descriptor['artifacts']):
        raise Ditto2Error('installed APK set does not match the selected input; refusing session reuse')


def explore_apk(apk_path, output_dir, serial, count=100, timeout=600, script_path=None, binary='droidbot', adb_binary='adb', split_paths=None, install_mode='install'):
    """Explore one device, preserving the app and all recorded artifacts."""
    apk = _apk(apk_path)
    apk_sha = _sha256(apk)
    if not serial or not serial.strip():
        raise Ditto2Error('device serial is required; inspect adb devices')
    if not 1 <= count <= 1000 or not 30 <= timeout <= 3600:
        raise Ditto2Error('count must be 1..1000 and timeout 30..3600 seconds')
    program = _binary('droidbot', {'droidbot': binary})
    adb = _binary('adb', {'adb': adb_binary})
    script = _script(script_path)
    if install_mode not in ('install', 'reuse'):
        raise Ditto2Error('install_mode must be install or reuse')
    with _export(output_dir) as root:
        descriptor = describe_input(apk, split_paths)
        if descriptor['native_abis'] and 'x86_64' not in descriptor['native_abis']:
            raise Ditto2Error('exploration requires an x86_64-compatible input')
        abis = _adb_read(adb, serial, ['shell', 'getprop', 'ro.product.cpu.abilist'], root, 'device-abis').split(',')
        if 'x86_64' not in [abi.strip() for abi in abis]:
            raise Ditto2Error('selected device does not support x86_64')
        target_script = None
        if script is not None:
            target_script = root / 'script.json'
            if script != target_script:
                shutil.copyfile(script, target_script)
        if install_mode == 'reuse':
            _verify_installed(adb, serial, descriptor, root)
        else:
            selected = [item['path'] for item in descriptor['artifacts']]
            action = 'install-multiple' if len(selected) > 1 else 'install'
            _run([adb, '-s', serial, action, '-r', *selected], root / 'adb.log', 180)
        command = [program, '-a', str(apk), '-d', serial, '-o', str(root), '-keep_app',
                   '-count', str(count), '-timeout', str(timeout)]
        if target_script is not None:
            command.extend(['-script', str(target_script)])
        if 'emulator' in serial.casefold():
            command.append('-is_emulator')
        # DroidBot invokes adb by name internally; use the same SDK as installation.
        child_env = {**os.environ, 'PATH': str(Path(adb).parent) + os.pathsep + os.environ.get('PATH', '')}
        _run(command, root / 'droidbot.log', timeout + 60, env=child_env)
        graph = _read_utg(root)
        if graph.get('app_sha256') != apk_sha or _sha256(apk) != apk_sha:
            raise Ditto2Error('DroidBot output APK hash differs from the supplied APK')
        if graph.get('device_serial') != serial:
            raise Ditto2Error('DroidBot output device differs from the selected device')
        if not graph['nodes']:
            raise Ditto2Error('DroidBot produced no screens')
        for node in graph['nodes']:
            try:
                _inside(root, node['image'])
            except Ditto2Error as error:
                raise Ditto2Error(f'DroidBot screenshot is missing: {node["image"]}') from error
        verify_input_files(descriptor)
        (root / 'exploration.json').write_text(json.dumps({'schema_version': 1, 'input': descriptor,
            'device_serial': serial, 'install_mode': install_mode, 'collection_status': 'complete',
            'script': 'script.json' if target_script else None}, indent=2) + '\n')
    result = {'apk_sha256': apk_sha, 'device_serial': serial, 'output_dir': str(root),
              'screens': len(graph['nodes']), 'transitions': len(graph['edges'])}
    if target_script is not None:
        result['script'] = str(target_script.relative_to(root))
    return result


def _inside(root, relative):
    root = Path(root).resolve()
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise Ditto2Error(f'evidence path must be relative to its export: {relative}')
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise Ditto2Error(f'evidence file is missing or outside its export: {relative}')
    return candidate


def _read_utg(exploration):
    file = _inside(exploration, 'utg.js')
    if file.stat().st_size > 32 * 1024 * 1024:
        raise Ditto2Error(f'oversized DroidBot graph: {file}')
    source = file.read_text(encoding='utf-8').strip()
    prefix = 'var utg ='
    if not source.startswith(prefix):
        raise Ditto2Error('DroidBot graph has an unexpected format')
    try:
        graph = json.loads(source[len(prefix):].strip().rstrip(';'))
        if not isinstance(graph, dict) or not isinstance(graph['nodes'], list) or not isinstance(graph['edges'], list):
            raise ValueError('expected nodes and edges')
        ids = [node['id'] for node in graph['nodes']]
        if not all(isinstance(node['id'], str) and isinstance(node['image'], str) for node in graph['nodes']) or len(ids) != len(set(ids)):
            raise ValueError('invalid or duplicate nodes')
        for edge in graph['edges']:
            if edge['from'] not in ids or edge['to'] not in ids or not isinstance(edge['events'], list):
                raise ValueError('invalid edge')
            if not all(isinstance(event['event_str'], str) for event in edge['events']):
                raise ValueError('invalid action')
    except (ValueError, KeyError, TypeError) as error:
        raise Ditto2Error(f'invalid DroidBot graph: {file}') from error
    return graph


def inspect_exploration(exploration_dir, offset=0, limit=50):
    """Page through screenshot and event paths for review, including unreviewed data."""
    if offset < 0 or not 1 <= limit <= 100:
        raise Ditto2Error('offset must be nonnegative and limit must be 1..100')
    root = Path(exploration_dir).expanduser().resolve()
    graph = _read_utg(root)
    nodes = graph['nodes']
    events = sorted((root / 'events').glob('event_*.json'))
    return {'apk_sha256': graph.get('app_sha256'), 'device_serial': graph.get('device_serial'),
            'screens': [{'id': node['id'], 'label': node.get('label'),
                         'package': node.get('package'),
                         'screenshot': str(_inside(root, node['image']))}
                        for node in nodes[offset:offset + limit]],
            'events': [{'id': path.stem, 'path': str(_inside(root, str(path.relative_to(root))))}
                       for path in events[offset:offset + limit]],
            'total_screens': len(nodes), 'total_events': len(events),
            'next_offset': offset + limit if offset + limit < max(len(nodes), len(events)) else None}


_TEXT_SUFFIXES = frozenset({'.java', '.kt', '.smali', '.xml', '.json', '.txt', '.log',
                            '.properties', '.md', '.js', '.gradle', '.textproto', '.version', '.frag'})


def _tool_allows_path(name, tool, relative):
    if tool.get('status') not in ('ok', 'partial'):
        return False
    allowed = tool.get('usable_paths', [name])
    if not any(relative.startswith(prefix + '/') for prefix in allowed):
        return False
    if name == 'jadx' and tool.get('fallback', {}).get('status') == 'failed' and relative.startswith('jadx/fallback/'):
        return False
    return True


def search_analysis(analysis_dir, query, source='all', offset=0, scan_offset=0, limit=20):
    """Search saved analyzer text with bounded output and resumable byte offsets."""
    root = Path(analysis_dir).expanduser().resolve()
    if not root.is_dir() or (root / '.incomplete').exists() or not (root / 'analysis.json').is_file():
        raise Ditto2Error('analysis export is missing or incomplete')
    if source not in ('all', 'apktool', 'jadx', 'r2flutter'):
        raise Ditto2Error('source must be all, apktool, jadx, or r2flutter')
    if not isinstance(query, str) or not 1 <= len(query) <= 500:
        raise Ditto2Error('query must contain 1..500 characters')
    if not 0 <= offset <= 10_000_000 or not 0 <= scan_offset <= 1_000_000_000 or not 1 <= limit <= 100:
        raise Ditto2Error('offset and scan_offset must be nonnegative; limit must be 1..100')
    manifest = _json(root / 'analysis.json')
    files = sorted(path for path in root.rglob('*')
                   if path.is_file() and path.suffix.lower() in _TEXT_SUFFIXES
                   and (source == 'all' or path.relative_to(root).parts[0] == source)
                   and (manifest.get('schema_version') != 2 or
                        _tool_allows_path(path.relative_to(root).parts[0],
                            manifest['tools'].get(path.relative_to(root).parts[0], {}),
                            path.relative_to(root).as_posix()))
                   and not (path.relative_to(root).as_posix().startswith('jadx/fallback/') and
                            manifest.get('tools', {}).get('jadx', {}).get('fallback', {}).get('status') == 'failed'))
    needle = query.encode('utf-8').lower()
    matches = []
    scanned = 0
    budget = 16 * 1024 * 1024
    for index in range(offset, len(files)):
        path = files[index]
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise Ditto2Error(f'analysis contains a linked or escaping text file: {path}')
        position = scan_offset if index == offset else 0
        size = path.stat().st_size
        with path.open('rb') as file:
            while position < size:
                file.seek(position)
                block = file.read(min(65536, budget - scanned))
                if not block:
                    break
                scanned += len(block)
                match = block.lower().find(needle)
                if match >= 0:
                    at = position + match
                    matches.append({'path': path.relative_to(root).as_posix(),
                                    'byte_offset': at,
                                    'excerpt': block[max(0, match - 120):match + len(needle) + 240]
                                    .decode('utf-8', 'replace')})
                    position = at + len(needle)
                    if len(matches) >= limit:
                        next_index = index if position < size else index + 1
                        return {'matches': matches,
                                'next_offset': next_index if next_index < len(files) else None,
                                'next_scan_offset': position if next_index == index else 0,
                                'scanned_bytes': scanned}
                else:
                    position += max(1, len(block) - len(needle) + 1)
                if scanned >= budget:
                    return {'matches': matches, 'next_offset': index,
                            'next_scan_offset': position, 'scanned_bytes': scanned}
    return {'matches': matches, 'next_offset': None, 'next_scan_offset': 0,
            'scanned_bytes': scanned}


def unify_evidence(apk_path, evidence_dir, confirmed_screens, confirmed_events, findings, gaps, static_apk_path=None):
    """Index reviewed relationships within the full analysis/ and exploration/ collection."""
    apk = _apk(apk_path)
    apk_sha = _sha256(apk)
    root = Path(evidence_dir).expanduser().resolve()
    if not root.is_dir():
        raise Ditto2Error('evidence_dir must contain analysis/ and exploration/ exports')
    output = root / 'review.json'
    if os.path.lexists(root / 'inputs.json'):
        raise Ditto2Error('inputs.json already exists; use a fresh review directory')
    if os.path.lexists(output):
        raise Ditto2Error(f'output already exists: {output}; use a new evidence directory for another review')
    analysis, exploration = root / 'analysis', root / 'exploration'
    for export in (analysis, exploration):
        if export.is_symlink() or not export.is_dir() or (export / '.incomplete').exists():
            raise Ditto2Error(f'export is missing, linked, or incomplete: {export}')
    manifest = _json(_inside(analysis, 'analysis.json'))
    graph = _read_utg(exploration)
    pair = None
    comparison_gaps = []
    runtime_manifest = None
    if (exploration / 'exploration.json').exists():
        runtime_manifest = _json(_inside(exploration, 'exploration.json'))
        verify_input_files(runtime_manifest['input'])
        if runtime_manifest['input']['artifacts'][0]['sha256'] != apk_sha or runtime_manifest.get('device_serial') != graph.get('device_serial'):
            raise Ditto2Error('runtime descriptor differs from supplied APK or graph device')
    if manifest.get('schema_version') == 2:
        if runtime_manifest is None:
            raise Ditto2Error('modern analysis requires a finalized runtime descriptor')
        static_input, runtime_input = manifest['input'], runtime_manifest['input']
        verify_input_files(static_input)
        verify_input_files(runtime_input)
        if runtime_input['artifacts'][0]['sha256'] != apk_sha or graph.get('app_sha256') != apk_sha:
            raise Ditto2Error('runtime APK hash differs between descriptor, graph and supplied APK')
        static_apk = _apk(static_apk_path) if static_apk_path else Path(static_input['artifacts'][0]['path'])
        if _sha256(static_apk) != static_input['artifacts'][0]['sha256'] or manifest['apk_sha256'] != static_input['artifacts'][0]['sha256']:
            raise Ditto2Error('static APK hash differs from analysis input')
        if runtime_manifest.get('device_serial') != graph.get('device_serial'):
            raise Ditto2Error('runtime descriptor device differs from graph')
        comparison = compare_inputs(static_input, runtime_input)
        if not comparison['compatible']:
            raise Ditto2Error('incompatible deliveries: ' + '; '.join(comparison['errors']))
        comparison_gaps = comparison['gaps']
        pair = {'schema_version': 1, 'static': static_input, 'runtime': runtime_input,
                'comparison': comparison}
    elif runtime_manifest and len(runtime_manifest['input']['artifacts']) != 1:
        raise Ditto2Error('legacy static evidence cannot establish identity for a runtime split delivery')
    elif manifest.get('apk_sha256') != apk_sha or graph.get('app_sha256') != apk_sha or (static_apk_path and _sha256(_apk(static_apk_path)) != apk_sha):
        raise Ditto2Error('APK hash differs between analysis, exploration, and supplied APK; legacy exports cannot link different deliveries')
    static = {}
    for name in ('apktool', 'jadx', 'r2flutter'):
        tool = manifest.get('tools', {}).get(name, {})
        if tool.get('status') not in ('ok', 'partial'):
            if manifest.get('schema_version') == 2:
                continue
            raise Ditto2Error(f'{name} analysis is missing or incomplete')
        if tool.get('path') != name or not (analysis / name).is_dir() or (analysis / name).is_symlink():
            raise Ditto2Error(f'{name} usable analysis is missing or incomplete')
        static[name] = {'path': f'analysis/{name}', 'status': tool['status']}
    if not static:
        raise Ditto2Error('no usable static analysis to review')
    nodes = {node['id']: node for node in graph['nodes']}
    confirmed_screens, confirmed_events = set(confirmed_screens), set(confirmed_events)
    if not confirmed_screens:
        raise Ditto2Error('confirm at least one screen after viewing its screenshot')
    screens = []
    for screen_id in sorted(confirmed_screens):
        node = nodes.get(screen_id)
        if node is None:
            raise Ditto2Error(f'unknown screen ID: {screen_id}')
        screenshot = _inside(exploration, node['image'])
        screens.append({'id': screen_id, 'label': node.get('label'), 'package': node.get('package'),
                        'screenshot': str(screenshot.relative_to(root)), 'screenshot_sha256': _sha256(screenshot),
                        'activity': node.get('activity')})
    graph_actions = {(edge['from'], edge['to'], action['event_str'])
                     for edge in graph['edges'] for action in edge['events']}
    events = {path.stem: path for path in (exploration / 'events').glob('event_*.json')}
    transitions = []
    for event_id in sorted(confirmed_events):
        if event_id not in events:
            raise Ditto2Error(f'unknown event ID: {event_id}')
        path = _inside(exploration, str(events[event_id].relative_to(exploration)))
        event = _json(path)
        if not isinstance(event, dict) or not isinstance(event.get('event'), dict):
            raise Ditto2Error(f'invalid recorded action: {path}')
        source, target = event.get('start_state'), event.get('stop_state')
        if source not in confirmed_screens or target not in confirmed_screens:
            raise Ditto2Error(f'event {event_id} requires confirmed endpoints')
        if (source, target, event.get('event_str')) not in graph_actions:
            raise Ditto2Error(f'event {event_id} does not match a DroidBot graph edge')
        transitions.append({'id': event_id, 'from': source, 'to': target, 'action': event['event'],
                            'event_file': str(path.relative_to(root)), 'event_sha256': _sha256(path)})
    if not findings:
        raise Ditto2Error('unified evidence needs at least one cited static finding')
    if not isinstance(gaps, list) or any(not isinstance(gap, str) or not gap.strip() for gap in gaps):
        raise Ditto2Error('gaps must be a list of nonempty descriptions; use [] if none observed')
    linked_findings = []
    for item in findings:
        finding = Finding.model_validate(item)
        if finding.analyzer not in static:
            raise Ditto2Error('finding cites an unusable analyzer')
        tool = manifest['tools'][finding.analyzer]
        path = _inside(analysis / finding.analyzer, finding.path)
        relative = path.relative_to(analysis).as_posix()
        if not _tool_allows_path(finding.analyzer, tool, relative):
            raise Ditto2Error('finding cites an unusable artifact export')
        if set(finding.screens) - confirmed_screens or set(finding.events) - confirmed_events:
            raise Ditto2Error('finding references must point to confirmed screens and events')
        linked_findings.append({**finding.model_dump(exclude={'path'}),
                                'source': str(path.relative_to(root)), 'source_sha256': _sha256(path)})
    gaps = [gap.strip() for gap in gaps] + comparison_gaps
    for name, tool in manifest['tools'].items():
        if tool.get('status') not in ('ok', 'partial'):
            gaps.append(f'{name.upper()} status {tool.get("status", "missing")}: {tool.get("error", "no usable export")}; inspect analysis/{tool.get("log", name + ".log")}')
        if tool.get('status') == 'partial':
            gaps.append(f'{name.upper()} export is partial; inspect analysis/{name}.log')
            if tool.get('fallback', {}).get('status') == 'failed':
                gaps.append(f'{name.upper()} fallback failed; inspect analysis/{tool["fallback"]["log"]}')
    result = {'schema_version': 3 if pair else 2, 'apk_sha256': apk_sha, 'source_apk': str(apk),
              'static': static, 'runtime': {'source': 'DroidBot', 'path': 'exploration',
              'device_serial': graph.get('device_serial'), 'package': graph.get('app_package'),
              'graph': 'exploration/utg.js'},
              'candidate_counts': {'screens': len(nodes), 'events': len(events)},
              'screens': screens, 'transitions': transitions, 'findings': linked_findings,
              'gaps': gaps}
    if pair:
        result['inputs'] = {'manifest': 'inputs.json', 'static_input_id': pair['static']['input_id'], 'runtime_input_id': pair['runtime']['input_id']}
    published = []
    try:
        if pair:
            _publish_json(root / 'inputs.json', pair)
            published.append(root / 'inputs.json')
        _publish_json(output, result)
    except Exception:
        for path in published:
            path.unlink(missing_ok=True)
        raise
    return {'evidence_dir': str(root), 'review_index': str(output),
            'analysis_dir': str(analysis), 'exploration_dir': str(exploration),
            'reviewed_screens': len(screens), 'reviewed_transitions': len(transitions),
            'findings': len(linked_findings), 'gaps': result['gaps']}


def _publish_json(output, data):
    """Publish exclusively without overwriting existing evidence or indexes."""
    output = Path(output).expanduser().absolute()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=output.parent, delete=False) as file:
            temporary = Path(file.name)
            json.dump(data, file, indent=2)
            file.write('\n')
        os.link(temporary, output)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
