# Copyright 2026 The Helium Authors
# You can use, redistribute, and/or modify this source code under
# the terms of the GPL-3.0 license that can be found in the LICENSE file.
"""Android branding against the pinned Chromium resource layout."""

from pathlib import Path
import os
import tempfile
import unittest
import xml.etree.ElementTree as xml

from android.branding import apply_branding, validate_app_name

# Exact upstream fixture, including its copyright notice:
# https://github.com/chromium/chromium/blob/156.0.8078.47/
# chrome/android/java/res_chromium_base/values/channel_constants.xml
CHROMIUM_CHANNEL_CONSTANTS = '''<?xml version="1.0" encoding="utf-8"?>
<!--
Copyright 2015 The Chromium Authors
Use of this source code is governed by a BSD-style license that can be
found in the LICENSE file.
-->

<resources xmlns:android="http://schemas.android.com/apk/res/android">
    <!-- The application name displayed to the user. -->
    <string name="app_name" translatable="false">Chromium</string>
    <string name="bookmark_widget_title" translatable="false">Chromium bookmarks</string>
    <string name="search_widget_title" translatable="false">Chromium search</string>
    <string name="quick_action_search_widget_title" translatable="false">Chromium quick action search</string>
</resources>
'''

RESOURCE_PATH = Path('chrome/android/java/res_chromium_base/values/channel_constants.xml')


