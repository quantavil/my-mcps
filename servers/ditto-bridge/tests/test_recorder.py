"""Human oracle capture uses the same controller and receipts as AI capture."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from recorder import Recorder


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        contract = {'phase_id': 'setup', 'checkpoints': [
            {'number': 1, 'id': 'role', 'fixture': 'fresh', 'setup': 'At role',
             'actions': ['Choose self'], 'artifacts': ['png', 'xml', 'trace']},
        ]}
        self.path = Path(self.temp.name) / 'phase.json'
        self.path.write_text(json.dumps(contract), encoding='utf-8')
        self.mobile = MagicMock()
        self.mobile.stage = Path(self.temp.name)
        self.mobile.actions = []
        self.mobile.records = []
        self.mobile.perform.return_value = {'action': 'tap'}
        self.mobile.inspect_ui.return_value = {'nodes': [
            {'text': 'For myself', 'description': '', 'resource_id': '',
             'bounds': [1, 2, 3, 4], 'clickable': True, 'package': 'app'}],
            'truncated': False}
        self.recorder = Recorder(self.mobile, self.path)

    def test_declared_and_incidental_taps_are_distinct(self):
        self.recorder.action('role', {'action': 'tap', 'x': 10, 'y': 20,
                                      'incidental': True})
        self.mobile.perform.assert_called_with('tap', x=10, y=20, step=None)
        self.recorder.action('role', {'action': 'tap', 'x': 30, 'y': 40})
        self.mobile.perform.assert_called_with('tap', x=30, y=40, step='Choose self')

    def test_capture_uses_contract_names_and_auto_observed_text(self):
        self.mobile.actions = [{'step': 'Choose self'}]
        self.recorder.capture('role')
        self.mobile.capture.assert_called_once()
        args = self.mobile.capture.call_args.kwargs
        self.assertEqual(args['checkpoint_id'], 'role')
        self.assertEqual(args['actions'], ['Choose self'])
        self.assertIn('For myself', args['observed_state'])
        self.assertEqual(args['observation_source'], 'human_recorder')

    def test_invalid_checkpoint_cannot_issue_a_tap(self):
        with self.assertRaisesRegex(ValueError, 'unknown checkpoint'):
            self.recorder.action('missing', {'action': 'tap', 'x': 1, 'y': 2})
        self.mobile.perform.assert_not_called()

    def test_completed_checkpoint_cannot_be_recorded_again(self):
        self.mobile.records = [{'checkpoint_id': 'role'}]
        with self.assertRaisesRegex(ValueError, 'already complete'):
            self.recorder.action('role', {'action': 'tap', 'x': 1, 'y': 2})

    def test_finalize_releases_device_after_all_checkpoints(self):
        self.mobile.records = [{'checkpoint_id': 'role'}]
        self.mobile.finalize.return_value = {'export_dir': '/tmp/capture'}
        self.assertEqual(self.recorder.finalize()['export_dir'], '/tmp/capture')
        self.mobile.release_device.assert_called_once()

    def test_local_panel_requires_token_and_routes_taps_to_controller(self):
        url = self.recorder.start()
        self.addCleanup(self.recorder.stop)
        with self.assertRaises(HTTPError) as denied:
            urlopen(url.split('?')[0] + 'api/status', timeout=2)
        self.assertEqual(denied.exception.code, 403)
        base = url.split('/?')[0]
        token = url.split('token=', 1)[1]
        request = Request(base + '/api/action?token=' + token,
                          data=json.dumps({'checkpoint_id': 'role', 'action': 'tap',
                                           'x': 10, 'y': 20}).encode(),
                          headers={'Content-Type': 'application/json'}, method='POST')
        with urlopen(request, timeout=2) as response:
            self.assertEqual(response.status, 200)
        self.mobile.perform.assert_called_with('tap', x=10, y=20, step='Choose self')


if __name__ == '__main__':
    unittest.main()
