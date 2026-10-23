# -*- coding: UTF-8 -*-
# Copyright 2026 CuribTech
# SPDX-License-Identifier: GPL-3.0-only
"""Run the real pruner to protect native Android source and build dependencies."""

from pathlib import Path
import subprocess
import sys

from .. import prune_binaries


def make_source_fixture(root):
    paths = [
        'chrome/android/java/src/Browser.java',
        'chrome/android/java/res_chromium_base/values/channel_constants.xml',
        'third_party/jdk/current/bin/java',
        'third_party/android_platform/frameworks/base/Api.java',
        'third_party/r8/custom_d8.jar',
        'third_party/r8/custom_r8.jar',
        'third_party/angle/third_party/r8/custom_d8.jar',
        'third_party/libphonenumber/dist/resources/metadata/country.dat',
        'third_party/android_build_tools/gradle_wrapper/gradle/wrapper/gradle-wrapper.jar',
        'build/android/stacktrace/java_deobfuscate_java.jar',
        'third_party/some_java_dependency/library.aar',
        'buildtools/linux64/gn',
        'unused/test-fixture.bin',
    ]
    for name in paths:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture', encoding='utf-8')
    pruning = root.parent / 'pruning.list'
    pruning.write_text('\n'.join(paths[4:]) + '\n', encoding='utf-8')
    return paths, pruning


def test_android_mode_preserves_sources_tools_and_java_data(tmp_path):
    source = tmp_path / 'src'
    paths, pruning = make_source_fixture(source)
    result = subprocess.run([
        sys.executable,
        str(Path(prune_binaries.__file__)), '--keep-android-dependencies',
        str(source),
        str(pruning)
    ],
                            capture_output=True,
                            text=True,
                            check=False)
    assert result.returncode == 0, result.stderr
    for relative in paths[:-1]:
        assert (source / relative).is_file(), relative
    assert not (source / paths[-1]).exists()


def test_existing_desktop_mode_keeps_its_original_pruning_behavior(tmp_path):
    source = tmp_path / 'src'
    paths, pruning = make_source_fixture(source)
    # Use only explicit files outside the contingent paths so the legacy mode
    # does not report a file already removed by its contingent-path pass.
    pruning.write_text('third_party/r8/custom_r8.jar\nunused/test-fixture.bin\n', encoding='utf-8')
    result = subprocess.run([
        sys.executable,
        str(Path(prune_binaries.__file__)), '--keep-build-dependencies',
        str(source),
        str(pruning)
    ],
                            capture_output=True,
                            text=True,
                            check=False)
    assert result.returncode == 0, result.stderr
    assert not (source / paths[0]).exists()
    assert not (source / paths[2]).exists()
    assert not (source / 'third_party/r8/custom_r8.jar').exists()
    assert (source / 'buildtools/linux64/gn').is_file()
