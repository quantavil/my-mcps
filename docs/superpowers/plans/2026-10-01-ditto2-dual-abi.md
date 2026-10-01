# Ditto2 dual ABI evidence implementation plan

> **For agentic workers:** Use the executing-plans skill to implement this plan task by task in the current session. Subagents require explicit authorization. Steps use checkbox syntax for tracking.

**Goal:** Connect ARM64 static evidence and x86_64 runtime evidence reliably while retaining usable partial results and original artifacts.

**Architecture:** Extend the existing MCP calls with explicit split lists and export-level input descriptors; connect them at unification. Keep onboarding in the existing script workflow and derived utilities separate from authoritative raw evidence.

**Tech Stack:** Python >=3.13,<3.14, existing FastMCP/Pydantic, unittest, Bash, Android SDK apkanalyzer/apksigner/ADB, Apktool, JADX, r2Flutter, installed DroidBot.

**Spec:** [Approved-scope design](../specs/2026-10-01-ditto2-dual-abi-design.md). This document formalizes the scope agreed in chat; implementation has not started.

## Global constraints

- Python remains >=3.13,<3.14; no new Python dependencies.
- Preserve all original APKs, raw states/events/screenshots/graphs, and existing review indexes.
- Two logical app inputs; standalone APKs and explicitly selected split sets are supported.
- No shared-artifact caching, automatic ABI downloads from unrelated sources, generic paywall framework, separate pre-seeding API, multi-run graph merging, or new graph viewer.
- Keep existing public required arguments; extend optional arguments and version export schemas.
- Retain event_count 1..1000 and timeout_seconds 30..3600 and selected-device subprocess behavior.
- Preserve unrelated and existing uncommitted changes in both repositories.

## Review focus

- A supplied ABI split must not masquerade as a standalone/base APK: Task 1 tests manifest split identity.
- Same package/version but altered shared code must not pass cross-ABI linking: Task 4 tests common DEX/assets mismatch.
- An existing wrong installed delivery must fail reuse before DroidBot runs: Task 3 tests pulled artifact mismatch.
- Analyzer files created before a nonzero exit must not be reported as usable automatically: Task 2 tests failed output exclusion.
- Identical screenshots with different interaction state must not be merged: Task 6 tests differing view/context payloads.

## File map

Paths below are relative to `/home/quantavil/Documents/my-mcps` unless explicitly marked otherwise.

- Create `servers/ditto2/inputs.py`: descriptor models, SDK discovery, signature/manifest validation, compatibility comparisons.
- Modify `servers/ditto2/core.py`: analysis, exploration, search, unification and compatibility migration; retain subprocess/export helpers.
- Modify `servers/ditto2/server.py`: optional input arguments and derived utility tool schemas.
- Create `servers/ditto2/derived.py`: semantic extraction and conservative graph grouping.
- Create `servers/ditto2/tests/test_inputs.py`, `test_derived.py`, `test_acquisition.py`; extend `test_core.py` and `test_server.py`.
- Modify `servers/ditto2/README.md` and only the Ditto2 portions of `docs/ditto-mcp-integration.md`.
- In `/home/quantavil/Documents/my-skills`, modify `skills/ditto2/SKILL.md`, `references/play-apk.md`, `references/setup.md`, and `scripts/download-play-apks.sh`.

## Task 1: Validate explicit app inputs

**Interfaces:** In inputs.py define `describe_input(apk_path: str, split_paths: list[str] | None = None) -> dict`, `verify_input_files(descriptor: dict) -> None`, and `compare_inputs(static: dict, runtime: dict) -> dict` returning `{compatible: bool, errors: list[str], gaps: list[str]}`. Descriptor fields and matching rules follow the spec; descriptor schema_version starts at 1.

- [x] Write test_inputs.py fixture tests using mocked SDK outputs and real ZIP fixtures. Assert standalone and base+ARM64 split descriptors include every artifact hash and ABI; reordered splits produce the same input_id; paths do not affect input_id.
- [x] Add rejection tests for split-as-base, mixed signers/package/version, duplicate split IDs, unresolved uses-split dependencies, invalid signatures, missing SDK tools, and changed files. Assert common DEX/assets divergence makes compare_inputs incompatible, and resource differences produce gaps.
- [x] Run `.venv/bin/python -m unittest discover -s tests -p test_inputs.py -v` from servers/ditto2; new API tests fail before implementation.
- [x] Implement SDK discovery and descriptor helpers. Decode manifest XML with `apkanalyzer manifest print`; verify signing with `apksigner verify --print-certs`, not ZIP certificate filenames. Use subprocess argument lists, bounded timeouts, and controlled errors. Reject unsupported signature identity rather than weakening comparison.
- [x] Rerun the focused tests until they pass. Commit only this task's files if commits are requested or the execution workflow requires them; never stage unrelated changes.

