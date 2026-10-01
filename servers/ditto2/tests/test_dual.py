import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import test_inputs as input_fixtures
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core

class DualTests(unittest.TestCase):
    setUp = input_fixtures.InputTests.setUp
    apk = input_fixtures.InputTests.apk
    # Input fixture helper inherited; these cases exercise the collection contract.
    def tools(self, fail=None):
        tools = {}
        for name in ('apktool', 'jadx', 'r2flutter'):
            path = self.root / name
            path.write_text('#!/usr/bin/env python3\nimport sys,pathlib\na=sys.argv[1:]\n'
                + ('print("[]")\n' if name == 'r2flutter' else
                   'p=pathlib.Path(a[a.index("-o" if "-o" in a else "-d")+1]);p.mkdir();(p/"clue.txt").write_text("needle")\n')
                + ('sys.exit(1)\n' if name == fail else ''))
            path.chmod(0o755)
            tools[name] = str(path)
        return tools

    def test_arm64_split_snapshot_and_partial_search(self):
        base = self.apk(entries={'classes.dex': b'dex'})
        arm = self.apk('arm.apk', 'config.arm64_v8a', entries={'lib/arm64-v8a/libapp.so': b'aot'})
        result = core.analyze_apk(base, self.root / 'analysis', binaries=self.tools(fail='jadx'), split_paths=[arm])
        self.assertEqual((self.root / 'analysis/libapp.so').read_bytes(), b'aot')
        self.assertEqual(result['tools']['jadx']['status'], 'failed')
        paths = [m['path'] for m in core.search_analysis(self.root / 'analysis', 'needle')['matches']]
        self.assertIn('apktool/base/clue.txt', paths)
        self.assertFalse(any(p.startswith('jadx/') for p in paths))

    def test_missing_analyzer_does_not_suppress_others(self):
        base = self.apk(entries={'lib/arm64-v8a/libapp.so': b'aot'})
        tools = self.tools()
        tools['apktool'] = '/missing/apktool'
        result = core.analyze_apk(base, self.root / 'analysis', binaries=tools)
        self.assertEqual(result['tools']['apktool']['status'], 'missing_tool')
        self.assertEqual(result['tools']['jadx']['status'], 'ok')
        self.assertEqual(result['tools']['r2flutter']['status'], 'ok')

    def explorer(self, base, splits=(), wrong=False):
        adb = self.root / 'adb'
        mapping = {str(p): str(p) for p in (base, *splits)}
        adb.write_text('#!/usr/bin/env python3\nimport sys,shutil,json\na=sys.argv[1:]\n'
                      + 'mapping=' + repr(mapping) + '\n'
                      + 'if "getprop" in a: print("x86_64,arm64-v8a")\n'
                      + 'elif "path" in a:\n    print("\\n".join("package:"+p for p in mapping))\n'
                      + 'elif "pull" in a:\n    ' + ('open(a[-1],"wb").write(b"wrong")' if wrong else 'shutil.copyfile(mapping[a[-2]],a[-1])') + '\n'
                      + 'else: print(a)\n')
        adb.chmod(0o755)
        droid = self.root / 'droidbot'
        droid.write_text('#!/usr/bin/env python3\nimport pathlib,json,hashlib,sys\na=sys.argv[1:];o=pathlib.Path(a[a.index("-o")+1]);p=pathlib.Path(a[a.index("-a")+1]);(o/"screen.png").write_bytes(b"screen");(o/"utg.js").write_text("var utg = "+json.dumps({"app_sha256":hashlib.sha256(p.read_bytes()).hexdigest(),"device_serial":a[a.index("-d")+1],"nodes":[{"id":"home","image":"screen.png"}],"edges":[]}))\n')
        droid.chmod(0o755)
        return str(adb), str(droid)

    def test_explicit_split_install_ignores_siblings(self):
        base = self.apk()
        split = self.apk('x86.apk', 'config.x86_64', entries={'lib/x86_64/libapp.so': b'x'})
        unrelated = self.apk('split_wrong.apk', 'config.arm64_v8a')
        adb, droid = self.explorer(base, [split])
        core.explore_apk(base, self.root / 'exploration', 'test-device', split_paths=[split], adb_binary=adb, binary=droid)
        log = (self.root / 'exploration/adb.log').read_text()
        self.assertIn('install-multiple', log)
        self.assertIn(str(split), log)
        self.assertNotIn(str(unrelated), log)
        self.assertNotIn('com.android.vending', log)
        self.assertTrue((self.root / 'exploration/exploration.json').exists())

    def test_reuse_verifies_installed_hashes_before_exploration(self):
        base = self.apk()
        adb, droid = self.explorer(base)
        core.explore_apk(base, self.root / 'good', 'test-device', install_mode='reuse', adb_binary=adb, binary=droid)
        self.assertFalse((self.root / 'good/adb.log').exists())
        adb, droid = self.explorer(base, wrong=True)
        with self.assertRaisesRegex(ValueError, 'installed'):
            core.explore_apk(base, self.root / 'bad', 'test-device', install_mode='reuse', adb_binary=adb, binary=droid)
        self.assertFalse((self.root / 'bad/droidbot.log').exists())

    def collection(self):
        base = self.apk(entries={'classes.dex': b'dex'})
        arm = self.apk('arm.apk', 'config.arm64_v8a', entries={'lib/arm64-v8a/libapp.so': b'aot'})
        x86 = self.apk('x86.apk', 'config.x86_64', entries={'lib/x86_64/libapp.so': b'x86'})
        core.analyze_apk(base, self.root / 'analysis', binaries=self.tools(), split_paths=[arm])
        adb, droid = self.explorer(base, [x86])
        core.explore_apk(base, self.root / 'exploration', 'test-device', split_paths=[x86], adb_binary=adb, binary=droid)
        finding = {'claim': 'Packaged clue', 'analyzer': 'apktool', 'path': 'base/clue.txt', 'screens': ['home']}
        return base, finding

    def test_cross_abi_unification_links_deliveries(self):
        base, finding = self.collection()
        core.unify_evidence(base, self.root, ['home'], [], [finding], [])
        linked = json.loads((self.root / 'inputs.json').read_text())
        self.assertNotEqual(linked['static']['input_id'], linked['runtime']['input_id'])
        review = json.loads((self.root / 'review.json').read_text())
        self.assertTrue(any('ARM64' in g for g in review['gaps']))

    def test_changed_split_is_rejected_at_unification(self):
        base, finding = self.collection()
        (self.root / 'arm.apk').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed'):
            core.unify_evidence(base, self.root, ['home'], [], [finding], [])
        self.assertFalse((self.root / 'review.json').exists())

    def test_finalized_partial_evidence_can_be_reviewed(self):
        base, finding = self.collection()
        manifest_path = self.root / 'analysis/analysis.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['tools']['r2flutter'] = {'status': 'unsupported_abi', 'error': 'snapshot missing'}
        manifest['collection_status'] = 'partial'
        manifest_path.write_text(json.dumps(manifest))
        core.unify_evidence(base, self.root, ['home'], [], [finding], [])
        review = json.loads((self.root / 'review.json').read_text())
        self.assertTrue(any('unsupported_abi' in g for g in review['gaps']))

    def test_failed_fallback_is_not_searchable_or_citable(self):
        base = self.apk(entries={'lib/arm64-v8a/libapp.so': b'aot', 'lib/x86_64/libapp.so': b'x86'})
        tools = self.tools()
        Path(tools['jadx']).write_text('#!/usr/bin/env python3\nimport sys,pathlib\na=sys.argv[1:];p=pathlib.Path(a[a.index("-d")+1]);p.mkdir();(p/"clue.txt").write_text("needle");sys.exit(1 if "fallback" in a else 3)\n')
        core.analyze_apk(base, self.root / 'analysis', binaries=tools)
        paths = [m['path'] for m in core.search_analysis(self.root / 'analysis', 'needle')['matches']]
        self.assertIn('jadx/clue.txt', paths)
        self.assertNotIn('jadx/fallback/clue.txt', paths)
        adb, droid = self.explorer(base)
        core.explore_apk(base, self.root / 'exploration', 'test-device', adb_binary=adb, binary=droid)
        with self.assertRaisesRegex(ValueError, 'unusable'):
            core.unify_evidence(base, self.root, ['home'], [], [{'claim': 'bad', 'analyzer': 'jadx', 'path': 'fallback/clue.txt'}], [])
