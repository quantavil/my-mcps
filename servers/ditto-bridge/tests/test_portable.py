import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from portable import DeviceLease, command, sdk_root, sdk_tool


class PortableTests(unittest.TestCase):
    def test_windows_sdk_default_and_override(self):
        self.assertEqual(sdk_root({'LOCALAPPDATA': 'C:/Users/Example/AppData/Local'}, windows=True),
                         Path('C:/Users/Example/AppData/Local/Android/Sdk'))
        self.assertEqual(sdk_root({'ANDROID_HOME': '/custom/sdk'}, windows=True), Path('/custom/sdk'))

    def test_macos_sdk_default(self):
        with patch('sys.platform', 'darwin'):
            self.assertEqual(sdk_root({}, windows=False), Path.home() / 'Library/Android/sdk')

    def test_sdk_tool_uses_shared_overrides_and_windows_paths(self):
        self.assertEqual(sdk_tool('adb', {'DITTO_ADB_BIN': '/custom adb'}, windows=True), '/custom adb')
        with patch('portable.shutil.which', return_value=None):
            self.assertEqual(sdk_tool('emulator', {'ANDROID_HOME': '/custom sdk'}, windows=True),
                             str(Path('/custom sdk/emulator/emulator.exe')))

    def test_cli_cannot_stop_an_emulator_owned_by_capture(self):
        import subprocess
        serial = 'emulator-65432'
        lease = DeviceLease(serial)
        lease.acquire()
        try:
            result = subprocess.run([sys.executable, str(Path(__file__).resolve().parent.parent / 'emulator_manager.py'), 'stop'],
                                    env={**os.environ, 'DITTO_AVD': 'unused', 'DITTO_EMULATOR_PORT': '65432'},
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 1)
            self.assertIn('owned', result.stderr)
        finally:
            lease.release()

    def test_device_owner_can_release_from_another_worker_thread(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as root, ThreadPoolExecutor(max_workers=1) as pool:
            first = DeviceLease('emulator-5554', root)
            pool.submit(first.acquire).result()
            first.release()
            second = DeviceLease('emulator-5554', root)
            second.acquire()
            second.release()

    def test_jadx_windows_launcher_uses_java_without_command_shell(self):
        with tempfile.TemporaryDirectory(prefix='ditto space ') as root:
            root = Path(root)
            (root / 'bin').mkdir()
            (root / 'lib').mkdir()
            bat = root / 'bin/jadx.bat'
            bat.touch()
            with patch.dict(os.environ, {'DITTO_JAVA_BIN': 'java-test'}):
                argv = command(str(bat), ['--version'])
            self.assertEqual(argv, ['java-test', '-cp', str(root / 'lib/*'),
                                    'jadx.cli.JadxCLI', '--version'])

    def test_jar_uses_java(self):
        with patch.dict(os.environ, {'DITTO_JAVA_BIN': 'java-test'}):
            self.assertEqual(command('/tools/apktool.jar', ['d', 'a b.apk']),
                             ['java-test', '-jar', '/tools/apktool.jar', 'd', 'a b.apk'])

    def test_two_owners_cannot_control_same_device(self):
        with tempfile.TemporaryDirectory() as root:
            first, second = DeviceLease('emulator-5554', root), DeviceLease('emulator-5554', root)
            first.acquire()
            try:
                with self.assertRaisesRegex(RuntimeError, 'owned'):
                    second.acquire()
            finally:
                first.release()
            second.acquire()
            second.release()


if __name__ == '__main__':
    unittest.main()
