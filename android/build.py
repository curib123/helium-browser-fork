#!/usr/bin/env python3
# Copyright 2026 The Helium Authors
# You can use, redistribute, and/or modify this source code under
# the terms of the GPL-3.0 license that can be found in the LICENSE file.
"""Prepare and build an experimental, development-signed chrome_public_apk.

This is the native Helium/Chromium browser. A successful orchestration test is
not evidence of a successful Chromium compilation or an Android device test.
"""

import argparse
import base64
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import zipfile

sys.dont_write_bytecode = True

if __package__:
    from .branding import apply_branding, validate_app_name
else:
    from branding import apply_branding, validate_app_name # pylint: disable=import-error

REPO_ROOT = Path(__file__).resolve().parents[1]
TARGET_CPUS = ('arm64', 'arm', 'x64', 'x86')
PACKAGE = 'com.curibtech.helium'
ABI_BY_CPU = {'arm64': 'arm64-v8a', 'arm': 'armeabi-v7a', 'x64': 'x86_64', 'x86': 'x86'}
PREPARE_PHASES = ('fetch', 'hooks', 'dependencies', 'patches', 'domains', 'names', 'translations',
                  'version', 'resources', 'branding')
GIB = 2**30
STATE_SCHEMA = 1


class BuildError(RuntimeError):
    """An unsafe or unsuccessful native build operation."""


@dataclass(frozen=True)
class BuildConfig:
    """Inputs that are fixed for the lifetime of one prepared source tree."""
    repo: Path
    build_dir: Path
    target_cpu: str = 'arm64'
    app_name: str = 'Curib Browser'
    jobs: int | None = None

    def __post_init__(self):
        object.__setattr__(self, 'repo', Path(self.repo).resolve())
        object.__setattr__(self, 'build_dir', Path(self.build_dir).resolve())
        if self.target_cpu not in TARGET_CPUS:
            raise BuildError(f'unsupported target CPU: {self.target_cpu}')
        validate_app_name(self.app_name)
        if self.jobs is not None and self.jobs < 1:
            raise BuildError('jobs must be a positive integer')
        if self.repo == self.build_dir or self.build_dir in self.repo.parents:
            raise BuildError('build directory must not be the repository or an ancestor of it')

    @property
    def src(self):
        """Chromium source checkout, always named src for gclient."""
        return self.build_dir / 'src'

    @property
    def out(self):
        """Separate GN output for the selected Android architecture."""
        return self.src / 'out' / ('CuribAndroid-' + self.target_cpu)

    @property
    def artifacts(self):
        """Verified development artifacts only."""
        return self.build_dir / 'artifacts'


