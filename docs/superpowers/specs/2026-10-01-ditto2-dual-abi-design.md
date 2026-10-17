# Ditto2 dual ABI evidence design

## Intent and agreed scope

Collect the evidence needed to rebuild an Android Flutter app: use a complete ARM64 input for primary static analysis and a complete x86_64 input for emulator exploration. Exactly two APK files suffice when both are standalone; Play split deliveries require their base and necessary splits. Do not merge or re-sign original APKs merely to force two files.

Success means both deliveries can be validated, independently analyzed/explored, and connected in one review without falsely claiming identical binaries or complete coverage. Include small semantic extraction and optional conservative graph grouping. Defer shared-artifact caching, generic paywall detection, and a separate pre-seeding API.

## Input and identity contract

Retain existing tool names and required positional arguments. Add explicit optional `split_paths` to analysis and exploration. The supplied `apk_path` must identify the base/standalone APK; no wildcard sibling installation. Validate the base and every explicitly supplied split using Android SDK `apkanalyzer` and `apksigner`; use ZIP contents for ABI and file hashes. Discover SDK tools from PATH, ANDROID_HOME, ANDROID_SDK_ROOT, then ~/Android/Sdk. These tools are available on the inspected host, but missing tools must produce an actionable error on other hosts.

Each input descriptor contains package name, version code/name, verified signer certificate digest set, base path/hash, split IDs/paths/hashes, and native ABIs. Reject duplicate paths/IDs, invalid signatures, conflicting package/version/signers, split APKs used as a base, unresolved declared split dependencies, and source files that change during collection. Preserve a hash of the sorted descriptor's identity fields as `input_id`; absolute paths do not influence it. Keep original files available through final unification and recheck their hashes there.

Analysis and exploration exports each store their own descriptor. Unification writes `inputs.json` linking static and runtime input IDs. For different binaries require matching package, version code/name, and signer set; compare common DEX and assets entries and reject divergent bytes. Resource/configuration differences and missing common entries are recorded as comparison limits. Matching metadata alone is not proof of identical ABI-specific behavior. Preserve a review gap stating that ARM64 native behavior was not explored. Identical legacy single-APK evidence can still use the original exact-hash path; legacy exports cannot establish different-input equivalence.

## Acquisition

Extend the existing Play pull script with a fourth optional expected ABI argument, default `x86_64`; allow `arm64-v8a`. Retain the URL/package, output directory, and serial arguments. Check the selected device ABI and pull its complete installed delivery. Native libraries in a standalone universal APK may include both supported ABIs; an explicitly incompatible ABI split is rejected. Preserve original signatures and SHA-256 hashes, and export acquisition metadata. Do not label installer metadata alone as proof of Play Integrity or provenance.

The ARM64 route requires an available compatible Play device or user-supplied complete input. Do not promise that the x86_64 emulator can download ARM64 splits, silently choose an unrelated release, or add an anonymous downloader. Missing ARM64 is an explicit gap; useful Android analysis still proceeds.

## Analysis and partial evidence

Run Apktool, JADX, and r2Flutter independently. Decode all supplied APK resources/assets into separate per-artifact Apktool directories; pass the coherent APK set to JADX. Locate exactly one ARM64 libapp.so across the selected set for r2Flutter. Missing/ambiguous snapshots are recorded, not a reason to suppress Apktool/JADX.

Analyzer statuses are `ok`, `partial`, `failed`, `missing_tool`, and `unsupported_abi`. Each result records logs, output paths when usable, and errors when present. Retain the existing JADX exit-3 fallback behavior. Overall collection status is `complete` only when all required analyzers are `ok`, `partial` when at least one has usable results, and `failed` otherwise. A normally finalized manifest may describe failed analyzers; `.incomplete` remains reserved for interrupted/unfinalized collections. Failed tool output is not automatically usable just because files exist.

Search and unification accept finalized partial collections and cite their limitations. Unification still requires reviewed runtime screens and a cited finding from usable static output. Schema versioning preserves existing finalized exports where possible; never relabel an old interrupted directory as finalized automatically.

## Exploration and onboarding

Add `install_mode="install"|"reuse"` to explore_apk, default `install`. Install mode installs exactly the validated x86_64 input using ADB without blindly assigning Play installer metadata. Reuse mode verifies installed base/split bytes against the selected input using pm path and temporary pulls before any exploration; mismatch fails before actions. Confirm device ABI compatibility. Record exploration identity in exploration.json as well as the existing raw UTG base hash.

Use existing script_path for guided onboarding and app-specific paywall dismissal using verified controls. DroidBot scripts must be replayable and recorded in the same run where possible. A replayable script can be run again against a verified installed session; no new pre-seeding API is needed. Manually captured steps outside DroidBot remain a labeled supplementary collection and review gap, never fabricated graph transitions. Verify that the installed DroidBot preserves persisted state; do not equate keep_app with preserving every live activity/session.

## Derived utilities

Add `extract_semantic_keys(exploration_dir, output_path)` for state JSON and saved accessibility XML. Extract observed tokens beginning debug_ or key_ from resource IDs, text/content descriptions, and explicit key fields. Return names, source paths, field/view references, state IDs where known, and counts in semantic_spec.json. Never invent models/controllers from these tokens. Preserve keys found only outside the raw graph with that status explicit.

Add optional `deduplicate_graph(exploration_dir, output_path)` producing graph_view.json. Group only states with identical screenshot bytes and identical full relevant view payload/foreground context; exclude recording IDs/timestamps alone. Missing context leaves a state ungrouped. This intentionally under-merges. Preserve every original node ID, edge and action reference, including self-loops, in mappings; original utg.js, state/event files, screenshots, and index.html are untouched. Raw IDs remain authoritative for review and transition validation. No new interactive graph viewer or multi-run merge is required.

## Limits and validation

Python remains >=3.13,<3.14 with existing FastMCP/Pydantic dependencies; use the standard library and existing Android SDK tools. Never overwrite original evidence or existing review indexes. Preserve current bounded device-event/time limits and subprocess cleanup behavior.

Validate descriptor identity, standalone/split collection, independent analyzer failures, runtime reuse mismatch, provenance-preserving utility output, and MCP schemas with fixtures. Finish with one bounded real matching ARM64/x86_64 app collection when inputs and devices exist. If unavailable, report exactly which live checks remain unverified. Rebuilding Flutter and clone comparison are later milestones, outside this change.
