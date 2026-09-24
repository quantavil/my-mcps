"""Local Ditto MCP interfaces. Each configured server selects one DITTO_ROLE."""
import os
import threading
from pathlib import Path
import subprocess
import sys
from typing import Literal

from fastmcp import FastMCP
from fastmcp.utilities.types import Image

from bridge import MOBILE, analyze_package as run_analysis, query_analysis as query_export
from recorder import Recorder

ROLE = os.environ.get('DITTO_ROLE', '')
if ROLE not in ('jadx', 'apktool', 'r2flutter', 'mobile-control'):
    raise SystemExit('DITTO_ROLE must name one Ditto capability')

mcp = FastMCP(f'Ditto {ROLE} local bridge')
controller_lock = threading.RLock()
RECORDER = None

if ROLE == 'mobile-control':
    @mcp.tool()
    def manage_emulator(operation: Literal['start', 'start-headless', 'status', 'check', 'stop'],
                        avd: str, port: int = 5554,
                        gpu: Literal['auto', 'software', 'host'] = 'auto',
                        accel: Literal['auto', 'on', 'off'] = 'auto') -> str:
        """Start/reuse or inspect a local AVD. Software GPU needs no graphics hardware.

        Uses shared SDK discovery on Windows and Linux. Startup waits up to 300
        seconds for Android boot, not app readiness. Never clears app data.
        """
        with controller_lock:
            if MOBILE.stage is not None and operation == 'stop':
                raise ValueError('finalize or abort the active capture before managing its emulator')
            env = {**os.environ, 'DITTO_AVD': avd, 'DITTO_EMULATOR_PORT': str(port),
                   'DITTO_GPU': gpu, 'DITTO_ACCEL': accel, 'DITTO_BOOT_TIMEOUT': '300'}
            result = subprocess.run([sys.executable, str(Path(__file__).with_name('emulator_manager.py')), operation],
                                    env=env, capture_output=True, text=True, timeout=330)
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip())
            return result.stdout.strip()

    @mcp.tool()
    def observe_screen() -> Image:
        """View the current emulator screen during an active capture, before choosing an action."""
        with controller_lock:
            if MOBILE.stage is None:
                raise ValueError('begin a capture before observing')
            return Image(data=MOBILE._screen(), format='png')

    @mcp.tool()
    def inspect_ui(query: str = '', limit: int = 60) -> dict:
        """Read compact visible labels, resource IDs, and bounds without image output."""
        with controller_lock:
            return MOBILE.inspect_ui(query, limit)

    @mcp.tool()
    def recorder_control(operation: Literal['start', 'status', 'stop'],
                         contract_path: str | None = None) -> dict:
        """Open/close a localhost still-image panel for human original capture.

        Begin a package-bound mobile capture first. Human clicks in the panel
        use the same controller and receipt; no video or cloud service is used.
        """
        global RECORDER
        with controller_lock:
            if operation == 'start':
                if RECORDER is not None:
                    raise ValueError('stop the existing recorder before starting another')
                if not contract_path:
                    raise ValueError('contract_path is required')
                RECORDER = Recorder(MOBILE, contract_path, controller_lock)
                try:
                    return {'url': RECORDER.start(), 'phase_id': RECORDER.contract['phase_id']}
                except Exception:
                    RECORDER = None
                    raise
            if operation == 'status':
                return {'active': RECORDER is not None,
                        'url': RECORDER.url if RECORDER is not None else None}
            if operation == 'stop':
                if RECORDER is not None:
                    RECORDER.stop()
                    RECORDER = None
                return {'active': False}
            raise ValueError('unknown recorder operation')

    @mcp.tool()
    def mobile_control(operation: Literal['probe', 'begin', 'perform', 'replay', 'run_checkpoints', 'capture', 'recover', 'finalize', 'abort'], serial: str | None = None,
                       target_id: str | None = None, apk_path: str | None = None,
                       package_name: str | None = None,
                       output_dir: str | None = None, action: Literal['tap', 'tap_target', 'wait_target', 'type', 'swipe', 'back', 'launch', 'stop', 'restart', 'reset', 'wait'] | None = None,
                       x: int | None = None, y: int | None = None,
                       end_x: int | None = None, end_y: int | None = None,
                       text: str | None = None, duration_ms: int = 300,
                       selector: str | None = None, match: Literal['exact', 'contains'] = 'exact',
                       step: str | None = None, number: int | None = None,
                       checkpoint_id: str | None = None,
                       fixture: str | None = None, setup: str | None = None,
                       actions: list[str] | None = None,
                       kinds: list[Literal['png', 'xml', 'trace', 'state']] | None = None,
                       observed_state: str | None = None,
                       steps: list[dict] | None = None,
                       plan: list[dict] | None = None) -> dict:
        """Probe, begin, perform, replay, capture, finalize, or abort a local emulator session.

        Probe: serial, target_id (AVD name), apk_path, package_name, output_dir.
        Begin: serial, apk_path, package_name, output_dir; acquires exclusive device ownership.
        Perform: action tap/tap_target/wait_target/type/swipe/back/launch/stop/restart/reset/wait.
        Label contract actions with step; reset clears this app's data, use only for declared fixtures.
        Replay: steps (1..100 perform argument objects); stops on the first failure.
        run_checkpoints: plan entries contain steps, optional expected UI target,
        and checkpoint capture fields. Stops with completed IDs on a mismatch.
        Recover: after restarting the same emulator, verify package/environment,
        retain completed checkpoints, and replay navigation to the failed state.
        Capture: number, checkpoint_id, fixture, setup, actions, kinds, observed_state.
        actions must equal executed step labels in order. observed_state is the agent's
        observation, to be checked against PNG/XML; command success alone is not UI success.
        Finalize retains capture; abort discards incomplete capture. Both release ownership.
        """
        with controller_lock:
            if RECORDER is not None and operation in ('perform', 'replay', 'run_checkpoints',
                                                      'capture', 'finalize', 'abort'):
                raise ValueError('stop the human recorder before AI-driven capture')
            if operation in ('probe', 'begin'):
                if not serial:
                    raise ValueError('serial is required')
                MOBILE.acquire_device(serial)
                try:
                    if operation == 'probe':
                        return MOBILE.probe(serial, target_id, apk_path, package_name, output_dir)
                    return MOBILE.begin(serial, apk_path, package_name, output_dir)
                except Exception:
                    MOBILE.abort()
                    raise
                finally:
                    if operation == 'probe':
                        MOBILE.release_device()
            if operation == 'perform':
                return MOBILE.perform(action, x, y, end_x, end_y, text,
                                      duration_ms, step, selector, match)
            if operation == 'replay':
                return MOBILE.replay(steps)
            if operation == 'run_checkpoints':
                return MOBILE.run_checkpoints(plan)
            if operation == 'recover':
                return MOBILE.recover()
            if operation == 'capture':
                return MOBILE.capture(number, checkpoint_id, fixture, setup,
                                      actions, kinds, observed_state)
            if operation == 'finalize':
                result = MOBILE.finalize()
                MOBILE.release_device()
                return result
            if operation == 'abort':
                return MOBILE.abort()
            raise ValueError('unknown mobile-control operation')
else:
    @mcp.tool()
    def analyze_package(apk_path: str, output_dir: str) -> dict:
        """Analyze an APK once. Reuse output_dir across phases for the same APK/tool version.

        Existing exports are verified and reused; changed inputs need a new directory.
        Returns paths and a compact receipt, never the full decompiler output.
        """
        return run_analysis(ROLE, apk_path, output_dir)

    @mcp.tool()
    def query_analysis(output_dir: str, query: str = '', path: str | None = None,
                       offset: int = 0, limit: int = 40, scan_offset: int = 0) -> dict:
        """Search cached analysis text, list files, or read a bounded file excerpt.

        Without path: offset is a file index; query is a literal text search.
        With path: offset is a byte offset; limit controls output KiB (maximum 64).
        Follow next_offset and next_scan_offset for pagination.
        Text search ignores ASCII case; non-ASCII bytes match literally. No decompiler is rerun.
        """
        return query_export(output_dir, query, path, offset, limit, ROLE, scan_offset)

if __name__ == '__main__':
    mcp.run()