def file_sha256(path):
    """Hash large APKs without loading them into memory."""
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def write_json(path, data):
    """Persist phase intent before modifying source, replacing state atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def build_inputs(config):
    """Fingerprint actual build inputs, including uncommitted patch changes.

    Generated images are built in the build directory; tests, docs, and CI files
    do not affect source preparation and are deliberately excluded.
    """
    required = ('chromium_version.txt', 'version.txt', 'revision.txt', 'flags.gn', 'deps.ini',
                'pruning.list', 'domain_regex.list', 'domain_substitution.list', 'patches/series',
                'android/build.py', 'android/branding.py', 'android/flags.android.gn',
                'android/revision.txt', 'resources/generate_resources.txt',
                'resources/helium_resources.txt')
    paths = {config.repo / name for name in required}
    for path in paths:
        if not path.is_file():
            raise BuildError(f'missing build input: {path}')
    android_flags = (config.repo / 'android/flags.android.gn').read_text(encoding='utf-8')
    packages = re.findall(r'^\s*chrome_public_manifest_package\s*=\s*"([^"\n]+)"\s*(?:#.*)?$',
                          android_flags, re.MULTILINE)
    if packages != [PACKAGE]:
        raise BuildError('Android GN package must match PACKAGE in android/build.py (' + PACKAGE +
                         '); update both deliberately and use a fresh build directory')
    for name in ('.requirements.txt', '.python-version'):
        path = config.repo / name
        if path.is_file():
            paths.add(path)
    for directory in ('patches', 'android/patches', 'resources', 'i18n', 'utils'):
        pattern = '*.py' if directory == 'utils' else '*'
        for path in (config.repo / directory).rglob(pattern):
            relative = path.relative_to(config.repo)
            if (path.is_file() and not {'__pycache__', 'tests', 'generated'} & set(relative.parts)
                    and path.suffix not in ('.pyc', '.pyo') and not path.name.startswith('.')):
                paths.add(path)
    if (config.repo /
            'android/patches').exists() and not (config.repo / 'android/patches/series').is_file():
        raise BuildError('android/patches exists without a quilt series file')
    digest = hashlib.sha256()
    for path in sorted(paths):
        relative = path.relative_to(config.repo).as_posix()
        digest.update(relative.encode('utf-8') + b'\0')
        digest.update(bytes.fromhex(file_sha256(path)))
    version = (config.repo / 'chromium_version.txt').read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+\.\d+', version):
        raise BuildError('chromium_version.txt must contain one numeric Chromium version')
    return {
        'fingerprint': digest.hexdigest(),
        'chromium_version': version,
        'repo': str(config.repo),
        'build_dir': str(config.build_dir),
        'target_cpu': config.target_cpu,
        'app_name': config.app_name,
        'package': PACKAGE,
    }


class BuildState:
    """Reject stale or interrupted source transformations instead of repeating them."""

    def __init__(self, directory, inputs):
        self.path = directory / 'build-state.json'
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding='utf-8'))
            except (OSError, ValueError) as error:
                raise BuildError(
                    f'invalid build state: {self.path}; use a fresh build directory') from error
            if not isinstance(self.data, dict) or self.data.get('schema') != STATE_SCHEMA:
                raise BuildError('invalid build state schema; use a fresh build directory')
            if self.data.get('inputs') != inputs:
                raise BuildError('build inputs are stale or changed; use a fresh build directory')
            completed = self.data.get('completed')
            if (not isinstance(completed, list) or len(completed) > len(PREPARE_PHASES)
                    or completed != list(PREPARE_PHASES[:len(completed)])):
                raise BuildError('invalid build state phase order; use a fresh build directory')
            if self.data.get('active_phase') is not None:
                raise BuildError(
                    'interrupted or incomplete phase ' + str(self.data['active_phase']) +
                    '; preserve this tree for diagnosis and use a fresh build directory')
        else:
            for name in ('src', 'depot_tools', '.gclient', 'resources'):
                path = directory / name
                if path.exists() and (not path.is_dir() or any(path.iterdir())):
                    raise BuildError(
                        f'unowned build contents at {path}; use a fresh build directory')
            self.data = {
                'schema': STATE_SCHEMA,
                'inputs': inputs,
                'completed': [],
                'active_phase': None
            }

    @property
    def completed(self):
        """Successfully completed transformations, in their required order."""
        return self.data['completed']

    def start(self, phase):
        """Record an in-progress phase before its first source write."""
        if (self.data['active_phase'] is not None or len(self.completed) == len(PREPARE_PHASES)
                or phase != PREPARE_PHASES[len(self.completed)]):
            raise BuildError(f'invalid phase order: {phase}')
        self.data['active_phase'] = phase
        write_json(self.path, self.data)

    def finish(self, phase, metadata=None):
        """Mark a phase complete only after every command has succeeded."""
        if self.data['active_phase'] != phase:
            raise BuildError(f'invalid phase completion: {phase}')
        if metadata:
            self.data.update(metadata)
        self.completed.append(phase)
        self.data['active_phase'] = None
        write_json(self.path, self.data)


def effective_memory_bytes():
    """Account for physical RAM and a container's memory cap."""
    try:
        memory = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
    except (OSError, ValueError):
        return 0
    limits = [
        Path('/sys/fs/cgroup/memory.max'),
        Path('/sys/fs/cgroup/memory/memory.limit_in_bytes')
    ]
    cgroups = Path('/proc/self/cgroup')
    if cgroups.is_file():
        for line in cgroups.read_text(encoding='utf-8').splitlines():
            fields = line.split(':', 2)
            if len(fields) == 3 and fields[0] == '0':
                limits.append(Path('/sys/fs/cgroup') / fields[2].lstrip('/') / 'memory.max')
    for path in limits:
        try:
            limit = path.read_text(encoding='utf-8').strip()
            if limit.isdigit() and int(limit) > 0:
                memory = min(memory, int(limit))
        except OSError:
            continue
    return memory


