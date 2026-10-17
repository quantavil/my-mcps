# Ditto2 dual-ABI verification — 2026-10-01

Implemented explicit standalone/split APK descriptors, ARM64 static and x86_64 runtime evidence linking, independent partial analyzer exports, verified installed-byte reuse, acquisition metadata, script-based onboarding guidance, semantic-key extraction and optional conservative graph grouping. Raw evidence remains authoritative.

## Verification

- Ditto2 Python suite: 63 tests passed, including split ownership, failed-output exclusion and legacy/modern receipt regression checks.
- MCP repository: 22 tests passed. Skills CLI self-test and acquisition script syntax passed.
- Synced Archify upstream tests cannot pass in the packaged skill checkout: upstream repository README/docs/benchmark files and parse5 are absent. The two changed test files likewise fail on missing repository fixtures (18 failures, one skip); no Archify implementation was edited. Diagnostics: `/home/quantavil/Documents/ditto2-dual-abi-validation/archify-sync-tests.log`.
- FastMCP lists seven tools with optional split/reuse/static-input arguments validated by schema tests.
- Real signed baby-tracker 2.9.0 / 20209000 x86_64 base + ABI/density splits validated. Reuse verified installed APK bytes before a bounded scripted run.
- Live run captured two screens, two confirmed transitions and eleven semantic keys. Optional graph grouping retained both nodes. Partial exports unified successfully; all six output manifests parsed.
- Live static analysis: Apktool succeeded; JADX failed with Java heap exhaustion; r2Flutter lacked an ARM64 snapshot. Failed analyzer output is excluded from search and citations.
- Matching ARM64 input was unavailable. Cross-ABI linkage passed fixture tests; a paired live ARM64/x86_64 run and complete onboarding/paywall journeys remain unverified.

Evidence: `/home/quantavil/Documents/ditto2-dual-abi-validation/live/review.json`. APKs and runtime captures are kept outside Git.

## Review resolutions and scope

Independent review findings were reproduced and fixed: failed JADX fallback admission, global rather than per-module split requirements, modern runtime receipts bypassed by legacy analysis, and missing stdout acquisition diagnostics. Regression tests passed after each fix.

Feature branches in existing checkouts preserve necessary dirty prerequisites and deployed symlinks; they provide branch separation, not filesystem isolation. Pre-existing bridge/config/integration-document changes remain outside the commits. The final Ditto2 implementation incorporates the existing split-install and acquisition fixes in its scope.

Sync fetched upstream archify, paperclip and qt-qml skill changes. These are included as authorized sync changes. Ditto2 local lock metadata was refreshed. MCP and skill deployment completed; Codex's managed-ditto2 entry points to the repository server and its skill symlink resolves to the updated skill. Existing MCP clients must reconnect to discover updated tools.

Acquisition metadata records signer/installer observations without certifying Play provenance. Use one controller per device. Shared caches, generic paywall detection, new pre-seeding APIs, automatic deduplication and Flutter reconstruction were excluded from this milestone.
