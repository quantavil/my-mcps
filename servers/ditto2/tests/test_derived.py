import json
import tempfile
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import derived

class DerivedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'states').mkdir()
        self.views = [{'text': 'debug_log_activity_diaper', 'resource_id': 'key_note', 'enabled': True, 'clickable': True, 'bounds': [[0, 0], [10, 10]]}]
        for name in ('a', 'b', 'c'):
            (self.root / f'{name}.png').write_bytes(b'same screenshot')
            state = {'state_str': name, 'foreground_activity': 'app.Home', 'views': self.views, 'activity_stack': ['app.Home']}
            if name == 'c':
                state = {**state, 'views': [{**self.views[0], 'enabled': False}]}
            (self.root / f'states/state_{name}.json').write_text(json.dumps(state))
        (self.root / 'utg.js').write_text('var utg = ' + json.dumps({'nodes': [{'id': n, 'image': f'{n}.png'} for n in ('a', 'b', 'c')], 'edges': [{'from': 'a', 'to': 'b', 'events': [{'event_str': 'tap'}]}, {'from': 'b', 'to': 'b', 'events': [{'event_str': 'loop'}]}]}))

    def test_grouping_preserves_all_actions_and_raw_evidence(self):
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = derived.deduplicate_graph(str(self.root), str(self.root / 'graph_view.json'))
        output = json.loads((self.root / 'graph_view.json').read_text())
        self.assertEqual(output['node_mapping'], {'a': 'a', 'b': 'a', 'c': 'c'})
        self.assertEqual(len(output['edges']), 2)
        self.assertEqual(output['edges'][1]['events'][0]['event_str'], 'loop')
        for p, content in before.items():
            self.assertEqual(Path(p).read_bytes(), content)
        with self.assertRaises(FileExistsError):
            derived.deduplicate_graph(str(self.root), str(self.root / 'graph_view.json'))

    def test_missing_view_context_is_not_grouped(self):
        for name in ('a', 'b'):
            (self.root / f'states/state_{name}.json').write_text(json.dumps({'state_str': name}))
        derived.deduplicate_graph(str(self.root), str(self.root / 'graph_view.json'))
        output = json.loads((self.root / 'graph_view.json').read_text())
        self.assertEqual(output['node_mapping']['b'], 'b')
        self.assertTrue(output['gaps'])

    def test_semantics_have_sources_and_out_of_graph_status(self):
        (self.root / 'extra.xml').write_text('<hierarchy><node resource-id="key_manual" content-desc="debug_manual" /></hierarchy>')
        (self.root / 'states/bad.json').write_text('broken')
        derived.extract_semantic_keys(str(self.root), str(self.root / 'semantic_spec.json'))
        output = json.loads((self.root / 'semantic_spec.json').read_text())
        keys = {k['name']: k for k in output['keys']}
        self.assertIn('debug_log_activity_diaper', keys)
        self.assertIn('key_manual', keys)
        self.assertFalse(keys['key_manual']['occurrences'][0]['in_graph'])
        self.assertEqual(keys['key_note']['occurrences'][0]['field'], 'resource_id')
        self.assertTrue(output['gaps'])
        self.assertTrue(output['source_hashes'])

    def test_output_cannot_replace_raw_file(self):
        with self.assertRaises(FileExistsError):
            derived.extract_semantic_keys(str(self.root), str(self.root / 'utg.js'))
