# Ditto local MCP integration

The four `ditto-*` entries provide JADX, Apktool, r2Flutter, and mobile-control.
The duplicate JADX GUI and Apktool server entries were removed; their local CLIs
remain installed for the Ditto wrappers. The configured `mobile-mcp` runs the
open-source Mobile Next package locally against devices on this machine; its
live device-list call succeeded on 2026-09-23. Local emulator use needs no Mobile
Next Cloud account or plan. The cloud service is optional and is not configured
here. The separate
`dart` entry uses the official Dart MCP
for Flutter clone development. All Ditto backends run locally; mobile control
uses the Android SDK and a local emulator inside its MCP server.

## Capability contract

The three analyzer servers expose `analyze_package(apk_path, output_dir)`. They
run a real analyzer, bind the receipt to the supplied APK SHA-256, and publish an
immutable directory containing `receipt.json`, `result.json`, and analysis files.
Failed, unsupported, or oversized analysis leaves no healthy export. r2Flutter
requires an ARM64 Flutter AOT `libapp.so`; debug APKs without it are unsupported.
FlutterDec was removed from configuration, bridge code, and the generator after
its APK probes failed; the historical findings below explain the removal.

`ditto-mobile-control` exposes one `mobile_control` tool with `probe`, `begin`,
`preview_begin`, `perform`, `replay`, `run_checkpoints`, `capture`, `recover`, `finalize`, and `abort` operations. Probe binds a named local emulator
to a real installed APK and tests launch, tap, type, swipe, back, screenshot,
and hierarchy capture. Probe and capture reuse an installed APK only after verifying its exact hash; otherwise they install the supplied APK. Capture
exports declared PNG, XML, trace, or state artifacts with `capture.json`.
Use `tap_target` to wait for a control and tap it, and a unique `expect` marker
on each checkpoint to guard screen transitions. Fixed waits are for states without usable targets.
`recorder_control` opens a localhost browser walkthrough inside mobile-control.
Humans navigate freely with taps, swipes, Back and supported text; a phase checklist
guides them without enforcing action order. Separate workers refresh stills and
save candidate PNG/XML during pauses; optional Save screen bookmarks are available.
Input never waits for hierarchy capture. `actions.jsonl` preserves attempts,
results and detours; `exploration.json` links candidates to input positions.
AI reviews candidate images and promotes selected checkpoints in one batch, preserving
package/device identity, capture times, and the actual action log. Original capture
does not require a second walkthrough or replay. Human navigation need not match
an AI-written action protocol. Selection establishes the reference state, not proof
of persistence, network behavior, or an unobserved interaction.

For clone capture the same panel shows the frozen original beside the live Android
screen. The human navigates and selects **Capture & compare**, then **Next** and
**Done**. Capture & compare saves settled evidence and invokes the skill's shared
import/comparison operation; it displays the resulting original/clone/diff image.
The skill checkout is supplied explicitly, so the MCP does not duplicate its diff
engine. Optional notes help AI review the batch. AI implements corrections; the
human revisits affected checkpoints. There is no automatic navigation-owner handoff.

Selected phase files have stable names. Successful replacement updates current
metadata and clears obsolete verdicts; failed capture must not publish a new pass.
The original stays frozen unless replacement is explicitly requested. One current
report links the evidence and records the APK identities actually tested.
Done exports the walkthrough and releases the device; stop closes the panel.
Direct emulator clicks are not recorded. Low-level replay tools remain available
for explicit automation and diagnostics; they are not required by this human workflow.
For hot-reload development, start the clone with `flutter run`, then call
`preview_begin(serial, target_id, package_name)` on the already-running app.
`perform`, `replay`, `observe_screen`, and `inspect_ui` work in preview; `abort`
ends it. Preview does not install or hash an APK and cannot call `capture`,
`run_checkpoints`, or `finalize`. Its images are not Ditto evidence. Stop the
Flutter run session and start an APK-bound capture for phase comparison.
Network and Flutter semantics capture are unavailable and fail explicitly if
requested. Use only a declared fixture and checkpoint protocol; the controller
cannot determine whether a tap reached the intended app state by itself.

## Validation

Run the bridge tests after controller changes:

```text
uv run --project servers/ditto-bridge --locked python -m unittest discover -s servers/ditto-bridge/tests -p 'test_*.py'
```

Run the manager's `bun test`, `bunx tsc --noEmit`, and `bun run check` when changing
its configuration/deployment code. A listed tool or valid configuration does not
prove runtime capability. Keep real APK/emulator receipts with the project that
was tested. Reuse valid analyzer receipts; check the live capture environment.
Native Windows runtime validation remains pending. No local release builds.

