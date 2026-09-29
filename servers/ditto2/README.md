# Ditto2 MCP

Local Linux MCP for the evidence stage of Android Flutter reconstruction. The handoff is an evidence directory containing complete analyzer exports, assets, screenshots, states, actions and navigation data. `review.json` connects reviewed items; it does not replace the collection or certify coverage.

## Setup

Requires Python 3.13, Java, Apktool, JADX, r2Flutter, Android SDK `adb`, and DroidBot on `PATH`. A device is needed only for actual exploration. DroidBot is installed separately so its legacy dependencies do not constrain the MCP. The Ditto2 skill's `references/setup.md` performs missing-tool setup using the approved pinned Python-wheel build; dependencies use prebuilt wheels. The validated upstream revision is `cc4cc93cc53941ccc188c59ef45ab173e2347113`; an existing wheel from that revision can be installed without building:

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
2. `explore_apk(apk_path, output_dir=".../evidence/exploration", device_serial, event_count=100, timeout_seconds=600, script_path=None)`: bounded DroidBot exploration. Tries adb on PATH, then `ANDROID_HOME`, `ANDROID_SDK_ROOT`, and `~/Android/Sdk`; passes the selected adb directory to DroidBot. Installs the supplied APK with `adb install -r` before exploration and leaves it installed. An install failure stops the run. An optional `script_path` receives a basic structure check and is copied to `script.json`; DroidBot validates the full script grammar.
3. `inspect_exploration(exploration_dir, offset, limit)`: page through screenshot and event paths; `next_offset` continues both lists until exhausted. `event_count` is a hard cap on input events across the app and Android system screens, not a screen count. DroidBot's DFS policy can spend many actions in nested pickers or unchanged/repeated controls, so a valid graph does not guarantee a particular app tab was reached. Verify target screens in the states/events; retry with a suitable budget or capture a targeted interaction separately and label it outside the DroidBot graph.
4. `search_analysis(analysis_dir, query, source="all", offset=0, scan_offset=0, limit=20)`: search saved analyzer text and return paths, byte offsets and bounded excerpts. Filter `source` to one analyzer or search all three. Continue with both returned offsets; each call scans at most 16 MiB and does not rerun analyzers.
5. `unify_evidence(apk_path, evidence_dir, confirmed_screen_ids, confirmed_event_ids, findings, gaps)`: validate the completed exports and write a new review index. Findings contain `claim`, `analyzer`, `path` relative to that analyzer, and optional `screens`/`events` lists. Open screenshots, event records and cited files before confirming them.

Keep the entire evidence directory. Paths inside the review index are relative to it, except the original APK path. Unreviewed artifacts remain available for later investigation. Unification requires at least one reviewed screen and cited finding; that is a validity check, not a claim that enough evidence exists to clone the whole app. Record coverage limits in `gaps`. JADX exit code 3 means some classes failed to decompile; Ditto2 keeps the generated source, marks it partial, and automatically cites its log in the review gaps. It also runs
`jadx -m fallback --no-res` into `analysis/jadx/fallback/`, retaining a separate
`jadx-fallback.log` and fallback status. A failed fallback is a review gap; it
does not invalidate the primary exports. Raw instructions do not repair Java source.

Existing exports and review indexes are never overwritten. Failures retain partial output and logs under `.incomplete`; failed exports cannot be unified. Resolve the cause and use fresh output directories for another run. DroidBot's graph omits some ineffective or repeated actions; their raw event files remain available but cannot be confirmed as graph transitions.

DroidBot has an [open upstream report for Android API 32+](https://github.com/honeynet/droidbot/issues/154). Check the device API before exploration; if DroidBot exits without a valid graph, retain and report its `.incomplete` logs instead of treating the run as coverage.

## Checks

```sh
servers/ditto2/.venv/bin/python -m unittest discover -s servers/ditto2/tests -v
```

Fixture tests exercise tool failures, process cleanup, reference validation and the MCP interface without an APK or device. Real app fidelity requires a later live run.