def preflight_issues(*,
                     system,
                     machine,
                     memory_bytes,
                     free_bytes,
                     fetched=False,
                     missing_tools=(),
                     missing_modules=()):
    """Evaluate conservative local gates without network access or mutations."""
    issues = []
    if system != 'Linux':
        issues.append('native Android builds require Linux')
    if machine.lower() not in ('x86_64', 'amd64'):
        issues.append('native Android builds require an x86-64 host')
    if memory_bytes < 8 * GIB:
        issues.append(
            f'at least 8 GiB RAM is required; effective memory is {memory_bytes / GIB:.1f} GiB')
    minimum_disk = 30 if fetched else 100
    if free_bytes < minimum_disk * GIB:
        issues.append(f'at least {minimum_disk} GiB free disk is required at this stage; '
                      f'available space is {free_bytes / GIB:.1f} GiB')
    if missing_tools:
        issues.append('missing host tools: ' + ', '.join(missing_tools))
    if missing_modules:
        issues.append('missing Python modules: ' + ', '.join(missing_modules) +
                      '; install the repository .requirements.txt with this Python interpreter')
    return issues


def inspect_host(config):
    """Check prerequisites before any clone, hook, or dependency download."""
    path = config.build_dir
    while not path.exists():
        path = path.parent
    free = shutil.disk_usage(path).free
    memory = effective_memory_bytes()
    fetched = False
    state_path = config.build_dir / 'build-state.json'
    if state_path.is_file():
        try:
            saved = json.loads(state_path.read_text(encoding='utf-8'))
            fetched = isinstance(saved, dict) and 'fetch' in saved.get('completed', [])
        except (OSError, ValueError, TypeError):
            pass
    print(f'Host: {platform.system()} {platform.machine()}; effective RAM {memory / GIB:.1f} GiB; '
          f'free disk {free / GIB:.1f} GiB')
    return preflight_issues(system=platform.system(),
                            machine=platform.machine(),
                            memory_bytes=memory,
                            free_bytes=free,
                            fetched=fetched,
                            missing_tools=tuple(name
                                                for name in ('git', 'curl', 'patch', 'tar', 'xz')
                                                if not shutil.which(name)),
                            missing_modules=tuple(name for name in ('PIL', )
                                                  if importlib.util.find_spec(name) is None))


class CommandRunner: # pylint: disable=too-few-public-methods
    """Run argument lists directly; display shell quoting only for readable logs."""

    def __init__(self, config, dry_run=False):
        self.dry_run = dry_run
        self.environment = os.environ.copy()
        self.environment.update({
            'PATH': str(config.build_dir / 'depot_tools') + os.pathsep +
            self.environment.get('PATH', ''),
            'DEPOT_TOOLS_UPDATE': '0',
            'PYTHONDONTWRITEBYTECODE': '1',
            'GCLIENT_FILE': str(config.build_dir / '.gclient'),
            'SISO_PATH': str(config.src / 'third_party/siso/cipd/siso'),
        })

    def run(self, arguments, *, cwd=None, capture=False):
        """Stop on every non-zero command; never interpolate user input in a shell."""
        command = [str(argument) for argument in arguments]
        print('$ ' + shlex.join(command), flush=True)
        if self.dry_run:
            return ''
        try:
            result = subprocess.run(command,
                                    cwd=cwd,
                                    env=self.environment,
                                    check=True,
                                    text=True,
                                    capture_output=capture)
        except subprocess.CalledProcessError as error:
            details = ((error.stdout or '') + (error.stderr or '')).strip()
            raise BuildError(f'command failed ({error.returncode}): {shlex.join(command)}' +
                             ('\n' + details if details else '')) from error
        except OSError as error:
            raise BuildError(f'cannot execute {command[0]}: {error}') from error
        return result.stdout.strip() if capture else ''