## Task 2: Analyze complete inputs without losing partial evidence

**Interfaces:** Extend core/server `analyze_apk(apk_path, output_dir, split_paths=None)`; preserve core's existing binaries injection. analysis.json schema_version 2 records input descriptor, collection_status, and tools. Existing source names remain apktool/jadx/r2flutter.

- [x] Extend test_core.py: ARM64 snapshot in an explicit split succeeds; standalone succeeds; missing ARM64 still invokes Apktool/JADX; missing one executable does not stop the others; analyzer nonzero output is excluded from usable results; invalid r2Flutter JSON records failure; JADX exit 3 retains primary/fallback data and gaps.
- [x] Run focused tests and confirm the changed-behavior assertions fail against current code.
- [x] Validate inputs before executing analyzers. Export Apktool per artifact under apktool/base and apktool/<split-id>; invoke JADX with the explicit coherent input list. Locate ARM64 snapshot across inputs and preserve existing extraction size/symlink protections.
- [x] Catch and record each analyzer's failure independently. Write the final manifest after all analyzers are attempted; reserve .incomplete for unfinished collection and unexpected interruption. Record output/log paths and collection_status according to the spec, without requiring parallel processes.
- [x] Extend search_analysis to consume usable directories from finalized version-2 manifests and retain version-1 support. Test partial-export search, failed-output exclusion, and interrupted-export rejection.
- [x] Run `.venv/bin/python -m unittest discover -s tests -p test_core.py -v`; expect all relevant tests passing. Update old assertions that deliberately encoded fail-fast behavior.

## Task 3: Explore only the selected, verified runtime input

**Interfaces:** Extend public explore_apk optional arguments with `split_paths: list[str] | None = None` and `install_mode: Literal['install', 'reuse'] = 'install'`. Preserve script_path and core binary injection. Write exploration.json schema_version 1 with input descriptor, serial, install_mode, copied script reference and collection status.

- [x] Add tests that install commands include exactly the supplied artifacts, reject incompatible native ABI, ignore unrelated sibling APKs, and stop on installation failure.
- [x] Add reuse tests using mocked pm path and temporary pulls: correct installed hashes skip installation; wrong/missing/extra delivery artifacts fail before DroidBot; pull errors keep diagnostics. Ensure no test touches a real device.
- [x] Run focused tests to verify failure before implementation.
- [x] Implement install/reuse verification using Task 1. Remove filename-glob split selection and unconditional Play installer attribution. Keep bounded commands, adb PATH propagation, script preservation, and UTG checks.
- [x] Write exploration.json only for finalized valid graph output, retaining the raw UTG base hash check and verifying all selected input files remain unchanged.
- [x] Run test_core.py and MCP interface tests. Add schema tests for split_paths/install_mode, default behavior, and forwarding optional arguments without blocking the event loop.

## Task 4: Link cross-ABI evidence and expose gaps consistently

**Interfaces:** Keep `unify_evidence(apk_path, evidence_dir, confirmed_screen_ids, confirmed_event_ids, findings, gaps)` required arguments; apk_path identifies the runtime base. Add optional `static_apk_path: str | None = None` for validating a distinct static base. Split identities come from finalized export descriptors, not a second wildcard scan. Write inputs.json schema_version 1 and review.json schema_version 3; source findings/transition IDs continue to reference raw evidence.

- [x] Add cross-ABI fixture tests with different valid file hashes, matching descriptors, and reviewed findings/transitions. Assert successful review links both input IDs and records ARM64 runtime coverage limits.
- [x] Add rejection tests for mismatched release/signers/shared code, changed split bytes, missing descriptors in different-input legacy exports, invalid review references, and existing inputs.json/review.json. Preserve identical-input legacy compatibility.
- [x] Run focused tests and confirm failures before implementation.
- [x] Update unification to validate input descriptors and Task 1 comparisons, accept finalized usable partial analyzer output, and automatically add precise missing/failed/partial analyzer gaps. Retain cited static findings and confirmed endpoint requirements.
- [x] Publish inputs.json and review.json using exclusive writes and rollback of newly written indexes on publication failure. Never overwrite evidence; reject existing indexes before writing either file.
- [x] Run test_core.py and test_server.py. Assert public schema describes the new optional argument and partial-collection behavior.

## Task 5: Acquire both ABIs and document recorded onboarding

**Interfaces:** Script usage becomes `download-play-apks.sh PLAY_URL_OR_PACKAGE [OUTPUT_DIR] [PLAY_DEVICE_SERIAL] [EXPECTED_ABI]`, default ABI x86_64; ARM64 value arm64-v8a. A supplied serial determines the device; no hidden ARM64 device acquisition. acquisition.json records package, requested/reported ABI, installed paths, artifact hashes and reported installer metadata, without claiming cryptographic provenance.

