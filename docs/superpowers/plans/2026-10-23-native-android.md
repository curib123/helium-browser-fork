# Native Android Implementation Plan

> **For agentic workers:** Execute independent parts with clear file ownership;
> integrate and review the whole change before publishing the fork branch.

**Goal:** Give the owner's Helium fork a native Android build path without Flutter.

**Architecture:** Opt-in Android source checkout, existing Helium patch and
resource pipeline, native Chromium `chrome_public_apk`, custom Android identity.
Preserve the desktop build defaults and isolate Android build state.

**Tech Stack:** Python 3, GN, Chromium Android Java/C++, depot_tools, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-23-native-android-design.md`

## Global constraints

- Work only in the owner's fork, on `android/native-apk`.
- No Flutter or replacement WebView application.
- Default application ID `com.curibtech.helium`; display name `Curib Browser`.
- Build host Linux x64, at least 100 GiB free disk and 8 GiB RAM.
- No implicit destructive cleanup, privileged OS installation, production
  signing, upstream PR, or release publication.
- Report native compilation and device testing separately from tooling tests.

## Review focus

- Android checkout must include Linux host tools as well as target dependencies.
- A failed preparation must never be mistaken for a reusable completed stage.
- Changes in patches, architecture, identity, or versions invalidate build state.
- App labels must survive XML/Android resource escaping without source injection.
- Missing, unsigned, stale, or wrong-package APKs must not become deliverables.

## Tasks

### 1. Android dependency checkout (parent)

- [x] Add regression tests for unchanged desktop gclient defaults and Android
  custom variables/target OS/target CPU selections.
- [x] Add `--android`, `--target-cpu`, and `--nohooks` flags to `utils/clone.py`.
- [x] Reject incompatible CLI flags before touching source directories.
- [x] Run the checkout tests and existing utility suite.

### 2. Android compatibility patch integration (port reviewer)

- [x] Inspect PR #2680's exact merge-base delta and integrate it without
  overwriting newer patch changes from the fork's current base.
- [x] Record provenance and validate patch syntax/configuration.

### 3. Native build pipeline (build implementer)

- [x] Implement/test `doctor`, `fetch`, `prepare`, and `build` commands.
- [x] Preserve Helium transform order, pinned sources, and failure propagation.
- [x] Guard source reuse and verify package/signature before copying artifacts.

### 4. Android branding (branding implementer)

- [x] Verify source-supported package GN argument and native label paths at
  Chromium `156.0.8078.47`.
- [x] Implement/test `apply_branding(src: Path, app_name: str)`.

### 5. Integration, CI, documentation, and delivery (parent)

- [x] Add lightweight validation and manual self-hosted APK build workflow.
- [x] Write exact build commands, signing limitations, and device checks.
- [x] Execute all tooling tests, configuration checks, and independent review.
- [x] Record the actual build blocker; only attach an APK if it was compiled
  and independently verified.

Delivery target: the owner's `android/native-apk` branch and a draft pull
request into the same fork's `main` branch.

## Integration results

All 64 local tests pass using the pinned Python 3.10 environment. Review found
and resolved desktop pruning that deleted Android dependencies, a package-ID
configuration mismatch, and AAPT label escaping. The new pruning mode is
tested through the real utility and leaves desktop defaults unchanged.

The source-state guarantee is deliberately limited to recorded stages,
repository inputs, Git revisions, and `.gclient`; it does not attest every
prepared source file. Documentation and artifact metadata expose that limit.
Full compilation and device testing remain blocked by machine capacity and
are not marked complete. See `android/VALIDATION.md` for the evidence.
