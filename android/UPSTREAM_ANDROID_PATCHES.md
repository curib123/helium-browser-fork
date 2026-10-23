# Upstream Android compatibility patches

## Status

This fork includes an experimental adaptation of upstream Android compilation
work. It is not an official Helium Android release. The upstream pull request was
open and unmerged when inspected on 2026-10-23; importing its changes does not
establish that the complete browser compiles or runs on Android.

## Provenance

| Item | Source |
| --- | --- |
| Upstream pull request | [imputnet/helium#2680: patches: fix building on android](https://github.com/imputnet/helium/pull/2680) |
| Upstream commit | [`b510be4875dc675a3ed81e4d2edeef27396da68c`](https://github.com/imputnet/helium/commit/b510be4875dc675a3ed81e4d2edeef27396da68c) |
| Upstream commit author | jj; pull request opened by `dumbmoron` |
| Delta base | [`832faa7822566270e10c0d3262f702f9d0fea81f`](https://github.com/imputnet/helium/commit/832faa7822566270e10c0d3262f702f9d0fea81f) |
| Fork revision used for integration | [`f29a25968e483f8bb85e47829ccd487bbe229199`](https://github.com/curib123/helium-browser-fork/commit/f29a25968e483f8bb85e47829ccd487bbe229199) |
| Chromium revision validated | `156.0.8078.47`, as recorded in `chromium_version.txt` |

The upstream delta was merged against the fork's current patch files using the
shared base above. The fork's intervening Chromium updates were retained instead
of replacing its files with the older upstream versions. No changes were sent to
the upstream repository.

Existing copyright headers and license notices are retained. Helium-specific
changes are covered by [LICENSE](../LICENSE); imported ungoogled-chromium portions
retain their existing notices and
[LICENSE.ungoogled_chromium](../LICENSE.ungoogled_chromium). The source links above
identify the upstream author and exact revision.

## Changes included

- Exclude desktop keyboard-shortcut registration and desktop avatar helpers from
  Android compilation.
- Generate Java bindings for Helium's noise Mojo interface.
- Separate the onboarding message handler from desktop WebUI and extension
  dependencies. Android preference writes are restricted to the supported
  onboarding/service preferences, and managed preferences remain protected.
- Place setup URL constants where Android can use them.
- Make extension headers conditional on the relevant extension build flags.
- Guard Safe Browsing-dependent Android download, notification, enterprise
  interstitial, and upload-debug code when those features are disabled.
- Initialize Safe Browsing GN dependency lists outside disabled code blocks.

The changes remain in the existing ten patch files and their existing positions
in `patches/series`; no alternate or duplicate patch series is introduced.

## Adaptations for this fork

The upstream work predates this fork's Chromium 156 update. Integration therefore
includes these adjustments:

1. Refresh the affected hunk positions against the pinned Chromium sources and
   the earlier patches in the repository's series.
2. Preserve the newer `CloudBinaryUploadServiceBase::LogResponseDebugInfo`
   parameters, including HTTP status and response data, while adding the Safe
   Browsing guard.
3. Preserve Chromium 156's current Safe Browsing dependency changes, consolidating
   the existing `deps = []` initialization with the upstream initialization
   instead of introducing a redundant assignment.
4. Remove the unconditional duplicate extension installer include left in the
   upstream onboarding handler, keeping that header inside the extension guard.
   Keep the browser-process include only in the desktop section where it is used.

## Validation performed

- `python devutils/check_patch_files.py` passed for the complete repository patch
  series, checking parsing, file references, duplicates, and unused patches.
- The existing `devutils/validate_patches.py` validator passed for the changed
  source sections and every earlier/later patch touching those sections: **16
  ordered patch subsets over 15 source paths**. Twelve original files were
  retrieved from the exact Chromium `156.0.8078.47` tag; three source files are
  created by the patches. The unmodified baseline passed the same check before
  the candidate was tested.
- Every refreshed hunk matched its complete source context. The final strict
  validator passed without relying on patch fuzz or offsets.
- A check of newly introduced source additions found no trailing whitespace.
  Blank context lines in unified patch files intentionally retain their required
  space prefix.

These checks validate patch syntax and application for the modified source
sections. They do not replace validation of the entire patch series against a
full source tree, GN generation, native compilation, APK installation, or device
testing. No Android binary was built as part of this patch import.

On a suitable build machine, first validate the complete patch series against an
unmodified checkout of the pinned Chromium version:

```sh
python devutils/validate_patches.py -l /path/to/unmodified/chromium/src -v
```

The native Android build and runtime must then be checked. In particular, these
compatibility patches do not provide a finished mobile onboarding flow or prove
desktop extension/uBlock Origin parity on Android.
