# Copyright 2026 The Helium Authors
# You can use, redistribute, and/or modify this source code under
# the terms of the GPL-3.0 license that can be found in the LICENSE file.
"""Brand native Android labels after Helium's resource and i18n preparation.

The resource path and names were checked against Chromium 156.0.8078.47:
https://github.com/chromium/chromium/blob/156.0.8078.47/chrome/android/java/
res_chromium_base/values/channel_constants.xml

The application ID is set separately using Chromium's supported
``chrome_public_manifest_package`` GN argument. Java packages, license notices,
in-browser Helium strings, and launcher artwork are not changed here.
"""

from pathlib import Path
import re
import xml.etree.ElementTree as xml
from xml.sax.saxutils import escape

RESOURCE_PATH = Path('chrome/android/java/res_chromium_base/values/channel_constants.xml')
_LABEL_SUFFIXES = {
    'app_name': '',
    'bookmark_widget_title': ' bookmarks',
    'search_widget_title': ' search',
    'quick_action_search_widget_title': ' quick action search',
}


def validate_app_name(app_name: str) -> None:
    """Require 1–80 printable characters without leading or trailing spaces."""
    if (not isinstance(app_name, str) or not 1 <= len(app_name) <= 80
            or app_name != app_name.strip() or not app_name.isprintable()):
        raise ValueError(
            'app name must be 1–80 printable characters without surrounding whitespace')


def apply_branding(src: Path, app_name: str) -> None:
    """Set the native app/widget labels, failing before writes if the layout changed.

Only the four known string elements are edited. Their existing non-translatable
status is retained, and ``formatted=false`` makes percent signs literal. Android
quoting preserves repeated spaces; both Android escapes and XML entities are
applied so user input cannot become a resource reference or markup.

See https://developer.android.com/guide/topics/resources/string-resource
for the two distinct escaping stages.
"""
    validate_app_name(app_name)
    src = Path(src).resolve()
    if not src.is_dir():
        raise ValueError(f'Chromium source directory does not exist: {src}')
    resource = (src / RESOURCE_PATH).resolve()
    if not resource.is_relative_to(src) or not resource.is_file():
        raise ValueError(f'expected Android branding resource inside {src}: {RESOURCE_PATH}')

    original = resource.read_bytes().decode('utf-8')
    if '<!DOCTYPE' in original or '<!ENTITY' in original:
        raise ValueError(f'unsupported XML declarations in {RESOURCE_PATH}')
    try:
        root = xml.fromstring(original)
    except xml.ParseError as error:
        raise ValueError(f'invalid Android branding XML: {RESOURCE_PATH}') from error
    if root.tag != 'resources':
        raise ValueError(f'expected <resources> in {RESOURCE_PATH}')

    modified = original
    for name, suffix in _LABEL_SUFFIXES.items():
        nodes = root.findall(f"string[@name='{name}']")
        if len(nodes) != 1 or len(nodes[0]) or nodes[0].get('translatable') != 'false':
            raise ValueError(f'expected one non-translatable plain string named {name}')

        # Target only known string elements, preserving comments, the original
        # license header, namespace spelling, and all unrelated resource bytes.
        pattern = re.compile(rf'(?P<opening><string\b[^>]*\bname\s*=\s*(?P<quote>[\'\"])'
                             rf'{name}(?P=quote)[^>]*>)[^<]*(?P<closing></string\s*>)')
        matches = list(pattern.finditer(modified))
        if len(matches) != 1:
            raise ValueError(f'unexpected XML layout for Android string {name}')
        match = matches[0]
        opening = match.group('opening')
        if nodes[0].get('formatted') is None:
            opening = opening[:-1] + ' formatted="false">'
        elif nodes[0].get('formatted') != 'false':
            opening = re.sub(r'\bformatted\s*=\s*([\'\"]).*?\1', 'formatted="false"', opening)

        label = ''.join('\\' + char if char in '\\\'"@?' else char for char in app_name + suffix)
        replacement = opening + '"' + escape(label) + '"' + match.group('closing')
        modified = modified[:match.start()] + replacement + modified[match.end():]

    # Validate the complete result before touching the source tree. A repeat
    # application with the same label leaves both contents and mtime unchanged.
    xml.fromstring(modified)
    if modified != original:
        resource.write_bytes(modified.encode('utf-8'))