@contextmanager
def build_lock(directory):
    """Do not let concurrent invocations mutate the same checkout."""
    import fcntl # pylint: disable=import-outside-toplevel
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.build.lock').open('a', encoding='utf-8') as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise BuildError('another native build is using this build directory') from error
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def android_tools(config, preview=False):
    """Use SDK/JDK/aapt2 binaries supplied by the pinned Chromium checkout.

    Paths follow build/config/android/config.gni and internal_rules.gni at the
    pinned Chromium revision; no user-installed Android SDK is substituted.
    """
    source_config = config.src / 'build/config/android/config.gni'
    version = '<from-config.gni>'
    if source_config.is_file():
        match = re.search(r'public_android_sdk_build_tools_version\s*=\s*"([\d.]+)"',
                          source_config.read_text(encoding='utf-8'))
        if not match:
            raise BuildError(
                f'cannot locate pinned public SDK build-tools version in {source_config}')
        version = match.group(1)
    elif not preview:
        raise BuildError(f'missing Android SDK configuration: {source_config}')
    paths = {
        'java': config.src / 'third_party/jdk/current/bin/java',
        'keytool': config.src / 'third_party/jdk/current/bin/keytool',
        'aapt2': config.src / 'third_party/android_build_tools/aapt2/cipd/aapt2',
        'apksigner': config.src / 'third_party/android_sdk/public/build-tools' / version /
        'lib/apksigner.jar',
        'keystore': config.src / 'build/android/chromium-debug.keystore',
    }
    if not preview:
        for path in paths.values():
            if not path.is_file():
                raise BuildError(f'missing pinned Android verification tool or key: {path}')
    return paths


def verification_commands(config, apk, preview=False):
    """The public development certificate is checked independently of APK signing."""
    paths = android_tools(config, preview=preview)
    return [
        [
            paths['keytool'], '-exportcert', '-rfc', '-keystore', paths['keystore'], '-storepass',
            'chromium', '-alias', 'chromiumdebugkey'
        ],
        [paths['java'], '-jar', paths['apksigner'], 'verify', '--verbose', '--print-certs', apk],
        [paths['aapt2'], 'dump', 'badging', apk],
    ]


def verify_apk(config, apk, runner): # pylint: disable=too-many-locals
    """Refuse absent, unsigned, misbranded, or wrong-architecture APKs."""
    if not apk.is_file() or apk.stat().st_size == 0:
        raise BuildError(f'build did not produce the required APK: {apk}')
    digest = file_sha256(apk)
    abi = ABI_BY_CPU[config.target_cpu]
    try:
        with zipfile.ZipFile(apk) as archive:
            native = archive.getinfo(f'lib/{abi}/libchrome.so')
            if native.file_size == 0 or 'classes.dex' not in archive.namelist():
                raise BuildError('APK is missing native Chromium or its Java application')
    except (zipfile.BadZipFile, KeyError) as error:
        raise BuildError(
            f'APK lacks the native Chromium engine for {abi} (libchrome.so)') from error
    commands = verification_commands(config, apk)
    certificate_text = runner.run(commands[0], capture=True)
    match = re.search(r'-----BEGIN CERTIFICATE-----\s*(.*?)\s*-----END CERTIFICATE-----',
                      certificate_text, re.DOTALL)
    if not match:
        raise BuildError('could not read the pinned development signing certificate')
    try:
        certificate = base64.b64decode(''.join(match.group(1).split()), validate=True)
    except ValueError as error:
        raise BuildError('invalid pinned development signing certificate') from error
    expected_signer = hashlib.sha256(certificate).hexdigest()
    signatures = runner.run(commands[1], capture=True)
    signers = re.findall(r'Signer #\d+ certificate SHA-256 digest:\s*([0-9a-fA-F]{64})', signatures)
    if len(signers) != 1 or signers[0].lower() != expected_signer:
        raise BuildError('APK signer does not match the pinned Chromium development certificate')
    badging = runner.run(commands[2], capture=True)
    package = re.search(r"^package: name='([^']+)'", badging, re.MULTILINE)
    if not package or package.group(1) != PACKAGE:
        raise BuildError(f'APK package must be {PACKAGE}')
    native = re.search(r'^native-code:(.*)$', badging, re.MULTILINE)
    if not native or abi not in re.findall(r"'([^']+)'", native.group(1)):
        raise BuildError(f'APK native ABI must include {abi}')
    if not re.search(r'^application-debuggable\s*$', badging, re.MULTILINE):
        raise BuildError('experimental APK must remain debuggable')
    label = re.search(r"^application-label:'(.*)'$", badging, re.MULTILINE)
    # AAPT2 prints ResTable::normalizeForOutput(), escaping backslashes before
    # double quotes. validate_app_name already excludes non-printable characters.
    expected_label = config.app_name.replace('\\', '\\\\').replace('"', '\\"')
    if not label or label.group(1) != expected_label:
        raise BuildError('APK application label does not match the requested app name')
    if file_sha256(apk) != digest:
        raise BuildError('APK changed while its signature and identity were being verified')
    return {
        'package': PACKAGE,
        'native_abi': abi,
        'app_name': config.app_name,
        'certificate_sha256': expected_signer,
        'debuggable': True,
        'sha256': digest
    }


