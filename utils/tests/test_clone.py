# -*- coding: UTF-8 -*-
# Copyright 2026 CuribTech
# SPDX-License-Identifier: GPL-3.0-only
"""Regression coverage for opt-in Chromium Android dependency checkout."""

import ast
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from .. import clone


@pytest.mark.parametrize('pgo,os_name,cpus', [
    ('linux', 'linux', ['x64', 'arm64']),
    ('mac-arm', 'mac', ['x64', 'arm64']),
    ('win32', 'win', ['x64', 'arm64', 'x86']),
    ('win64', 'win', ['x64', 'arm64']),
])
def test_desktop_dependency_configuration_is_unchanged(pgo, os_name, cpus):
    config = clone.create_gclient_config(pgo)
    variables = config['solutions'][0]['custom_vars']
    assert config['target_os'] == [os_name]
    assert config['target_cpu'] == cpus
    assert variables['checkout_android'] is False
    assert variables['checkout_pgo_profiles'] is True
    assert config['solutions'][0]['name'] == 'src'
    assert config['cache_dir'] is None


@pytest.mark.parametrize('cpu', ['arm64', 'arm', 'x64', 'x86'])
def test_android_includes_target_dependencies_and_linux_host_tools(cpu):
    config = clone.create_gclient_config('linux', android=True, target_cpu=cpu)
    variables = config['solutions'][0]['custom_vars']
    assert config['target_os'] == ['android', 'linux']
    assert set(config['target_cpu']) == {'x64', cpu}
    assert len(config['target_cpu']) == len(set(config['target_cpu']))
    assert variables['checkout_android'] is True
    assert variables['checkout_pgo_profiles'] is False
    assert variables['checkout_ios'] is False
    assert config['target_os_only'] is True


@pytest.mark.parametrize('flags,message', [
    (['--android', '--pgo', 'mac'], 'not allowed with argument'),
    (['--target-cpu', 'arm64'], '--target-cpu requires --android'),
    (['--android', '--custom-config', 'unused.gclient'], 'cannot be combined'),
    (['--android', '--target-cpu', 'riscv64'], 'invalid choice'),
])
def test_incompatible_cli_arguments_fail_before_creating_sources(tmp_path, flags, message):
    source = tmp_path / 'src'
    result = subprocess.run(
        [sys.executable, str(Path(clone.__file__)), '-o',
         str(source)] + flags,
        capture_output=True,
        text=True,
        check=False)
    assert result.returncode == 2
    assert message in result.stderr
    assert not source.exists()


def test_nohooks_keeps_android_fetch_separate_from_host_setup(tmp_path, monkeypatch):
    source = tmp_path / 'src'
    depot_revision = 'a' * 40
    source_revision = 'b' * 40
    fetched = []
    commands = []

    def fake_checkout(url, revision, destination, environment):
        fetched.append((url, revision, destination))
        destination.mkdir(parents=True)
        if destination == source:
            (source / 'DEPS').write_text("'depot_tools.git' + '@' + '" + depot_revision + "',",
                                         encoding='utf-8')

    def fake_run(command, **kwargs):
        commands.append((command, kwargs))
        return SimpleNamespace(stdout=source_revision + '\n')

    monkeypatch.setattr(clone, 'checkout', fake_checkout)
    monkeypatch.setattr(clone, 'run', fake_run)
    args = SimpleNamespace(output=source,
                           dt_commit=None,
                           custom_config=None,
                           pgo='linux',
                           android=True,
                           target_cpu='arm64',
                           nohooks=True)
    clone.clone(args)

    assert fetched[0][1] == 'refs/tags/' + clone.get_chromium_version()
    assert fetched[1][1] == depot_revision
    config = {}
    for statement in ast.parse((tmp_path / '.gclient').read_text()).body:
        config[statement.targets[0].id] = ast.literal_eval(statement.value)
    assert config['solutions'][0]['custom_vars']['checkout_android'] is True
    sync_command, sync_options = commands[-1]
    assert '--nohooks' in sync_command
    assert 'src@' + source_revision in sync_command
    assert sync_options['check'] is True
    assert sync_options['cwd'] == tmp_path
    assert sync_options['env']['DEPOT_TOOLS_UPDATE'] == '0'