class BrandingTests(unittest.TestCase):
    """Exercise real temporary files; never require Chromium or the SDK."""

    def setUp(self):
        self.temporary_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_dir.cleanup)
        self.directory = Path(self.temporary_dir.name)
        self.src = self.directory / 'src'
        self.resource = self.src / RESOURCE_PATH
        self.resource.parent.mkdir(parents=True)
        self.resource.write_text(CHROMIUM_CHANNEL_CONSTANTS, encoding='utf-8')

    def test_labels_use_the_requested_name(self):
        apply_branding(self.src, 'Curib Browser')

        strings = {node.get('name'): node for node in xml.parse(self.resource).getroot()}
        self.assertEqual(strings['app_name'].text, '"Curib Browser"')
        self.assertEqual(strings['bookmark_widget_title'].text, '"Curib Browser bookmarks"')
        self.assertEqual(strings['search_widget_title'].text, '"Curib Browser search"')
        self.assertEqual(strings['quick_action_search_widget_title'].text,
                         '"Curib Browser quick action search"')
        for node in strings.values():
            self.assertEqual(node.get('translatable'), 'false')
            self.assertEqual(node.get('formatted'), 'false')

    def test_license_and_other_resources_are_preserved(self):
        unrelated = '<string name="internal_package">org.chromium.chrome</string>\n'
        original = CHROMIUM_CHANNEL_CONSTANTS.replace('</resources>', unrelated + '</resources>')
        self.resource.write_text(original, encoding='utf-8')
        other = self.src / 'unrelated.xml'
        other.write_text('Chromium must stay here.', encoding='utf-8')

        apply_branding(self.src, 'Curib Browser')

        result = self.resource.read_text(encoding='utf-8')
        self.assertEqual(result.split('    <string', 1)[0], original.split('    <string', 1)[0])
        self.assertTrue(result.endswith(unrelated + '</resources>\n'))
        self.assertEqual(other.read_text(encoding='utf-8'), 'Chromium must stay here.')

    def test_xml_and_android_characters_are_escaped_as_literal_text(self):
        apply_branding(self.src, 'Curib & <Browser> \\"Owner\'s" @home? %s %d')

        root = xml.parse(self.resource).getroot()
        self.assertEqual(len(root), 4)
        self.assertEqual(
            root.find("string[@name='app_name']").text,
            r'''"Curib & <Browser> \\\"Owner\'s\" \@home\? %s %d"''')
        self.assertNotIn('<Browser>', self.resource.read_text(encoding='utf-8'))

    def test_unicode_and_repeated_spaces_are_preserved(self):
        apply_branding(self.src, 'Curib  浏览器')
        self.assertEqual(
            xml.parse(self.resource).find("string[@name='app_name']").text, '"Curib  浏览器"')

    def test_reapplying_does_not_rewrite_the_resource(self):
        apply_branding(self.src, 'Curib Browser')
        original = self.resource.read_bytes()
        os.utime(self.resource, ns=(1_000_000_000, 1_000_000_000))

        apply_branding(self.src, 'Curib Browser')

        self.assertEqual(self.resource.read_bytes(), original)
        self.assertEqual(self.resource.stat().st_mtime_ns, 1_000_000_000)

    def test_rebranding_replaces_a_previous_name(self):
        apply_branding(self.src, 'First Browser')
        apply_branding(self.src, 'Second Browser')

        root = xml.parse(self.resource).getroot()
        self.assertEqual(root.find("string[@name='app_name']").text, '"Second Browser"')
        self.assertNotIn('First Browser', self.resource.read_text(encoding='utf-8'))

    def test_invalid_names_are_rejected_before_writing(self):
        for name in ('', ' ', ' Browser', 'Browser ', 'Browser\n', 'Browser\tName', 'Browser\x00',
                     'Browser\u202e', '\ud800', 'a' * 81, None, 123):
            with self.subTest(name=repr(name)):
                with self.assertRaises(ValueError):
                    validate_app_name(name)
                with self.assertRaises(ValueError):
                    apply_branding(self.src, name)
                self.assertEqual(self.resource.read_text(encoding='utf-8'),
                                 CHROMIUM_CHANNEL_CONSTANTS)

    def test_invalid_source_path_is_rejected(self):
        for path in (self.directory / 'absent', self.resource):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    apply_branding(path, 'Curib Browser')

    def test_missing_resource_is_rejected(self):
        self.resource.unlink()
        with self.assertRaises(ValueError):
            apply_branding(self.src, 'Curib Browser')

    def test_resource_cannot_resolve_outside_the_source_tree(self):
        outside = self.directory / 'outside.xml'
        outside.write_text(CHROMIUM_CHANNEL_CONSTANTS, encoding='utf-8')
        self.resource.unlink()
        self.resource.symlink_to(outside)

        with self.assertRaises(ValueError):
            apply_branding(self.src, 'Curib Browser')

        self.assertEqual(outside.read_text(encoding='utf-8'), CHROMIUM_CHANNEL_CONSTANTS)

    def test_resource_directory_cannot_resolve_outside_the_source_tree(self):
        outside = self.directory / 'outside'
        outside.mkdir()
        moved_resource = outside / self.resource.name
        self.resource.replace(moved_resource)
        self.resource.parent.rmdir()
        self.resource.parent.symlink_to(outside, target_is_directory=True)

        with self.assertRaises(ValueError):
            apply_branding(self.src, 'Curib Browser')

        self.assertEqual(moved_resource.read_text(encoding='utf-8'), CHROMIUM_CHANNEL_CONSTANTS)

    def test_unexpected_or_malformed_xml_is_not_modified(self):
        for original in (
                CHROMIUM_CHANNEL_CONSTANTS.replace('</resources>', ''),
                CHROMIUM_CHANNEL_CONSTANTS.replace('name="app_name"', 'name="unknown_name"'),
                CHROMIUM_CHANNEL_CONSTANTS.replace(
                    '</resources>',
                    '<string name="app_name" translatable="false">Extra</string></resources>'),
                CHROMIUM_CHANNEL_CONSTANTS.replace('>Chromium</string>',
                                                   '>Chr<b>om</b>ium</string>'),
                CHROMIUM_CHANNEL_CONSTANTS.replace('translatable="false"', 'translatable="true"'),
                CHROMIUM_CHANNEL_CONSTANTS.replace('<resources ',
                                                   '<wrong ').replace('</resources>', '</wrong>'),
                CHROMIUM_CHANNEL_CONSTANTS.replace(
                    '<resources ', '<!DOCTYPE resources [<!ENTITY label "Injected">]><resources '),
        ):
            with self.subTest(original=original):
                self.resource.write_text(original, encoding='utf-8')
                with self.assertRaises(ValueError):
                    apply_branding(self.src, 'Curib Browser')
                self.assertEqual(self.resource.read_text(encoding='utf-8'), original)


if __name__ == '__main__':
    unittest.main()
