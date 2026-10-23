"""Tests of native-build safety boundaries; no Chromium downloads or compiler."""

import base64
import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

from android import build

REPO = Path(__file__).resolve().parents[2]


class CommandLineSmokeTests(unittest.TestCase):

    def test_help_explains_fetch_and_native_browser_target(self):
        result = subprocess.run(
            [sys.executable, str(REPO / 'android/build.py'), '--help'],
            capture_output=True,
            text=True,
            check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('fetch', result.stdout)
        self.assertIn('chrome_public_apk', result.stdout)

    def test_invalid_architecture_and_job_counts_are_rejected(self):
        for arguments in (['build', '--target-cpu',
                           'mips'], ['build', '--jobs', '0'], ['build', '--jobs',
                                                               '-2'], ['build', '--jobs', 'many']):
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    build.make_parser().parse_args(arguments)
                self.assertEqual(error.exception.code, 2)

    def test_dry_run_creates_no_files_and_executes_no_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / 'new-build'
            output = io.StringIO()
            with mock.patch('subprocess.run', side_effect=AssertionError('executed a command')):
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                    result = build.main(['build', '--build-dir', str(destination), '--dry-run'])
            self.assertEqual(result, 0)
            self.assertFalse(destination.exists())
            self.assertIn('--nohooks', output.getvalue())
            self.assertIn('--fail-on-unused-args', output.getvalue())
            self.assertIn('chrome_public_apk', output.getvalue())
            self.assertIn('apksigner.jar', output.getvalue())


class HostTests(unittest.TestCase):

    def check(self, **changes):
        parameters = dict(system='Linux',
                          machine='x86_64',
                          memory_bytes=16 * 2**30,
                          free_bytes=200 * 2**30,
                          fetched=False,
                          missing_tools=(),
                          missing_modules=())
        parameters.update(changes)
        return build.preflight_issues(**parameters)

    def test_unsupported_host_or_small_capacity_blocks_build(self):
        for changes, fragment in (({
                'system': 'Darwin'
        }, 'Linux'), ({
                'machine': 'aarch64'
        }, 'x86-64'), ({
                'memory_bytes': 4 * 2**30
        }, 'RAM'), ({
                'free_bytes': 28 * 2**30
        }, 'disk'), ({
                'missing_tools': ('git', )
        }, 'git'), ({
                'missing_modules': ('PIL', )
        }, 'PIL')):
            with self.subTest(changes=changes):
                self.assertIn(fragment, ' '.join(self.check(**changes)))

    def test_completed_fetch_does_not_require_another_100_gib(self):
        self.assertEqual(self.check(fetched=True, free_bytes=40 * 2**30), [])
        self.assertIn('disk', ' '.join(self.check(fetched=True, free_bytes=10 * 2**30)))

    def test_failed_preflight_never_starts_a_source_fetch(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / 'build'
            with mock.patch.object(build, 'inspect_host', return_value=['not enough disk']):
                with mock.patch('subprocess.run', side_effect=AssertionError('command started')):
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                            io.StringIO()):
                        result = build.main(['fetch', '--build-dir', str(destination)])
            self.assertEqual(result, 1)
            self.assertFalse(destination.exists())


class StateTests(unittest.TestCase):

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.inputs = {'chromium_version': '156.0.8078.47', 'fingerprint': 'first'}

    def test_unowned_source_is_preserved_and_rejected(self):
        source = self.directory / 'src'
        source.mkdir()
        marker = source / 'my-work.txt'
        marker.write_text('keep me', encoding='utf-8')
        with self.assertRaisesRegex(build.BuildError, 'unowned|fresh'):
            build.BuildState(self.directory, self.inputs)
        self.assertEqual(marker.read_text(encoding='utf-8'), 'keep me')

    def test_stale_input_is_rejected_before_next_phase(self):
        state = build.BuildState(self.directory, self.inputs)
        state.start('fetch')
        state.finish('fetch')
        with self.assertRaisesRegex(build.BuildError, 'inputs|stale'):
            build.BuildState(self.directory, dict(self.inputs, fingerprint='different'))

    def test_interrupted_phase_is_never_automatically_reapplied(self):
        state = build.BuildState(self.directory, self.inputs)
        state.start('fetch')
        with self.assertRaisesRegex(build.BuildError, 'interrupted|incomplete'):
            build.BuildState(self.directory, self.inputs)
        data = json.loads((self.directory / 'build-state.json').read_text(encoding='utf-8'))
        self.assertEqual(data['active_phase'], 'fetch')
        self.assertEqual(data['completed'], [])

    def test_completed_phases_must_be_an_ordered_prefix(self):
        state = build.BuildState(self.directory, self.inputs)
        state.start('fetch')
        state.finish('fetch')
        path = self.directory / 'build-state.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        data['completed'] = ['fetch', 'patches']
        path.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaisesRegex(build.BuildError, 'order|invalid'):
            build.BuildState(self.directory, self.inputs)


def fixture_repo(root):
    """Small real input tree; external Chromium/depot commands are replaced below."""
    files = {
        'chromium_version.txt': '156.0.8078.47\n',
        'version.txt': '0\n',
        'revision.txt': '1\n',
        'flags.gn': 'chrome_pgo_phase = 0\n',
        'deps.ini': '[test]\n',
        'pruning.list': '',
        'domain_regex.list': '',
        'domain_substitution.list': '',
        'patches/series': 'one.patch\n',
        'patches/one.patch': 'base patch',
        'android/patches/series': 'two.patch\n',
        'android/patches/two.patch': 'android patch',
        'android/flags.android.gn': ('target_os = "android"\n'
                                     'chrome_public_manifest_package = "com.curibtech.helium"\n'),
        'android/revision.txt': '1\n',
        'android/branding.py': '# branding input\n',
        'android/build.py': '# pipeline input\n',
        'resources/generate_resources.txt': '',
        'resources/helium_resources.txt': '',
        'resources/logo.png': 'resource input',
        'i18n/source.gen.json': '[]',
        'i18n/languages.json': '{}',
    }
    for name in ('clone', 'downloads', 'prune_binaries', 'patches', 'domain_substitution',
                 'name_substitution', 'i18n_apply', 'helium_version', 'generate_resources',
                 'replace_resources'):
        files['utils/' + name + '.py'] = '# external command\n'
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')


class ExternalCommands:
    """Replace network/compiler/Android tools while preserving their boundaries."""

    def __init__(self, config, failure=None, create_apk=True):
        self.config = config
        self.failure = failure
        self.create_apk = create_apk
        self.calls = []
        self.revision = 'a' * 40
        self.depot_revision = 'b' * 40
        self.certificate = b'fixture DER certificate'
        self.badging = ("package: name='com.curibtech.helium' versionCode='1' versionName='1.0'\n"
                        "application-label:'Curib Browser'\napplication-debuggable\n"
                        "native-code: 'arm64-v8a'\n")
        self.signer = hashlib.sha256(self.certificate).hexdigest()

    def make_source(self):
        source = self.config.src
        for directory in (source / '.git', self.config.build_dir / 'depot_tools/.git'):
            directory.mkdir(parents=True, exist_ok=True)
        (self.config.build_dir / '.gclient').write_text('target_os = ["android", "linux"]\n')
        paths = {
            'DEPS': "'depot_tools.git' + '@' + '" + self.depot_revision + "',\n",
            'build/config/android/config.gni': 'public_android_sdk_build_tools_version = "37.0.0"\n',
            'chrome/android/java/res_chromium_base/values/channel_constants.xml': '<resources>' +
            ''.join('<string name="' + name + '" translatable="false">Chromium</string>'
                    for name in ('app_name', 'bookmark_widget_title', 'search_widget_title',
                                 'quick_action_search_widget_title')) + '</resources>',
            'buildtools/linux64/gn': '',
            'third_party/depot_tools/autoninja.py': '',
            'third_party/siso/cipd/siso': '',
            'third_party/jdk/current/bin/java': '',
            'third_party/jdk/current/bin/keytool': '',
            'third_party/android_sdk/public/build-tools/37.0.0/lib/apksigner.jar': '',
            'third_party/android_build_tools/aapt2/cipd/aapt2': '',
            'build/android/chromium-debug.keystore': '',
        }
        for name, content in paths.items():
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')

    def write_apk(self):
        apk = self.config.out / 'apks/ChromePublic.apk'
        apk.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(apk, 'w') as archive:
            archive.writestr('lib/arm64-v8a/libchrome.so', b'fixture native browser')
            archive.writestr('classes.dex', b'fixture Java')
        return apk

    def run(self, arguments, *, cwd=None, capture=False):
        arguments = [str(argument) for argument in arguments]
        self.calls.append(arguments)
        if self.failure and any(self.failure in argument for argument in arguments):
            raise build.BuildError('fixture command failure: ' + self.failure)
        if any(argument.endswith('/clone.py') for argument in arguments):
            self.make_source()
        if arguments[0] == 'git':
            return self.revision if arguments[2] == str(self.config.src) else self.depot_revision
        if any(argument.endswith('/autoninja.py') for argument in arguments) and self.create_apk:
            self.write_apk()
        if any(argument.endswith('/keytool') for argument in arguments):
            return '-----BEGIN CERTIFICATE-----\n' + base64.b64encode(
                self.certificate).decode() + '\n-----END CERTIFICATE-----\n'
        if any(argument.endswith('/apksigner.jar') for argument in arguments):
            return 'Verifies\nSigner #1 certificate SHA-256 digest: ' + self.signer + '\n'
        if any(argument.endswith('/aapt2') for argument in arguments):
            return self.badging
        return ''


class BuildFixture(unittest.TestCase):

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.repo = root / 'repo'
        fixture_repo(self.repo)
        self.config = build.BuildConfig(repo=self.repo, build_dir=root / 'build')
        self.commands = ExternalCommands(self.config)

    def command_names(self):
        return [' '.join(call) for call in self.commands.calls]


class PipelineTests(BuildFixture):

    def test_selected_pruning_mode_preserves_real_android_sources_and_tools(self):

        class RealPruningCommands(ExternalCommands):

            def run(self, arguments, *, cwd=None, capture=False):
                if any(str(argument).endswith('/prune_binaries.py') for argument in arguments):
                    result = subprocess.run([
                        sys.executable,
                        str(REPO / 'utils/prune_binaries.py'),
                        *[str(argument) for argument in arguments[2:]]
                    ],
                                            cwd=cwd,
                                            check=False,
                                            capture_output=True,
                                            text=True)
                    if result.returncode:
                        raise build.BuildError('real pruning command failed: ' + result.stderr)
                return super().run(arguments, cwd=cwd, capture=capture)

        commands = RealPruningCommands(self.config)
        protected = ('chrome/android/java/res_chromium_base/values/channel_constants.xml',
                     'third_party/jdk/current/bin/java', 'third_party/r8/custom_d8.jar',
                     'third_party/r8/custom_r8.jar',
                     'third_party/libphonenumber/dist/java/libphonenumber.jar',
                     'third_party/libphonenumber/src/resources/PhoneNumberMetadataProto_US')
        pruning = self.repo / 'pruning.list'
        pruning.write_text('\n'.join(('unused-test.bin', *protected[2:])) + '\n')
        pipeline = build.Pipeline(self.config, commands)
        commands.make_source()
        for name in ('unused-test.bin', *protected[2:]):
            path = self.config.src / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'fixture binary')
        pipeline.patches()
        for name in protected:
            with self.subTest(path=name):
                self.assertTrue((self.config.src / name).is_file(), name + ' was pruned')
        self.assertFalse((self.config.src / 'unused-test.bin').exists())

    def test_package_mismatch_is_rejected_before_network(self):
        flags = self.repo / 'android/flags.android.gn'
        flags.write_text(flags.read_text().replace('com.curibtech.helium', 'org.example.other'))
        with self.assertRaisesRegex(build.BuildError, 'package|PACKAGE'):
            build.Pipeline(self.config, self.commands).fetch()
        self.assertEqual(self.commands.calls, [])

    def test_runtime_files_do_not_invalidate_prepared_inputs(self):
        before = build.build_inputs(self.config)
        (self.repo / 'utils/.coverage').write_bytes(b'runtime coverage output')
        self.assertEqual(build.build_inputs(self.config), before)

    def test_python_dependency_changes_invalidate_prepared_inputs(self):
        requirements = self.repo / '.requirements.txt'
        requirements.write_text('Pillow==11.3.0\n')
        before = build.build_inputs(self.config)
        requirements.write_text('Pillow==10.0.0\n')
        self.assertNotEqual(build.build_inputs(self.config), before)

    def test_prepare_orders_transforms_and_resume_does_not_reapply_them(self):
        build.Pipeline(self.config, self.commands).prepare()
        names = self.command_names()
        expected = [
            'clone.py', 'runhooks', 'downloads.py retrieve', 'downloads.py unpack',
            'prune_binaries.py', 'patches.py apply', 'domain_substitution.py apply',
            'name_substitution.py', 'i18n_apply.py', 'helium_version.py', 'generate_resources.py',
            'replace_resources.py'
        ]
        positions = [
            next(index for index, command in enumerate(names) if text in command)
            for text in expected
        ]
        self.assertEqual(positions, sorted(positions))
        clone = next(command for command in names if 'clone.py' in command)
        self.assertIn('--android', clone)
        self.assertIn('--nohooks', clone)
        patches = next(command for command in names if 'patches.py apply' in command)
        self.assertLess(patches.index(str(self.repo / 'patches')),
                        patches.index(str(self.repo / 'android/patches')))
        resource = self.config.src / 'chrome/android/java/res_chromium_base/values/channel_constants.xml'
        self.assertIn('Curib Browser', resource.read_text(encoding='utf-8'))
        before = len([command for command in names if 'patches.py apply' in command])
        build.Pipeline(self.config, self.commands).prepare()
        self.assertEqual(
            len([command for command in self.command_names() if 'patches.py apply' in command]),
            before)

    def test_failed_patching_stops_and_cannot_be_resumed_over_partial_edits(self):
        self.commands.failure = 'patches.py'
        with self.assertRaisesRegex(build.BuildError, 'fixture command failure'):
            build.Pipeline(self.config, self.commands).prepare()
        self.assertFalse(any('name_substitution.py' in command for command in self.command_names()))
        with self.assertRaisesRegex(build.BuildError, 'interrupted|incomplete'):
            build.Pipeline(self.config, self.commands).prepare()

    def test_changed_patch_inputs_invalidate_the_prepared_source(self):
        build.Pipeline(self.config, self.commands).prepare()
        (self.repo / 'patches/one.patch').write_text('new patch input', encoding='utf-8')
        with self.assertRaisesRegex(build.BuildError, 'inputs|stale'):
            build.Pipeline(self.config, self.commands).prepare()

    def test_changed_chromium_revision_is_rejected(self):
        build.Pipeline(self.config, self.commands).fetch()
        self.commands.revision = 'c' * 40
        with self.assertRaisesRegex(build.BuildError, 'revision'):
            build.Pipeline(self.config, self.commands).prepare()
        self.assertFalse(any('runhooks' in command for command in self.command_names()))

    def test_fetch_stops_before_hooks_and_can_later_prepare(self):
        build.Pipeline(self.config, self.commands).fetch()
        self.assertFalse(any('runhooks' in command for command in self.command_names()))
        build.Pipeline(self.config, self.commands).prepare()
        self.assertEqual(sum('clone.py' in command for command in self.command_names()), 1)
        self.assertEqual(sum('runhooks' in command for command in self.command_names()), 1)

    def test_success_is_published_only_after_package_and_signature_verification(self):
        result = build.Pipeline(self.config, self.commands).build()
        manifest = json.loads(result.with_suffix('.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['verification']['package'], 'com.curibtech.helium')
        self.assertEqual(manifest['verification']['certificate_sha256'], self.commands.signer)
        self.assertEqual(manifest['sha256'], hashlib.sha256(result.read_bytes()).hexdigest())
        self.assertIn('development', manifest['signing'])
        self.assertFalse(manifest['device_tested'])
        names = self.command_names()
        self.assertLess(next(i for i, name in enumerate(names) if 'autoninja.py' in name),
                        next(i for i, name in enumerate(names) if 'apksigner.jar' in name))
        self.assertIn('--fail-on-unused-args', '\n'.join(names))

    def test_compiler_failure_cannot_publish_an_artifact(self):
        self.commands.failure = 'autoninja.py'
        with self.assertRaisesRegex(build.BuildError, 'fixture command failure'):
            build.Pipeline(self.config, self.commands).build()
        self.assertFalse(self.config.artifacts.exists())

    def test_successful_command_without_apk_cannot_publish(self):
        self.commands.create_apk = False
        with self.assertRaisesRegex(build.BuildError, 'APK|apk'):
            build.Pipeline(self.config, self.commands).build()
        self.assertFalse(self.config.artifacts.exists())


class ArtifactTests(BuildFixture):

    def setUp(self):
        super().setUp()
        self.commands.make_source()
        self.apk = self.commands.write_apk()

    def test_wrong_package_is_rejected(self):
        self.commands.badging = self.commands.badging.replace('com.curibtech.helium',
                                                              'org.chromium.chrome')
        with self.assertRaisesRegex(build.BuildError, 'package'):
            build.verify_apk(self.config, self.apk, self.commands)

    def test_unsigned_apk_is_rejected(self):
        self.commands.failure = 'apksigner.jar'
        with self.assertRaises(build.BuildError):
            build.verify_apk(self.config, self.apk, self.commands)

    def test_unexpected_signer_is_rejected(self):
        self.commands.signer = 'f' * 64
        with self.assertRaisesRegex(build.BuildError, 'sign|certificate'):
            build.verify_apk(self.config, self.apk, self.commands)

    def test_wrong_native_abi_is_rejected(self):
        self.commands.badging = self.commands.badging.replace('arm64-v8a', 'x86_64')
        with self.assertRaisesRegex(build.BuildError, 'ABI|architecture'):
            build.verify_apk(self.config, self.apk, self.commands)

    def test_apk_without_native_chromium_engine_is_rejected(self):
        with zipfile.ZipFile(self.apk, 'w') as archive:
            archive.writestr('classes.dex', b'fixture Java')
        with self.assertRaisesRegex(build.BuildError, 'native|libchrome'):
            build.verify_apk(self.config, self.apk, self.commands)

    def test_non_debuggable_apk_is_rejected(self):
        self.commands.badging = self.commands.badging.replace('application-debuggable\n', '')
        with self.assertRaisesRegex(build.BuildError, 'debuggable'):
            build.verify_apk(self.config, self.apk, self.commands)

    def test_aapt_escaped_label_is_checked_against_the_raw_requested_name(self):
        name = 'Curib "Browser" \\ Dev'
        config = build.BuildConfig(repo=self.repo, build_dir=self.config.build_dir, app_name=name)
        # Literal output from AAPT's normalizeForOutput: escape backslashes and
        # double quotes. Apostrophes and Unicode remain unchanged.
        self.commands.badging = self.commands.badging.replace('Curib Browser',
                                                              'Curib \\"Browser\\" \\\\ Dev')
        try:
            result = build.verify_apk(config, self.apk, self.commands)
        except build.BuildError as error:
            self.fail('a correctly escaped native label must verify: ' + str(error))
        self.assertEqual(result['app_name'], name)


if __name__ == '__main__':
    unittest.main()
