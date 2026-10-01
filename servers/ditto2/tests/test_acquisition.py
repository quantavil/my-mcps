import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPT = Path(os.environ.get('DITTO2_SKILLS_ROOT', '/home/quantavil/Documents/my-skills')) / 'skills/ditto2/scripts/download-play-apks.sh'

@unittest.skipUnless(SCRIPT.is_file(), 'Ditto2 skill checkout is unavailable')
class AcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / 'base.apk'
        self.adb = self.root / 'adb'

    def run_script(self, expected='x86_64', device='x86_64', libraries=('x86_64',), absent=False, broken=False, corrupt=False, stdout_failure=False):
        with zipfile.ZipFile(self.base, 'w') as z:
            z.writestr('AndroidManifest.xml', b'manifest')
            for abi in libraries:
                z.writestr(f'lib/{abi}/libapp.so', b'aot')
        if corrupt:
            self.base.write_bytes(b'not a zip')
        self.adb.write_text('#!/usr/bin/env python3\nimport sys,shutil\na=sys.argv[1:]\n'
            + 'if "get-state" in a: print("device")\n'
            + 'elif "getprop" in a: print(' + repr(device) + ')\n'
            + 'elif "dumpsys" in a: print("installerPackageName=com.android.vending versionCode=1 versionName=1.0")\n'
            + 'elif "path" in a:\n'
            + '    if a[-1]=="com.android.vending": print("package:/play.apk")\n'
            + ('    else: print("stdout transport failure");sys.exit(1)\n' if stdout_failure else
               '    else: print("transport failure",file=sys.stderr);sys.exit(1)\n' if broken else
               '    else: sys.exit(1)\n' if absent else '    else: print(' + repr('package:' + str(self.base)) + ')\n')
            + 'elif "pull" in a: shutil.copyfile(a[-2],a[-1])\n')
        self.adb.chmod(0o755)
        env = {**os.environ, 'DITTO2_ADB_BIN': str(self.adb)}
        return subprocess.run(['bash', str(SCRIPT), 'com.example.app', str(self.root / 'out'), 'test-device', expected], text=True, capture_output=True, env=env)

    def test_arm64_and_universal_deliveries(self):
        result = self.run_script('arm64-v8a', 'arm64-v8a', ('arm64-v8a', 'x86_64'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.root / 'out/acquisition.json').read_text())['requested_abi'], 'arm64-v8a')
        again = self.run_script('arm64-v8a', 'arm64-v8a', ('arm64-v8a',))
        self.assertNotEqual(again.returncode, 0)

    def test_wrong_device_fails(self):
        result = self.run_script('arm64-v8a', 'x86_64')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'out/base.apk').exists())

    def test_transport_failure_is_not_reported_as_missing_app(self):
        result = self.run_script(broken=True)
        self.assertIn('transport failure', result.stderr)
        self.assertNotIn('Install com.example.app from', result.stderr)

    def test_absent_app_opens_listing(self):
        result = self.run_script(absent=True)
        self.assertIn('Install com.example.app from', result.stderr)
        self.assertFalse((self.root / 'out/base.apk').exists())

    def test_corrupt_pull_publishes_nothing(self):
        result = self.run_script(corrupt=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'out/base.apk').exists())

    def test_stdout_error_is_preserved_in_diagnostics(self):
        result = self.run_script(stdout_failure=True)
        self.assertIn('stdout transport failure', result.stderr)
