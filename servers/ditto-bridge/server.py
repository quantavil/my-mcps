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
            if (MOBILE.stage is not None or MOBILE.preview_mode) and operation == 'stop':
                raise ValueError('finalize or abort the active mobile session before managing its emulator')
            env = {**os.environ, 'DITTO_AVD': avd, 'DITTO_EMULATOR_PORT': str(port),
                   'DITTO_GPU': gpu, 'DITTO_ACCEL': accel, 'DITTO_BOOT_TIMEOUT': '300'}
            result = subprocess.run([sys.executable, str(Path(__file__).with_name('emulator_manager.py')), operation],
                                    env=env, capture_output=True, text=True, timeout=330)
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip())
            return result.stdout.strip()

    @mcp.tool()
    def observe_screen() -> Image:
        """View the current emulator screen during an active capture or preview."""
        with controller_lock:
            if MOBILE.stage is None and not MOBILE.preview_mode:
                raise ValueError('begin a capture or preview before observing')
            return Image(data=MOBILE._screen(), format='png')

    @mcp.tool()
    def inspect_ui(query: str = '', limit: int = 60) -> dict:
        """Read compact visible labels, resource IDs, and bounds without image output."""
        with controller_lock:
            if RECORDER is not None:
                raise ValueError('human recorder owns hierarchy capture; finish and stop it first')
            return MOBILE.inspect_ui(query, limit)

    @mcp.tool()
    def recorder_control(operation: Literal['start', 'status', 'finish', 'stop'],
                         contract_path: str | None = None,
                         checkpoint_ids: list[str] | None = None) -> dict:
        """Open a localhost walkthrough: free taps/swipes/back/type, automatic stills.

        Begin package-bound capture first. The checklist guides, never gates input.
        Save screen bookmarks a candidate; pauses auto-save distinct screens and
        available XML. Input logs preserve detours/failures. AI reviews candidates
        and validates replay while collecting declared phase checkpoints.
        Finish exports exploration.json/actions.jsonl/PNGs/XML and releases the device.
        Stop closes the panel; then finish to keep candidates or mobile abort to discard.
        No video, cloud service, or direct-emulator click recording.
        """
        global RECORDER
        if operation == 'stop':
            with controller_lock:
                recorder = RECORDER
            # HTTP handlers acquire this lock too; do not join them while holding it.
            if recorder is not None:
                recorder.stop()
            with controller_lock:
                if RECORDER is recorder and recorder is not None and recorder.finished:
                    RECORDER = None
            return {'active': False, 'capture_pending': RECORDER is not None}
        if operation == 'finish':
            with controller_lock:
                recorder = RECORDER
            if recorder is None:
                raise ValueError('no active recorder')
            return recorder.finish()
        with controller_lock:
            if operation == 'start':
                if MOBILE.preview_mode:
                    raise ValueError('human recorder requires an evidence capture, not a preview')
                if RECORDER is not None:
                    raise ValueError('stop the existing recorder before starting another')
                if not contract_path:
                    raise ValueError('contract_path is required')
                RECORDER = Recorder(MOBILE, contract_path, controller_lock, checkpoint_ids)
                try:
                    return {'url': RECORDER.start(), 'phase_id': RECORDER.contract['phase_id']}
                except Exception:
                    RECORDER = None
                    raise
            if operation == 'status':
                return {'active': RECORDER is not None,
                        'url': RECORDER.url if RECORDER is not None and RECORDER.server is not None else None,
                        'progress': RECORDER.status() if RECORDER is not None else None}
            raise ValueError('unknown recorder operation')

    @mcp.tool()
    def mobile_control(operation: Literal['probe', 'begin', 'preview_begin', 'perform', 'replay', 'run_checkpoints', 'capture', 'recover', 'finalize', 'abort'], serial: str | None = None,
                       target_id: str | None = None, apk_path: str | None = None,
                       package_name: str | None = None,
                       output_dir: str | None = None, action: Literal['tap', 'tap_target', 'wait_target', 'type', 'swipe', 'back', 'launch', 'stop', 'restart', 'reset', 'wait'] | None = None,
                       x: int | None = None, y: int | None = None,
                       end_x: int | None = None, end_y: int | None = None,
                       text: str | None = None, duration_ms: int = 300,
                       target_timeout_ms: int = 5000,
                       selector: str | None = None, match: Literal['exact', 'contains'] = 'exact',
                       step: str | None = None, number: int | None = None,
                       checkpoint_id: str | None = None,
                       fixture: str | None = None, setup: str | None = None,
                       actions: list[str] | None = None,
                       kinds: list[Literal['png', 'xml', 'trace', 'state']] | None = None,
                       observed_state: str | None = None,
                       steps: list[dict] | None = None,
                       plan: list[dict] | None = None, plan_path: str | None = None) -> dict:
        """Probe, begin, perform, replay, capture, finalize, or abort a local emulator session.

        Probe: serial, target_id (AVD name), apk_path, package_name, output_dir.
        Begin: serial, apk_path, package_name, output_dir; acquires exclusive device ownership.
        Preview_begin: serial, target_id, package_name; controls an already running
        debug app without installation, receipts, or eligible phase evidence.
        Perform: action tap/tap_target/wait_target/type/swipe/back/launch/stop/restart/reset/wait.
        tap_target waits up to target_timeout_ms (default 5000, max 30000) and
        taps the resolved node without a redundant hierarchy lookup.
        Label contract actions with step; reset clears this app's data, use only for declared fixtures.
        Replay: steps (1..100 perform argument objects); stops on the first failure.
        run_checkpoints: plan entries contain steps, optional unique expected UI
        target, optional expect_timeout_ms (default 5000, max 30000), and
        checkpoint capture fields. Waits for the target before capturing and
        stops with completed IDs on a mismatch.
        Or supply plan_path to capture.json (replay.plan) or a reviewed {plan: [...]} file.
        File-loaded plans require a unique expect marker at every checkpoint;
        review selectors and fixtures before reuse on a different app/build.
        Recover: after restarting the same emulator, verify package/environment,
        retain completed checkpoints, and replay navigation to the failed state.
        Capture: number, checkpoint_id, fixture, setup, actions, kinds, observed_state.
        actions must equal executed step labels in order. observed_state is the agent's
        observation, to be checked against PNG/XML; command success alone is not UI success.
        Finalize retains capture; abort ends a capture or preview. Both release ownership.
        """
        global RECORDER
        with controller_lock:
            if RECORDER is not None:
                if operation == 'abort' and RECORDER.stopped:
                    RECORDER = None
                else:
                    raise ValueError('finish/stop the human recorder before AI-driven control; stop then abort to discard')
            if operation in ('probe', 'begin', 'preview_begin'):
                if not serial:
                    raise ValueError('serial is required')
                MOBILE.acquire_device(serial)
                try:
                    if operation == 'probe':
                        return MOBILE.probe(serial, target_id, apk_path, package_name, output_dir)
                    if operation == 'preview_begin':
                        if not target_id:
                            raise ValueError('target_id is required for preview')
                        return MOBILE.preview_begin(serial, target_id, package_name)
                    return MOBILE.begin(serial, apk_path, package_name, output_dir)
                except Exception:
                    MOBILE.abort()
                    raise
                finally:
                    if operation == 'probe':
                        MOBILE.release_device()
            if operation == 'perform':
                return MOBILE.perform(action, x, y, end_x, end_y, text,
                                      duration_ms, step, selector, match, target_timeout_ms)
            if operation == 'replay':
                return MOBILE.replay(steps)
            if operation == 'run_checkpoints':
                return MOBILE.run_checkpoints(plan, plan_path)
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
