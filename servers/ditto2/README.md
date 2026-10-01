# Ditto2 MCP

Local Linux MCP for collecting evidence to rebuild Android Flutter apps. Use ARM64 for primary static analysis and x86_64 for emulator exploration. Each input can be a standalone APK or the original signed base plus an explicit complete split list. Preserve the entire evidence directory; review.json is an index, not a coverage certificate.

## Setup

Requires Python 3.13, Java, Android SDK apkanalyzer/apksigner/adb, Apktool, JADX, r2Flutter, and DroidBot. SDK discovery uses PATH, ANDROID_HOME, ANDROID_SDK_ROOT, then ~/Android/Sdk. Analyzer availability is recorded independently; descriptor validation requires the SDK inspection tools. The skill's references/setup.md documents the approved pinned DroidBot Python-wheel setup (revision cc4cc93cc53941ccc188c59ef45ab173e2347113). Other dependencies use prebuilt wheels.

```sh
uv sync --project servers/ditto2 --locked
servers/ditto2/.venv/bin/python servers/ditto2/server.py
```

## Tools

| Tool | Input and behavior |
|---|---|
| analyze_apk(apk_path, output_dir, split_paths=None) | Validate the selected delivery, decode each APK with Apktool, pass the set to JADX, and locate ARM64 libapp.so across it for r2Flutter. Each analyzer is independent. |
| explore_apk(apk_path, output_dir, device_serial, event_count=100, timeout_seconds=600, script_path=None, split_paths=None, install_mode="install") | Validate x86_64/device compatibility and install exactly the selected artifacts. Reuse mode pulls and hashes the installed delivery before any DroidBot action. Preserve app installation, scripts, logs, and raw UTG. |
| inspect_exploration(exploration_dir, offset=0, limit=50) | Page through raw screenshot/event paths for review. |
| search_analysis(analysis_dir, query, source="all", offset=0, scan_offset=0, limit=20) | Search usable finalized exports without rerunning tools. Continue with both offsets; 16 MiB scan budget per call. |
| unify_evidence(apk_path, evidence_dir, confirmed_screen_ids, confirmed_event_ids, findings, gaps, static_apk_path=None) | apk_path is the runtime base; static_apk_path optionally identifies the static base. Validate both descriptors, reviewed raw IDs and source findings; write inputs.json and review.json exclusively. |
| extract_semantic_keys(exploration_dir, output_path) | Write a new semantic_spec.json with observed debug_/key_ tokens, source hashes and view/field references. These are clues, not verified architecture. |
| deduplicate_graph(exploration_dir, output_path) | Optionally write a new graph_view.json grouping identical screenshot bytes and full state context. Preserve all original IDs/edges/actions and self-loops. |

All input paths are explicit. No sibling globbing, APK merging/re-signing, generic paywall hook, or automatic ARM64 download is performed. A compatible ARM64 Play device or supplied complete input is required; the acquisition script accepts an expected ABI. Same package/version/signers and consistent common DEX/assets are required to link different deliveries. Resource differences and native runtime coverage limits remain gaps. Original APK hashes are rechecked at handoff. Metadata/signature agreement is not proof of ABI-specific behavioral equivalence.

## Collection and failure semantics

Use fresh analysis/ and exploration/ directories for each collection. analysis.json version 2 and exploration.json version 1 retain descriptors for every artifact. Final review.json version 3 links both inputs through inputs.json. Legacy finalized same-APK evidence remains supported; legacy exports cannot establish different-delivery identity.

Per-tool statuses: ok, partial, failed, missing_tool, unsupported_abi. Collection status is complete only when all analyzers are ok, partial when at least one has usable results, failed otherwise. Finalized partial analysis remains searchable/reviewable; failed tool output is excluded. .incomplete identifies interrupted/unfinalized collection, which must be resolved before handoff. Unification requires a reviewed runtime screen and a finding from usable static output; it adds analyzer limitations automatically.

JADX exit code 3 retains partial Java and triggers a separate raw-instruction fallback; fallback failure does not discard primary exports, and fallback success does not repair Java source. Apktool outputs are under apktool/base and apktool/<split-id>; findings cite paths relative to their analyzer, e.g. base/res/values/strings.xml.

## Onboarding and review

Use script_path for recorded guided onboarding and app-specific verified paywall controls. Use reuse after persisted onboarding; keep_app leaves the app installed but does not preserve every live activity. Manual screenshots/actions outside UTG remain supplementary evidence with explicit gaps. Never manufacture graph edges or review derived IDs as raw transitions.

Greedy DFS event_count caps all input events, not unique screens. Check requested screen coverage; retry with targeted scripts or another bounded run into a fresh directory. DroidBot has an upstream API 32+ runtime failure report; valid nonempty output and reviewed screenshots establish coverage, not CLI help or a nominal process exit. Use one controller per device during exploration.

## Checks

```sh
servers/ditto2/.venv/bin/python -m unittest discover -s servers/ditto2/tests -v
bash -n ../my-skills/skills/ditto2/scripts/download-play-apks.sh
```

Fixture tests cover identities, mismatches, failures, installation/session reuse, exclusive publication, source references, derived graph preservation, acquisition, and MCP schemas. Actual app/device execution is a separate bounded integration check. Rebuilding and testing a Flutter clone are later milestones.
