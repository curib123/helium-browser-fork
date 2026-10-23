# Native Android port of Curib's Helium fork

## Intent and scope

Continue the owner's request to make `curib123/helium-browser-fork` into a
custom Android APK without Flutter. Preserve the actual Chromium/Helium
codebase. This work belongs to the owner's fork; it is not a contribution
request to imputnet. Existing upstream contribution policies and licenses
remain in the repository.

## Approaches considered

1. **Native Chromium Android port (selected implementation assumption).**
   Retains Helium's shared patches and Chromium's native Android browser.
   Requires Android compatibility fixes, packaging, a large Linux build
   machine, and subsequent device testing.
2. **A new Java/Kotlin WebView browser.** A much smaller APK project, but it
   would not carry over Helium's engine or extensions. Not selected because
   the owner identified this specific fork.
3. **Repackage a desktop binary.** Not a valid Android implementation.

The optional approach question received no answer. The native port is an
implementation assumption based on the linked fork, not a separately
approved product specification.

## Deliverable and architecture

- Add an opt-in Android dependency checkout to `utils/clone.py`, retaining
  desktop defaults. Pin Chromium to `chromium_version.txt` and depot_tools
  to the revision in Chromium's DEPS.
- Integrate the Android compatibility delta from upstream PR #2680,
  recording its exact source and unmerged status.
- Add `android/build.py` to preflight, fetch, prepare, compile, and verify
  `chrome_public_apk`. Keep the existing Helium transformation order.
- Use application ID `com.curibtech.helium` and label `Curib Browser` as
  editable defaults. Use Chromium's supported package GN argument, not
  global Java package substitutions.
- Keep build output isolated under `build/android`; check repository input
  fingerprints, recorded stages, Git revisions, and `.gclient` configuration
  before reusing prepared sources. Reject recorded interruptions or changed
  inputs. Manual source edits at the same Git HEAD are not fully detected;
  unsupported edits require a fresh build directory.
- Add lightweight CI for tooling and an explicitly invoked APK build job
  on a suitably provisioned self-hosted Linux x64 runner.

## Constraints and verification

This is an **experimental port**, not a release claim. The current workspace
has approximately 28 GiB free, below Chromium's documented 100 GB minimum.
Do not call tooling tests a successful native build. Compilation, APK
signature/package inspection, installation, first launch, navigation, tabs,
downloads, permissions, and update behavior are separate gates.

Use at least 100 GiB free and 8 GiB RAM in the build preflight; recommend
200 GiB free and 16 GiB or more RAM for practical development. Require a
Linux x86-64 host. Never install OS packages, reset existing sources, sign
with a personal release key, or publish a release implicitly.

Development APKs must be explicitly identified as such. Desktop Helium
extension support, uBlock Origin, onboarding, and privacy-feature parity
are unverified on Android and must not be promised.

## Validation priorities

Test dependency configuration for desktop and Android, preflight failure
before network work, stale/interrupted build detection, failure propagation,
label escaping, and refusal to present an unsigned or misidentified APK.
Run existing Python suites and repository configuration/patch checks.
Document the remaining native compilation and device-validation gates.
