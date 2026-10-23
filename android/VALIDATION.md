# Android port validation

Date: **2026-10-23**. Fork base: `f29a25968e483f8bb85e47829ccd487bbe229199`.
Chromium source version: **156.0.8078.47**.

## Release status

**No APK has been compiled, installed, or device-tested in this implementation
session.** This branch contains experimental port code and build tooling.
It is not a production Android release.

The available workspace has approximately 28 GiB free, below the build driver's
100 GiB fresh-checkout requirement. A full native build is therefore blocked
before downloading Chromium. No paid machine was provisioned and no release
signing key was created.

## Executed checks

| Check | Result |
|---|---|
| Existing utilities, source-fetch, and Android pruning tests | 21 passed |
| Existing development utility tests | 2 passed |
| Native Android label tests | 12 passed |
| Native build-driver tests | 29 passed |
| Complete patch-series syntax and repository configuration checks | Passed |
| Patch-series lint, added-file licenses, header guards, source whitespace | Passed |
| Utility Pylint and Android build-driver Pylint | Passed with pinned Python 3.10 dependencies |
| Generated translation source consistency and translation lint | Passed |
| Changed patch sections applied to exact pinned Chromium source | Passed: 16 ordered subsets covering 15 source paths |
| Independent implementation review | Passed after fixing Android pruning, package preflight, and escaped APK labels |
| Host preflight and dry-run | Capacity failure reported before network; dry-run creates no build output |
| Full patch-series application to the complete Chromium source | Not run |
| GN generation and `chrome_public_apk` compilation | Not run; machine capacity blocker |
| Built APK signature and manifest inspection | Not run; requires a built APK |
| Installation and Android runtime behavior | Not run; requires built APK and device/emulator |

The changed-section patch check used twelve original files retrieved from the
pinned Chromium tag plus three files created by patches. It checked every patch
touching those sections in repository order, without accepting fuzz or offsets.
This gives evidence for those changes only; it is not a complete source build.
See [upstream provenance](UPSTREAM_ANDROID_PATCHES.md).

The initial interpreter was Python 3.12. The repository's old pinned Pylint
runner crashed under that interpreter while importing standard-library modules.
Verification was moved to the repository's declared Python **3.10** instead of
changing its unrelated dependency pins. The final test run used Python 3.10.22:
**64 tests passed** across the four groups above.

The build-state checks cover repository input fingerprints, recorded stages,
Git revisions, and `.gclient` configuration. They do not detect every manual
edit or partial reset within a prepared Chromium checkout. Prepared-source
edits are unsupported, and the artifact manifest records
`source_worktree_fully_verified: false`.

## Reproduce tooling checks

After installing `.requirements.txt` with the repository's declared Python:

```bash
./devutils/run_utils_tests.sh
./devutils/run_devutils_tests.sh
python -m unittest discover -s android/tests -v
python devutils/validate_config.py
python devutils/lint.py
python android/build.py build --dry-run
```

The first two scripts use `python3` from `PATH`; activate the intended virtual
environment first. Dry-run is a command/state inspection, not a native build.

## Native build gates

1. Run the driver on a provisioned Linux x64 machine with sufficient free
   storage and memory, and the Chromium host dependencies installed.
2. Validate the full patch series against unmodified pinned sources.
3. Complete GN generation with `--fail-on-unused-args` and compile
   `chrome_public_apk` successfully.
4. Verify the APK signature, application ID `com.curibtech.helium`, native
   library ABI, minimum SDK, display name, source revision, and SHA-256.
5. Record the build log and artifact manifest, then perform the tests below.

## Android device checklist

Record the device, Android version, APK SHA-256, package ID, and observed result
for each check. Do not mark a check passed based only on code inspection.

- [ ] Install the development APK and launch **Curib Browser** without a crash.
- [ ] Confirm the configured package can coexist with Chrome and another Chromium app.
- [ ] Browse HTTPS sites, search, use back/forward, refresh, and open/close tabs.
- [ ] Test normal and private browsing, background/resume, and process recreation.
- [ ] Test navigation and input in portrait, landscape, split screen, and keyboard resize.
- [ ] Verify file downloads and uploads, including permission denial/cancellation.
- [ ] Check camera, microphone, location, notifications, and external intent prompts.
- [ ] Test certificate error behavior and mixed-content handling.
- [ ] Verify history/bookmark persistence and private-session data clearing.
- [ ] Inspect first-run onboarding and Helium service consent before relying on them.
- [ ] Test an update signed with the same intended key without losing browser data.
- [ ] Audit mobile settings, extension availability, filtering, and privacy behavior
  individually before making feature-parity claims.

Production distribution additionally requires an independently managed release
signing identity, tested updates, licensing/source distribution review, and a
process for shipping Chromium security updates. This development build path does
not establish those release gates.
