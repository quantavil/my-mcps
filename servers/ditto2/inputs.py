"""Validate explicit, original APK deliveries and compare their provenance."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from collections import defaultdict

ANDROID = '{http://schemas.android.com/apk/res/android}'


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def sdk_binary(name):
    found = shutil.which(name)
    if found:
        return found
    for sdk in (os.environ.get('ANDROID_HOME'), os.environ.get('ANDROID_SDK_ROOT'), Path.home() / 'Android/Sdk'):
        if not sdk:
            continue
        root = Path(sdk)
        candidates = ([root / 'cmdline-tools/latest/bin' / name] if name == 'apkanalyzer'
                      else sorted(root.glob(f'build-tools/*/{name}'), key=lambda p: tuple(int(x) for x in re.findall(r'\d+', str(p.parent.name))), reverse=True))
        for candidate in candidates:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    raise ValueError(f'{name} executable is missing; install Android SDK command-line/build tools')


def _command(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(f'{Path(command[0]).name}: {error}') from error
    if result.returncode:
        raise ValueError(f'{Path(command[0]).name} exited {result.returncode}: {result.stderr[-4000:]}')
    return result.stdout


def _identity(descriptor):
    identity = {key: descriptor[key] for key in ('package', 'version_code', 'version_name', 'signers', 'native_abis')}
    identity['artifacts'] = [{k: item[k] for k in ('id', 'sha256', 'native_abis', 'entries')} for item in descriptor['artifacts']]
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def describe_input(apk_path, split_paths=None):
    paths = [Path(p).expanduser().resolve() for p in [apk_path, *(split_paths or [])]]
    if len(paths) != len(set(paths)):
        raise ValueError('duplicate APK paths')
    analyzer, signer = sdk_binary('apkanalyzer'), sdk_binary('apksigner')
    artifacts, manifests = [], []
    for index, path in enumerate(paths):
        if not path.is_file() or path.suffix.lower() != '.apk' or not zipfile.is_zipfile(path):
            raise ValueError(f'APK is missing or invalid: {path}')
        digest = sha256(path)
        try:
            manifest = ET.fromstring(_command([analyzer, 'manifest', 'print', str(path)]))
        except ET.ParseError as error:
            raise ValueError(f'invalid decoded manifest: {path}') from error
        split = manifest.get('split', '')
        if (index == 0 and split) or (index > 0 and not split):
            raise ValueError('apk_path must be the base APK and split_paths must contain split APKs')
        artifact_id = split or 'base'
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', artifact_id) or artifact_id in ('.', '..'):
            raise ValueError('unsafe split ID')
        if any(item['id'] == artifact_id for item in artifacts):
            raise ValueError('duplicate split IDs')
        signature = _command([signer, 'verify', '--print-certs', str(path)])
        certs = sorted(set(s.lower() for s in re.findall(r'Signer #\d+ certificate SHA-256 digest:\s*([0-9a-fA-F]{64})', signature)))
        if not certs:
            raise ValueError(f'no verified signer certificate: {path}')
        metadata = {'package': manifest.get('package'), 'version_code': manifest.get(ANDROID + 'versionCode'),
                    'version_name': manifest.get(ANDROID + 'versionName', base_metadata['version_name'] if artifacts else ''), 'signers': certs}
        if not metadata['package'] or not metadata['version_code']:
            raise ValueError(f'missing package/version identity: {path}')
        if artifacts:
            for key in metadata:
                if metadata[key] != base_metadata[key]:
                    raise ValueError(f'APK set has conflicting {key}')
        else:
            base_metadata = metadata
        entries, abis = {}, set()
        with zipfile.ZipFile(path) as z:
            if len(z.namelist()) != len(set(z.namelist())):
                raise ValueError(f'duplicate ZIP entries: {path}')
            for entry in z.infolist():
                name = entry.filename
                if name.startswith('lib/') and name.endswith('.so') and len(name.split('/')) >= 3:
                    abis.add(name.split('/')[1])
                if name.endswith('.dex') or name.startswith(('assets/', 'res/')) or name == 'resources.arsc':
                    if not entry.is_dir():
                        with z.open(entry) as stream:
                            entries[name] = hashlib.file_digest(stream, 'sha256').hexdigest()
        artifacts.append({'id': artifact_id, 'path': str(path), 'sha256': digest, 'native_abis': sorted(abis), 'entries': entries})
        manifests.append(manifest)
        if sha256(path) != digest:
            raise ValueError(f'APK changed during validation: {path}')
    ids = {item['id'] for item in artifacts}
    supplied_types = defaultdict(set)
    def owner(manifest):
        config = manifest.get('configForSplit') or manifest.get(ANDROID + 'configForSplit')
        return config or (manifest.get('split') if manifest.get(ANDROID + 'isFeatureSplit') == 'true' else 'base')
    for manifest in manifests:
        supplied_types[owner(manifest)].update(t for t in manifest.get(ANDROID + 'splitTypes', '').split(',') if t)
    for manifest in manifests:
        deps = [node.get(ANDROID + 'name') for node in manifest.findall('uses-split')]
        config = manifest.get('configForSplit') or manifest.get(ANDROID + 'configForSplit')
        if config:
            deps.append(config)
        if any(dep not in ids for dep in deps):
            raise ValueError('unresolved declared split dependency')
        required = {t for t in manifest.get(ANDROID + 'requiredSplitTypes', '').split(',') if t}
        missing = required - supplied_types[owner(manifest)]
        if missing:
            raise ValueError(f'missing required split types for {owner(manifest)}: {sorted(missing)}')
    artifacts = [artifacts[0], *sorted(artifacts[1:], key=lambda item: item['id'])]
    result = {'schema_version': 1, **base_metadata, 'artifacts': artifacts,
              'native_abis': sorted({abi for item in artifacts for abi in item['native_abis']})}
    result['input_id'] = _identity(result)
    return result


def verify_input_files(descriptor):
    try:
        if descriptor['schema_version'] != 1 or descriptor['input_id'] != _identity(descriptor):
            raise ValueError('input identity is invalid')
        for artifact in descriptor['artifacts']:
            if sha256(artifact['path']) != artifact['sha256']:
                raise ValueError(f'APK changed since collection: {artifact["path"]}')
    except (KeyError, TypeError, OSError) as error:
        raise ValueError(f'invalid or unavailable input identity: {error}') from error


def compare_inputs(static, runtime):
    errors, gaps = [], []
    for key in ('package', 'version_code', 'version_name', 'signers'):
        if static[key] != runtime[key]:
            errors.append(f'different {key}')
    def entries(descriptor):
        return {(a['id'], name): digest for a in descriptor['artifacts'] for name, digest in a['entries'].items()}
    left, right = entries(static), entries(runtime)
    for artifact, name in sorted(left.keys() & right.keys()):
        if left[artifact, name] != right[artifact, name]:
            if name.endswith('.dex') or name.startswith('assets/'):
                errors.append(f'divergent shared code/assets: {artifact}/{name}')
            else:
                gaps.append(f'resource differs: {artifact}/{name}')
    if left.keys() != right.keys():
        gaps.append('Deliveries contain different code/resource entries; unmatched entries were not compared.')
    if static['input_id'] != runtime['input_id']:
        gaps.append('Static and runtime deliveries differ; ARM64 native behavior was not explored by this x86_64 run.')
    return {'compatible': not errors, 'errors': errors, 'gaps': gaps}
