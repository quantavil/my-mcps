"""Optional derived views. Original runtime files and graph IDs remain authoritative."""
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from core import Ditto2Error, _inside, _json, _publish_json, _read_utg, _sha256

KEY = re.compile(r'(?<![A-Za-z0-9_])(?:debug|key)_[A-Za-z0-9_]+')
FIELDS = {'text', 'resource_id', 'resource-id', 'content_description', 'content-desc', 'key'}


def _sources(root, graph):
    states, hashes, gaps = {}, {}, []
    for path in sorted((root / 'states').glob('*.json')):
        relative = path.relative_to(root).as_posix()
        safe = _inside(root, relative)
        if safe.stat().st_size > 32 * 1024 * 1024:
            gaps.append(f'oversized state: {relative}')
            continue
        hashes[relative] = _sha256(safe)
        try:
            data = _json(safe)
            if not isinstance(data, dict):
                raise Ditto2Error('state must be an object')
        except Ditto2Error as error:
            gaps.append(f'{relative}: {error}')
            continue
        states[relative] = data
    return states, hashes, gaps


def extract_semantic_keys(exploration_dir: str, output_path: str) -> dict:
    root = Path(exploration_dir).expanduser().resolve()
    graph = _read_utg(root)
    states, hashes, gaps = _sources(root, graph)
    graph_ids = {node['id'] for node in graph['nodes']}
    occurrences = defaultdict(list)
    def capture(value, file, field, view, state_id=None):
        if not isinstance(value, str):
            return
        for token in sorted(set(KEY.findall(value))):
            occurrences[token].append({'path': file, 'field': field, 'view': view,
                                       'state_id': state_id, 'in_graph': state_id in graph_ids})
    for file, state in states.items():
        state_id = state.get('state_str')
        views = state.get('views', [])
        if not isinstance(views, list):
            gaps.append(f'{file}: invalid views')
            continue
        for index, view in enumerate(views):
            if isinstance(view, dict):
                for field in sorted(FIELDS & view.keys()):
                    capture(view[field], file, field, index, state_id)
    for path in sorted(root.rglob('*.xml')):
        file = path.relative_to(root).as_posix()
        safe = _inside(root, file)
        if safe.stat().st_size > 32 * 1024 * 1024:
            gaps.append(f'oversized XML: {file}')
            continue
        hashes[file] = _sha256(safe)
        try:
            tree = ET.parse(safe)
        except ET.ParseError as error:
            gaps.append(f'{file}: {error}')
            continue
        for index, element in enumerate(tree.iter()):
            for field in sorted(FIELDS & element.attrib.keys()):
                capture(element.attrib[field], file, field, index)
    data = {'schema_version': 1, 'source': str(root), 'source_hashes': hashes,
            'keys': [{'name': name, 'count': len(items), 'occurrences': items} for name, items in sorted(occurrences.items())],
            'gaps': gaps, 'interpretation': 'Observed semantic clues, not verified internal models or controllers.'}
    _publish_json(output_path, data)
    return {'output_path': str(Path(output_path).absolute()), 'keys': len(data['keys']), 'gaps': gaps}


def deduplicate_graph(exploration_dir: str, output_path: str) -> dict:
    root = Path(exploration_dir).expanduser().resolve()
    graph = _read_utg(root)
    states, hashes, gaps = _sources(root, graph)
    by_id = defaultdict(list)
    for file, state in states.items():
        by_id[state.get('state_str')].append((file, state))
    signatures, mapping, groups = {}, {}, {}
    for node in sorted(graph['nodes'], key=lambda item: item['id']):
        node_id = node['id']
        screenshot = _inside(root, node['image'])
        image_hash = _sha256(screenshot)
        hashes[node['image']] = image_hash
        candidates = by_id[node_id]
        signature = None
        if len(candidates) == 1:
            file, state = candidates[0]
            if isinstance(state.get('views'), list) and state['views'] and state.get('foreground_activity'):
                payload = {k: v for k, v in state.items() if k not in {'state_str', 'state_str_content_free', 'tag', 'timestamp', 'screenshot_path'}}
                context = {k: node.get(k) for k in ('package', 'activity')}
                signature = hashlib.sha256(json.dumps([image_hash, payload, context], sort_keys=True).encode()).hexdigest()
        if signature is None:
            gaps.append(f'{node_id}: missing or ambiguous full state context; left ungrouped')
        canonical = signatures.setdefault(signature, node_id) if signature else node_id
        mapping[node_id] = canonical
        groups.setdefault(canonical, []).append(node_id)
    edges = [{**edge, 'from': mapping[edge['from']], 'to': mapping[edge['to']],
              'original_edge_index': index, 'original_from': edge['from'], 'original_to': edge['to']}
             for index, edge in enumerate(graph['edges'])]
    data = {'schema_version': 1, 'source_graph': 'utg.js', 'source_graph_sha256': _sha256(root / 'utg.js'),
            'source': str(root), 'source_hashes': hashes, 'node_mapping': mapping,
            'groups': [{'canonical_id': key, 'original_ids': values} for key, values in groups.items()],
            'edges': edges, 'gaps': gaps, 'authority': 'Use original graph IDs for review and transition validation.'}
    _publish_json(output_path, data)
    return {'output_path': str(Path(output_path).absolute()), 'original_nodes': len(mapping), 'groups': len(groups), 'gaps': gaps}
