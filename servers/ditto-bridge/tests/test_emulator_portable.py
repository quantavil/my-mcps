"""Portable launcher contract tests. All process execution is mocked."""
import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from emulator_manager import Manager


class PortableEmulatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = {"DITTO_AVD": "test-avd", "DITTO_EMULATOR_LOG": str(Path(self.temp.name) / "emulator.log")}
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def test_windows_sdk_defaults_and_overrides(self):
        manager = Manager({**self.env, "LOCALAPPDATA": "C:/Users/test/AppData/Local"}, windows=True)
        self.assertEqual(Path(manager.adb).name, "adb.exe")
        self.assertEqual(Path(manager.emulator).name, "emulator.exe")
        self.assertIn("AppData", manager.adb)
        manager = Manager({**self.env, "ANDROID_HOME": "/custom sdk", "DITTO_ADB_BIN": "/override adb"}, windows=True)
        self.assertEqual(manager.adb, "/override adb")
        self.assertEqual(manager.emulator, str(Path("/custom sdk/emulator/emulator.exe")))

    def test_start_launch_arguments_and_detachment(self):
        for windows, headless, gpu in ((False, False, "auto"), (False, True, "auto"), (True, True, "host")):
            with self.subTest(windows=windows, headless=headless):
                manager = Manager({**self.env, "DITTO_GPU": gpu}, windows=windows)
                with patch.object(manager, "state", return_value=None), \
                     patch.object(manager, "run", return_value="test-avd"), \
                     patch.object(manager, "wait_for_boot") as boot, \
                     patch.object(manager, "identity") as identity, \
                     patch("emulator_manager.subprocess.Popen") as launch, \
                     patch.object(subprocess, "CREATE_NEW_PROCESS_GROUP", 512, create=True), \
                     patch.object(subprocess, "DETACHED_PROCESS", 8, create=True):
                    events = []
                    boot.side_effect = lambda: events.append("boot")
                    identity.side_effect = lambda: events.append("identity")
                    manager.start(headless)
                    args = launch.call_args.args[0]
                    self.assertEqual(args[args.index("-gpu") + 1], gpu)
                    self.assertEqual(args[args.index("-accel") + 1], "auto")
                    self.assertEqual("-no-window" in args, headless)
                    self.assertNotIn("-wipe-data", args)
                    self.assertEqual(events, ["boot", "identity"])
                    if windows:
                        self.assertEqual(launch.call_args.kwargs["creationflags"], 520)
                    else:
                        self.assertTrue(launch.call_args.kwargs["start_new_session"])

    def test_unready_device_never_launches(self):
        for state in ("offline", "unauthorized"):
            manager = Manager(self.env)
            with patch.object(manager, "state", return_value=state), patch("emulator_manager.subprocess.Popen") as launch:
                with self.assertRaisesRegex(RuntimeError, state):
                    manager.start()
                launch.assert_not_called()

    def test_existing_device_identity_is_checked_twice_without_launch(self):
        manager = Manager(self.env)
        with patch.object(manager, "state", return_value="device"), \
             patch.object(manager, "identity") as identity, \
             patch.object(manager, "wait_for_boot"), \
             patch.object(manager, "adb_command"), \
             patch("emulator_manager.subprocess.Popen") as launch:
            manager.start()
            self.assertEqual(identity.call_count, 2)
            launch.assert_not_called()

    def test_identity_mismatch_rejected(self):
        manager = Manager(self.env)
        with patch.object(manager, "adb_command", return_value="wrong-avd\nOK"):
            with self.assertRaisesRegex(RuntimeError, "expected"):
                manager.identity()

    def test_requires_explicit_avd_and_refuses_to_stop_wrong_device(self):
        with self.assertRaisesRegex(ValueError, 'DITTO_AVD'):
            Manager({}).require_avd()
        manager = Manager(self.env)
        with patch.object(manager, 'state', return_value='device'), patch.object(
                manager, 'adb_command', return_value='other-avd') as adb:
            with self.assertRaisesRegex(RuntimeError, 'expected'):
                manager.stop()
            self.assertEqual(adb.call_args.args, ('emu', 'avd', 'name'))

    def test_boot_timeout_is_bounded(self):
        manager = Manager({**self.env, "DITTO_BOOT_TIMEOUT": "1"})
        with patch("emulator_manager.time.monotonic", side_effect=[0, 0, 0, 0.5, 2]), \
             patch("emulator_manager.time.sleep"), \
             patch.object(manager, "adb_command", return_value="0") as adb:
            with self.assertRaisesRegex(RuntimeError, "Boot timed out"):
                manager.wait_for_boot()
            self.assertLessEqual(adb.call_args.kwargs["timeout"], 1)

    def test_failed_adb_query_propagates_without_launch(self):
        manager = Manager(self.env)
        with patch.object(manager, "run", side_effect=subprocess.CalledProcessError(1, ["adb"])), \
             patch("emulator_manager.subprocess.Popen") as launch:
            with self.assertRaises(subprocess.CalledProcessError):
                manager.start()
            launch.assert_not_called()

    def test_configuration_rejects_bad_ports_and_timeouts(self):
        for overrides in ({"DITTO_EMULATOR_PORT": "5555"}, {"DITTO_EMULATOR_PORT": "abc"}, {"DITTO_BOOT_TIMEOUT": "0"}):
            with self.assertRaises(ValueError):
                Manager({**self.env, **overrides})


if __name__ == "__main__":
    unittest.main()
