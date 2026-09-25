"""Free navigation, asynchronous capture, and recoverable exploration evidence."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from recorder import PANEL, Recorder


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.path = root / 'phase.json'
        self.path.write_text(json.dumps({'phase_id': 'setup', 'checkpoints': [
            {'id': 'role', 'capture_when': 'Self is selected'},
            {'id': 'goal', 'capture_when': 'Conceive is selected'}]}))
        self.mobile = MagicMock()
        self.mobile.stage = root / 'stage'
        self.mobile.stage.mkdir()
        self.mobile.output = root / 'export'
        self.mobile._screen.return_value = b'\x89PNG\r\n\x1a\nimage'
        self.mobile._hierarchy.return_value = b'<hierarchy/>'
        self.mobile.target = {'id': 'device'}
        self.mobile.environment = {'density': 280}
        self.mobile.package_name = 'example.original'
        self.mobile.package_sha = 'abc'
        self.mobile.receipt = {'server': 'ditto-mobile-control'}
        self.mobile.preview_mode = False
        self.mobile.records = []
        self.mobile.actions = []
        self.mobile.perform.return_value = {'result': 'command_executed'}
        self.recorder = Recorder(self.mobile, self.path)
        self.addCleanup(self.recorder.stop)

    def sample(self, png=None):
        self.recorder.preview = png or self.mobile._screen.return_value
        self.recorder.preview_revision = self.recorder.revision
        self.recorder.stable_frames = 2
        self.recorder.last_input = time.monotonic() - 2
        self.recorder._sample()

    @unittest.skipUnless(shutil.which('node'), 'Node checks browser JavaScript syntax')
    def test_panel_javascript(self):
        script = PANEL.split('<script>')[1].split('</script>')[0].replace('__TOKEN__', '"test"')
        result = subprocess.run([shutil.which('node'), '--check'], input=script,
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_free_navigation_and_failed_input_preserve_raw_log(self):
        for action in ({'action': 'tap', 'x': 10, 'y': 20}, {'action': 'back'}, {'action': 'back'}):
            self.recorder.action(action)
        self.mobile.perform.side_effect = RuntimeError('ADB disconnected')
        with self.assertRaisesRegex(RuntimeError, 'disconnected'):
            self.recorder.action({'action': 'back'})
        log = [json.loads(line) for line in (self.mobile.stage / 'actions.jsonl').read_text().splitlines()]
        self.assertEqual(len(log), 8)
        self.assertEqual([e['status'] for e in log[-2:]], ['requested', 'uncertain'])
        self.assertEqual(self.recorder.revision, 4)
        self.mobile.inspect_ui.assert_not_called()
        self.mobile.capture.assert_not_called()
        self.mobile._hierarchy.assert_not_called()

    def test_auto_dedup_and_manual_bookmark(self):
        self.sample()
        self.recorder.action({'action': 'back'})
        self.sample()  # same screen after a harmless back press
        self.assertEqual(len(self.recorder.shots), 1)
        self.recorder.save()
        self.sample()
        self.assertEqual(len(self.recorder.shots), 2)
        self.assertTrue(self.recorder.shots[-1]['manual'])
        self.assertEqual(self.recorder.shots[-1]['after_input'], 1)

    def test_delayed_transition_without_another_input_is_captured(self):
        self.sample()
        self.sample(b'next settled screen')
        self.assertEqual(len(self.recorder.shots), 2)
        self.assertEqual([shot['after_input'] for shot in self.recorder.shots], [0, 0])

    def test_navigation_during_xml_is_nonblocking_and_discards_mixed_sample(self):
        entered, release = threading.Event(), threading.Event()
        self.mobile._hierarchy.side_effect = lambda **_: (entered.set(), release.wait(2), b'<hierarchy/>')[-1]
        worker = threading.Thread(target=self.sample)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.recorder.action({'action': 'back'})
            self.assertEqual(self.recorder.revision, 1)
        finally:
            release.set()
            worker.join(2)
        self.assertEqual(self.recorder.shots, [])

    def test_xml_failure_keeps_image_with_explicit_gap(self):
        self.mobile._hierarchy.side_effect = RuntimeError('timeout')
        self.sample()
        self.assertEqual(len(self.recorder.shots), 1)
        self.assertIsNone(self.recorder.shots[0]['xml'])
        self.assertIn('timeout', self.recorder.shots[0]['warning'])
        self.assertEqual(self.recorder.status()['xml_saved'], 0)

    def test_status_counts_available_xml_separately_from_saved_screens(self):
        self.sample()
        self.assertEqual(self.recorder.status()['saved'], 1)
        self.assertEqual(self.recorder.status()['xml_saved'], 1)
        self.mobile._hierarchy.side_effect = RuntimeError('timeout')
        self.sample(b'next settled screen')
        self.assertEqual(self.recorder.status()['saved'], 2)
        self.assertEqual(self.recorder.status()['xml_saved'], 1)

    def test_anr_is_retained_as_a_warning_not_an_app_success(self):
        self.mobile._hierarchy.return_value = b"""<hierarchy><node resource-id="android:id/alertTitle" text="System UI isn't responding"/></hierarchy>"""
        self.sample()
        self.assertIn('ANR', self.recorder.shots[0]['warning'])
        self.assertIn('ANR', self.recorder.status()['warning'])

    def test_scene_change_while_xml_runs_does_not_claim_pair(self):
        def change(**_):
            self.recorder.preview = b'changed'
            return b'<hierarchy/>'
        self.mobile._hierarchy.side_effect = change
        self.sample()
        self.assertIsNone(self.recorder.shots[0]['xml'])

    def test_finish_exports_candidates_and_stop_keeps_finish_available(self):
        self.recorder.action({'action': 'back'})
        self.sample()
        self.recorder.stop()
        result = self.recorder.finish()
        self.assertFalse(result['eligible_for_phase'])
        pack = json.loads((Path(result['export_dir']) / 'exploration.json').read_text())
        self.assertFalse(pack['eligible_for_phase'])
        self.assertEqual(pack['replay_status'], 'unverified')
        self.assertEqual(pack['shots'][0]['after_input'], 1)
        self.assertTrue((Path(result['export_dir']) / 'actions.jsonl').exists())
        self.mobile.abort.assert_called_once()
        with self.assertRaisesRegex(ValueError, 'no active'):
            self.recorder.action({'action': 'back'})
        self.assertTrue(self.recorder.finish()['finished'])

    def test_finish_rejects_unsaved_bookmark_and_empty_pack(self):
        with self.assertRaisesRegex(ValueError, 'wait for a screen'):
            self.recorder.finish()
        self.recorder.save()
        with self.assertRaisesRegex(ValueError, 'still saving'):
            self.recorder.finish()
        self.mobile.abort.assert_not_called()

    def test_limit_cannot_leave_an_unsavable_bookmark(self):
        self.recorder.shots = [{}] * 100
        with self.assertRaisesRegex(ValueError, 'limit'):
            self.recorder.save()
        self.assertFalse(self.recorder.bookmark)

    def test_bad_action_is_rejected_before_device_input(self):
        for payload in ({'action': 'reset'}, {'action': 'back', 'step': 'declared'},
                        {'action': 'tap', 'x': True, 'y': 2}):
            with self.assertRaises(ValueError):
                self.recorder.action(payload)
        self.mobile.perform.assert_not_called()

    def test_http_token_and_real_action_endpoint(self):
        # Start HTTP only; separate tests exercise collection races deterministically.
        with patch.object(self.recorder, '_collect'), patch.object(self.recorder, '_refresh'):
            url = self.recorder.start()
        base, token = url.split('/?token=')
        with self.assertRaises(HTTPError) as denied:
            urlopen(base + '/api/status', timeout=2)
        self.assertEqual(denied.exception.code, 403)
        request = Request(base + '/api/action?token=' + token, data=b'{"action":"back"}',
                          headers={'Content-Type': 'application/json'}, method='POST')
        with urlopen(request, timeout=2) as response:
            self.assertEqual(json.load(response)['status'], 'executed')
            self.assertEqual(response.headers['Referrer-Policy'], 'no-referrer')
        self.mobile.perform.assert_called_once_with(action='back')
        self.sample()
        self.recorder.finish()
        with urlopen(base + '/api/preview?token=' + token, timeout=2) as response:
            self.assertEqual(response.read(), self.mobile._screen.return_value)

    def test_saved_files_endpoint_lists_only_capture_artifacts(self):
        with patch.object(self.recorder, '_collect'), patch.object(self.recorder, '_refresh'):
            url = self.recorder.start()
        base, token = url.split('/?token=')
        self.sample()
        with urlopen(base + '/api/files?token=' + token, timeout=2) as response:
            listing = response.read().decode()
        for name in ('001.png', '001.xml', 'actions.jsonl', 'exploration.json'):
            self.assertIn(name, listing)
        with urlopen(base + '/api/file?token=' + token + '&name=001.png', timeout=2) as response:
            self.assertEqual(response.read(), self.mobile._screen.return_value)
            self.assertIn('attachment', response.headers['Content-Disposition'])
        with self.assertRaises(HTTPError) as denied:
            urlopen(base + '/api/files', timeout=2)
        self.assertEqual(denied.exception.code, 403)
        for name in ('../phase.json', 'phase.json'):
            with self.assertRaises(HTTPError) as missing:
                urlopen(base + '/api/file?token=' + token + '&name=' + name, timeout=2)
            self.assertEqual(missing.exception.code, 404)
        self.recorder.finish()
        with urlopen(base + '/api/file?token=' + token + '&name=001.xml', timeout=2) as response:
            self.assertEqual(response.read(), b'<hierarchy/>')

    def test_mcp_stop_then_finish_keeps_pack_and_clears_handle(self):
        import asyncio
        import importlib.util
        import os
        from fastmcp import Client
        spec = importlib.util.spec_from_file_location('recorder_lifecycle_server',
            Path(__file__).resolve().parent.parent / 'server.py')
        module = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {'DITTO_ROLE': 'mobile-control'}):
            spec.loader.exec_module(module)
        module.MOBILE = self.mobile
        async def check():
            async with Client(module.mcp) as client:
                with patch.object(Recorder, '_collect'), patch.object(Recorder, '_refresh'):
                    await client.call_tool('recorder_control', {
                        'operation': 'start', 'contract_path': str(self.path)})
                self.recorder = module.RECORDER
                try:
                    self.sample()
                    await client.call_tool('recorder_control', {'operation': 'stop'})
                    status = await client.call_tool('recorder_control', {'operation': 'status'})
                    self.assertIsNone(status.data['url'])
                    result = await client.call_tool('recorder_control', {'operation': 'finish'})
                    self.assertEqual(result.data['saved'], 1)
                    await client.call_tool('recorder_control', {'operation': 'stop'})
                    self.assertIsNone(module.RECORDER)
                finally:
                    self.recorder.stop()
        asyncio.run(check())

    def test_guide_filter_validation(self):
        selected = Recorder(self.mobile, self.path, checkpoint_ids=['goal'])
        self.assertEqual([c['id'] for c in selected.status()['checkpoints']], ['goal'])
        with self.assertRaisesRegex(ValueError, 'unknown'):
            Recorder(self.mobile, self.path, checkpoint_ids=['missing'])


if __name__ == '__main__':
    unittest.main()