- [x] Create test_acquisition.py with a fake ADB executable and ZIP fixtures. Assert default x86_64 compatibility, explicit ARM64 acceptance, wrong device rejection, universal standalone acceptance, fresh-install listing behavior, non-absence ADB error reporting, fresh output enforcement, and no partial publication after pull/validation failure.
- [x] Run `.venv/bin/python -m unittest discover -s tests -p test_acquisition.py -v`; expect new behavior to fail initially. Resolve the script path relative to sibling my-skills or through DITTO2_SKILLS_ROOT, with an explicit skip reason when the skill checkout is unavailable.
- [x] Extend the script's ABI validation and stage acquisition.json with the pulled APKs. Replace broad pm path error suppression with explicit expected-absence handling that retains diagnostics. Do not add APK merging, re-signing, a generic downloader, or installer-flag guarantees.
- [x] Update play-apk.md and SKILL.md with paired examples using separate output directories and explicit split_paths for MCP calls. Explain the compatible ARM64 Play-device/supplied-input requirement and partial fallback.
- [x] Document guided script-based onboarding/paywall dismissal, reuse mode, bounded budgets, recorded evidence, and supplementary manual gaps in SKILL.md/setup.md. Verify script grammar against installed DroidBot rather than inventing a new walkthrough DSL.
- [x] Run acquisition tests and `bash -n` on the script. Include changes in both repositories in the review; preserve their existing patches.

## Task 6: Add small, provenance-preserving derived utilities

**Interfaces:** In derived.py define `extract_semantic_keys(exploration_dir: str, output_path: str) -> dict` and `deduplicate_graph(exploration_dir: str, output_path: str) -> dict`; expose matching MCP tools. Output paths must be new files and cannot replace source artifacts. Use existing core path/graph validators without introducing circular imports; core does not import derived.

- [x] Write test_derived.py for debug_/key_ tokens in actual DroidBot view field shapes and XML, repeated occurrences, empty captures, malformed source reporting, out-of-graph states, source paths/view references, and source preservation.
- [x] Add graph tests for exact screenshot+view/context grouping; same screenshot with different text/action flags/context stays separate; missing view data stays separate; original node IDs, all edge actions and self-loops remain mapped. Assert raw bytes are unchanged and existing output paths are rejected.
- [x] Run focused tests before implementation; implement extraction with standard-library JSON/XML/regex and graph grouping using the spec's strict signatures. Keep malformed/missing-state limitations explicit. Save semantic_spec.json/graph_view.json with schema_version 1, source hashes and source references.
- [x] Register tools in server.py with accurate read/write annotations; existing exploration inspection and unification still use raw UTG IDs. Do not automatically run deduplication, modify UTG HTML, merge runs, or infer internal model classes.
- [x] Run test_derived.py and test_server.py. Document optional use and outputs in README/SKILL.md.

## Task 7: Verify the integrated workflow and finish documentation

- [x] Update README.md tool signatures, statuses, input examples, acquisition limitations, and raw-versus-derived authority. Update only relevant Ditto2 sections of integration documentation; preserve unrelated edits.
- [x] Run `.venv/bin/python -m unittest discover -s tests -v` from servers/ditto2. Expected: all old compatibility tests and new behavior tests pass. Run skill script syntax check and inspect MCP tool schemas with FastMCP Client.
- [x] Read a generated fixture analysis.json, exploration.json, inputs.json, review.json, semantic_spec.json and graph_view.json together; verify every cited path/hash/raw ID is resolvable and no missing analyzer is presented as complete.
- [ ] For one available matching release, acquire/validate ARM64 and x86_64 inputs, run static analysis, then a bounded x86_64 script/exploration run using an explicit device serial. Review screenshots and transitions; test reuse after persisted onboarding; unify evidence and inspect both derived outputs.
- [x] If compatible inputs/device are unavailable, retain the successful fixture verification and report the exact live blocker. Do not download an unrelated version or claim live success.
- [x] Review both diffs against the spec and report changes, test results, live limitations and untouched pre-existing changes. Do not build or launch a Flutter clone in this milestone.

## Execution and acceptance

Recommended execution: implement directly in the current session, following dependencies 1 → 2/3 → 4, then 5/6 → 7. No delegation is necessary. Before implementation, inspect repository/host instructions and isolation needs again; this plan-writing turn creates documentation only.

Accepted when complete inputs work without sibling guessing, cross-ABI evidence links without bypassing identity checks, successful partial output remains usable, onboarding limits remain honest, and derived outputs preserve original evidence. Live integration remains explicitly unverified until the bounded real run succeeds.

Acceptance note: Matching ARM64 input was unavailable. Cross-ABI integration passed fixture tests; the live x86_64 run verified installed-byte reuse, two screens/transitions, semantic extraction and partial-evidence unification. Integration-document edits were omitted to preserve its pre-existing user patch. See the verification report for live limits.
