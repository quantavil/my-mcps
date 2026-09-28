# Ditto2 MCP

Local Linux MCP for the evidence stage of Android Flutter reconstruction. The handoff is an evidence directory containing complete analyzer exports, assets, screenshots, states, actions and navigation data. `review.json` connects reviewed items; it does not replace the collection or certify coverage.

## Setup

Requires Python 3.13, Java, Apktool, JADX, r2Flutter, Android SDK `adb`, and DroidBot on `PATH`. A device is needed only for actual exploration. DroidBot is installed separately so its legacy dependencies do not constrain the MCP. The validated upstream revision is `cc4cc93cc53941ccc188c59ef45ab173e2347113`; an existing wheel from that revision can be installed without building:

```sh
uv tool install --python 3.13 --no-build --with 'setuptools<81' --with standard-telnetlib /path/to/droidbot.whl
```

From `my-mcps`:

```sh
uv sync --project servers/ditto2 --locked
servers/ditto2/.venv/bin/python servers/ditto2/server.py
```

## Tools

1. `analyze_apk(apk_path, output_dir=".../evidence/analysis")`: run all three required analyzers and retain their exports. Requires ARM64 Flutter AOT.
2. `explore_apk(apk_path, output_dir=".../evidence/exploration", device_serial)`: bounded DroidBot exploration. Explicitly installs the supplied APK with `adb install -r` before exploration, then leaves it installed. An install failure stops the run; it never falls back to a previously installed build.
3. `inspect_exploration(exploration_dir, offset, limit)`: page through screenshot and event paths; `next_offset` continues both lists until exhausted.
4. `unify_evidence(apk_path, evidence_dir, confirmed_screen_ids, confirmed_event_ids, findings, gaps)`: validate the completed exports and write a new review index. Findings contain `claim`, `analyzer`, `path` relative to that analyzer, and optional `screens`/`events` lists. Open screenshots, event records and cited files before confirming them.

Keep the entire evidence directory. Paths inside the review index are relative to it, except the original APK path. Unreviewed artifacts remain available for later investigation. Unification requires at least one reviewed screen and cited finding; that is a validity check, not a claim that enough evidence exists to clone the whole app. Record coverage limits in `gaps`. JADX exit code 3 means some classes failed to decompile; Ditto2 keeps the generated source, marks it partial, and automatically cites its log in the review gaps.

Existing exports and review indexes are never overwritten. Failures retain partial output and logs under `.incomplete`; failed exports cannot be unified. Resolve the cause and use fresh output directories for another run. DroidBot's graph omits some ineffective or repeated actions; their raw event files remain available but cannot be confirmed as graph transitions.

DroidBot has an [open upstream report for Android API 32+](https://github.com/honeynet/droidbot/issues/154). Check the device API before exploration; if DroidBot exits without a valid graph, retain and report its `.incomplete` logs instead of treating the run as coverage.

## Checks

```sh
servers/ditto2/.venv/bin/python -m unittest discover -s servers/ditto2/tests -v
```

Fixture tests exercise tool failures, process cleanup, reference validation and the MCP interface without an APK or device. Real app fidelity requires a later live run.
