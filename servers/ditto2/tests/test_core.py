import hashlib
import json
import os
import signal
import time
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_inputs import sdk_output

from core import _binary, _run, Ditto2Error, analyze_apk, explore_apk, inspect_exploration, search_analysis, unify_evidence


class Ditto2CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch('inputs._command', side_effect=sdk_output).start()
        patch('inputs.sdk_binary', side_effect=lambda name: name).start()
        self.adb = self.root / 'adb'
        self.adb.write_text('#!/usr/bin/env python3\nimport sys\nprint("x86_64" if "getprop" in sys.argv else sys.argv[1:])\nprint("Success" if "getprop" not in sys.argv else "")\n')
        self.adb.chmod(0o755)
        self.apk = self.root / 'original.apk'
        with zipfile.ZipFile(self.apk, 'w') as archive:
            archive.writestr('AndroidManifest.xml', b'manifest')
        self.sha = hashlib.sha256(self.apk.read_bytes()).hexdigest()

    def unify(self, screens, events, findings, gaps):
        result = unify_evidence(self.apk, self.root, screens, events, findings, gaps)
        self.assertEqual(result['evidence_dir'], str(self.root))
        return json.loads(Path(result['review_index']).read_text())

    def test_adb_discovery_uses_sdk_when_absent_from_path(self):
        sdk = self.root / 'sdk'
        binary = sdk / 'platform-tools' / 'adb'
        binary.parent.mkdir(parents=True)
        binary.write_text('#!/bin/sh\nexit 0\n')
        binary.chmod(0o755)
        with patch.dict(os.environ, {'PATH': '', 'ANDROID_HOME': str(sdk)}):
            self.assertEqual(_binary('adb'), str(binary))
            with self.assertRaises(Ditto2Error):
                _binary('adb', {'adb': '/missing/explicit-adb'})

    def test_adb_discovery_tries_sdk_root_after_invalid_android_home(self):
        sdk = self.root / 'sdk'
        binary = sdk / 'platform-tools' / 'adb'
        binary.parent.mkdir(parents=True)
        binary.write_text('#!/bin/sh\nexit 0\n')
        binary.chmod(0o755)
        with patch.dict(os.environ, {'PATH': '', 'ANDROID_HOME': str(self.root / 'invalid'),
                                    'ANDROID_SDK_ROOT': str(sdk)}):
            self.assertEqual(_binary('adb'), str(binary))

    def test_run_passes_selected_environment_to_child(self):
        output = self.root / 'env.log'
        _run([sys.executable, '-c', 'import os; print(os.environ["DITTO_TEST_VALUE"])'],
             output, 5, env={**os.environ, 'DITTO_TEST_VALUE': 'selected-sdk'})
        self.assertEqual(output.read_text().strip(), 'selected-sdk')

    def test_analysis_without_arm64_finalizes_failed_stage(self):
        result = analyze_apk(self.apk, self.root / 'analysis', binaries={name: '/missing/' + name for name in ('apktool', 'jadx', 'r2flutter')})
        self.assertEqual(result['tools']['r2flutter']['status'], 'unsupported_abi')
        self.assertEqual(result['collection_status'], 'failed')
        self.assertFalse((self.root / 'analysis' / '.incomplete').exists())

    def test_analysis_rejects_invalid_r2flutter_json_without_publishing(self):
        with zipfile.ZipFile(self.apk, 'a') as archive:
            archive.writestr('lib/arm64-v8a/libapp.so', b'fake snapshot')
        scripts = {}
        for name in ('apktool', 'jadx', 'r2flutter'):
            script = self.root / name
            script.write_text('#!/usr/bin/env python3\n'
                              'import pathlib, sys\n'
                              'args = sys.argv[1:]\n'
                              "name = pathlib.Path(sys.argv[0]).name\n"
                              "if name == 'r2flutter': print('not json')\n"
                              "else: pathlib.Path(args[args.index('-o' if name == 'apktool' else '-d') + 1]).mkdir()\n")
            script.chmod(0o755)
            scripts[name] = str(script)
        result = analyze_apk(self.apk, self.root / 'analysis', binaries=scripts)
        self.assertEqual(result['tools']['r2flutter']['status'], 'failed')
        self.assertEqual(result['collection_status'], 'partial')
        self.assertFalse((self.root / 'analysis' / '.incomplete').exists())

    def test_exploration_rejects_graph_with_missing_screen_image(self):
        script = self.root / 'droidbot'
        script.write_text('#!/usr/bin/env python3\n'
                          'import hashlib, json, pathlib, sys\n'
                          'args = sys.argv[1:]\n'
                          "apk = pathlib.Path(args[args.index('-a') + 1])\n"
                          "output = pathlib.Path(args[args.index('-o') + 1])\n"
                          "serial = args[args.index('-d') + 1]\n"
                          "graph = {'app_sha256': hashlib.sha256(apk.read_bytes()).hexdigest(), "
                          "'device_serial': serial, 'nodes': [{'id': 'home', 'image': 'states/missing.png'}], 'edges': []}\n"
                          "(output / 'utg.js').write_text('var utg =\\n' + json.dumps(graph))\n")
        script.chmod(0o755)
        with self.assertRaisesRegex(Ditto2Error, 'screenshot'):
            explore_apk(self.apk, self.root / 'exploration', 'emulator-5554', binary=str(script), adb_binary=str(self.adb))
        self.assertTrue((self.root / 'exploration' / '.incomplete').exists())

    def fixture(self):
        analysis = self.root / 'analysis'
        analysis.mkdir()
        (analysis / 'analysis.json').write_text(json.dumps({
            'schema_version': 1, 'apk_sha256': self.sha,
            'tools': {name: {'status': 'ok', 'path': name} for name in ('apktool', 'jadx', 'r2flutter')}
        }))
        for name in ('apktool', 'jadx', 'r2flutter'):
            (analysis / name).mkdir()
        explore = self.root / 'exploration'
        (explore / 'states').mkdir(parents=True)
        (explore / 'events').mkdir()
        for state in ('home', 'settings'):
            (explore / 'states' / f'{state}.png').write_bytes(b'png-' + state.encode())
        utg = {'app_sha256': self.sha, 'app_package': 'example.app',
               'device_serial': 'emulator-5554',
               'nodes': [
                   {'id': 'home', 'image': 'states/home.png', 'label': 'Home', 'package': 'example.app'},
                   {'id': 'settings', 'image': 'states/settings.png', 'label': 'Settings', 'package': 'example.app'},
               ], 'edges': [{'from': 'home', 'to': 'settings',
                             'events': [{'event_id': 1, 'event_type': 'touch',
                                         'event_str': 'TouchEvent(home)'}]}]}
        (explore / 'utg.js').write_text('var utg =\n' + json.dumps(utg))
        (explore / 'events' / 'event_one.json').write_text(json.dumps({
            'event': {'event_type': 'touch'}, 'start_state': 'home',
            'stop_state': 'settings', 'event_str': 'TouchEvent(home)'}))
        return analysis, explore

    def finding(self, analysis):
        source = analysis / 'apktool' / 'res' / 'values'
        source.mkdir(parents=True, exist_ok=True)
        (source / 'strings.xml').write_text('<string name="title">Settings</string>')
        return {'claim': 'Packaged Settings title', 'analyzer': 'apktool',
                'path': 'res/values/strings.xml', 'screens': ['settings'], 'events': []}

    def test_unified_evidence_links_confirmed_screens_and_action(self):
        analysis, explore = self.fixture()
        result = self.unify(
                                ['home', 'settings'], ['event_one'],
                                findings=[self.finding(analysis)], gaps=['Sign-in not explored'])
        self.assertEqual(result['apk_sha256'], self.sha)
        self.assertEqual(set(result['static']), {'apktool', 'jadx', 'r2flutter'})
        self.assertEqual([item['id'] for item in result['screens']], ['home', 'settings'])
        self.assertEqual(result['transitions'][0]['to'], 'settings')
        self.assertEqual(result['candidate_counts'], {'screens': 2, 'events': 1})
        self.assertEqual(result['gaps'], ['Sign-in not explored'])
        self.assertEqual(json.loads((self.root / 'review.json').read_text()), result)

    def test_exploration_inspection_pages_events(self):
        _, explore = self.fixture()
        (explore / 'events' / 'event_two.json').write_text('{}')
        page = inspect_exploration(explore, offset=0, limit=1)
        self.assertEqual([item['id'] for item in page['events']], ['event_one'])
        self.assertEqual(page['next_offset'], 1)

    def test_unified_evidence_rejects_unconfirmed_transition_endpoint(self):
        analysis, explore = self.fixture()
        with self.assertRaisesRegex(Ditto2Error, 'confirmed endpoints'):
            self.unify(
                           ['home'], ['event_one'], findings=[self.finding(analysis)], gaps=[])
        self.assertFalse((self.root / 'review.json').exists())

    def test_unified_evidence_rejects_wrong_apk(self):
        analysis, explore = self.fixture()
        utg_file = explore / 'utg.js'
        utg = json.loads(utg_file.read_text().split('=', 1)[1])
        utg['app_sha256'] = '0' * 64
        utg_file.write_text('var utg =\n' + json.dumps(utg))
        with self.assertRaisesRegex(Ditto2Error, 'APK hash'):
            self.unify(
                           ['home'], [], findings=[self.finding(analysis)], gaps=[])

    def test_unified_evidence_links_a_static_finding_to_reviewed_screen(self):
        analysis, explore = self.fixture()
        finding = self.finding(analysis)
        source = analysis / 'apktool' / 'res' / 'values'
        result = self.unify(
                                ['home', 'settings'], [], findings=[finding], gaps=[])
        self.assertEqual(result['findings'][0]['screens'], ['settings'])
        self.assertEqual(result['findings'][0]['source_sha256'], hashlib.sha256(
            (source / 'strings.xml').read_bytes()).hexdigest())

    def test_unified_evidence_rejects_event_absent_from_graph(self):
        analysis, explore = self.fixture()
        event = explore / 'events' / 'event_one.json'
        data = json.loads(event.read_text())
        data['event_str'] = 'UnrelatedAction'
        event.write_text(json.dumps(data))
        with self.assertRaisesRegex(Ditto2Error, 'graph edge'):
            self.unify(
                           ['home', 'settings'], ['event_one'],
                           findings=[self.finding(analysis)], gaps=[])

    def test_unified_evidence_requires_a_cited_static_finding(self):
        analysis, explore = self.fixture()
        with self.assertRaisesRegex(Ditto2Error, 'static finding'):
            self.unify(
                           ['home'], [], findings=[], gaps=[])

    def test_unify_cannot_overwrite_source_apk(self):
        analysis, explore = self.fixture()
        original = self.apk.read_bytes()
        (self.root / 'review.json').symlink_to(self.apk)
        with self.assertRaises((Ditto2Error, FileExistsError)):
            self.unify(
                           ['home', 'settings'], [], [self.finding(analysis)], [])
        self.assertEqual(self.apk.read_bytes(), original)

    def test_event_symlink_outside_export_is_rejected(self):
        analysis, explore = self.fixture()
        event = explore / 'events/event_one.json'
        external = self.root / 'external.json'
        event.rename(external)
        event.symlink_to(external)
        with self.assertRaisesRegex(Ditto2Error, 'outside'):
            self.unify(
                           ['home', 'settings'], ['event_one'], [self.finding(analysis)], [])

    def test_failed_exploration_keeps_diagnostics(self):
        script = self.root / 'droidbot'
        script.write_text('#!/usr/bin/env python3\nimport sys\nprint("diagnostic detail")\nsys.exit(1)\n')
        script.chmod(0o755)
        with self.assertRaises(Ditto2Error):
            explore_apk(self.apk, self.root / 'failed', 'test-device', binary=str(script), adb_binary=str(self.adb))
        logs = list(self.root.rglob('droidbot.log'))
        self.assertEqual(len(logs), 1)
        self.assertIn('diagnostic detail', logs[0].read_text())

    def test_timeout_stops_descendants(self):
        pid_file = self.root / 'child.pid'
        script = ('import subprocess, sys, time; from pathlib import Path; '
                  'p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"]); '
                  'Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(30)')
        try:
            with self.assertRaisesRegex(Ditto2Error, 'timed out'):
                _run([sys.executable, '-c', script, str(pid_file)], self.root / 'timeout.log', 0.5)
            pid = int(pid_file.read_text())
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                stat = Path(f'/proc/{pid}/stat')
                try:
                    state = stat.read_text().split()[2]
                except (FileNotFoundError, ProcessLookupError):
                    break
                if state == 'Z':
                    break
                time.sleep(0.02)
            else:
                self.fail('child process survived timeout')
        finally:
            if pid_file.exists():
                try:
                    os.kill(int(pid_file.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_successful_analysis_keeps_assets_snapshot_and_separate_logs(self):
        with zipfile.ZipFile(self.apk, 'a') as archive:
            archive.writestr('lib/arm64-v8a/libapp.so', b'snapshot')
        scripts = {}
        for name in ('apktool', 'jadx', 'r2flutter'):
            script = self.root / name
            script.write_text('#!/usr/bin/env python3\n'
                              'import pathlib, sys\n'
                              'args = sys.argv[1:]\n'
                              'name = pathlib.Path(sys.argv[0]).name\n'
                              'if name == "r2flutter":\n'
                              '    print("[]"); print("diagnostic", file=sys.stderr)\n'
                              'else:\n'
                              '    out = pathlib.Path(args[args.index("-o" if name == "apktool" else "-d") + 1])\n'
                              '    out.mkdir(); (out / "asset.bin").write_bytes(b"retained asset")\n')
            script.chmod(0o755)
            scripts[name] = str(script)
        output = self.root / 'analysis'
        result = analyze_apk(self.apk, output, binaries=scripts)
        self.assertEqual((output / 'apktool/base/asset.bin').read_bytes(), b'retained asset')
        self.assertEqual((output / 'libapp.so').read_bytes(), b'snapshot')
        self.assertEqual(json.loads((output / 'r2flutter/header.json').read_text()), [])
        self.assertIn('diagnostic', (output / 'r2flutter/header.log').read_text())
        self.assertFalse((output / '.incomplete').exists())
        self.assertEqual(result['output_dir'], str(output))

    def test_jadx_export_with_exit_code_three_is_retained_as_partial(self):
        with zipfile.ZipFile(self.apk, 'a') as archive:
            archive.writestr('lib/arm64-v8a/libapp.so', b'snapshot')
        scripts = {}
        for name in ('apktool', 'jadx', 'r2flutter'):
            script = self.root / name
            script.write_text('#!/usr/bin/env python3\n'
                              'import pathlib, sys\n'
                              'args = sys.argv[1:]\n'
                              'name = pathlib.Path(sys.argv[0]).name\n'
                              'if name == "r2flutter": print("{}")\n'
                              'else:\n'
                              '    out = pathlib.Path(args[args.index("-o" if name == "apktool" else "-d") + 1])\n'
                              '    out.mkdir(); (out / "partial.txt").write_text("available")\n'
                              '    if name == "jadx" and "fallback" not in args: print("47 class errors", file=sys.stderr); sys.exit(3)\n')
            script.chmod(0o755)
            scripts[name] = str(script)
        output = self.root / 'analysis'
        result = analyze_apk(self.apk, output, binaries=scripts)
        jadx = result['tools']['jadx']
        self.assertEqual(jadx['status'], 'partial')
        self.assertEqual(jadx['exit_code'], 3)
        self.assertEqual(jadx['fallback'], {'status': 'ok', 'path': 'jadx/fallback', 'mode': 'fallback'})
        self.assertEqual((output / 'jadx/fallback/partial.txt').read_text(), 'available')
        self.assertEqual((output / 'jadx/partial.txt').read_text(), 'available')
        self.assertIn('47 class errors', (output / 'jadx.log').read_text())
        self.assertFalse((output / '.incomplete').exists())

    def test_failed_jadx_fallback_keeps_other_analysis_usable(self):
        with zipfile.ZipFile(self.apk, 'a') as archive:
            archive.writestr('lib/arm64-v8a/libapp.so', b'fake snapshot')
        scripts = {}
        for name in ('apktool', 'jadx', 'r2flutter'):
            script = self.root / name
            script.write_text('#!/usr/bin/env python3\n'
                              'import pathlib, sys\n'
                              'args = sys.argv[1:]\n'
                              'name = pathlib.Path(sys.argv[0]).name\n'
                              'if name == "r2flutter": print("{}")\n'
                              'else:\n'
                              '    out = pathlib.Path(args[args.index("-o" if name == "apktool" else "-d") + 1])\n'
                              '    out.mkdir(parents=True); (out / "source.txt").write_text("available")\n'
                              '    if name == "jadx": sys.exit(1 if "fallback" in args else 3)\n')
            script.chmod(0o755)
            scripts[name] = str(script)
        output = self.root / 'analysis'
        result = analyze_apk(self.apk, output, binaries=scripts)
        self.assertFalse((output / '.incomplete').exists())
        self.assertEqual(result['tools']['jadx']['status'], 'partial')
        self.assertEqual(result['tools']['jadx']['fallback']['status'], 'failed')
        self.assertIn('exited 1', result['tools']['jadx']['fallback']['error'])
        self.assertTrue((output / 'jadx-fallback.log').is_file())

    def test_search_analysis_finds_later_files_and_resumes_within_large_file(self):
        analysis = self.root / 'analysis'
        (analysis / 'jadx').mkdir(parents=True)
        (analysis / 'r2flutter').mkdir()
        (analysis / 'analysis.json').write_text('{}')
        (analysis / 'jadx' / 'A.java').write_text('irrelevant')
        (analysis / 'jadx' / 'B.java').write_text('alpha needle beta needle gamma')
        (analysis / 'r2flutter' / 'strings.json').write_text('needle')
        (analysis / 'r2flutter' / 'image.png').write_bytes(b'needle')
        first = search_analysis(analysis, 'needle', limit=1)
        self.assertEqual(first['matches'][0]['path'], 'jadx/B.java')
        second = search_analysis(analysis, 'needle', offset=first['next_offset'],
                                 scan_offset=first['next_scan_offset'], limit=2)
        self.assertEqual([item['path'] for item in second['matches']],
                         ['jadx/B.java', 'r2flutter/strings.json'])
        self.assertIsNone(second['next_offset'])

    def test_search_analysis_requires_complete_export_and_safe_source(self):
        analysis = self.root / 'analysis'
        analysis.mkdir()
        (analysis / '.incomplete').write_text('failed')
        with self.assertRaisesRegex(Ditto2Error, 'incomplete'):
            search_analysis(analysis, 'needle')
        (analysis / '.incomplete').unlink()
        (analysis / 'analysis.json').write_text('{}')
        with self.assertRaisesRegex(Ditto2Error, 'source'):
            search_analysis(analysis, 'needle', source='outside')

    def test_search_analysis_continues_after_scan_budget(self):
        analysis = self.root / 'analysis'
        (analysis / 'jadx').mkdir(parents=True)
        (analysis / 'analysis.json').write_text('{}')
        (analysis / 'jadx' / 'Large.java').write_bytes(b'x' * (17 * 1024 * 1024) + b'needle')
        first = search_analysis(analysis, 'needle', source='jadx')
        self.assertEqual(first['matches'], [])
        self.assertIsNotNone(first['next_offset'])
        second = search_analysis(analysis, 'needle', source='jadx',
                                 offset=first['next_offset'], scan_offset=first['next_scan_offset'])
        self.assertEqual(second['matches'][0]['byte_offset'], 17 * 1024 * 1024)

    def test_unification_keeps_partial_analyzer_gap_explicit(self):
        analysis, _ = self.fixture()
        manifest = json.loads((analysis / 'analysis.json').read_text())
        manifest['tools']['jadx']['status'] = 'partial'
        manifest['tools']['jadx']['exit_code'] = 3
        manifest['tools']['jadx']['fallback'] = {'status': 'failed', 'mode': 'fallback',
                                                'log': 'jadx-fallback.log'}
        (analysis / 'analysis.json').write_text(json.dumps(manifest))
        self.finding(analysis)
        review = self.unify(['home', 'settings'], [], [
            {'claim': 'Settings title', 'analyzer': 'apktool',
             'path': 'res/values/strings.xml', 'screens': ['settings']}
        ], [])
        self.assertTrue(any('JADX' in gap and 'analysis/jadx.log' in gap for gap in review['gaps']))
        self.assertTrue(any('fallback failed' in gap and 'analysis/jadx-fallback.log' in gap
                            for gap in review['gaps']))

    def test_exploration_keeps_app_installed(self):
        script = self.root / 'droidbot'
        script.write_text('#!/usr/bin/env python3\n'
                          'import hashlib, json, pathlib, sys\n'
                          'args = sys.argv[1:]\n'
                          'assert "-keep_app" in args\n'
                          'out = pathlib.Path(args[args.index("-o") + 1])\n'
                          'apk = pathlib.Path(args[args.index("-a") + 1])\n'
                          '(out / "args.json").write_text(json.dumps(args))\n'
                          '(out / "screen.png").write_bytes(b"screen")\n'
                          'graph = {"app_sha256": hashlib.sha256(apk.read_bytes()).hexdigest(), '
                          '"device_serial": args[args.index("-d") + 1], '
                          '"nodes": [{"id": "home", "image": "screen.png"}], "edges": []}\n'
                          '(out / "utg.js").write_text("var utg = " + json.dumps(graph))\n')
        script.chmod(0o755)
        result = explore_apk(self.apk, self.root / 'exploration', 'emulator-5554', binary=str(script), adb_binary=str(self.adb))
        self.assertEqual(result['screens'], 1)
        self.assertIn('-is_emulator', json.loads((self.root / 'exploration/args.json').read_text()))
        self.assertIn(str(['-s', 'emulator-5554', 'install', '-r', str(self.apk)]),
                      (self.root / 'exploration/adb.log').read_text())
        self.assertFalse((self.root / 'exploration/.incomplete').exists())

    def test_unification_preserves_unreviewed_artifacts_and_relative_paths(self):
        analysis, explore = self.fixture()
        unreviewed = explore / 'events/event_unreviewed.json'
        unreviewed.write_text('{"raw": "unreviewed action"}')
        finding = self.finding(analysis)
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = self.unify(['home', 'settings'], [], [finding], ['Unreviewed actions'])
        for name, content in before.items():
            self.assertEqual((self.root / name).read_bytes(), content)
        self.assertFalse(Path(result['findings'][0]['source']).is_absolute())
        self.assertEqual(result['candidate_counts']['events'], 2)
        self.assertTrue(unreviewed.is_file())

    def test_incomplete_export_cannot_be_unified(self):
        analysis, _ = self.fixture()
        (analysis / '.incomplete').write_text('failed run')
        with self.assertRaisesRegex(Ditto2Error, 'incomplete'):
            self.unify(['home', 'settings'], [], [self.finding(analysis)], [])

    def test_existing_review_cannot_be_overwritten(self):
        analysis, _ = self.fixture()
        finding = self.finding(analysis)
        self.unify(['home', 'settings'], [], [finding], [])
        original = (self.root / 'review.json').read_bytes()
        with self.assertRaisesRegex(Ditto2Error, 'already exists'):
            self.unify(['home', 'settings'], [], [finding], ['changed'])
        self.assertEqual((self.root / 'review.json').read_bytes(), original)

    def test_install_failure_prevents_exploring_a_stale_build(self):
        self.adb.write_text('#!/usr/bin/env python3\nimport sys\nprint("x86_64" if "getprop" in sys.argv else "INSTALL_FAILED")\nsys.exit(0 if "getprop" in sys.argv else 1)\n')
        script = self.root / 'droidbot'
        script.write_text('#!/usr/bin/env python3\nraise RuntimeError("must not explore")\n')
        script.chmod(0o755)
        with self.assertRaisesRegex(Ditto2Error, 'adb exited 1'):
            explore_apk(self.apk, self.root / 'exploration', 'test-device',
                        binary=str(script), adb_binary=str(self.adb))
        self.assertFalse((self.root / 'exploration/droidbot.log').exists())
        self.assertIn('INSTALL_FAILED', (self.root / 'exploration/adb.log').read_text())

    def test_exploration_rejects_missing_script_file(self):
        with self.assertRaisesRegex(Ditto2Error, 'script file is missing'):
            explore_apk(self.apk, self.root / 'exploration', 'emulator-5554',
                        script_path=self.root / 'missing.json', adb_binary=str(self.adb))

    def test_exploration_rejects_invalid_json_script(self):
        broken = self.root / 'broken.json'
        broken.write_text('{invalid-json')
        with self.assertRaisesRegex(Ditto2Error, 'invalid JSON'):
            explore_apk(self.apk, self.root / 'exploration', 'emulator-5554',
                        script_path=broken, adb_binary=str(self.adb))

    def test_exploration_rejects_non_dict_json_script(self):
        array_script = self.root / 'array.json'
        array_script.write_text('[1, 2, 3]')
        with self.assertRaisesRegex(Ditto2Error, 'JSON object'):
            explore_apk(self.apk, self.root / 'exploration', 'emulator-5554',
                        script_path=array_script, adb_binary=str(self.adb))

    def test_exploration_rejects_script_missing_droidbot_sections(self):
        script = self.root / 'empty.json'
        script.write_text('{}')
        with self.assertRaisesRegex(Ditto2Error, 'views'):
            explore_apk(self.apk, self.root / 'exploration', 'emulator-5554',
                        script_path=script, adb_binary=str(self.adb))

    def test_exploration_passes_script_to_droidbot_and_preserves_in_export(self):
        script_source = self.root / 'custom_script.json'
        script_content = {'views': {'btn': {'text': 'Settings'}}, 'states': {}, 'operations': {}, 'main': {}}
        script_source.write_text(json.dumps(script_content))
        script = self.root / 'droidbot'
        script.write_text('#!/usr/bin/env python3\n'
                          'import hashlib, json, pathlib, sys\n'
                          'args = sys.argv[1:]\n'
                          'assert "-script" in args\n'
                          'script_arg = pathlib.Path(args[args.index("-script") + 1])\n'
                          'out = pathlib.Path(args[args.index("-o") + 1])\n'
                          'apk = pathlib.Path(args[args.index("-a") + 1])\n'
                          'assert script_arg == out / "script.json"\n'
                          'assert script_arg.is_file()\n'
                          '(out / "screen.png").write_bytes(b"screen")\n'
                          'graph = {"app_sha256": hashlib.sha256(apk.read_bytes()).hexdigest(), '
                          '"device_serial": args[args.index("-d") + 1], '
                          '"nodes": [{"id": "home", "image": "screen.png"}], "edges": []}\n'
                          '(out / "utg.js").write_text("var utg = " + json.dumps(graph))\n')
        script.chmod(0o755)
        result = explore_apk(self.apk, self.root / 'exploration', 'emulator-5554',
                             script_path=script_source, binary=str(script), adb_binary=str(self.adb))
        self.assertEqual(result['screens'], 1)
        self.assertEqual(result.get('script'), 'script.json')
        preserved = self.root / 'exploration' / 'script.json'
        self.assertTrue(preserved.is_file())
        self.assertEqual(json.loads(preserved.read_text()), script_content)


if __name__ == '__main__':
    unittest.main()

# These compatibility cases intentionally combine a legacy static export and a modern runtime receipt.
class LegacyRuntimeDescriptorTests(unittest.TestCase):
    setUp = Ditto2CoreTests.setUp
    fixture = Ditto2CoreTests.fixture
    finding = Ditto2CoreTests.finding
    unify = Ditto2CoreTests.unify
    def test_legacy_static_cannot_admit_unverified_runtime_splits(self):
        analysis, exploration = self.fixture()
        split = self.root / 'config.apk'
        with zipfile.ZipFile(split, 'w') as archive:
            archive.writestr('AndroidManifest.xml', '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.example.app" android:versionCode="1" android:versionName="1.0" split="config.en" />')
        from inputs import describe_input
        descriptor = describe_input(self.apk, [split])
        (exploration / 'exploration.json').write_text(json.dumps({'schema_version': 1, 'input': descriptor, 'device_serial': 'emulator-5554'}))
        finding = self.finding(analysis)
        with self.assertRaisesRegex(Ditto2Error, 'legacy'):
            self.unify(['home', 'settings'], [], [finding], [])
        split.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.unify(['home', 'settings'], [], [finding], [])