## Reuse and capture behavior

`analyze_package` reuses an integrity-checked export for the same APK and analyzer
version. `query_analysis` lists files, searches literal text, or reads bounded
excerpts without rerunning analysis. Search continuation uses both `next_offset`
(file index) and `next_scan_offset` (byte position); pass both to resume. Receipts
and retained captures do not expire merely because time passed or a server restarted.

Mobile `perform` records arguments and a named protocol `step`; `replay` runs a
bounded list of those actions. `observe_screen` returns the current image. The low-level automated `capture`
operation requires `observed_state` and declared steps matching the executed record.
Human recorder capture preserves actual inputs instead of enforcing that protocol.
Both paths must reject environment drift. The agent must verify the visible result: successful
ADB input only proves command execution. `reset`, `stop`, and `restart` support
explicit lifecycle checks. A foreground state dump alone does not prove persistence.
Typing currently supports simple ASCII text; Unicode input, network capture, and
Flutter semantics are unsupported. One OS lock owns each emulator until finalize,
abort, or process exit. Independent code and analysis tasks can run concurrently.

## Linux and Windows setup

Install Python 3.11+, uv, Java, JADX, Apktool, r2Flutter, and Android SDK platform-tools,
build-tools, emulator, and a compatible AVD. The bridge is standalone Python;
analyzer binaries and SDK components remain external prerequisites. From this
checkout run:

```text
uv run --project servers/ditto-bridge --locked python servers/ditto-bridge/configure.py --dart --output ditto-mcp.json
```

Merge the generated entries into the client's MCP configuration and restart it.
The generator resolves this checkout and uv, needs no Bun, and refuses to overwrite
an existing output. Omit `--dart` when Dart is not installed. The repository-wide deploy command is `bun run deploy`.

Set `ANDROID_HOME` or `ANDROID_SDK_ROOT` for a custom SDK. Optional overrides:
`DITTO_JADX_BIN`, `DITTO_APKTOOL_BIN`, `DITTO_R2FLUTTER_BIN`,
`DITTO_ADB_BIN`, `DITTO_EMULATOR_BIN`, `DITTO_AAPT_BIN`.
Use absolute paths if GUI clients have a different PATH. Java archives, Windows
JADX/Apktool distribution launchers, and Dart's Windows launcher are resolved to
native executables without a shell. Other batch launchers need a supported native
executable or Java archive.

The mobile MCP exposes `manage_emulator(operation, avd, port=5554, gpu="auto", accel="auto")`.
Use `gpu="software"` without GPU hardware. It invokes the shared Python emulator
manager in this repository. SDK discovery has one implementation in `portable.py`;
the skill no longer ships a launcher or shell wrapper. The CLI equivalent is
`uv run --project servers/ditto-bridge --locked python servers/ditto-bridge/emulator_manager.py start`
with `DITTO_AVD` set. `DITTO_GPU` and `DITTO_ACCEL` control the CLI defaults. CPU virtualization and GPU rendering are separate;
software rendering does not make ARM-only APKs run on every x86 AVD. Choose a
compatible APK/AVD/host combination. Native Windows testing remains pending.

## Dependency and controller decisions

Both repositories use committed uv lockfiles and binary-only installation. The
bridge uses FastMCP for MCP schemas/transport and filelock for Windows/Linux device
ownership across worker threads. The skill uses Pillow for image I/O and resizing,
NumPy for image comparison, Pydantic for records, and Typer for its main CLI.
No GPU is required by these libraries. Update selected dependencies with `uv lock --upgrade-package NAME`,
then run the relevant tests before committing the refreshed lockfile.

Mobile Next remains available for exploration. Ditto phase capture keeps its direct
ADB adapter: it needs package hashes, raw hierarchy and environment checks beyond
Mobile Next's documented general UI tools. Routing through a second MCP would
retain those custom operations and add another transport. Do not operate the same
emulator through Mobile Next while Ditto owns a capture; the Ditto lock only
coordinates clients of this bridge. The dedicated controller is not a general
replacement for Mobile Next's physical-device or iOS support.

Sources: [uv locking](https://docs.astral.sh/uv/concepts/projects/sync/),
[Pillow](https://pillow.readthedocs.io/en/stable/reference/Image.html),
[filelock](https://py-filelock.readthedocs.io/en/stable/),
[Mobile Next tools](https://github.com/mobile-next/mobile-mcp#-available-mcp-tools).
