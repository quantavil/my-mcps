#!/usr/bin/env python3
"""Reuse one explicitly selected Android emulator on Windows or Linux.

SDK discovery is shared with the MCP controller. Never resets app data.
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

from portable import sdk_tool, DeviceLease


class Manager:
    def __init__(self, env=None, windows=None):
        env = os.environ if env is None else env
        self.windows = os.name == "nt" if windows is None else windows
        self.adb = sdk_tool('adb', env, self.windows)
        self.emulator = sdk_tool('emulator', env, self.windows)
        self.avd = env.get("DITTO_AVD", "")
        port = env.get("DITTO_EMULATOR_PORT", "5554")
        timeout = env.get("DITTO_BOOT_TIMEOUT", "300")
        if not re.fullmatch(r"[0-9]+", port) or not 1024 <= int(port) <= 65534 or int(port) % 2:
            raise ValueError("DITTO_EMULATOR_PORT must be an even port between 1024 and 65534")
        if not re.fullmatch(r"[1-9][0-9]*", timeout):
            raise ValueError("DITTO_BOOT_TIMEOUT must be a positive integer")
        self.port = port
        self.serial = f"emulator-{port}"
        self.boot_timeout = int(timeout)
        self.gpu = env.get("DITTO_GPU") or "auto"
        self.accel = env.get("DITTO_ACCEL") or "auto"
        if self.accel not in ("auto", "on", "off"):
            raise ValueError("DITTO_ACCEL must be auto, on, or off")
        self.log = Path(env.get("DITTO_EMULATOR_LOG") or Path(tempfile.gettempdir()) / f"ditto-emulator-{port}.log")

    def run(self, args, timeout=15):
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=True)
        return result.stdout.strip()

    def adb_command(self, *args, timeout=15):
        return self.run([self.adb, "-s", self.serial, *args], timeout=timeout)

    def require_avd(self):
        if not self.avd:
            raise ValueError("Set DITTO_AVD to the AVD to use")

    def state(self):
        for line in self.run([self.adb, "devices"]).splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[0] == self.serial:
                return fields[1]
        return None

    def identity(self):
        lines = self.adb_command("emu", "avd", "name").splitlines()
        actual = lines[0].strip() if lines else ""
        if actual != self.avd:
            raise RuntimeError(f"{self.serial} is running {actual!r}, expected {self.avd!r}")

    def require_ready_device(self):
        state = self.state()
        if state != "device":
            raise RuntimeError(f"{self.serial} is {state or 'not attached'}; restore its ADB connection")
        self.identity()

    def wait_for_boot(self):
        deadline = time.monotonic() + self.boot_timeout
        while time.monotonic() < deadline:
            remaining = max(0.01, deadline - time.monotonic())
            try:
                completed = self.adb_command("shell", "getprop", "sys.boot_completed", timeout=min(5, remaining))
                if completed == "1":
                    # A successful launch does not claim any app UI is stable.
                    return
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                pass
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))
        raise RuntimeError(f"Boot timed out after {self.boot_timeout}s; inspect {self.log}")

    def start(self, headless=False):
        self.require_avd()
        state = self.state()
        if state == "device":
            self.identity()
            print(f"Reusing {self.serial} ({self.avd})")
        elif state is not None:
            raise RuntimeError(f"{self.serial} is {state}; restore its ADB connection before starting")
        else:
            if self.avd not in self.run([self.emulator, "-list-avds"]).splitlines():
                raise ValueError(f"AVD not found: {self.avd}")
            args = [self.emulator, "-avd", self.avd, "-port", self.port, "-gpu", self.gpu, "-accel", self.accel]
            if headless:
                args += ["-no-window", "-no-audio", "-no-boot-anim"]
            detach = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
                      if self.windows else {"start_new_session": True})
            with self.log.open("ab") as log:
                subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                 close_fds=True, **detach)
            print(f"Starting {self.serial} ({self.avd}, gpu={self.gpu}, accel={self.accel}); log: {self.log}")
        self.wait_for_boot()
        self.identity()
        print(f"Ready: {self.serial} (Android boot completed; assert app state before capture)")
        print(self.adb_command("shell", "wm", "size"))
        print(self.adb_command("shell", "wm", "density"))

    def stop(self):
        self.require_avd()
        self.require_ready_device()
        print(self.adb_command("emu", "kill"))

    def check(self):
        print(f"adb: {self.adb}\nemulator: {self.emulator}\navd: {self.avd or '<unset>'}\ngpu: {self.gpu}")
        # Diagnostic only: a failed accelerator check still permits device listing.
        try:
            print(self.run([self.emulator, "-accel-check"]))
        except subprocess.CalledProcessError as exc:
            print(f"Acceleration check failed: {exc.stdout or exc.stderr}", file=sys.stderr)
        print(self.run([self.adb, "devices", "-l"]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    for command in ("start", "start-headless", "status", "check", "stop"):
        commands.add_parser(command)
    args = parser.parse_args(argv)
    lease = None
    try:
        manager = Manager()
        if args.command in ("start", "start-headless", "stop"):
            lease = DeviceLease(manager.serial)
            lease.acquire()
        if args.command in ("start", "start-headless"):
            manager.start(headless=args.command == "start-headless")
        elif args.command == "stop":
            manager.stop()
        elif args.command == "check":
            manager.check()
        else:
            print(manager.run([manager.adb, "devices", "-l"]))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if lease is not None:
            lease.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
