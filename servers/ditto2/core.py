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


def _binary(name, overrides=None):
    chosen = (overrides or {}).get(name, name)
    found = shutil.which(str(chosen))
    if found is None and str(chosen) == name:
        found = shutil.which(str(Path(sys.executable).parent / name))
    if found is None:
        raise Ditto2Error(f'{name} executable is missing; install it on PATH')
    return found


def _run(command, log_path, timeout, stdout_path=None, accepted_codes=(0,)):
    with Path(log_path).open('wb') as log:
        with (Path(stdout_path).open('wb') if stdout_path else nullcontext(log)) as output:
            with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output,
                                  stderr=log, start_new_session=True) as process:
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


def analyze_apk(apk_path, output_dir, binaries=None):
    """Keep all three exports and mark usable partial JADX output for review."""
    apk = _apk(apk_path)
    apk_sha = _sha256(apk)
    with _export(output_dir) as root:
        snapshot = _arm64_snapshot(apk, root)
        programs = {name: _binary(name, binaries) for name in ('apktool', 'jadx', 'r2flutter')}
        _run([programs['apktool'], 'd', '-s', str(apk), '-o', str(root / 'apktool')],
             root / 'apktool.log', 900)
        if not (root / 'apktool').is_dir():
            raise Ditto2Error('Apktool exited without producing an export')
        jadx_code = _run([programs['jadx'], '-d', str(root / 'jadx'), str(apk)],
                         root / 'jadx.log', 1800, accepted_codes=(0, 3))
        if not (root / 'jadx').is_dir():
            raise Ditto2Error('JADX exited without producing an export')
        (root / 'r2flutter').mkdir()
        for flag, name in (('-H', 'header'), ('-f', 'functions'), ('-z', 'strings')):
            path = root / 'r2flutter' / f'{name}.json'
            _run([programs['r2flutter'], '-j', flag, str(snapshot)],
                 path.with_suffix('.log'), 600, stdout_path=path)
            try:
                data = _json(path)
                if not isinstance(data, (dict, list)):
                    raise Ditto2Error('expected an object or array')
            except Ditto2Error as error:
                raise Ditto2Error(f'invalid r2Flutter JSON in {path}: {error}') from error
        if _sha256(apk) != apk_sha:
            raise Ditto2Error('APK changed during analysis')
        result = {'schema_version': 1, 'apk_sha256': apk_sha, 'apk_path': str(apk),
                  'tools': {name: {'status': 'ok', 'path': name} for name in programs}}
        if jadx_code == 3:
            result['tools']['jadx'].update(status='partial', exit_code=jadx_code)
        (root / 'analysis.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return {**result, 'output_dir': str(root)}


def explore_apk(apk_path, output_dir, serial, count=100, timeout=600, binary='droidbot', adb_binary='adb'):
    """Explore one device, preserving the app and all recorded artifacts."""
    apk = _apk(apk_path)
    apk_sha = _sha256(apk)
    if not serial or not serial.strip():
        raise Ditto2Error('device serial is required; inspect adb devices')
    if not 1 <= count <= 1000 or not 30 <= timeout <= 3600:
        raise Ditto2Error('count must be 1..1000 and timeout 30..3600 seconds')
    program = _binary('droidbot', {'droidbot': binary})
    adb = _binary('adb', {'adb': adb_binary})
    with _export(output_dir) as root:
        _run([adb, '-s', serial, 'install', '-r', str(apk)], root / 'adb.log', 180)
        command = [program, '-a', str(apk), '-d', serial, '-o', str(root), '-keep_app',
                   '-count', str(count), '-timeout', str(timeout)]
        if 'emulator' in serial.casefold():
            command.append('-is_emulator')
        _run(command, root / 'droidbot.log', timeout + 60)
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
    return {'apk_sha256': apk_sha, 'device_serial': serial, 'output_dir': str(root),
            'screens': len(graph['nodes']), 'transitions': len(graph['edges'])}


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


def unify_evidence(apk_path, evidence_dir, confirmed_screens, confirmed_events, findings, gaps):
    """Index reviewed relationships within the full analysis/ and exploration/ collection."""
    apk = _apk(apk_path)
    apk_sha = _sha256(apk)
    root = Path(evidence_dir).expanduser().resolve()
    if not root.is_dir():
        raise Ditto2Error('evidence_dir must contain analysis/ and exploration/ exports')
    output = root / 'review.json'
    if os.path.lexists(output):
        raise Ditto2Error(f'output already exists: {output}; use a new evidence directory for another review')
    analysis, exploration = root / 'analysis', root / 'exploration'
    for export in (analysis, exploration):
        if export.is_symlink() or not export.is_dir() or (export / '.incomplete').exists():
            raise Ditto2Error(f'export is missing, linked, or incomplete: {export}')
    manifest = _json(_inside(analysis, 'analysis.json'))
    graph = _read_utg(exploration)
    if not isinstance(manifest, dict) or manifest.get('apk_sha256') != apk_sha or graph.get('app_sha256') != apk_sha:
        raise Ditto2Error('APK hash differs between analysis, exploration, and supplied APK')
    static = {}
    for name in ('apktool', 'jadx', 'r2flutter'):
        tool = manifest.get('tools', {}).get(name, {})
        if tool.get('status') not in ('ok', 'partial') or tool.get('path') != name or not (analysis / name).is_dir() or (analysis / name).is_symlink():
            raise Ditto2Error(f'{name} analysis is missing or incomplete')
        static[name] = {'path': f'analysis/{name}'}
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
        path = _inside(analysis / finding.analyzer, finding.path)
        if set(finding.screens) - confirmed_screens or set(finding.events) - confirmed_events:
            raise Ditto2Error('finding references must point to confirmed screens and events')
        linked_findings.append({**finding.model_dump(exclude={'path'}),
                                'source': str(path.relative_to(root)), 'source_sha256': _sha256(path)})
    gaps = [gap.strip() for gap in gaps]
    for name, tool in manifest['tools'].items():
        if tool.get('status') == 'partial':
            gaps.append(f'{name.upper()} export is partial; inspect analysis/{name}.log')
    result = {'schema_version': 2, 'apk_sha256': apk_sha, 'source_apk': str(apk),
              'static': static, 'runtime': {'source': 'DroidBot', 'path': 'exploration',
              'device_serial': graph.get('device_serial'), 'package': graph.get('app_package'),
              'graph': 'exploration/utg.js'},
              'candidate_counts': {'screens': len(nodes), 'events': len(events)},
              'screens': screens, 'transitions': transitions, 'findings': linked_findings,
              'gaps': gaps}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=root, delete=False) as file:
            temporary = Path(file.name)
            json.dump(result, file, indent=2)
            file.write('\n')
        os.link(temporary, output)  # Atomic publication that cannot replace an existing file.
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return {'evidence_dir': str(root), 'review_index': str(output),
            'analysis_dir': str(analysis), 'exploration_dir': str(exploration),
            'reviewed_screens': len(screens), 'reviewed_transitions': len(transitions),
            'findings': len(linked_findings), 'gaps': result['gaps']}
