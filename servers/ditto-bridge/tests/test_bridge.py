"""Evidence safety checks for the Ditto MCP backend."""
import json
import tempfile
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge import BridgeError, MobileController, analyze_package, digest, query_analysis


class BridgeTests(unittest.TestCase):
    def test_matching_installed_apk_is_not_reinstalled(self):
        controller = MobileController()
        with patch.object(controller, '_assert_installed_apk'), patch.object(controller, '_adb') as adb:
            self.assertFalse(controller._ensure_installed(Path('app.apk'), 'example.app', 'a' * 64))
        adb.assert_not_called()

    def test_changed_apk_is_installed_and_verified(self):
        controller = MobileController()
        with patch.object(controller, '_assert_installed_apk', side_effect=[
                BridgeError('installed APK hash differs'), None]) as verify, patch.object(controller, '_adb') as adb:
            self.assertTrue(controller._ensure_installed(Path('app.apk'), 'example.app', 'a' * 64))
        adb.assert_called_once_with('install', '-r', 'app.apk', timeout=180)
        self.assertEqual(verify.call_count, 2)

    def test_hierarchy_commands_share_one_timeout_budget(self):
        controller = MobileController()
        with patch.object(controller, '_adb') as adb, patch.object(
                controller, '_bytes', return_value=b'<hierarchy/>') as read:
            adb.return_value = 'UI hierchary dumped to: /sdcard/ditto-hierarchy.xml'
            controller._hierarchy(timeout=0.02)
        self.assertLessEqual(adb.call_args.kwargs['timeout'], 0.02)
        self.assertLessEqual(read.call_args.kwargs['timeout'], 0.02)

    def test_hierarchy_does_not_read_stale_file_after_failed_dump(self):
        controller = MobileController()
        with patch.object(controller, '_adb', return_value='ERROR: could not get idle state'), patch.object(
                controller, '_bytes') as read:
            with self.assertRaisesRegex(BridgeError, 'hierarchy dump failed'):
                controller._hierarchy()
        read.assert_not_called()

    def test_target_tap_waits_for_transition_without_repeating_the_lookup(self):
        controller = MobileController()
        controller.stage = Path(tempfile.gettempdir())
        with patch.object(controller, '_target', side_effect=[
                BridgeError('UI target not found: Cycle Tracking'), {'bounds': [10, 20, 90, 60]}]) as target, patch.object(
                controller, '_adb') as adb, patch('bridge.time.sleep'):
            controller.perform('tap_target', selector='Cycle Tracking')
        self.assertEqual(target.call_count, 2)
        adb.assert_called_once_with('shell', 'input', 'tap', 50, 40)

    def test_saved_replay_rejects_missing_screen_guards_before_acting(self):
        controller = MobileController()
        controller.stage = Path(tempfile.gettempdir())
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'capture.json'
            path.write_text(json.dumps({'replay': {'status': 'candidate', 'plan': [
                {'steps': [{'action': 'tap', 'x': 1, 'y': 2}],
                 'checkpoint': {'checkpoint_id': 'one'}}]}}))
            with patch.object(controller, 'perform') as perform:
                with self.assertRaisesRegex(BridgeError, 'expect'):
                    controller.run_checkpoints(plan_path=str(path))
            perform.assert_not_called()

    def test_capture_exports_replay_candidate_and_linked_timings(self):
        with tempfile.TemporaryDirectory() as root:
            controller = MobileController()
            controller.stage = Path(root) / 'stage'
            controller.stage.mkdir()
            controller.output = Path(root) / 'export'
            controller.environment = {'viewport_px': '1080x2400'}
            controller.receipt = {'server': 'ditto-mobile-control-mcp', 'tool': 'mobile_control', 'limitations': []}
            controller.target = {'kind': 'emulator', 'id': 'test'}
            controller.package_sha = 'a' * 64
            controller.package_name = 'example.app'
            with patch.object(controller, '_adb', return_value=''), patch.object(
                    controller, '_environment', return_value=controller.environment), patch.object(
                    controller, '_assert_app_focus'), patch.object(controller, '_screen', return_value=b'png'):
                controller.perform('tap', x=1, y=2, step='Open')
                controller.capture(1, 'one', 'fresh', 'Start', ['Open'], ['png', 'trace'],
                                   observed_state='Settings')
                result = controller.finalize()
            capture = json.loads((Path(result['export_dir']) / 'capture.json').read_text())
            self.assertEqual(capture['replay']['status'], 'candidate')
            self.assertEqual(capture['replay']['plan'][0]['steps'][0],
                             {'action': 'tap', 'x': 1, 'y': 2, 'step': 'Open'})
            self.assertEqual(capture['timings']['checkpoint_ids'], ['one'])
            self.assertIn('wall_ms', capture['timings'])
            self.assertIn('duration_ms', capture['artifacts'][0]['executed_actions'][0])

    def test_saved_guarded_replay_executes_and_exports_new_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            controller = MobileController()
            controller.stage = Path(root) / 'stage'
            controller.stage.mkdir()
            controller.output = Path(root) / 'export'
            controller.environment = {'viewport_px': '1080x2400'}
            controller.receipt = {'server': 'ditto-mobile-control-mcp', 'tool': 'mobile_control', 'limitations': []}
            controller.package_sha = 'a' * 64
            controller.package_name = 'example.app'
            source = Path(root) / 'reviewed.json'
            source.write_text(json.dumps({'plan': [{'expect': 'Settings',
                'steps': [{'action': 'tap_target', 'selector': 'Continue', 'step': 'Open'}],
                'checkpoint': {'number': 1, 'checkpoint_id': 'one', 'fixture': 'fresh',
                    'setup': 'Start', 'actions': ['Open'], 'kinds': ['png', 'trace'],
                    'observed_state': 'Settings visible; inspect captured image'}}]}))
            xml = b'<hierarchy><node text="Continue" bounds="[0,0][10,10]"/><node text="Settings" bounds="[0,20][20,40]"/></hierarchy>'
            with patch.object(controller, '_hierarchy', return_value=xml), patch.object(
                    controller, '_adb') as adb, patch.object(controller, '_assert_app_focus'), patch.object(
                    controller, '_environment', return_value=controller.environment), patch.object(
                    controller, '_screen', return_value=b'png'):
                result = controller.run_checkpoints(plan_path=str(source))
                exported = controller.finalize()
            self.assertEqual(result['completed'], ['one'])
            adb.assert_called_once_with('shell', 'input', 'tap', 5, 5)
            receipt = json.loads((Path(exported['export_dir']) / 'capture.json').read_text())
            self.assertEqual(receipt['replay']['plan'][0]['expect'], 'Settings')
            self.assertEqual(receipt['replay']['status'], 'candidate')
            self.assertTrue((Path(exported['export_dir']) / '001_one.png').exists())

    def test_analyzer_failure_cannot_export_healthy_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            apk = Path(root) / 'sample.apk'
            apk.write_bytes(b'invalid apk')
            output = Path(root) / 'export'
            with patch('bridge.version', return_value='test'), patch(
                    'bridge._analyze', side_effect=BridgeError('backend failed')):
                with self.assertRaisesRegex(BridgeError, 'backend failed'):
                    analyze_package('jadx', apk, output)
            self.assertFalse(output.exists())
            self.assertEqual(list(Path(root).glob('.ditto-export-*')), [])

    def test_capture_rejects_unbacked_or_duplicate_evidence(self):
        controller = MobileController()
        controller.stage = Path('/tmp')
        for kinds in (['png', 'semantics'], ['png', 'network'], ['png', 'png']):
            with self.assertRaises(BridgeError):
                controller.capture(1, 'one', 'fixture', '', [], kinds)

    def test_sha256_is_file_content_hash(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'payload'
            path.write_bytes(b'abc')
            self.assertEqual(digest(path),
                             'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')

    def test_analysis_reuses_verified_export_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as root:
            apk = Path(root) / 'sample.apk'
            apk.write_bytes(b'apk')
            output = Path(root) / 'export'
            def analyze(role, package, stage):
                (stage / 'analysis').mkdir()
                (stage / 'analysis/source.txt').write_text('example source')
                return {'command': role}
            with patch('bridge.version', return_value='test'), patch(
                    'bridge._analyze', side_effect=analyze) as backend:
                analyze_package('jadx', apk, output)
                result = analyze_package('jadx', apk, output)
                self.assertTrue(result['cached'])
                self.assertEqual(backend.call_count, 1)
                (output / 'analysis/source.txt').write_text('changed')
                with self.assertRaisesRegex(BridgeError, 'changed|hash'):
                    analyze_package('jadx', apk, output)

    def test_query_resumes_large_file_and_reports_actual_match_offset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            apk = root / 'sample.apk'
            apk.write_bytes(b'apk')
            output = root / 'export'
            prefix = b'x' * (33 * 1024 * 1024 + 123)
            def analyze(role, package, stage):
                (stage / 'source.txt').write_bytes(prefix + b'needle')
                return {'command': role}
            with patch('bridge.version', return_value='test'), patch(
                    'bridge._analyze', side_effect=analyze):
                analyze_package('jadx', apk, output)
            first = query_analysis(output, query='needle')
            self.assertTrue(first['scan_limit_reached'])
            self.assertEqual(first['next_offset'], 0)
            second = query_analysis(output, query='needle',
                                    offset=first['next_offset'],
                                    scan_offset=first['next_scan_offset'])
            self.assertEqual(second['matches'][0]['byte_offset'], len(prefix))

    def test_perform_records_arguments_and_protocol_step(self):
        controller = MobileController()
        controller.stage = Path(tempfile.gettempdir())
        with patch.object(controller, '_adb', return_value=''):
            result = controller.perform('tap', x=42, y=91, step='Open settings')
        self.assertEqual(result['arguments'], {'x': 42, 'y': 91})
        self.assertEqual(result['step'], 'Open settings')

    def test_preview_controls_running_app_without_creating_evidence(self):
        controller = MobileController()
        with patch.object(controller, '_identity') as identity, patch.object(
                controller, '_assert_app_focus') as focus, patch.object(
                controller, '_adb', return_value='package:/data/app/base.apk'):
            result = controller.preview_begin('emulator-5554', 'floww_fresh', 'com.example.floww')
            identity.assert_called_once_with('emulator-5554', 'floww_fresh')
            focus.assert_called_once_with('com.example.floww')
            self.assertEqual(result['mode'], 'preview')
            self.assertIsNone(controller.stage)
            event = controller.perform('tap', x=42, y=91)
            self.assertEqual(event['action'], 'tap')
            self.assertEqual(controller.actions, [])
            with self.assertRaisesRegex(BridgeError, 'capture'):
                controller.capture(1, 'one', 'fixture', 'setup', [], ['png'],
                                   observed_state='Preview')
            with self.assertRaisesRegex(BridgeError, 'capture'):
                controller.finalize()
            with self.assertRaisesRegex(BridgeError, 'capture'):
                controller.run_checkpoints([{'checkpoint': {'checkpoint_id': 'one'}}])
        controller.abort()
        self.assertFalse(controller.preview_mode)

    def test_capture_refuses_unexecuted_protocol(self):
        controller = MobileController()
        controller.stage = Path(tempfile.gettempdir())
        with self.assertRaisesRegex(BridgeError, 'protocol'):
            controller.capture(1, 'one', 'fixture', '', ['Open settings'],
                               ['png'], observed_state='Settings visible')

    def test_environment_is_read_at_capture_not_copied_from_probe(self):
        controller = MobileController()
        controller.stage = Path(tempfile.gettempdir())
        controller.environment = {'font_scale': '1.0'}
        with patch.object(controller, '_environment', return_value={'font_scale': '1.5'}):
            with self.assertRaisesRegex(BridgeError, 'environment'):
                controller.capture(1, 'one', 'fixture', '', [], ['png'],
                                   observed_state='Settings visible')

    def test_environment_reads_android_14_rotation_and_default_font_scale(self):
        controller = MobileController()
        responses = {
            ('shell', 'wm', 'size'): 'Physical size: 1080x2400',
            ('shell', 'wm', 'density'): 'Physical density: 420',
            ('shell', 'getprop', 'ro.build.version.sdk'): '34',
            ('shell', 'getprop', 'persist.sys.locale'): 'en-US',
            ('shell', 'settings', 'get', 'system', 'font_scale'): 'null',
            ('shell', 'cmd', 'uimode', 'night'): 'Night mode: no',
            ('shell', 'dumpsys', 'input'): 'InputDeviceOrientation: 0',
            ('shell', 'dumpsys', 'window', 'displays'): 'mRotation=0',
            ('shell', 'getprop', 'ro.hardware.egl'): 'emulation',
        }
        with patch.object(controller, '_adb', side_effect=lambda *args: responses[args]):
            environment = controller._environment()
        self.assertEqual(environment['orientation'], 0)
        self.assertEqual(environment['font_scale'], '1.0')

    def test_mcp_emulator_tool_validates_requests_and_preserves_active_capture(self):
        import asyncio
        import importlib.util
        import os
        import subprocess
        from fastmcp import Client
        from fastmcp.exceptions import ToolError
        spec = importlib.util.spec_from_file_location('ditto_test_server', Path(__file__).resolve().parent.parent / 'server.py')
        module = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {'DITTO_ROLE': 'mobile-control'}):
            spec.loader.exec_module(module)
        async def check():
            async with Client(module.mcp) as client:
                with patch.object(module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'devices', '')) as run:
                    await client.call_tool('manage_emulator', {'operation': 'status', 'avd': 'test'})
                    self.assertEqual(run.call_args.kwargs['env']['DITTO_AVD'], 'test')
                    run.reset_mock()
                    with self.assertRaises(ToolError):
                        await client.call_tool('manage_emulator', {'operation': 'wipe', 'avd': 'test'})
                    with patch.object(module.MOBILE, 'stage', Path('/active')):
                        with self.assertRaisesRegex(ToolError, 'finalize or abort'):
                            await client.call_tool('manage_emulator', {'operation': 'stop', 'avd': 'test'})
                    run.assert_not_called()
        asyncio.run(check())

    def test_mobile_mcp_exposes_compact_ui_and_checkpoint_runner(self):
        import asyncio
        import importlib.util
        import os
        from fastmcp import Client
        spec = importlib.util.spec_from_file_location(
            'ditto_mobile_server_test', Path(__file__).resolve().parent.parent / 'server.py')
        module = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {'DITTO_ROLE': 'mobile-control'}):
            spec.loader.exec_module(module)
        async def check():
            async with Client(module.mcp) as client:
                tools = {tool.name for tool in await client.list_tools()}
                self.assertIn('inspect_ui', tools)
                self.assertIn('recorder_control', tools)
                with patch.object(module.MOBILE, 'acquire_device'), patch.object(
                        module.MOBILE, 'preview_begin', return_value={
                            'mode': 'preview', 'evidence_eligible': False}) as preview:
                    await client.call_tool('mobile_control', {
                        'operation': 'preview_begin', 'serial': 'emulator-5554',
                        'target_id': 'floww_fresh', 'package_name': 'com.example.floww'})
                    preview.assert_called_once_with(
                        'emulator-5554', 'floww_fresh', 'com.example.floww')
                with patch.object(module.MOBILE, 'run_checkpoints', return_value={
                        'completed': ['one'], 'stopped_at': None}):
                    await client.call_tool('mobile_control', {'operation': 'run_checkpoints',
                        'plan': [{'steps': [], 'checkpoint': {'checkpoint_id': 'one'}}]})
        asyncio.run(check())

    def test_mobile_probe_rejects_anr_and_wrong_app_focus(self):
        controller = MobileController()
        for focus in ('Application Not Responding: com.android.systemui',
                      'com.android.settings/.FallbackHome'):
            with self.subTest(focus=focus), patch.object(
                    controller, '_adb', return_value=f'mCurrentFocus=Window{{1 u0 {focus}}}'):
                with self.assertRaises(BridgeError):
                    controller._assert_app_focus('com.example.app')
        with patch.object(controller, '_adb', return_value=(
                'mCurrentFocus=Window{1 u0 com.example.app/.MainActivity}')):
            controller._assert_app_focus('com.example.app')

    def test_focus_wait_limits_each_adb_call_to_remaining_deadline(self):
        controller = MobileController()
        observed = []
        def check(package_name, timeout=30):
            observed.append(timeout)
            raise BridgeError('wrong app focus')
        with patch.object(controller, '_assert_app_focus', side_effect=check):
            with self.assertRaisesRegex(BridgeError, 'wrong app focus'):
                controller._wait_for_app_focus('com.example.app', seconds=0.01)
        self.assertTrue(observed)
        self.assertLessEqual(max(observed), 0.01)

    def test_inspect_ui_returns_compact_matching_nodes(self):
        controller = MobileController()
        controller.stage = Path(tempfile.gettempdir())
        xml = (b'<hierarchy><node text="" content-desc="" bounds="[0,0][100,100]" '
               b'clickable="false"><node text="Confirm" content-desc="" '
               b'resource-id="next" bounds="[10,20][90,60]" clickable="true" '
               b'package="example.app"/></node></hierarchy>')
        with patch.object(controller, '_hierarchy', return_value=xml):
            result = controller.inspect_ui('Confirm')
        self.assertEqual(len(result['nodes']), 1)
        self.assertEqual(result['nodes'][0]['text'], 'Confirm')
        self.assertEqual(result['nodes'][0]['bounds'], [10, 20, 90, 60])

    def test_tap_target_uses_unique_ui_node_and_records_selector(self):
        controller = MobileController()
        controller.stage = Path(tempfile.gettempdir())
        xml = (b'<hierarchy><node text="Confirm" content-desc="" '
               b'bounds="[10,20][90,60]" clickable="true"/></hierarchy>')
        with patch.object(controller, '_hierarchy', return_value=xml), patch.object(
                controller, '_adb', return_value='') as adb:
            event = controller.perform('tap_target', selector='Confirm', step='Confirm setup')
        adb.assert_called_once_with('shell', 'input', 'tap', 50, 40)
        self.assertEqual(event['arguments']['selector'], 'Confirm')

    def test_replay_stops_before_tap_if_expected_ui_is_absent(self):
        controller = MobileController()
        controller.stage = Path(tempfile.gettempdir())
        with patch.object(controller, 'inspect_ui', return_value={'nodes': []}), patch.object(
                controller, '_adb') as adb:
            with self.assertRaisesRegex(BridgeError, 'step 1'):
                controller.replay([{'action': 'tap_target', 'selector': 'Missing', 'target_timeout_ms': 1}])
        adb.assert_not_called()

    def test_run_checkpoints_keeps_completed_capture_on_later_failure(self):
        controller = MobileController()
        controller.stage = Path(tempfile.gettempdir())
        with patch.object(controller, 'replay', side_effect=[{'executed': 1}, BridgeError('missing target')]), patch.object(
                controller, 'capture', return_value={'checkpoint_id': 'first', 'artifacts': []}):
            result = controller.run_checkpoints([
                {'steps': [{'action': 'tap', 'x': 1, 'y': 1}], 'checkpoint': {'checkpoint_id': 'first'}},
                {'steps': [{'action': 'tap', 'x': 2, 'y': 2}], 'checkpoint': {'checkpoint_id': 'second'}},
            ])
        self.assertEqual(result['completed'], ['first'])
        self.assertEqual(result['stopped_at'], 'second')

    def test_run_checkpoints_waits_for_transition_before_capture(self):
        controller = MobileController()
        controller.stage = Path(tempfile.gettempdir())
        def saved(**checkpoint):
            controller.replay_plan.append({'checkpoint': checkpoint})
        with patch.object(controller, '_target', side_effect=[
                BridgeError('UI target not found: Periods'), {'bounds': [1, 2, 3, 4]}]) as target, patch.object(
                controller, 'capture', side_effect=saved) as capture, patch(
                'bridge.time.sleep'):
            result = controller.run_checkpoints([{
                'expect': 'Periods',
                'checkpoint': {'checkpoint_id': 'periods'},
            }])
        self.assertEqual(result['completed'], ['periods'])
        self.assertEqual(target.call_count, 2)
        capture.assert_called_once_with(checkpoint_id='periods')
        self.assertEqual(controller.replay_plan[0]['expect'], 'Periods')

    def test_recover_keeps_completed_checkpoints_and_discards_incomplete_actions(self):
        controller = MobileController()
        controller.stage = Path(tempfile.gettempdir())
        controller.serial = 'emulator-5554'
        controller.target = {'kind': 'emulator', 'id': 'floww_fresh'}
        controller.environment = {'viewport_px': '1080x2400'}
        controller.package_name = 'example.app'
        controller.package_sha = 'a' * 64
        controller.apk_path = Path('/tmp/example.apk')
        controller.records = [{'checkpoint_id': 'first'}]
        controller.actions = [{'action': 'tap'}]
        with patch.object(controller, '_identity'), patch.object(
                controller, '_environment', return_value=controller.environment), patch.object(
                controller, '_assert_installed_apk'), patch.object(controller, '_adb', return_value=''):
            result = controller.recover()
        self.assertEqual(result['completed'], ['first'])
        self.assertEqual(controller.actions, [])
        self.assertEqual(len(controller.records), 1)

    def test_capture_returns_compact_receipt_while_retaining_full_trace(self):
        with tempfile.TemporaryDirectory() as root:
            controller = MobileController()
            controller.stage = Path(root)
            controller.environment = {'viewport_px': '1080x2400'}
            controller.receipt = {'server': 'ditto-mobile-control-mcp',
                                  'tool': 'mobile_control', 'limitations': []}
            controller.target = {'kind': 'emulator', 'id': 'test'}
            controller.package_sha = 'a' * 64
            controller.package_name = 'example.app'
            controller.actions = [{'action': 'tap', 'step': 'Open',
                                   'arguments': {'x': 1, 'y': 2}}]
            with patch.object(controller, '_environment', return_value=controller.environment), patch.object(
                    controller, '_assert_app_focus'), patch.object(controller, '_screen',
                    return_value=b'\x89PNG\r\n\x1a\nimage'):
                response = controller.capture(1, 'first', 'fresh', 'Start', ['Open'],
                                              ['png', 'trace'], observed_state='First screen')
            self.assertNotIn('executed_actions', response['artifacts'][0])
            trace = json.loads((Path(root) / '001_first.trace').read_text())
            self.assertEqual(trace['actions'][0]['step'], 'Open')


if __name__ == '__main__':
    unittest.main()