class Pipeline:
    """Follow Helium's native preparation order, then build one APK target."""

    def __init__(self, config, runner=None, preview=False):
        self.config = config
        self.runner = runner if runner is not None else CommandRunner(config, dry_run=preview)
        self.preview = preview
        self.inputs = build_inputs(config)
        self.state = None if preview else BuildState(config.build_dir, self.inputs)
        if self.state is not None and 'fetch' in self.state.completed:
            self.check_sources()

    def utility(self, name, *arguments, capture=False):
        """Use this interpreter for all repository build utilities."""
        return self.runner.run(
            [sys.executable, self.config.repo / 'utils' / (name + '.py'), *arguments],
            cwd=self.config.repo,
            capture=capture)

    def source_record(self):
        """Record immutable Git revisions and gclient configuration after fetch."""
        config = self.config
        result = {}
        for name, directory in (('chromium', config.src), ('depot_tools',
                                                           config.build_dir / 'depot_tools')):
            if not (directory / '.git').exists():
                raise BuildError(f'missing managed {name} checkout: {directory}')
            revision = self.runner.run(['git', '-C', directory, 'rev-parse', 'HEAD'], capture=True)
            if not re.fullmatch('[0-9a-f]{40}', revision):
                raise BuildError(f'invalid {name} source revision')
            result[name] = revision
        result['gclient_sha256'] = file_sha256(config.build_dir / '.gclient')
        deps = (config.src / 'DEPS').read_text(encoding='utf-8')
        depot = re.search(r"depot_tools\.git'\s*\+\s*'@'\s*\+\s*'([^']+)'", deps)
        if not depot or depot.group(1) != result['depot_tools']:
            raise BuildError('depot_tools revision does not match the Chromium DEPS pin')
        return result

    def check_sources(self):
        """A different checkout must never inherit a completed-patch stamp."""
        if self.state.data.get('source') != self.source_record():
            raise BuildError(
                'source revision or gclient configuration changed; use a fresh build directory')

    def phase(self, name, action):
        """Skip only completed transformations with matching build inputs."""
        if self.preview:
            print(f'\n[{name}]')
            action()
            return
        if name in self.state.completed:
            print(f'[{name}] already complete')
            return
        print(f'\n[{name}]', flush=True)
        self.state.start(name)
        metadata = action()
        self.state.finish(name, metadata)

    def fetch_sources(self):
        """The clone utility refuses existing sources and pins source/depot_tools."""
        self.utility('clone', '--android', '--target-cpu', self.config.target_cpu, '--nohooks',
                     '--output', self.config.src)
        if self.preview:
            print('Record exact Chromium/depot_tools revisions and .gclient checksum.')
            return None
        return {'source': self.source_record()}

    def fetch(self):
        """Stop before hooks so the user can install system dependencies manually."""
        self.phase('fetch', self.fetch_sources)

    def dependencies(self):
        """Verify and unpack Helium's additional, pinned assets."""
        config = self.config
        cache = config.build_dir / 'download_cache'
        if not self.preview:
            cache.mkdir(parents=True, exist_ok=True)
        for command in ('retrieve', 'unpack'):
            arguments = [command, '-i', config.repo / 'deps.ini', '-c', cache]
            if command == 'unpack':
                arguments.append(config.src)
            self.utility('downloads', *arguments)

    def patches(self):
        """Keep the downloaded toolchain while applying base and Android patches."""
        config = self.config
        self.utility('prune_binaries', '--keep-android-dependencies', config.src,
                     config.repo / 'pruning.list')
        directories = [config.repo / 'patches']
        if (config.repo / 'android/patches/series').is_file():
            directories.append(config.repo / 'android/patches')
        self.utility('patches', 'apply', '--no-fuzz', config.src, *directories)

    def resources(self):
        """Generate Helium artwork in a private copy to keep input hashes stable."""
        config = self.config
        resources = config.build_dir / 'resources'
        if self.preview:
            print(f'Copy {config.repo / "resources"} to {resources}, excluding generated files.')
        else:
            shutil.copytree(config.repo / 'resources',
                            resources,
                            ignore=shutil.ignore_patterns('generated', '__pycache__'))
        self.utility('generate_resources', resources / 'generate_resources.txt', resources)
        self.utility('replace_resources', resources / 'helium_resources.txt', resources, config.src)

    def branding(self):
        """Apply the custom native Android label after all Helium substitutions."""
        if self.preview:
            print(f'Apply Android app/widget labels: {self.config.app_name!r}')
        else:
            apply_branding(self.config.src, self.config.app_name)

    def prepare(self):
        """The shared Helium Linux preparation sequence, with Android dependencies."""
        config = self.config
        self.fetch()
        self.phase(
            'hooks', lambda: self.runner.run([config.build_dir / 'depot_tools/gclient', 'runhooks'],
                                             cwd=config.build_dir))
        self.phase('dependencies', self.dependencies)
        self.phase('patches', self.patches)
        self.phase(
            'domains', lambda: self.
            utility('domain_substitution', 'apply', '-r', config.repo / 'domain_regex.list', '-f',
                    config.repo / 'domain_substitution.list', '-c', config.build_dir /
                    'domains.tar.gz', config.src))
        self.phase(
            'names', lambda: self.utility('name_substitution', '--sub', '-t', config.src,
                                          '--backup-path', config.build_dir / 'names.tar'))
        self.phase('translations', lambda: self.utility('i18n_apply', '-t', config.src))
        self.phase(
            'version',
            lambda: self.utility('helium_version', '--tree', config.repo, '--platform-tree', config.
                                 repo / 'android', '--chromium-tree', config.src))
        self.phase('resources', self.resources)
        self.phase('branding', self.branding)

    def gn_arguments(self):
        """Common Helium flags followed by explicit Android development settings."""
        config = self.config
        return ('# Generated by android/build.py; change checked-in inputs instead.\n' +
                (config.repo / 'flags.gn').read_text(encoding='utf-8').rstrip() + '\n' +
                (config.repo / 'android/flags.android.gn').read_text(encoding='utf-8').rstrip() +
                '\n' + f'target_cpu = "{config.target_cpu}"\n' +
                f'v8_target_cpu = "{config.target_cpu}"\n')

    def build(self):
        """Compile and verify; a failed compiler run can safely resume via Ninja."""
        self.prepare()
        config = self.config
        arguments = self.gn_arguments()
        if self.preview:
            print(f'\nWrite {config.out / "args.gn"}:\n{arguments}')
        else:
            config.out.mkdir(parents=True, exist_ok=True)
            (config.out / 'args.gn').write_text(arguments, encoding='utf-8')
        self.runner.run([
            config.src / 'buildtools/linux64/gn', 'gen',
            config.out.relative_to(config.src), '--fail-on-unused-args'
        ],
                        cwd=config.src)
        jobs = config.jobs or max(1, min(os.cpu_count() or 1,
                                         effective_memory_bytes() // (2 * GIB)))
        self.runner.run([
            sys.executable, config.src / 'third_party/depot_tools/autoninja.py', '-C', config.out,
            '-j',
            str(jobs), 'chrome_public_apk'
        ],
                        cwd=config.src)
        apk = config.out / 'apks/ChromePublic.apk'
        if self.preview:
            for command in verification_commands(config, apk, preview=True):
                self.runner.run(command, cwd=config.src, capture=True)
            print('Require a signed, debuggable APK with the requested app label, '
                  f'package {PACKAGE}, and a native Chromium library '
                  f'for {ABI_BY_CPU[config.target_cpu]}.')
            print(f'Copy verified APK, SHA-256, and JSON manifest to {config.artifacts}.')
            return None
        verification = verify_apk(config, apk, self.runner)
        return self.publish(apk, verification, arguments)

    def publish(self, apk, verification, arguments):
        """Expose only bytes that passed APK signature and identity verification."""
        config = self.config
        filename = f'CuribBrowser-{self.inputs["chromium_version"]}-{config.target_cpu}-dev.apk'
        config.artifacts.mkdir(parents=True, exist_ok=True)
        destination = config.artifacts / filename
        temporary = destination.with_suffix('.apk.tmp')
        shutil.copyfile(apk, temporary)
        digest = file_sha256(temporary)
        if digest != verification['sha256']:
            temporary.unlink()
            raise BuildError('APK changed after verification; no artifact was published')
        manifest = {
            'schema': 1,
            'artifact': filename,
            'sha256': digest,
            'built_at_utc': datetime.now(timezone.utc).isoformat(),
            'inputs': self.inputs,
            'source': self.state.data['source'],
            'source_worktree_fully_verified': False,
            'target': 'chrome_public_apk',
            'gn_args': arguments,
            'signing': 'Chromium public development key; not a release key',
            'experimental': True,
            'device_tested': False,
            'verification': verification,
        }
        temporary.replace(destination)
        checksum = destination.with_suffix('.apk.sha256')
        checksum_tmp = checksum.with_suffix('.sha256.tmp')
        checksum_tmp.write_text(f'{digest}  {filename}\n', encoding='utf-8')
        checksum_tmp.replace(checksum)
        write_json(destination.with_suffix('.json'), manifest)
        return destination


def positive_int(value):
    """Parse a positive job count."""
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('must be a positive integer')
    return number


def make_parser():
    """Build the deliberately small native-build command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='command', required=True)
    descriptions = {
        'doctor': 'Check the Linux build host without downloading anything.',
        'fetch': 'Fetch pinned Android sources without running hooks.',
        'prepare': 'Fetch, run hooks, and apply the complete Helium patch pipeline.',
        'build': 'Prepare and compile chrome_public_apk, then verify and copy the APK.',
    }
    for command, description in descriptions.items():
        subparser = subparsers.add_parser(command, help=description, description=description)
        subparser.add_argument('--build-dir', type=Path, default=REPO_ROOT / 'build/android')
        subparser.add_argument('--target-cpu', choices=TARGET_CPUS, default='arm64')
        subparser.add_argument('--app-name', default='Curib Browser')
        subparser.add_argument('--jobs', type=positive_int)
        subparser.add_argument('--dry-run',
                               action='store_true',
                               help='Print the complete plan without writes or command execution.')
    return parser


def main(argv=None):
    """Command-line entrypoint."""
    args = make_parser().parse_args(argv)
    try:
        config = BuildConfig(repo=REPO_ROOT,
                             build_dir=args.build_dir,
                             target_cpu=args.target_cpu,
                             app_name=args.app_name,
                             jobs=args.jobs)
        issues = inspect_host(config)
        for issue in issues:
            print(('DRY RUN preflight: ' if args.dry_run else 'ERROR: ') + issue, file=sys.stderr)
        if args.command == 'doctor':
            if not issues:
                print('Host bootstrap checks passed. '
                      'Install Chromium system dependencies after fetch; '
                      'a full native build and device test are still required.')
            return int(bool(issues))
        if args.dry_run:
            print('DRY RUN: complete plan; no commands or filesystem writes will occur.')
            pipeline = Pipeline(config, preview=True)
            getattr(pipeline, args.command)()
            return 0
        if issues:
            return 1
        with build_lock(config.build_dir):
            pipeline = Pipeline(config)
            result = getattr(pipeline, args.command)()
        if args.command == 'fetch':
            print('Pinned sources fetched without hooks. Review and run:')
            print(shlex.join([str(config.src / 'build/install-build-deps.sh')]))
            print('Then run prepare or build with the same options.')
        elif args.command == 'prepare':
            print('Native source preparation completed. '
                  'Compilation and device verification are still required.')
        else:
            print(f'Verified development APK: {result}')
            print('Experimental and signed with Chromium\'s public development key. '
                  'Device verification is still required.')
        return 0
    except (BuildError, OSError, ValueError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Interrupted. Incomplete preparation phases are preserved and will not be replayed.',
              file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())
