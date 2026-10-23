# Curib Browser: experimental native Android port

This directory adds an Android build path to the owner's Helium fork, using
Chromium's native Android browser and Helium's shared patches. It does not use
Flutter. The default application ID is `com.curibtech.helium`, and its native
application/widget label is **Curib Browser**.

**Status: port implementation and build tooling; a complete APK compilation
and Android device test have not yet been verified.** A passing tooling CI
run does not mean that a working APK exists. See
[VALIDATION.md](VALIDATION.md) for the evidence and remaining gates.

## What this adds

- Pinned Android dependency checkout, including Linux host tools.
- Android compatibility changes adapted from upstream
  [PR #2680](https://github.com/imputnet/helium/pull/2680); see
  [UPSTREAM_ANDROID_PATCHES.md](UPSTREAM_ANDROID_PATCHES.md).
- A build driver for `chrome_public_apk`, with preflight checks, source-state
  protection, development APK verification, and artifact checksums.
- Native app and widget labels, using the package-name switch supported by
  the pinned Chromium source.
- Tooling CI and a manually invoked APK job for a provisioned Linux runner.

The Android browser uses Chromium's Android interface. Desktop Helium's
extensions, uBlock Origin integration, onboarding, shortcuts, and privacy
behavior require separate mobile validation. They are not advertised as
working by this port. The label customization does not replace every
in-browser Helium string or supply new Android launcher artwork.

## Build machine

Use an **x86-64 Linux** host. The driver requires at least **100 GiB free disk**
for a fresh checkout and **8 GiB RAM**. **200 GiB free and 16 GiB or more RAM**
are practical development recommendations. Compilation time depends heavily
on CPU, RAM, storage, and the selected job count.

The repository pins Chromium in `../chromium_version.txt` and Python in
`../.python-version`. The initial port was inspected against Chromium
**156.0.8078.47**, whose public Android APK target requires Android 10/API 29
or later. The final APK's manifest must be checked when the source is updated.

See Chromium's official
[Android build instructions](https://chromium.googlesource.com/chromium/src/+/main/docs/android_build_instructions.md).
The build driver never installs OS packages with `sudo`, deletes an existing
source checkout, or provisions a paid build machine.

## First local build

Run from the fork's repository root. Install Git, Python 3.10, and the normal
Chromium Linux build prerequisites before the build. Python dependencies can
be kept inside the ignored `build` directory:

```bash
python3.10 -m venv build/python
source build/python/bin/activate
python -m pip install -r .requirements.txt

python android/build.py doctor
python android/build.py fetch
```

The fetch step downloads the pinned source and Android dependencies but
defers build hooks. Install the host packages using the script from that
source checkout. Review its package changes before granting privileges:

```bash
cd build/android/src
./build/install-build-deps.sh
cd ../../..
```

Then prepare and compile:

```bash
python android/build.py build --target-cpu arm64 --jobs 4
```

The script follows Helium's platform build order: dependency download,
Android-aware pruning, patch application, domain
substitution, name substitution, translations, version generation, resource
generation/replacement, native Android labels, GN generation, and compilation.
Build hooks run after the initial source fetch and before those transforms.
The pruning step uses `--keep-android-dependencies` to retain Android sources,
the JDK, D8/R8, Java archives, and Android dependency metadata.

Use `--jobs 2` on a smaller host. The script does not enable remote execution
or require a Google build service. Use the same `--target-cpu` and
`--app-name` arguments for all commands sharing one build directory.

### Output and verification

Chromium's original build output is:

```text
build/android/src/out/CuribAndroid-arm64/apks/ChromePublic.apk
```

Only after signature and package verification succeeds does the build driver
copy the APK, SHA-256 checksum, and a build manifest into:

```text
build/android/artifacts/
```

If compilation or verification fails, no successful build should be inferred
from the presence of an old file. Use the current run's exit code and manifest.

### Other CPUs and names

| Device ABI | `--target-cpu` |
|---|---|
| arm64-v8a | `arm64` (default; most current phones) |
| armeabi-v7a | `arm` |
| x86_64 | `x64` |
| x86 | `x86` |

Use a separate build directory when changing architecture or app name:

```bash
python android/build.py fetch --build-dir build/android-x64 --target-cpu x64
python android/build.py build --build-dir build/android-x64 --target-cpu x64 --jobs 4
```

For 32-bit targets, Chromium's host dependency installer may need `--lib32`.
The driver validates app names before invoking resource generation. The
application ID is defined by `chrome_public_manifest_package` in
`flags.android.gn` and the verifier's `PACKAGE` constant in `build.py`.
For a different ID, update both deliberately and start with a fresh build
directory. The driver rejects mismatched values before source preparation.

## Rebuilding and recovery

Preparation records repository input fingerprints, completed stages, source
Git revisions, and the `.gclient` configuration. Repeating a completed stage
with matching recorded inputs is supported. If versions, patches, CPU, or
branding change, or a non-idempotent preparation stage was interrupted, the
driver stops with a recovery explanation. Choose a fresh `--build-dir` after
reviewing the error. It never resets or deletes a source directory to recover.

These checks do not verify every file in the prepared Chromium working tree.
Manual edits or partial resets inside that checkout can go undetected when
its Git HEAD stays unchanged. Such edits are unsupported: change this fork's
patches or build inputs and use a fresh build directory instead. The artifact
manifest explicitly records `source_worktree_fully_verified: false`.

You can inspect the complete command sequence on a smaller machine:

```bash
python android/build.py build --dry-run
```

Dry-run prints the intended work and any host limitations without downloading,
patching, compiling, or creating build output. It cannot establish that the
native code compiles.

The lower-level fetch utility is also available:

```bash
python utils/clone.py --android --target-cpu arm64 --nohooks -o build/manual/src
```

That is a separate manual checkout. Do not point the managed driver at a
pre-existing unrecorded source tree.

## GitHub Actions

The **Native Android port** workflow runs lightweight tooling checks on pushes
and pull requests. APK compilation is a separate, manual-only job that requires
a self-hosted runner with these labels:

```text
self-hosted, Linux, X64, chromium-android
```

Provision that runner with the Chromium host dependencies and enough storage
before selecting **Build APK**. The workflow does not run code from pull
requests on a self-hosted machine. It uses read-only repository permissions,
does not require a release signing key, and uploads verified development
artifacts instead of publishing a release.

GitHub's **Run workflow** button becomes available once the workflow file is
on the default branch. Until the port's draft PR is merged, the local commands
above can be used directly from `android/native-apk`.

## Development signing and testing

This path uses Chromium's development signing configuration. It is suitable
for local testing, not a production signing identity. A distributable release
needs a securely retained private signing key, a repeatable release-signing
process, and tested upgrade behavior. Never commit a private key or password.

After a successful build, use Chromium's generated installer with an attached
Android device on which you have enabled USB debugging:

```bash
build/android/src/out/CuribAndroid-arm64/bin/chrome_public_apk install
build/android/src/out/CuribAndroid-arm64/bin/chrome_public_apk launch
```

Follow the device checklist in [VALIDATION.md](VALIDATION.md) before calling the
port usable. A successful installation alone does not verify browser behavior.

## License and attribution

The repository's GPL-3.0 terms and imported-code licenses continue to apply.
Chromium and imported patches retain their original notices. This is Curib's
unofficial fork; no upstream endorsement or official Helium Android release
is implied. No upstream issue or pull request is created by these build tools.
