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
`perform`, `replay`, `capture`, `finalize`, and `abort` operations. Probe binds a named local emulator
to a real installed APK and tests launch, tap, type, swipe, back, screenshot,
and hierarchy capture. A capture session installs the exact supplied APK and
exports declared PNG, XML, trace, or state artifacts with `capture.json`.
Use the bounded `perform(action="wait", duration_ms=...)` action when startup
needs time before a declared checkpoint.
Network and Flutter semantics capture are unavailable and fail explicitly if
requested. Use only a declared fixture and checkpoint protocol; the controller
cannot determine whether a tap reached the intended app state by itself.

## Verification

Run `uv run --project servers/ditto-bridge --locked python -m unittest discover -s servers/ditto-bridge/tests -p 'test_*.py'`,
`bun test`, `bunx tsc --noEmit`, and `bun run check` from this repository.
Then connect to each configured MCP over stdio and call its actual tool on an
APK or emulator. A listed tool, installed CLI, or valid configuration is not a
successful capability probe. The required Ditto preflight passes only when all
four required verified receipts match the original APK and emulator. Dart MCP has its
own protocol and tool-list check and does not replace any Ditto capability.

## Local capability audit (2026-09-23)

The same locally built Flutter release APK (SHA-256
`84b21261b9a0544880a6e9099b9b59f0d252cf3be7fafa21824c34cf3e411ab4`)
was submitted through each analyzer MCP over stdio. JADX, Apktool, and
r2Flutter returned package-bound exports. JADX reported eight classes that it
could not decompile; its receipt records that limitation. FlutterDec's real
call failed on that APK with `missing symbol _kDartVmSnapshotData`, so it has
no healthy receipt. The tested APK uses Dart 3.13's combined `_kDartSnapshotData`
and `_kDartSnapshotText` symbols, which this FlutterDec loader does not support.
A separate public Flutter APK had a recognized snapshot
hash but no exact adapter registry record for its feature set. These outcomes
do not supply an optional FlutterDec export. They do not establish the revised
four-capability preflight either, because the mobile probe below used another APK.

The official Dart MCP completed a stdio tool call (`pub_dev_search`). The
mobile-control MCP completed a probe and PNG/XML/trace/state capture on the
local `floww_parity` AVD using a separate locally built debug APK (SHA-256
`c2aeccdecfa7be2ee875a037ad227fe53e549a8b8cddb46bd2b4710d041b99a2`).
Both the final probe screenshot and checkpoint screenshot visibly show the
clone screen. Earlier cold-boot attempts showed a System UI ANR dialog, which
the stricter controller rejected. The successful run used a 720x1280 local
emulator after its UI settled. The probe and capture exports are in
`/tmp/ditto-probe-mobile-verified-20260923` and
`/tmp/ditto-capture-mobile-verified-20260923`. The final receipt records the
emulator's `en-US` locale.

## Reuse and capture behavior

`analyze_package` reuses an integrity-checked export for the same APK and analyzer
version. `query_analysis` lists files, searches literal text, or reads bounded
excerpts without rerunning analysis. Search continuation uses both `next_offset`
(file index) and `next_scan_offset` (byte position); pass both to resume. Receipts
and retained captures do not expire merely because time passed or a server restarted.

Mobile `perform` records arguments and a named protocol `step`; `replay` runs a
bounded list of those actions. `observe_screen` returns the current image. Capture
requires `observed_state` and declared steps matching the executed record, and
rejects environment drift. The agent must verify the visible result: successful
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

## Latest focused audit

After the reuse changes, real stdio calls to JADX, Apktool, and r2Flutter succeeded
on the release APK above, including cache reuse and bounded queries. The same APK
failed the mobile probe: its ARM64 Flutter library could not load on the local
x86_64 AVD (`EM_AARCH64` versus `EM_X86_64`). Thus the updated four-capability
pipeline has no complete same-APK runtime pass. The earlier debug-APK capture
reported above predates these controller changes and is not proof of the new flow.

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
